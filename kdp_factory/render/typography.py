"""Real typefaces, registered once.

The engine shipped its first books in Helvetica and Times, because those are
the faces ReportLab has without being given any. They are why the covers looked
like a memo. These are vendored (see ``assets/fonts/OFL.txt``) rather than
downloaded at build time: a build has to be hermetic, or "same niche, same
seed, same book" stops being true on a machine with no network.

Four roles, chosen to work on a page a person writes on:

* ``poster`` — Archivo Black. One weight, and that weight is heavy. It is what
  a cover needs to survive being 200 pixels tall in a search grid.
* ``display`` — Cormorant Garamond. High contrast, generous, made for size.
  It carries the quieter covers and the title page.
* ``text`` — Lora. A sturdy workhorse serif that stays readable at 11pt beside
  a ruled line.
* ``sans`` — Karla. Labels, numbers, letterspaced small caps.
* ``mono`` — IBM Plex Mono. Puzzle grids, where columns must line up.

If a file is missing the role falls back to a builtin, so a broken install
renders an ugly book rather than no book.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger("kdp_factory")

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# role -> weight -> (registered name, file, builtin fallback)
# The registered name is the font's own PostScript name, so the print gate can
# read the PDF's BaseFont entries and compare them against what was asked for.
FACES: dict[str, dict[str, tuple[str, str, str]]] = {
    "poster": {
        "regular": ("ArchivoBlack-Regular", "ArchivoBlack-Regular.ttf", "Helvetica-Bold"),
        "bold": ("ArchivoBlack-Regular", "ArchivoBlack-Regular.ttf", "Helvetica-Bold"),
        "italic": ("ArchivoBlack-Regular", "ArchivoBlack-Regular.ttf", "Helvetica-BoldOblique"),
    },
    "display": {
        "regular": ("CormorantGaramond-Light", "CormorantGaramond-Light.ttf", "Times-Roman"),
        "bold": ("CormorantGaramond-SemiBold", "CormorantGaramond-SemiBold.ttf", "Times-Bold"),
        "italic": ("CormorantGaramond-Light", "CormorantGaramond-Light.ttf", "Times-Italic"),
    },
    "text": {
        "regular": ("Lora-Regular", "Lora-Regular.ttf", "Times-Roman"),
        "bold": ("Lora-SemiBold", "Lora-SemiBold.ttf", "Times-Bold"),
        "italic": ("Lora-Italic", "Lora-Italic.ttf", "Times-Italic"),
    },
    "sans": {
        "regular": ("Karla-Regular", "Karla-Regular.ttf", "Helvetica"),
        "bold": ("Karla-SemiBold", "Karla-SemiBold.ttf", "Helvetica-Bold"),
        "italic": ("Karla-Regular", "Karla-Regular.ttf", "Helvetica-Oblique"),
    },
    "mono": {
        "regular": ("IBMPlexMono-Regular", "IBMPlexMono-Regular.ttf", "Courier"),
        "bold": ("IBMPlexMono-SemiBold", "IBMPlexMono-SemiBold.ttf", "Courier-Bold"),
        "italic": ("IBMPlexMono-Regular", "IBMPlexMono-Regular.ttf", "Courier-Oblique"),
    },
}

_registered: dict[str, str] = {}
_done = False


def register_all() -> dict[str, str]:
    """Register every vendored face. Idempotent, and safe to call at import."""
    global _done
    if _done:
        return _registered
    for role, weights in FACES.items():
        for weight, (name, filename, fallback) in weights.items():
            key = f"{role}/{weight}"
            if name in _registered.values():
                _registered[key] = name
                continue
            path = FONT_DIR / filename
            if not path.is_file():
                log.warning("font %s missing; %s falls back to %s", filename, key, fallback)
                _registered[key] = fallback
                continue
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
                _registered[key] = name
            except Exception as exc:  # noqa: BLE001 - an unusable font is not fatal
                log.warning("could not register %s (%s); using %s", filename, exc, fallback)
                _registered[key] = fallback
    _done = True
    return _registered


def font(role: str, weight: str = "regular") -> str:
    """The registered PostScript name for a role, e.g. ``font("display", "bold")``."""
    register_all()
    key = f"{role}/{weight}"
    if key in _registered:
        return _registered[key]
    if role in FACES:
        return _registered.get(f"{role}/regular", "Helvetica")
    # An unknown role is a caller bug, but a book that renders beats a crash.
    log.warning("unknown font role %r; using sans", role)
    return _registered.get("sans/regular", "Helvetica")


def vendored() -> bool:
    """True when the real typefaces are present — used by the print gate."""
    register_all()
    return all(not name.startswith(("Helvetica", "Times", "Courier"))
               for name in _registered.values())


def letterspaced(text: str, spacing: str = " ") -> str:
    """Small-caps labels read better with air between the letters."""
    return spacing.join(text)
