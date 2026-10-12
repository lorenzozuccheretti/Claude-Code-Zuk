"""Buyer-visible words, per language.

Every string a buyer reads that is not content — "How to use this book", the
copyright lines, the back-cover sentence, the cover flash — lives in a YAML
table next to the template packs, one per language. The renderers and the copy
ask for a key; they never carry a sentence of their own.

That is what lets one engine print an Italian book without an Italian fork of
the renderer, and it keeps the English output byte-identical: the English
table holds exactly the strings the code used to hard-wire.

``Brand.language`` picks the table. It is normalised once, here, so "en",
"English" and "english" are the same book and a typo is a ConfigError rather
than a silently English Italian book.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from ..errors import ConfigError
from ..spec.kdp import trim_size
from .packs import TEMPLATES_DIR

# Accepted spellings -> canonical language name. The canonical name is what
# Brand.language holds after normalising, and what the locale file is keyed by.
LANGUAGES: dict[str, str] = {
    "english": "english",
    "en": "english",
    "italian": "italian",
    "italiano": "italian",
    "it": "italian",
}

# Canonical language -> locale file suffix.
CODES: dict[str, str] = {"english": "en", "italian": "it"}


def normalize_language(language: str) -> str:
    """The canonical name for a language, or a ConfigError naming the options."""
    key = str(language or "").strip().lower()
    if key in LANGUAGES:
        return LANGUAGES[key]
    raise ConfigError(
        f"unknown language {language!r}; the factory has locale tables for: "
        f"{', '.join(sorted(LANGUAGES))}"
    )


def language_code(language: str) -> str:
    return CODES[normalize_language(language)]


@lru_cache(maxsize=None)
def _load(code: str) -> dict[str, Any]:
    path = TEMPLATES_DIR / f"locale_{code}.yaml"
    if not path.is_file():
        raise ConfigError(f"locale table not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"locale table {path} must contain a mapping")
    return data


def strings(language: str) -> dict[str, Any]:
    """The whole string table for a language. Cached; treat it as read-only."""
    return _load(language_code(language))


def lookup(table: dict[str, Any], key: str) -> Any:
    """A dotted key from a table, e.g. ``lookup(t, "interior.date")``.

    A missing key is a defect in the locale file, so it fails loudly with the
    key's name rather than printing nothing on the page.
    """
    node: Any = table
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(
                f"locale table for {table.get('language', '?')!r} has no key {key!r}"
            )
        node = node[part]
    return node


def text(table: dict[str, Any], key: str, **fields: Any) -> str:
    """A dotted key, formatted with ``fields`` (``str.format`` placeholders)."""
    value = lookup(table, key)
    if not isinstance(value, str):
        raise ConfigError(f"locale key {key!r} is not a string")
    return value.format(**fields) if fields else value


def format_trim(table: dict[str, Any], trim: str, style: str) -> str:
    """A trim size written the way this language writes it.

    ``style`` is a key under ``trim`` in the locale table ("cover", "listing").
    English keeps the inch strings exactly as the niche spells them ("8.5",
    "6"), because that is what the books have always printed; a metric locale
    gets centimetres to one decimal with its own decimal mark.
    """
    pattern = lookup(table, f"trim.{style}")
    width_raw, _, height_raw = str(trim).partition("x")
    spec = trim_size(trim)
    mark = str(table.get("decimal", "."))

    def cm(inches: float) -> str:
        return f"{inches * 2.54:.1f}".replace(".", mark)

    return pattern.format(
        w=width_raw.strip(), h=height_raw.strip(),
        w_cm=cm(spec.width), h_cm=cm(spec.height),
    )


__all__ = [
    "CODES",
    "LANGUAGES",
    "format_trim",
    "language_code",
    "lookup",
    "normalize_language",
    "strings",
    "text",
]
