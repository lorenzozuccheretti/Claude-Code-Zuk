"""Reading back what was written.

The gates do not ask the renderer what it produced — they open the PDF. These
helpers are the only way they look at it.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

POINTS_PER_INCH = 72.0


def _reader(path: Path) -> PdfReader:
    return PdfReader(str(path))


def pdf_page_count(path: str | Path) -> int:
    """The page count of the file that actually exists on disk."""
    return len(_reader(Path(path)).pages)


def pdf_page_sizes_in(path: str | Path) -> list[tuple[float, float]]:
    """Every page's trim box in inches, rounded to a thousandth."""
    sizes = []
    for page in _reader(Path(path)).pages:
        box = page.mediabox
        sizes.append(
            (
                round(float(box.width) / POINTS_PER_INCH, 3),
                round(float(box.height) / POINTS_PER_INCH, 3),
            )
        )
    return sizes


def embedded_fonts(path: str | Path) -> set[str]:
    """Every font the PDF actually carries, by BaseFont name.

    A book that silently fell back to Helvetica looks like a memo, and the only
    place that shows up is here — the plan and the renderer both believed they
    were using the real faces.
    """
    names: set[str] = set()
    for page in _reader(Path(path)).pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        fonts = resources.get("/Font")
        if not fonts:
            continue
        try:
            fonts = fonts.get_object()
        except AttributeError:  # pragma: no cover - already resolved
            pass
        for entry in fonts.values():
            try:
                base = entry.get_object().get("/BaseFont")
            except AttributeError:  # pragma: no cover
                continue
            if base:
                # Subset names are prefixed like "AAAAAA+Lora-Regular".
                names.add(str(base).lstrip("/").split("+")[-1])
    return names


def extract_pages_text(path: str | Path) -> list[str]:
    """Text per page, as a reader would see it."""
    return [page.extract_text() or "" for page in _reader(Path(path)).pages]


def extract_text(path: str | Path) -> str:
    return "\n".join(extract_pages_text(path))
