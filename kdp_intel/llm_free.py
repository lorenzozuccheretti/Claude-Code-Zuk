"""Model backends that cost nothing.

* ``HandoffLLM``   - each request becomes a file in ``handoff/``; whoever
                     answers it (this Claude Code session, or you pasting it
                     into a chat you already pay nothing extra for) writes the
                     JSON next to it, and the next run picks it up. No key.
* ``GeminiLLM``    - Google AI Studio free tier (``GEMINI_API_KEY``, no card).
                     Paced to stay under the per-minute limit.
* ``OpenAICompatLLM`` - any OpenAI-compatible endpoint: OpenRouter's ``:free``
                     models, a local Ollama or LM Studio.

Every backend sits behind ``CachedLLM``: an answer is stored under the hash
of (schema, system, prompt), so a rerun never spends a request twice, a
daily quota that runs out loses nothing, and a stopped run resumes where it
stopped. Answers are validated against the schema before they are trusted;
an invalid answer gets one repair attempt with the validation error.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .llm import LLM, LLMError, T, inline_refs, strict_schema


class PendingLLM(Exception):
    """A hand-off request is waiting for an answer."""

    def __init__(self, task_ids: list[str], folder: Path) -> None:
        super().__init__(f"{len(task_ids)} richieste in attesa in {folder}")
        self.task_ids, self.folder = task_ids, folder


def task_id(schema: type[BaseModel], system: str, prompt: str) -> str:
    digest = hashlib.sha256(f"{schema.__name__}\n{system}\n{prompt}".encode()).hexdigest()
    return f"{schema.__name__.lower()}-{digest[:12]}"


def _json_from(text: str) -> str:
    """The JSON object in a reply, tolerating a ```json fence around it."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "Rispondi con un solo oggetto JSON valido che rispetti questo JSON Schema "
        "(tutti i campi sono obbligatori; usa stringhe o liste vuote per i campi non usati). "
        "Nessun testo prima o dopo il JSON.\n\n"
        + json.dumps(inline_refs(strict_schema(schema)), ensure_ascii=False, indent=1)
    )


class CachedLLM:
    """Answers from disk when it can, asks the backend when it must."""

    def __init__(self, inner: Any, folder: Path) -> None:
        self.inner = inner
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.calls: list[tuple[str, str]] = []
        self.hits = 0

    def structured(self, *, system: str, prompt: str, schema: type[T], effort: str = "high",
                   max_tokens: int = 32000) -> T:
        tid = task_id(schema, system, prompt)
        answer = self.folder / f"{tid}.json"
        if answer.exists():
            try:
                result = schema.model_validate_json(_json_from(answer.read_text(encoding="utf-8")))
                self.hits += 1
                return result
            except ValidationError as exc:
                if self.inner is None:  # a hand-written answer that does not fit
                    raise LLMError(f"{answer.name} non rispetta lo schema {schema.__name__}: {exc}") from exc
        self.calls.append((schema.__name__, prompt))
        if self.inner is None:
            self._write_request(tid, schema, system, prompt)
            raise PendingLLM([tid], self.folder)
        result = self.inner.structured(system=system, prompt=prompt, schema=schema, effort=effort,
                                       max_tokens=max_tokens)
        answer.write_text(result.model_dump_json(indent=1), encoding="utf-8")
        return result

    def _write_request(self, tid: str, schema: type[BaseModel], system: str, prompt: str) -> None:
        (self.folder / f"{tid}.request.md").write_text(
            f"# Richiesta {tid}\n\n"
            f"Scrivi la risposta in `{tid}.json` nella stessa cartella, poi rilancia il comando.\n\n"
            f"## Istruzioni di sistema\n\n{system}\n\n## Richiesta\n\n{prompt}\n\n"
            f"## Formato della risposta\n\n{schema_instructions(schema)}\n",
            encoding="utf-8",
        )

    def pending(self) -> list[Path]:
        return sorted(p for p in self.folder.glob("*.request.md")
                      if not (self.folder / p.name.replace(".request.md", ".json")).exists())


class _Repairing:
    """Shared validate-and-repair loop for backends that return text."""

    def _complete(self, system: str, prompt: str, schema: type[BaseModel], max_tokens: int) -> str:
        raise NotImplementedError

    def structured(self, *, system: str, prompt: str, schema: type[T], effort: str = "high",
                   max_tokens: int = 32000) -> T:
        text = self._complete(system, prompt, schema, max_tokens)
        try:
            return schema.model_validate_json(_json_from(text))
        except ValidationError as exc:
            repair = (f"{prompt}\n\nLa tua risposta precedente non rispettava lo schema:\n{exc}\n"
                      f"Risposta precedente:\n{text[:6000]}\n\nRestituisci il JSON corretto.")
            text = self._complete(system, repair, schema, max_tokens)
            try:
                return schema.model_validate_json(_json_from(text))
            except ValidationError as exc2:
                raise LLMError(f"risposta non conforme a {schema.__name__}: {exc2}") from exc2


class GeminiLLM(_Repairing):
    """Gemini API free tier. Free-tier prompts may be used by Google to
    improve its products: fine for public sources, not for private data."""

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash", rpm: int = 8,
                 client: httpx.Client | None = None, max_wait: float = 120.0) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY non impostata (gratuita su aistudio.google.com)")
        self.api_key, self.model = api_key, model
        self.min_interval = 60.0 / max(1, rpm)
        self.client = client or httpx.Client(timeout=300.0)
        self.max_wait = max_wait
        self._last = 0.0

    def _complete(self, system: str, prompt: str, schema: type[BaseModel], max_tokens: int) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": min(max_tokens, 65536),
                "responseFormat": {"text": {"mimeType": "application/json",
                                            "schema": inline_refs(strict_schema(schema))}},
            },
        }
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.client.post(self.ENDPOINT.format(model=self.model), json=body,
                                 headers={"x-goog-api-key": self.api_key})
            if r.status_code == 429:  # per-minute limit: wait as told; per-day: stop
                text = r.text
                if "PerDay" in text or "per day" in text.lower():
                    raise LLMError("quota giornaliera Gemini esaurita: rilancia domani, "
                                   "le risposte già ricevute sono in cache")
                m = re.search(r'"retryDelay":\s*"(\d+)', text)
                time.sleep(min(self.max_wait, float(m.group(1)) if m else 2 ** attempt * 5))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code >= 400:
                raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:400]}")
            data = r.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise LLMError(f"Gemini non ha risposto: {data.get('promptFeedback')}")
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        raise LLMError("Gemini: troppi tentativi falliti (limite di frequenza)")


class OpenAICompatLLM(_Repairing):
    """OpenRouter ``:free`` models, Ollama (``http://localhost:11434/v1``),
    LM Studio, or any other OpenAI-compatible chat endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "", client: httpx.Client | None = None,
                 rpm: int = 15) -> None:
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.client = client or httpx.Client(timeout=600.0)
        self.min_interval = 60.0 / max(1, rpm)
        self._last = 0.0
        self._json_schema_ok = True

    def _complete(self, system: str, prompt: str, schema: type[BaseModel], max_tokens: int) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"{prompt}\n\n{schema_instructions(schema)}"}]
        for attempt in range(6):
            fmt = ({"type": "json_schema", "json_schema": {
                       "name": schema.__name__, "strict": True,
                       "schema": inline_refs(strict_schema(schema))}}
                   if self._json_schema_ok else {"type": "json_object"})
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.client.post(f"{self.base_url}/chat/completions", headers=headers, json={
                "model": self.model, "messages": messages, "max_tokens": max_tokens, "response_format": fmt})
            if r.status_code == 400 and self._json_schema_ok:
                self._json_schema_ok = False  # this model only knows json_object
                continue
            if r.status_code in (429, 502, 503):
                time.sleep(2 ** attempt * 5)
                continue
            if r.status_code >= 400:
                raise LLMError(f"{self.base_url} HTTP {r.status_code}: {r.text[:400]}")
            return r.json()["choices"][0]["message"]["content"] or ""
        raise LLMError(f"{self.model}: troppi tentativi falliti")


def make_llm(kind: str, cache_dir: Path, model: str = "") -> CachedLLM:
    """``auto`` uses Gemini if GEMINI_API_KEY is set, otherwise hand-off."""
    env = os.environ.get
    if kind == "auto":
        kind = "gemini" if env("GEMINI_API_KEY") else "handoff"
    if kind == "handoff":
        inner = None
    elif kind == "gemini":
        inner = GeminiLLM(env("GEMINI_API_KEY", ""), model or "gemini-3.8-flash")
    elif kind == "openrouter":
        if not env("OPENROUTER_API_KEY"):
            raise LLMError("OPENROUTER_API_KEY non impostata (gratuita su openrouter.ai)")
        inner = OpenAICompatLLM("https://openrouter.ai/api/v1", model or "openrouter/free",
                                env("OPENROUTER_API_KEY", ""))
    elif kind == "ollama":
        inner = OpenAICompatLLM(env("OLLAMA_BASE_URL", "http://localhost:11434/v1"), model or "qwen3:14b")
    elif kind == "claude":
        from .llm import ClaudeLLM  # noqa: PLC0415 - paid, optional

        inner = ClaudeLLM(model=model or "claude-opus-5-5")
    else:
        raise LLMError(f"backend sconosciuto {kind!r}: handoff | gemini | openrouter | ollama | claude")
    return CachedLLM(inner, cache_dir)


__all__ = ["CachedLLM", "GeminiLLM", "OpenAICompatLLM", "PendingLLM", "make_llm", "task_id", "LLM"]
