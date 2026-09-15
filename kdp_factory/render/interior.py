"""Station 2's renderer: an ``InteriorPlan`` becomes a print-ready PDF.

Three things matter here beyond drawing:

* **Page size is the trim size.** These interiors carry no bleed, so the PDF
  page is exactly the trim KDP expects — the print gate re-measures it.
* **Margins mirror.** Odd pages are right-hand pages: the gutter is on their
  left, and on the right for even pages. The gutter itself comes from the spec
  card, keyed to the page count.
* **The output is invariant.** ReportLab is put in invariant mode so two runs
  of the same plan produce byte-identical files, which is what makes the
  determinism test meaningful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from reportlab import rl_config
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from ..booktypes.base import InteriorPlan, PageSpec
from ..config import EngineConfig
from ..errors import RenderError
from ..spec.kdp import INCH, gutter_margin_in, outside_margin_in, trim_size

TOP_MARGIN_IN = 0.6
BOTTOM_MARGIN_IN = 0.6


def _hex(value: str) -> Color:
    return HexColor(value)


class InteriorRenderer:
    """Draws one plan. One instance per book, no shared state between runs."""

    def __init__(self, plan: InteriorPlan, config: EngineConfig) -> None:
        self.plan = plan
        self.config = config
        self.brand = config.brand
        self.palette = config.brand.palette
        self.trim = trim_size(plan.trim_size)
        self.gutter = gutter_margin_in(plan.page_count)
        self.outside = max(outside_margin_in(bleed=False), 0.5)
        self.templates: dict[str, Callable[[pdfcanvas.Canvas, PageSpec, int], None]] = {
            "title_page": self._title_page,
            "copyright_page": self._copyright_page,
            "belongs_to_page": self._belongs_to_page,
            "how_to_use_page": self._how_to_use_page,
            "section_divider": self._section_divider,
            "prompt_page": self._prompt_page,
            "notes_page": self._notes_page,
            "blank_page": self._blank_page,
            "closing_page": self._closing_page,
            "puzzle_page": self._puzzle_page,
            "solution_page": self._solution_page,
            "week_plan_page": self._week_plan_page,
            "week_review_page": self._week_review_page,
            "month_page": self._month_page,
        }

    # ------------------------------------------------------------ geometry
    def font(self, role: str, weight: str = "regular") -> str:
        return self.brand.fonts.resolve(role, weight)

    def margins(self, page_number: int) -> tuple[float, float, float, float]:
        """(left, right, top, bottom) in points for this page's parity."""
        recto = page_number % 2 == 1  # page 1 is a right-hand page
        left = (self.gutter if recto else self.outside) * INCH
        right = (self.outside if recto else self.gutter) * INCH
        return left, right, TOP_MARGIN_IN * INCH, BOTTOM_MARGIN_IN * INCH

    def text_frame(self, page_number: int) -> tuple[float, float, float, float]:
        """(x, y, width, height) of the live area for this page."""
        left, right, top, bottom = self.margins(page_number)
        width = self.trim.width_pt - left - right
        height = self.trim.height_pt - top - bottom
        if width <= 0 or height <= 0:
            raise RenderError(
                f"margins leave no live area on a {self.plan.trim_size} page"
            )
        return left, bottom, width, height

    # -------------------------------------------------------------- output
    def render(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        previous_invariant = rl_config.invariant
        rl_config.invariant = 1
        try:
            c = pdfcanvas.Canvas(
                str(out),
                pagesize=(self.trim.width_pt, self.trim.height_pt),
                invariant=1,
                pageCompression=1,
            )
            c.setTitle(self.plan.title)
            c.setSubject(self.plan.subtitle)
            c.setAuthor(self.brand.author)
            c.setCreator(f"kdp-factory ({self.plan.book_type})")
            for index, page in enumerate(self.plan.pages):
                page_number = index + 1
                draw = self.templates.get(page.template)
                if draw is None:
                    raise RenderError(
                        f"no renderer for template {page.template!r}; known templates: "
                        f"{', '.join(sorted(self.templates))}"
                    )
                draw(c, page, page_number)
                if page.show_page_number and self.config.quality.require_page_numbers:
                    self._page_number(c, page_number)
                c.showPage()
            c.save()
        finally:
            rl_config.invariant = previous_invariant
        return out

    # ------------------------------------------------------------ elements
    def _page_number(self, c: pdfcanvas.Canvas, page_number: int) -> None:
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.light_ink))
        c.drawCentredString(self.trim.width_pt / 2, 0.38 * INCH, str(page_number))

    def _wrap(self, text: str, font: str, size: float, width: float) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, font, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _draw_paragraph(
        self,
        c: pdfcanvas.Canvas,
        text: str,
        x: float,
        y: float,
        width: float,
        font: str,
        size: float,
        leading: float | None = None,
        color: str | None = None,
        centred: bool = False,
    ) -> float:
        leading = leading or size * 1.45
        c.setFont(font, size)
        c.setFillColor(_hex(color or self.palette.ink))
        for line in self._wrap(text, font, size, width):
            if centred:
                c.drawCentredString(x + width / 2, y, line)
            else:
                c.drawString(x, y, line)
            y -= leading
        return y

    def _ruled_lines(
        self,
        c: pdfcanvas.Canvas,
        x: float,
        top_y: float,
        width: float,
        count: int,
        spacing: float,
    ) -> float:
        c.setStrokeColor(_hex(self.palette.rule))
        c.setLineWidth(0.5)
        y = top_y
        for _ in range(count):
            c.line(x, y, x + width, y)
            y -= spacing
        return y

    def _label_line(
        self, c: pdfcanvas.Canvas, label: str, x: float, y: float, width: float
    ) -> None:
        font = self.font("accent")
        c.setFont(font, 9)
        c.setFillColor(_hex(self.palette.light_ink))
        c.drawString(x, y + 4, label)
        c.setStrokeColor(_hex(self.palette.rule))
        c.setLineWidth(0.5)
        label_width = pdfmetrics.stringWidth(label, font, 9) + 6
        c.line(x + label_width, y, x + width, y)

    # ----------------------------------------------------------- templates
    def _title_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        top = y + height
        cursor = top - height * 0.22
        cursor = self._draw_paragraph(
            c, page.data["title"], x, cursor, width,
            self.font("display", "bold"), 30, 36, self.palette.primary, centred=True,
        )
        cursor -= 10
        c.setStrokeColor(_hex(self.palette.accent))
        c.setLineWidth(1.2)
        c.line(x + width * 0.3, cursor, x + width * 0.7, cursor)
        cursor -= 28
        self._draw_paragraph(
            c, page.data.get("subtitle", ""), x, cursor, width,
            self.font("body"), 13, 18, self.palette.light_ink, centred=True,
        )
        c.setFont(self.font("accent"), 10)
        c.setFillColor(_hex(self.palette.light_ink))
        c.drawCentredString(x + width / 2, y + 0.35 * INCH, page.data.get("imprint", ""))

    def _copyright_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, _ = self.text_frame(n)
        cursor = y + 2.2 * INCH
        for line in page.data.get("lines", []):
            cursor = self._draw_paragraph(
                c, line, x, cursor, width, self.font("body"), 9, 12,
                self.palette.light_ink,
            )
            cursor -= 4

    def _belongs_to_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height * 0.62
        cursor = self._draw_paragraph(
            c, page.data.get("heading", ""), x, cursor, width,
            self.font("display"), 16, 22, self.palette.primary, centred=True,
        )
        cursor -= 30
        for field in page.data.get("fields", []):
            self._label_line(c, field, x + width * 0.12, cursor, width * 0.76)
            cursor -= 44

    def _how_to_use_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 20
        cursor = self._draw_paragraph(
            c, page.data.get("heading", ""), x, cursor, width,
            self.font("display", "bold"), 16, 22, self.palette.primary,
        )
        cursor -= 8
        intro = page.data.get("intro")
        if intro:
            cursor = self._draw_paragraph(
                c, intro, x, cursor, width, self.font("body", "italic"), 11, 15,
                self.palette.light_ink,
            )
            cursor -= 10
        for bullet in page.data.get("bullets", []):
            c.setFont(self.font("accent"), 11)
            c.setFillColor(_hex(self.palette.accent))
            c.drawString(x, cursor, "•")
            cursor = self._draw_paragraph(
                c, bullet, x + 14, cursor, width - 14, self.font("body"), 11, 15
            )
            cursor -= 8

    def _section_divider(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height * 0.55
        index = page.data.get("index")
        if index:
            c.setFont(self.font("accent"), 10)
            c.setFillColor(_hex(self.palette.accent))
            c.drawCentredString(x + width / 2, cursor + 30, f"PART {index}")
        self._draw_paragraph(
            c, page.data.get("section", ""), x, cursor, width,
            self.font("display", "bold"), 22, 26, self.palette.primary, centred=True,
        )
        c.setStrokeColor(_hex(self.palette.rule))
        c.setLineWidth(0.8)
        c.line(x + width * 0.35, cursor - 16, x + width * 0.65, cursor - 16)

    def _prompt_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 6
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.accent))
        c.drawString(x, cursor, f"{page.data['number']:03d} / {page.data['of']:03d}")
        if page.data.get("date_line"):
            self._label_line(c, "Date", x + width * 0.62, cursor, width * 0.38)
        cursor -= 26
        cursor = self._draw_paragraph(
            c, page.data["prompt"], x, cursor, width,
            self.font("display", "bold"), 13.5, 18, self.palette.primary,
        )
        cursor -= 18
        lines = int(page.data.get("lines", 12))
        spacing = max(20.0, (cursor - y) / max(lines, 1))
        self._ruled_lines(c, x, cursor, width, lines, spacing)

    def _notes_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 6
        cursor = self._draw_paragraph(
            c, page.data.get("heading", "Notes"), x, cursor, width,
            self.font("display"), 12, 16, self.palette.light_ink,
        )
        cursor -= 12
        spacing = 24.0
        count = int((cursor - y) // spacing)
        self._ruled_lines(c, x, cursor, width, count, spacing)

    def _blank_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        return None

    def _closing_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height * 0.62
        for line in page.data.get("lines", []):
            cursor = self._draw_paragraph(
                c, line, x, cursor, width, self.font("body"), 12, 17,
                self.palette.ink, centred=True,
            )
            cursor -= 10
        also_by = page.data.get("also_by") or []
        if also_by:
            cursor -= 18
            cursor = self._draw_paragraph(
                c, "Also from this press", x, cursor, width,
                self.font("accent"), 10, 14, self.palette.accent, centred=True,
            )
            cursor -= 4
            for title in also_by:
                cursor = self._draw_paragraph(
                    c, title, x, cursor, width, self.font("body"), 10, 14,
                    self.palette.light_ink, centred=True,
                )
        note = page.data.get("note")
        if note:
            self._draw_paragraph(
                c, note, x, y + 0.7 * INCH, width, self.font("body", "italic"), 9.5, 13,
                self.palette.light_ink, centred=True,
            )
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.light_ink))
        c.drawCentredString(x + width / 2, y + 0.3 * INCH, page.data.get("imprint", ""))

    # ------------------------------------------------------------- puzzles
    def _grid_metrics(
        self, size: int, x: float, width: float, top: float, bottom: float
    ) -> tuple[float, float, float]:
        """(cell, grid_x, grid_top) sized to fit both the width and the height."""
        cell = min(width / size, (top - bottom) / size)
        grid_x = x + (width - cell * size) / 2
        return cell, grid_x, top

    def _draw_grid(
        self,
        c: pdfcanvas.Canvas,
        grid: list[str],
        cell: float,
        grid_x: float,
        grid_top: float,
        font_scale: float = 0.62,
        color: str | None = None,
    ) -> None:
        size = len(grid)
        font = self.font("mono") if "mono" in vars(self.brand.fonts) else "Courier"
        c.setFont(font, cell * font_scale)
        c.setFillColor(_hex(color or self.palette.ink))
        for row_index, row in enumerate(grid):
            baseline = grid_top - (row_index + 1) * cell + cell * 0.3
            for col_index, letter in enumerate(row):
                c.drawCentredString(
                    grid_x + col_index * cell + cell / 2, baseline, letter
                )

    def _puzzle_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 4
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.accent))
        c.drawString(x, cursor, f"PUZZLE {page.data['number']}")
        cursor -= 22
        cursor = self._draw_paragraph(
            c, page.data["theme"], x, cursor, width,
            self.font("display", "bold"), 16, 20, self.palette.primary,
        )
        cursor -= 8

        words = page.data["words"]
        columns = 4 if width > 5.5 * INCH else 3
        rows = -(-len(words) // columns)
        word_block = rows * 14 + 10
        grid_bottom = y + word_block
        cell, grid_x, grid_top = self._grid_metrics(
            len(page.data["grid"]), x, width, cursor, grid_bottom
        )
        self._draw_grid(c, page.data["grid"], cell, grid_x, grid_top)

        c.setFont(self.font("accent"), 9.5)
        c.setFillColor(_hex(self.palette.ink))
        column_width = width / columns
        for index, word in enumerate(words):
            col = index % columns
            row = index // columns
            c.drawString(x + col * column_width, y + word_block - 14 - row * 14, word)

    def _solution_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        solutions = page.data["solutions"]
        slot_height = height / max(len(solutions), 1)
        for index, solution in enumerate(solutions):
            top = y + height - index * slot_height
            c.setFont(self.font("accent"), 9)
            c.setFillColor(_hex(self.palette.accent))
            c.drawString(x, top - 10, f"PUZZLE {solution['number']} — {solution['theme']}")
            grid = solution["grid"]
            cell, grid_x, grid_top = self._grid_metrics(
                len(grid), x, width, top - 22, top - slot_height + 10
            )
            self._draw_grid(c, grid, cell, grid_x, grid_top, 0.6, self.palette.light_ink)
            c.setStrokeColor(_hex(self.palette.accent))
            c.setLineWidth(max(0.8, cell * 0.08))
            c.setLineCap(1)
            for placement in solution["placements"]:
                word = placement["word"]
                start_x = grid_x + (placement["col"] + 0.5) * cell
                start_y = grid_top - (placement["row"] + 0.5) * cell
                end_x = start_x + placement["d_col"] * (len(word) - 1) * cell
                end_y = start_y - placement["d_row"] * (len(word) - 1) * cell
                c.line(start_x, start_y, end_x, end_y)

    # -------------------------------------------------------------- planner
    def _week_plan_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 4
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.accent))
        c.drawString(x, cursor, f"WEEK {page.data['week']} OF {page.data['of']}")
        self._label_line(c, "Week of", x + width * 0.6, cursor, width * 0.4)
        cursor -= 24

        c.setStrokeColor(_hex(self.palette.rule))
        c.setLineWidth(0.6)
        focus_height = 46
        c.rect(x, cursor - focus_height, width, focus_height, stroke=1, fill=0)
        c.setFont(self.font("accent"), 8.5)
        c.setFillColor(_hex(self.palette.accent))
        c.drawString(x + 8, cursor - 14, "THIS WEEK'S FOCUS")
        self._draw_paragraph(
            c, page.data["focus"], x + 8, cursor - 30, width - 16,
            self.font("body", "italic"), 10.5, 13, self.palette.ink,
        )
        cursor -= focus_height + 16

        days = page.data.get("day_names", [])
        slot = (cursor - y) / max(len(days), 1)
        for day in days:
            c.setFont(self.font("display", "bold"), 10)
            c.setFillColor(_hex(self.palette.primary))
            c.drawString(x, cursor - 11, day.upper())
            line_count = max(2, int(slot // 16) - 1)
            self._ruled_lines(c, x + 70, cursor - 12, width - 70, line_count, 16)
            cursor -= slot

    def _week_review_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 4
        c.setFont(self.font("accent"), 9)
        c.setFillColor(_hex(self.palette.accent))
        c.drawString(x, cursor, f"WEEK {page.data['week']} — HABITS & REVIEW")
        cursor -= 26

        habits = page.data.get("habits", [])
        days = page.data.get("day_names", [])
        label_width = width * 0.34
        box = min(18.0, (width - label_width) / max(len(days), 1))
        c.setFont(self.font("accent"), 7.5)
        c.setFillColor(_hex(self.palette.light_ink))
        for index, day in enumerate(days):
            c.drawCentredString(
                x + label_width + index * box + box / 2, cursor, day[:1]
            )
        cursor -= 6
        for habit in habits:
            c.setFont(self.font("body"), 10)
            c.setFillColor(_hex(self.palette.ink))
            c.drawString(x, cursor - box + 5, habit)
            c.setStrokeColor(_hex(self.palette.rule))
            c.setLineWidth(0.6)
            for index in range(len(days)):
                c.rect(
                    x + label_width + index * box + 1.5,
                    cursor - box + 1.5,
                    box - 3,
                    box - 3,
                    stroke=1,
                    fill=0,
                )
            cursor -= box + 4
        cursor -= 12

        questions = page.data.get("review_questions", [])
        slot = (cursor - y) / max(len(questions), 1)
        for question in questions:
            c.setFont(self.font("display", "bold"), 10)
            c.setFillColor(_hex(self.palette.primary))
            c.drawString(x, cursor - 11, question)
            line_count = max(1, int(slot // 18) - 1)
            self._ruled_lines(c, x, cursor - 24, width, line_count, 18)
            cursor -= slot

    def _month_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height - 4
        self._draw_paragraph(
            c, page.data["month"], x, cursor - 14, width,
            self.font("display", "bold"), 20, 24, self.palette.primary,
        )
        cursor -= 48
        days = page.data.get("day_names", []) or ["M", "T", "W", "T", "F", "S", "S"]
        columns = len(days)
        cell_w = width / columns
        rows = 6
        # Fill the page: a monthly page with a grid crammed into the top third
        # wastes the paper the buyer is holding.
        cell_h = min(cell_w * 1.6, (cursor - y) / rows)
        c.setFont(self.font("accent"), 8.5)
        c.setFillColor(_hex(self.palette.light_ink))
        for index, day in enumerate(days):
            c.drawCentredString(x + index * cell_w + cell_w / 2, cursor, day[:3].upper())
        cursor -= 8
        c.setStrokeColor(_hex(self.palette.rule))
        c.setLineWidth(0.6)
        for row in range(rows):
            for col in range(columns):
                c.rect(
                    x + col * cell_w,
                    cursor - (row + 1) * cell_h,
                    cell_w,
                    cell_h,
                    stroke=1,
                    fill=0,
                )


def render_interior(plan: InteriorPlan, config: EngineConfig, path: str | Path) -> Path:
    return InteriorRenderer(plan, config).render(path)
