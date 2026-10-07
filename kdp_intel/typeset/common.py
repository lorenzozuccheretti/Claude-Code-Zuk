"""What both typesetters share: the book to set, its geometry, inline parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kdp_factory.spec import kdp as spec

from ..config import BookMeta
from ..models import ChapterDraft, Outline

FONTS_DIR = Path(spec.__file__).resolve().parents[1] / "assets" / "fonts"

# bold, italic, citation; anything else is plain text.
_INLINE = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|\[\[S:([a-z0-9]+-[0-9a-f]{6})\]\]")

WORDS_PER_PAGE = 330  # 6x9, 10.5pt Lora, with tables and callouts


@dataclass
class BookContent:
    meta: BookMeta
    outline: Outline
    chapters: list[ChapterDraft]
    sources: dict[str, dict[str, Any]]  # the ingest manifest
    facts_as_of: str
    index_terms: list[str] = field(default_factory=list)

    def words(self) -> int:
        from ..agents.writer import word_count

        return sum(word_count(c) for c in self.chapters)


@dataclass(frozen=True)
class Geometry:
    trim_w: float
    trim_h: float
    inside: float
    outside: float
    top: float
    bottom: float
    planned_pages: int
    spec_gutter: float

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in ("trim_w", "trim_h", "inside", "outside", "top", "bottom",
                                               "planned_pages", "spec_gutter")}


def geometry(trim: str, page_count: int) -> Geometry:
    """Margins for this page count. The inside margin is KDP's gutter minimum
    for the thickness plus a reading allowance; never less than the spec."""
    t = spec.trim_size(trim)
    pages = max(int(spec.KDP_SPEC["page_count"]["min"]), page_count)
    gutter = spec.gutter_margin_in(pages)
    return Geometry(
        trim_w=t.width, trim_h=t.height,
        inside=round(max(0.75, gutter + 0.3), 3),
        outside=max(0.5, spec.outside_margin_in(bleed=False)),
        top=0.75, bottom=0.8, planned_pages=pages, spec_gutter=gutter,
    )


def inline_tokens(text: str) -> list[tuple[str, str]]:
    """[("text"|"strong"|"emph"|"cite", value), ...]"""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(("strong", m.group(1)))
        elif m.group(2) is not None:
            out.append(("emph", m.group(2)))
        else:
            out.append(("cite", m.group(3)))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    # A citation marker sits after the full stop it supports; keep the
    # footnote mark tight against the punctuation.
    for i, (kind, value) in enumerate(out):
        if kind == "text" and i + 1 < len(out) and out[i + 1][0] == "cite":
            out[i] = (kind, value.rstrip())
    return out


def citation_text(meta: dict[str, Any] | None, sid: str) -> tuple[str, str]:
    """(label, url) for a footnote or a bibliography line."""
    if not meta:
        return f"Fonte {sid}", ""
    when = meta.get("published") or ""
    accessed = meta.get("retrieved") or ""
    parts = [meta.get("publisher") or "", f"«{meta.get('title') or sid}»"]
    if when:
        parts.append(when)
    label = ", ".join(p for p in parts if p)
    if accessed:
        label += f" (consultato il {accessed})"
    return label, meta.get("url", "")


PART_LABELS = ["Introduzione", "Parte prima", "Parte seconda", "Parte terza", "Conclusione"]
