"""The cover, made in Canva (free plan), sized by the interior that exists.

The spine width depends on the page count, so the cover is specified only
after the interior PDF is final. ``cover_spec`` reads that PDF and returns
the full-wrap size; ``guide`` draws a template at exactly that size, with
bleed, trim, spine folds, safe zones and the barcode area, to upload into
Canva as the bottom layer (delete or hide it before exporting);
``check_cover`` verifies the PDF exported from Canva ("PDF per la stampa",
crop marks and bleed marks OFF, because KDP wants the bleed inside the page
size, not marks around it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from kdp_factory.spec import kdp as spec

MM = 25.4
TOLERANCE_IN = 0.02


@dataclass(frozen=True)
class CoverSpec:
    geometry: spec.CoverGeometry
    barcode_box_in: tuple[float, float, float, float]  # x, y (from bottom-left), w, h

    def as_dict(self) -> dict:
        g = self.geometry
        out = g.as_dict()
        out.update({
            "wrap_width_mm": round(g.width * MM, 2), "wrap_height_mm": round(g.height * MM, 2),
            "spine_width_mm": round(g.spine_width * MM, 2),
            "back_panel_x_in": round(g.back_panel_x, 4), "spine_x_in": round(g.spine_x, 4),
            "front_panel_x_in": round(g.front_panel_x, 4),
            "barcode_box_in": [round(v, 4) for v in self.barcode_box_in],
            "canva": {
                "dimensioni_personalizzate": f"{g.width:.3f} x {g.height:.3f} in",
                "esporta": "PDF per la stampa, senza segni di taglio e di smarginatura",
                "testo_sul_dorso": "consentito" if g.spine_text_allowed else
                                   f"vietato sotto le {spec.KDP_SPEC['cover']['spine_text_min_pages']} pagine",
            },
        })
        return out


def cover_spec(interior_pdf: str | Path, trim: str = "6x9", paper: str = "bw_white") -> CoverSpec:
    pages = len(PdfReader(str(interior_pdf)).pages)
    g = spec.cover_geometry(trim, pages, paper)
    off = spec.KDP_SPEC["cover"]["barcode_offset_in"]
    w, h = g.barcode_keepout
    x = g.spine_x - float(off["from_spine_edge"]) - w  # back cover, next to the spine
    y = g.bleed + float(off["from_bottom_trim"])
    return CoverSpec(geometry=g, barcode_box_in=(x, y, w, h))


def guide(cs: CoverSpec, out_pdf: str | Path, out_png: str | Path | None = None, dpi: int = 150) -> None:
    """Template at exact wrap size. Red: bleed (cut away). Blue: trim and
    spine folds. Green: safe zone for text. Grey box: barcode, keep empty."""
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    g = cs.geometry
    pt = 72.0
    c = canvas.Canvas(str(out_pdf), pagesize=(g.width * pt, g.height * pt))
    red, blue, green = Color(0.85, 0.1, 0.1), Color(0.1, 0.3, 0.85), Color(0.1, 0.6, 0.2)

    def rect(x, y, w, h, color, dash=None, fill=None):
        c.setStrokeColor(color)
        c.setDash(*(dash or ()))
        if fill is not None:
            c.setFillColor(fill)
        c.rect(x * pt, y * pt, w * pt, h * pt, stroke=1, fill=1 if fill is not None else 0)

    c.setLineWidth(0.8)
    rect(0, 0, g.width, g.height, red)  # outer edge = bleed
    rect(g.bleed, g.bleed, g.width - 2 * g.bleed, g.height - 2 * g.bleed, blue, (6, 3))  # trim
    for x in (g.spine_x, g.front_panel_x):  # spine folds
        c.setStrokeColor(blue)
        c.setDash(2, 2)
        c.line(x * pt, 0, x * pt, g.height * pt)
    s = g.safe_margin
    for x0 in (g.back_panel_x, g.front_panel_x):
        rect(x0 + s, g.bleed + s, g.trim.width - 2 * s, g.trim.height - 2 * s, green, (1, 2))
    bx, by, bw, bh = cs.barcode_box_in
    rect(bx, by, bw, bh, Color(0.4, 0.4, 0.4), fill=Color(0.9, 0.9, 0.9))
    c.setDash()
    c.setFillColor(Color(0.3, 0.3, 0.3))
    c.setFont("Helvetica", 9)
    c.drawCentredString((bx + bw / 2) * pt, (by + bh / 2) * pt, "CODICE A BARRE (lasciare vuoto)")
    c.setFont("Helvetica", 11)
    mid = g.height / 2 * pt
    c.drawCentredString((g.back_panel_x + g.trim.width / 2) * pt, mid, "RETRO")
    c.drawCentredString((g.front_panel_x + g.trim.width / 2) * pt, mid, "FRONTE")
    c.setFont("Helvetica", 7)
    c.drawCentredString((g.spine_x + g.spine_width / 2) * pt, (g.height - g.bleed - s) * pt - 8,
                        f"DORSO {g.spine_width:.3f}\"")
    c.drawString((g.bleed + 0.05) * pt, (g.bleed + 0.05) * pt,
                 f"{g.width:.3f} x {g.height:.3f} in - {g.page_count} pagine - {g.paper}")
    c.showPage()
    c.save()
    if out_png:
        from PIL import Image, ImageDraw

        px = lambda v: round(v * dpi)  # noqa: E731
        img = Image.new("RGB", (px(g.width), px(g.height)), "white")
        d = ImageDraw.Draw(img)
        H = img.height
        box = lambda x, y, w, h: [px(x), H - px(y + h), px(x + w), H - px(y)]  # noqa: E731
        d.rectangle([0, 0, img.width - 1, H - 1], outline=(217, 26, 26), width=3)
        d.rectangle(box(g.bleed, g.bleed, g.width - 2 * g.bleed, g.height - 2 * g.bleed),
                    outline=(26, 77, 217), width=2)
        for x in (g.spine_x, g.front_panel_x):
            d.line([px(x), 0, px(x), H], fill=(26, 77, 217), width=2)
        for x0 in (g.back_panel_x, g.front_panel_x):
            d.rectangle(box(x0 + s, g.bleed + s, g.trim.width - 2 * s, g.trim.height - 2 * s),
                        outline=(26, 153, 51), width=2)
        d.rectangle(box(bx, by, bw, bh), outline=(100, 100, 100), fill=(230, 230, 230), width=2)
        img.save(str(out_png), dpi=(dpi, dpi))


def check_cover(cover_pdf: str | Path, cs: CoverSpec) -> list[str]:
    """Problems KDP would reject the exported cover for."""
    reader = PdfReader(str(cover_pdf))
    g = cs.geometry
    problems = []
    if len(reader.pages) != 1:
        problems.append(f"la copertina deve essere una sola pagina, ne ha {len(reader.pages)}")
    box = reader.pages[0].mediabox
    w, h = float(box.width) / 72, float(box.height) / 72
    if abs(w - g.width) > TOLERANCE_IN or abs(h - g.height) > TOLERANCE_IN:
        hint = " (sembra esportata con i segni di taglio: disattivali)" if w > g.width + 0.2 else ""
        problems.append(f"dimensioni {w:.3f} x {h:.3f} in, attese {g.width:.3f} x {g.height:.3f} in{hint}")
    return problems


def write_spec(cs: CoverSpec, folder: str | Path) -> dict[str, Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"spec": folder / "cover_spec.json", "guide_pdf": folder / "cover_guide.pdf",
             "guide_png": folder / "cover_guide.png"}
    paths["spec"].write_text(json.dumps(cs.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    guide(cs, paths["guide_pdf"], paths["guide_png"])
    return paths
