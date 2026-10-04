"""Helium 10, through its CSV exports.

Helium 10 has no public API for Cerebro or Magnet, so the engine reads the
CSV you export from either tool (amazon.it marketplace selected). Column
names differ between tools and versions; the aliases below cover the
current exports, and an unrecognised file fails loudly with the columns it
did find.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import KeywordMetric
from .base import ProviderError

_KEYWORD = ("Keyword Phrase", "Keyword", "Search Term")
_VOLUME = ("Search Volume", "Keyword Sales", "Estimated Search Volume")
_COMPETING = ("Competing Products", "Competitor Count", "Titles Competing")


def _pick(header: list[str], aliases: tuple[str, ...]) -> str | None:
    return next((h for h in header if h.strip() in aliases), None)


def _int(value: str) -> int | None:
    digits = value.replace(".", "").replace(",", "").replace(">", "").strip()
    return int(digits) if digits.isdigit() else None


class Helium10Export:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def volumes(self, keywords: list[str] | None = None) -> list[KeywordMetric]:
        with self.path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            kw, vol = _pick(header, _KEYWORD), _pick(header, _VOLUME)
            comp = _pick(header, _COMPETING)
            if not (kw and vol):
                raise ProviderError(f"{self.path.name}: not a Cerebro/Magnet export; columns: {header}")
            wanted = {k.lower() for k in keywords} if keywords else None
            out = []
            for row in reader:
                phrase = row[kw].strip()
                if wanted and phrase.lower() not in wanted:
                    continue
                out.append(KeywordMetric(
                    keyword=phrase,
                    search_volume=_int(row[vol]),
                    competing_products=_int(row[comp]) if comp else None,
                    source="helium10:csv",
                ))
            return out
