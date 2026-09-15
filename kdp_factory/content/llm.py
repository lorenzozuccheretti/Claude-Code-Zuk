"""The optional LLM hook.

Off by default, on purpose. Determinism is what makes the determinism test
meaningful and what makes a build reproducible a month later, so the engine
must be complete without a model in the loop.

When it is switched on, a provider can do two things:

* enrich copy (a sharper back-cover hook, a description rewrite), and
* give the substance gate a second opinion.

The second opinion is advisory unless ``llm.allow_llm_to_fail_gate`` is set.
A model's mood is not a build-stopping criterion by default; code is.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import LLMConfig


@dataclass(frozen=True)
class LLMVerdict:
    """What a model thought of an interior it was shown."""

    available: bool
    passed: bool | None = None
    notes: list[str] = None  # type: ignore[assignment]
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "passed": self.passed,
            "notes": list(self.notes or []),
            "error": self.error,
        }


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1000) -> str: ...


class NullProvider:
    """The default: answers nothing, costs nothing, changes nothing."""

    name = "null"

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1000) -> str:
        return ""


class AnthropicProvider:
    """Thin wrapper over the Anthropic SDK, imported only when actually used."""

    name = "anthropic"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{config.api_key_env} is not set; either export it or leave llm.enabled false"
            )
        try:
            import anthropic  # noqa: PLC0415 - optional dependency
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError(
                "the anthropic package is not installed; pip install 'kdp-factory[llm]'"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1000) -> str:
        message = self._client.messages.create(
            model=self.config.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=self.config.temperature,
            system=system or "You are a demanding editor. Answer only in the format asked for.",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )


def get_provider(config: LLMConfig) -> LLMProvider:
    if not config.enabled or config.provider == "null":
        return NullProvider()
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    raise RuntimeError(f"unknown LLM provider {config.provider!r}")


GRADER_SYSTEM = (
    "You are grading a finished book you did not write. You are a buyer who paid "
    "for it, not its author. Answer strictly as JSON."
)

GRADER_PROMPT = """Below is a sample of a self-published book's interior text, and what it claims to be.

TITLE: {title}
SUBTITLE: {subtitle}
PAGE COUNT: {page_count}

SAMPLE PAGES:
{sample}

Grade it on: padding, repetition, promise (does it deliver what the title claims),
value for money, errors, completeness.

Reply with JSON only, in this exact shape:
{{"passed": true|false, "notes": ["one line per problem, empty list if none"]}}
"""


def grade_interior(
    config: LLMConfig,
    title: str,
    subtitle: str,
    page_count: int,
    sample_pages: list[str],
    max_sample_pages: int = 12,
) -> LLMVerdict:
    """Ask a model for a second opinion on the interior. Never required."""
    if not config.enabled:
        return LLMVerdict(available=False, error="llm hook disabled")
    try:
        provider = get_provider(config)
    except RuntimeError as exc:
        return LLMVerdict(available=False, error=str(exc))

    sample = "\n\n---\n\n".join(
        page.strip() for page in sample_pages[:max_sample_pages] if page.strip()
    )
    try:
        raw = provider.complete(
            GRADER_PROMPT.format(
                title=title, subtitle=subtitle, page_count=page_count, sample=sample
            ),
            system=GRADER_SYSTEM,
            max_tokens=config.max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - never let the hook break a build
        return LLMVerdict(available=False, error=f"{type(exc).__name__}: {exc}")

    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return LLMVerdict(available=False, error="the model did not return usable JSON")
    return LLMVerdict(
        available=True,
        passed=bool(data.get("passed")),
        notes=[str(n) for n in (data.get("notes") or [])],
    )
