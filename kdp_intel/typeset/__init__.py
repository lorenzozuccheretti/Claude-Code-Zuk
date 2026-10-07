"""Set the book, then prove the PDF is printable.

The gutter depends on the page count and the page count depends on the
gutter, so setting runs at most three passes: plan from the word count,
compile, and recompile only if the real count lands in a different KDP
gutter band than the one used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .common import WORDS_PER_PAGE, BookContent, Geometry, geometry
from .preflight import Preflight, pad_to_even, preflight


@dataclass
class TypesetResult:
    pdf: Path
    source: Path
    geometry: Geometry
    preflight: Preflight
    passes: int


def _render(book: BookContent, engine: str, geo: Geometry, build: Path) -> tuple[Path, Path]:
    if engine == "typst":
        from .typst_engine import TypstWriter, compile_typst

        pdf = compile_typst(TypstWriter(book).document(geo), build)
        return pdf, build / "interior.typ"
    if engine == "weasyprint":
        from .weasy_engine import HtmlWriter, compile_html

        pdf = compile_html(HtmlWriter(book).document(geo), build)
        return pdf, build / "interior.html"
    raise ValueError(f"unknown engine {engine!r}: typst | weasyprint")


def typeset(book: BookContent, out_dir: str | Path, engine: str | None = None) -> TypesetResult:
    engine = engine or book.meta.engine
    build = Path(out_dir)
    planned = 12 + book.words() // WORDS_PER_PAGE  # front and back matter ~12 pages
    geo = geometry(book.meta.trim, planned)
    passes = 0
    while True:
        passes += 1
        pdf, source = _render(book, engine, geo, build)
        pages = pad_to_even(pdf)
        actual = geometry(book.meta.trim, pages)
        if actual.spec_gutter <= geo.spec_gutter or passes >= 3:
            break
        geo = actual
    report = preflight(pdf, book.meta.trim, book.meta.paper, geo)
    (build / "preflight.json").write_text(
        json.dumps({"engine": engine, "passes": passes, "geometry": geo.as_dict(), **report.as_dict()},
                   indent=2), encoding="utf-8")
    return TypesetResult(pdf=pdf, source=source, geometry=geo, preflight=report, passes=passes)


__all__ = ["BookContent", "typeset", "TypesetResult"]
