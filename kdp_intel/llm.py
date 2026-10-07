"""The model interface every agent uses, plus the paid Claude backend.

Agents ask for a pydantic model back (``structured``) and never parse prose,
so any backend that can return JSON for a schema will do. The free backends
(this Claude Code session by hand-off, Gemini's free tier, OpenRouter's free
models, a local Ollama) live in ``llm_free.py``.

``ClaudeLLM`` streams, because a chapter is long and a non-streaming call
with a large ``max_tokens`` risks the HTTP timeout. Refusals are routed
server-side to a fallback model (``fallbacks: "default"``); a refusal that
survives the chain raises instead of returning an empty object.

Tests use ``ScriptedLLM``, which answers from a function and costs nothing.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def structured(
        self, *, system: str, prompt: str, schema: type[T], effort: str = "high",
        max_tokens: int = 32000,
    ) -> T: ...


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """The same schema with every ``$ref`` to ``$defs`` replaced by its
    definition. Not every provider resolves references; all accept this."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A pydantic JSON schema reshaped for structured outputs.

    Every object gets ``additionalProperties: false`` and lists all of its
    properties as required; ``title`` and ``default`` keys are dropped.
    """

    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


class ClaudeLLM:
    """Claude through the official SDK, streaming, with refusal fallbacks."""

    def __init__(self, model: str = DEFAULT_MODEL, fallbacks: bool = True) -> None:
        import anthropic  # noqa: PLC0415 - optional at import time for tests

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.fallbacks = fallbacks
        self.usage: list[dict[str, int]] = []

    def structured(
        self, *, system: str, prompt: str, schema: type[T], effort: str = "high",
        max_tokens: int = 32000,
    ) -> T:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # The system prompt is the stable prefix across a run; cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": strict_schema(schema)},
            },
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"cannot reach the Claude API: {exc}") from exc

        usage = message.usage
        self.usage.append(
            {
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "cache_read": usage.cache_read_input_tokens or 0,
            }
        )
        if message.stop_reason == "refusal":
            category = getattr(message.stop_details, "category", None)
            raise LLMError(f"request declined (category: {category})")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"output hit max_tokens={max_tokens}; raise it or split the task")
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            return schema.model_validate_json(text)
        except ValueError as exc:
            raise LLMError(f"model output did not match {schema.__name__}: {exc}") from exc


class ScriptedLLM:
    """Deterministic stand-in: ``responder(schema, system, prompt)`` returns a
    model instance or a dict. Every call is recorded for assertions."""

    def __init__(self, responder: Callable[[type[BaseModel], str, str], Any]) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str]] = []

    def structured(
        self, *, system: str, prompt: str, schema: type[T], effort: str = "high",
        max_tokens: int = 32000,
    ) -> T:
        self.calls.append((schema.__name__, prompt))
        out = self.responder(schema, system, prompt)
        if isinstance(out, schema):
            return out
        if isinstance(out, str):
            return schema.model_validate_json(out)
        return schema.model_validate(json.loads(json.dumps(out)))
