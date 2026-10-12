"""Station 2's renderer: an ``InteriorPlan`` becomes a print-ready PDF.

Four things matter here beyond drawing:

* **Page size is the trim size.** These interiors carry no bleed, so the PDF
  page is exactly the trim KDP expects — the print gate re-measures it.
* **Margins mirror.** Odd pages are right-hand pages: the gutter is on their
  left, and on the right for even pages. The gutter itself comes from the spec
  card, keyed to the page count.
* **The inside matches the outside.** The interior takes its ink, rules and
  accent from the same ``design.Palette`` the cover uses, so a book is one
  object rather than two.
* **The output is invariant.** ReportLab is put in invariant mode so two runs
  of the same plan produce byte-identical files, which is what makes the
  determinism test meaningful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from ..booktypes.base import InteriorPlan, PageSpec
from ..config import EngineConfig
from ..content.locale import lookup, strings
from ..content.locale import text as say
from ..content.rng import StageRandom
from ..errors import RenderError
from ..spec.kdp import INCH, gutter_margin_in, outside_margin_in, trim_size
from .design import (
    MotifSpec,
    Palette,
    choose_motif,
    choose_palette,
    draw_motif,
    hex_color,
)
from .layout import (
    balanced,
    draw_block,
    draw_tracked,
    fit_size,
    rule,
    ruled_lines,
    tracked_width,
    wrap,
)
from .typography import font

TOP_MARGIN_IN = 0.7
BOTTOM_MARGIN_IN = 0.68

# Form metrics, in points (see ``InteriorRenderer.layout_form``).
FORM_GAP = 10.0        # between two elements
FORM_TITLE_H = 13.0    # a table, scale or checks title
FIELD_ROW = 26.0       # pitch of a row of label-and-rule fields
TABLE_HEADER = 16.0    # one-line column headers
TABLE_HEADER_2 = 24.0  # two-line column headers
TABLE_ROW = 18.0       # a handwritten table row
SCALE_PITCH = 19.0
SCALE_BOX_MAX = 15.0
SCALE_BOX_MIN = 10.0
CHECK_PITCH = 16.0
LINE_PITCH = 20.0      # a writing line
LINES_LABEL_H = 12.0


class InteriorRenderer:
    """Draws one plan. One instance per book, no shared state between runs."""

    def __init__(
        self,
        plan: InteriorPlan,
        config: EngineConfig,
        palette: Palette | None = None,
        motif: str | None = None,
        seed: int = 0,
    ) -> None:
        self.plan = plan
        self.config = config
        self.brand = config.brand
        rng = StageRandom(seed, "cover")  # same stream as the cover: one identity
        words = [plan.title, plan.subtitle]
        self.palette = palette or choose_palette(words, rng)
        self.motif = motif or choose_motif(words, rng)
        self.trim = trim_size(plan.trim_size)
        self.gutter = gutter_margin_in(plan.page_count)
        self.outside = max(outside_margin_in(bleed=False), 0.55)
        # Every label the renderer prints itself comes from the locale table;
        # what a page *says* comes from the plan.
        self.strings = strings(self.brand.language)
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
            "form_page": self._form_page,
            "text_page": self._text_page,
        }

    # ------------------------------------------------------------ geometry
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
        c.setFont(font("sans", "regular"), 8.5)
        c.setFillColor(hex_color(self.palette.interior_soft))
        c.drawCentredString(self.trim.width_pt / 2, 0.42 * INCH, str(page_number))

    def _eyebrow(self, c, text: str, x: float, y: float, colour: str | None = None) -> None:
        draw_tracked(c, text, x, y, font("sans", "bold"), 7.5,
                     colour or self.palette.accent_for_page, 1.5, centred=False)

    def _label_line(self, c, label: str, x: float, y: float, width: float) -> None:
        face = font("sans", "regular")
        draw_tracked(c, label, x, y + 5, face, 7, self.palette.interior_soft, 1.2,
                     centred=False)
        label_width = tracked_width(label, face, 7, 1.2) + 8
        rule(c, x + label_width, y, width - label_width, self.palette.interior_rule, 0.5)

    def _ornament(self, c, cx: float, y: float, colour: str, width: float = 54.0) -> None:
        """A small centred mark: a rule with a diamond on it."""
        rule(c, cx - width / 2, y, width, colour, 0.7)
        c.saveState()
        c.setFillColor(hex_color(colour))
        c.translate(cx, y)
        c.rotate(45)
        size = 2.6
        c.rect(-size / 2, -size / 2, size, size, stroke=0, fill=1)
        c.restoreState()

    # ----------------------------------------------------------- templates
    def _title_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        top = y + height

        c.saveState()
        clip = c.beginPath()
        clip.rect(0, 0, self.trim.width_pt, self.trim.height_pt)
        c.clipPath(clip, stroke=0, fill=0)
        draw_motif(c, self.motif, MotifSpec(
            x=x - width * 0.12, y=y + height * 0.16, width=width * 1.24,
            height=height * 0.3, color=pal.accent_for_page, alpha=0.13,
            extras={"step": 17.0, "radius": 0.9}))
        c.restoreState()

        display = font("display", "bold")
        size = fit_size(page.data["title"], display, width, 34, 18, 3)
        cursor = top - height * 0.16
        cursor = draw_block(c, page.data["title"], x, cursor, width, display, size,
                            size * 1.1, pal.interior_ink, centred=True, balance=True)

        cursor -= 14
        self._ornament(c, x + width / 2, cursor, pal.accent_for_page)
        cursor -= 26

        subtitle = page.data.get("subtitle", "")
        if subtitle:
            draw_block(c, subtitle, x + width * 0.06, cursor, width * 0.88,
                       font("text", "regular"), 11.5, 17, pal.interior_soft,
                       centred=True, balance=True)

        draw_tracked(c, page.data.get("imprint", ""), x + width / 2, y + 6,
                     font("sans", "regular"), 8, pal.interior_soft, 2.0)

    def _copyright_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, _ = self.text_frame(n)
        cursor = y + 2.4 * INCH
        for line in page.data.get("lines", []):
            cursor = draw_block(c, line, x, cursor, width, font("text", "regular"),
                                8.5, 12.5, self.palette.interior_soft)
            cursor -= 5

    def _belongs_to_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height * 0.66
        draw_tracked(c, page.data.get("heading", ""), x + width / 2, cursor,
                     font("sans", "regular"), 9, pal.interior_soft, 2.2)
        cursor -= 18
        self._ornament(c, x + width / 2, cursor, pal.accent_for_page, 40)
        cursor -= 46
        for field in page.data.get("fields", []):
            self._label_line(c, field, x + width * 0.1, cursor, width * 0.8)
            cursor -= 48

    def _how_to_use_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height - 18
        self._eyebrow(c, say(self.strings, "interior.how_to_use_eyebrow"), x, cursor)
        cursor -= 26
        cursor = draw_block(c, page.data.get("heading", ""), x, cursor, width,
                            font("display", "bold"), 21, 25, pal.interior_ink,
                            balance=True)
        cursor -= 10
        intro = page.data.get("intro")
        if intro:
            cursor = draw_block(c, intro, x, cursor, width * 0.94,
                                font("text", "italic"), 11, 16, pal.interior_soft)
            cursor -= 14
        for bullet in page.data.get("bullets", []):
            c.setFillColor(hex_color(pal.accent_for_page))
            c.circle(x + 2.2, cursor + 3.4, 1.8, stroke=0, fill=1)
            cursor = draw_block(c, bullet, x + 15, cursor, width - 15,
                                font("text", "regular"), 10.5, 15.5, pal.interior_ink)
            cursor -= 9

    def _section_divider(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height * 0.56
        index = page.data.get("index")
        if index:
            draw_tracked(c, say(self.strings, "interior.part", n=index),
                         x + width / 2, cursor + 34,
                         font("sans", "bold"), 8, pal.accent_for_page, 2.4)
        draw_block(c, page.data.get("section", ""), x, cursor, width,
                   font("display", "bold"), 27, 31, pal.interior_ink,
                   centred=True, balance=True)
        self._ornament(c, x + width / 2, cursor - 24, pal.accent_for_page, 64)

    def _prompt_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height

        self._eyebrow(c, f"{page.data['number']:03d} / {page.data['of']:03d}", x, cursor)
        if page.data.get("date_line"):
            self._label_line(c, say(self.strings, "interior.date"), x + width * 0.66,
                             cursor, width * 0.34)
        cursor -= 30

        text_face = font("text", "bold")
        prompt_size = fit_size(page.data["prompt"], text_face, width * 0.96, 14.5, 11, 4)
        cursor = draw_block(c, page.data["prompt"], x, cursor, width * 0.96,
                            text_face, prompt_size, prompt_size * 1.42,
                            pal.interior_ink, balance=True)
        cursor -= 16

        lines = int(page.data.get("lines", 12))
        spacing = max(21.0, (cursor - y) / max(lines, 1))
        ruled_lines(c, x, cursor, width, lines, spacing, pal.interior_rule, 0.5)

    def _notes_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        cursor = y + height
        self._eyebrow(c, page.data.get("heading") or say(self.strings, "interior.notes"),
                      x, cursor,
                      self.palette.interior_soft)
        cursor -= 26
        spacing = 24.0
        count = int((cursor - y) // spacing)
        ruled_lines(c, x, cursor, width, count, spacing, self.palette.interior_rule, 0.5)

    def _blank_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        return None

    def _closing_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height * 0.62
        for line in page.data.get("lines", []):
            cursor = draw_block(c, line, x + width * 0.06, cursor, width * 0.88,
                                font("text", "regular"), 12, 18, pal.interior_ink,
                                centred=True, balance=True)
            cursor -= 12

        also_by = page.data.get("also_by") or []
        if also_by:
            cursor -= 16
            self._ornament(c, x + width / 2, cursor + 10, pal.accent_for_page, 40)
            cursor -= 14
            draw_tracked(c, say(self.strings, "interior.also_from"), x + width / 2, cursor,
                         font("sans", "regular"), 7.5, pal.accent_for_page, 1.8)
            cursor -= 18
            for title in also_by:
                cursor = draw_block(c, title, x, cursor, width, font("text", "regular"),
                                    10, 14.5, pal.interior_soft, centred=True)
        note = page.data.get("note")
        if note:
            draw_block(c, note, x + width * 0.08, y + 0.75 * INCH, width * 0.84,
                       font("text", "italic"), 9.5, 13.5, pal.interior_soft, centred=True)
        draw_tracked(c, page.data.get("imprint", ""), x + width / 2, y + 6,
                     font("sans", "regular"), 8, pal.interior_soft, 2.0)

    # ------------------------------------------------------------- puzzles
    def _grid_metrics(self, size: int, x: float, width: float, top: float,
                      bottom: float) -> tuple[float, float, float]:
        cell = min(width / size, (top - bottom) / size)
        grid_x = x + (width - cell * size) / 2
        return cell, grid_x, top

    def _draw_grid(self, c, grid: list[str], cell: float, grid_x: float, grid_top: float,
                   font_scale: float = 0.6, color: str | None = None) -> None:
        face = font("mono", "regular")
        c.setFont(face, cell * font_scale)
        c.setFillColor(hex_color(color or self.palette.interior_ink))
        for row_index, row in enumerate(grid):
            baseline = grid_top - (row_index + 1) * cell + cell * 0.3
            for col_index, letter in enumerate(row):
                c.drawCentredString(grid_x + col_index * cell + cell / 2, baseline, letter)

    def _puzzle_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height

        self._eyebrow(c, say(self.strings, "interior.puzzle", n=page.data["number"]),
                      x, cursor)
        cursor -= 28
        cursor = draw_block(c, page.data["theme"], x, cursor, width,
                            font("display", "bold"), 19, 23, pal.interior_ink)
        cursor -= 6
        rule(c, x, cursor, width * 0.18, pal.accent_for_page, 1.0)
        cursor -= 14

        words = page.data["words"]
        columns = 4 if width > 5.5 * INCH else 3
        rows = -(-len(words) // columns)
        word_block = rows * 15 + 16
        grid_bottom = y + word_block
        cell, grid_x, grid_top = self._grid_metrics(
            len(page.data["grid"]), x, width, cursor, grid_bottom)
        self._draw_grid(c, page.data["grid"], cell, grid_x, grid_top)

        rule(c, x, y + word_block + 4, width, pal.interior_rule, 0.5)
        c.setFont(font("sans", "regular"), 9.5)
        c.setFillColor(hex_color(pal.interior_ink))
        column_width = width / columns
        for index, word in enumerate(words):
            col, row = index % columns, index // columns
            c.drawString(x + col * column_width, y + word_block - 16 - row * 15, word)

    def _solution_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        solutions = page.data["solutions"]
        slot_height = height / max(len(solutions), 1)
        for index, solution in enumerate(solutions):
            top = y + height - index * slot_height
            self._eyebrow(c, say(self.strings, "interior.solution", n=solution["number"],
                                 theme=solution["theme"]), x, top - 10)
            grid = solution["grid"]
            cell, grid_x, grid_top = self._grid_metrics(
                len(grid), x, width, top - 24, top - slot_height + 12)
            self._draw_grid(c, grid, cell, grid_x, grid_top, 0.58, pal.interior_soft)
            c.setStrokeColor(hex_color(pal.accent_for_page, 0.85))
            c.setLineWidth(max(0.9, cell * 0.09))
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
        pal = self.palette
        cursor = y + height

        self._eyebrow(c, say(self.strings, "interior.week_of_total",
                             week=page.data["week"], of=page.data["of"]), x, cursor)
        self._label_line(c, say(self.strings, "interior.week_of"), x + width * 0.62,
                         cursor, width * 0.38)
        cursor -= 26

        focus_height = 52
        c.setFillColor(hex_color(pal.accent_for_page, 0.07))
        c.rect(x, cursor - focus_height, width, focus_height, stroke=0, fill=1)
        rule(c, x, cursor - focus_height, width, pal.accent_for_page, 0.8)
        draw_tracked(c, say(self.strings, "interior.week_focus"), x + 10, cursor - 15,
                     font("sans", "bold"), 7, pal.accent_for_page, 1.4, centred=False)
        draw_block(c, page.data["focus"], x + 10, cursor - 32, width - 20,
                   font("text", "italic"), 11, 14, pal.interior_ink)
        cursor -= focus_height + 20

        days = page.data.get("day_names", [])
        slot = (cursor - y) / max(len(days), 1)
        for day in days:
            draw_tracked(c, day, x, cursor - 11, font("sans", "bold"), 8.5,
                         pal.interior_ink, 1.6, centred=False)
            line_count = max(2, int(slot // 17) - 1)
            ruled_lines(c, x + 76, cursor - 12, width - 76, line_count, 17,
                        pal.interior_rule, 0.5)
            cursor -= slot

    def _week_review_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height

        self._eyebrow(c, say(self.strings, "interior.week_review", week=page.data["week"]),
                      x, cursor)
        cursor -= 28

        habits = page.data.get("habits", [])
        days = page.data.get("day_names", [])
        label_width = width * 0.34
        box = min(18.0, (width - label_width) / max(len(days), 1))
        c.setFont(font("sans", "regular"), 7)
        c.setFillColor(hex_color(pal.interior_soft))
        for index, day in enumerate(days):
            c.drawCentredString(x + label_width + index * box + box / 2, cursor, day[:1])
        cursor -= 8
        for habit in habits:
            c.setFont(font("text", "regular"), 10)
            c.setFillColor(hex_color(pal.interior_ink))
            c.drawString(x, cursor - box + 5.5, habit)
            c.setStrokeColor(hex_color(pal.interior_rule))
            c.setLineWidth(0.6)
            for index in range(len(days)):
                c.rect(x + label_width + index * box + 1.5, cursor - box + 1.5,
                       box - 3, box - 3, stroke=1, fill=0)
            cursor -= box + 5
        cursor -= 14

        questions = page.data.get("review_questions", [])
        slot = (cursor - y) / max(len(questions), 1)
        for question in questions:
            draw_block(c, question, x, cursor - 11, width, font("text", "bold"), 10.5, 14,
                       pal.interior_ink)
            line_count = max(1, int(slot // 18) - 1)
            ruled_lines(c, x, cursor - 26, width, line_count, 18, pal.interior_rule, 0.5)
            cursor -= slot

    def _month_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        cursor = y + height
        draw_block(c, page.data["month"], x, cursor - 20, width,
                   font("display", "bold"), 26, 30, pal.interior_ink)
        cursor -= 34
        rule(c, x, cursor, width * 0.16, pal.accent_for_page, 1.0)
        cursor -= 22

        days = page.data.get("day_names", []) or list(
            lookup(self.strings, "interior.day_initials"))
        columns = len(days)
        cell_w = width / columns
        rows = 6
        cell_h = min(cell_w * 1.6, (cursor - y) / rows)
        c.setFont(font("sans", "regular"), 7.5)
        c.setFillColor(hex_color(pal.interior_soft))
        for index, day in enumerate(days):
            c.drawCentredString(x + index * cell_w + cell_w / 2, cursor, day[:3].upper())
        cursor -= 10
        c.setStrokeColor(hex_color(pal.interior_rule))
        c.setLineWidth(0.6)
        for row in range(rows):
            for col in range(columns):
                c.rect(x + col * cell_w, cursor - (row + 1) * cell_h, cell_w, cell_h,
                       stroke=1, fill=0)

    # -------------------------------------------------------------- logbook
    # A form is filled in by hand, often by someone tired, at a kitchen table.
    # Rows are a pen's height (18pt, a little under college ruling), labels are
    # never set below 6pt, and every element is measured before anything is
    # drawn: a form that does not fit raises instead of running off the page.
    def layout_form(self, page: PageSpec, n: int) -> list[dict]:
        """Where each element of a form page goes, top to bottom.

        Returns one entry per element — ``top``, ``height`` and, for a ``fill``
        element, the ``count`` of rows or lines it resolved to. Raises a
        RenderError naming the page and the element that does not fit.
        """
        x, y, width, height = self.text_frame(n)
        cursor = y + height - self._form_head(None, page, x, y + height, width)
        laid: list[dict] = []
        for index, element in enumerate(page.data.get("elements", [])):
            if laid:
                cursor -= FORM_GAP
            available = cursor - y
            count = None
            if element.get("rows") == "fill" or element.get("count") == "fill":
                base = self._element(None, page, element, x, cursor, width, 0)
                pitch = TABLE_ROW if element["type"] == "table" else LINE_PITCH
                count = int((available - base) // pitch + 1e-6)
                if count < 1:
                    self._no_room(page, n, index, element, base + pitch, available)
            needed = self._element(None, page, element, x, cursor, width, count)
            if needed > available + 0.01:
                self._no_room(page, n, index, element, needed, available)
            laid.append({"element": element, "top": cursor, "height": needed,
                         "count": count})
            cursor -= needed
        return laid

    def _no_room(self, page: PageSpec, n: int, index: int, element: dict,
                 needed: float, available: float) -> None:
        name = element.get("title") or element.get("label") or ""
        raise RenderError(
            f"form page {n} ({page.data.get('heading', '')!r}): element {index + 1} "
            f"({element['type']}{' ' + repr(name) if name else ''}) needs {needed:.0f}pt "
            f"but only {max(available, 0):.0f}pt of the {self.plan.trim_size} live area "
            f"is left. Remove rows, move it to a page of its own, or use a larger trim."
        )

    def _form_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        x, y, width, height = self.text_frame(n)
        laid = self.layout_form(page, n)
        self._form_head(c, page, x, y + height, width)
        for entry in laid:
            self._element(c, page, entry["element"], x, entry["top"], width, entry["count"])

    def _form_head(self, c, page: PageSpec, x: float, top: float, width: float) -> float:
        """Eyebrow, heading and accent rule. Returns the height it takes."""
        self._eyebrow_or_measure(c, page.data.get("eyebrow", ""), x, top)
        heading = page.data.get("heading", "")
        if not heading:
            return 18.0
        face = font("display", "bold")
        lines = wrap(heading, face, 17, width)
        if c is not None:
            draw_block(c, heading, x, top - 24, width, face, 17, 20, self.palette.interior_ink)
            rule(c, x, top - 24 - (len(lines) - 1) * 20 - 8, width * 0.14,
                 self.palette.accent_for_page, 1.0)
        return 44.0 + (len(lines) - 1) * 20

    def _eyebrow_or_measure(self, c, text: str, x: float, top: float) -> None:
        if c is not None and text:
            self._eyebrow(c, text, x, top)

    def _element(self, c, page: PageSpec, element: dict, x: float, top: float,
                 width: float, count: int | None) -> float:
        """Draw one element (or, with ``c=None``, only measure it). Returns its height.

        Measuring and drawing are the same code path, so a form cannot be
        measured one way and drawn another.
        """
        kind = element["type"]
        handler = {
            "fields": self._el_fields, "table": self._el_table, "scale": self._el_scale,
            "checks": self._el_checks, "lines": self._el_lines, "prompt": self._el_prompt,
            "text": self._el_text, "note": self._el_note,
        }.get(kind)
        if handler is None:
            raise RenderError(f"page {page.data.get('heading', '')!r}: unknown form "
                              f"element {kind!r}")
        return handler(c, page, element, x, top, width, count)

    def _el_title(self, c, page: PageSpec, title: str, x: float, top: float,
                  width: float) -> float:
        if not title:
            return 0.0
        face = font("sans", "bold")
        if tracked_width(title, face, 7, 1.4) > width:
            raise RenderError(f"page {page.data.get('heading', '')!r}: the title "
                              f"{title!r} is wider than the page")
        if c is not None:
            draw_tracked(c, title, x, top - 7, face, 7, self.palette.accent_for_page, 1.4,
                         centred=False)
        return FORM_TITLE_H

    def _too_wide(self, page: PageSpec, what: str, label: str, fix: str) -> RenderError:
        return RenderError(
            f"page {page.data.get('heading', '')!r}: the {what} {label!r} is wider than "
            f"its space on a {self.plan.trim_size} page; {fix}"
        )

    def _el_fields(self, c, page, element, x, top, width, count) -> float:
        items, columns = element["items"], int(element.get("columns", 2))
        gap = 14.0
        column_w = (width - gap * (columns - 1)) / columns
        face = font("sans", "regular")
        for item in items:
            if tracked_width(item, face, 7, 1.2) + 8 + 24 > column_w:
                raise self._too_wide(page, "field label", item, "use fewer columns")
        rows = -(-len(items) // columns)
        if c is not None:
            for index, item in enumerate(items):
                row, col = divmod(index, columns)
                self._label_line(c, item, x + col * (column_w + gap),
                                 top - 18 - row * FIELD_ROW, column_w)
        return 18.0 + (rows - 1) * FIELD_ROW

    def _header_lines(self, label: str, width: float) -> list[str] | None:
        """A column header in one line, or two, or None if it cannot fit."""
        face = font("sans", "bold")
        if tracked_width(label, face, 6.5, 0.6) <= width:
            return [label]
        words = label.split()
        for cut in range(len(words) - 1, 0, -1):
            first, second = " ".join(words[:cut]), " ".join(words[cut:])
            if (tracked_width(first, face, 6.5, 0.6) <= width
                    and tracked_width(second, face, 6.5, 0.6) <= width):
                return [first, second]
        return None

    def _el_table(self, c, page, element, x, top, width, count) -> float:
        pal = self.palette
        title_h = self._el_title(c, page, element.get("title", ""), x, top, width)
        rows = count if element.get("rows") == "fill" else int(element["rows"])
        total = sum(col["width"] for col in element["columns"])
        widths = [width * col["width"] / total for col in element["columns"]]
        headers = []
        for col, col_w in zip(element["columns"], widths):
            lines = self._header_lines(col["label"], col_w - 6)
            if lines is None:
                raise self._too_wide(page, "column", col["label"],
                                     "widen that column or shorten its label")
            headers.append(lines)
        header_h = TABLE_HEADER if all(len(h) <= 1 for h in headers) else TABLE_HEADER_2
        height = title_h + header_h + (rows or 0) * TABLE_ROW
        if c is None:
            return height
        t = top - title_h
        bottom = t - header_h - rows * TABLE_ROW
        c.setFillColor(hex_color(pal.accent_for_page, 0.08))
        c.rect(x, t - header_h, width, header_h, stroke=0, fill=1)
        face = font("sans", "bold")
        left = x
        for lines, col_w in zip(headers, widths):
            first = t - header_h / 2 - 2.3 + (len(lines) - 1) * 4.0
            for i, line in enumerate(lines):
                draw_tracked(c, line, left + 3, first - i * 8.0, face, 6.5, pal.interior_ink,
                             0.6, centred=False)
            left += col_w
        rule(c, x, t - header_h, width, pal.accent_for_page, 0.8)
        for row in range(1, rows + 1):
            rule(c, x, t - header_h - row * TABLE_ROW, width, pal.interior_rule, 0.5)
        c.setStrokeColor(hex_color(pal.interior_rule))
        c.setLineWidth(0.5)
        left = x
        for col_w in widths[:-1]:
            left += col_w
            c.line(left, t, left, bottom)
        return height

    def _el_scale(self, c, page, element, x, top, width, count) -> float:
        pal = self.palette
        title_h = self._el_title(c, page, element.get("title", ""), x, top, width)
        low, high = int(element["min"]), int(element["max"])
        steps = high - low + 1
        label_w = width * 0.3
        pitch = (width - label_w) / steps
        box = min(SCALE_BOX_MAX, pitch - 2.5)
        if box < SCALE_BOX_MIN:
            raise self._too_wide(page, "scale", element.get("title") or "scale",
                                 f"use a shorter range than {low}-{high}")
        text_face = font("text", "regular")
        for item in element["items"]:
            if pdfmetrics.stringWidth(item, text_face, 9) > label_w - 6:
                raise self._too_wide(page, "scale item", item, "shorten it")
        height = title_h + len(element["items"]) * SCALE_PITCH - 4
        if c is None:
            return height
        number_face = font("sans", "regular")
        for index, item in enumerate(element["items"]):
            row_top = top - title_h - index * SCALE_PITCH
            c.setFont(text_face, 9)
            c.setFillColor(hex_color(pal.interior_ink))
            c.drawString(x, row_top - box / 2 - 3, item)
            for step in range(steps):
                bx = x + label_w + step * pitch
                c.setStrokeColor(hex_color(pal.interior_rule))
                c.setLineWidth(0.6)
                c.rect(bx, row_top - box, box, box, stroke=1, fill=0)
                c.setFont(number_face, 6)
                c.setFillColor(hex_color(pal.interior_soft))
                c.drawCentredString(bx + box / 2, row_top - box / 2 - 2, str(low + step))
        return height

    def _el_checks(self, c, page, element, x, top, width, count) -> float:
        pal = self.palette
        title_h = self._el_title(c, page, element.get("title", ""), x, top, width)
        items, columns = element["items"], int(element.get("columns", 3))
        column_w = width / columns
        face = font("text", "regular")
        for item in items:
            if pdfmetrics.stringWidth(item, face, 9) > column_w - 17:
                raise self._too_wide(page, "checkbox label", item, "use fewer columns")
        rows = -(-len(items) // columns)
        height = title_h + rows * CHECK_PITCH - 4
        if c is None:
            return height
        for index, item in enumerate(items):
            row, col = divmod(index, columns)
            cx = x + col * column_w
            row_top = top - title_h - row * CHECK_PITCH
            c.setStrokeColor(hex_color(pal.interior_rule))
            c.setLineWidth(0.6)
            c.rect(cx, row_top - 10, 8.5, 8.5, stroke=1, fill=0)
            c.setFont(face, 9)
            c.setFillColor(hex_color(pal.interior_ink))
            c.drawString(cx + 13, row_top - 8.5, item)
        return height

    def _el_lines(self, c, page, element, x, top, width, count) -> float:
        label = element.get("label", "")
        label_h = LINES_LABEL_H if label else 0.0
        lines = count if element.get("count") == "fill" else int(element["count"])
        if c is not None:
            if label:
                draw_tracked(c, label, x, top - 7, font("sans", "regular"), 7,
                             self.palette.interior_soft, 1.2, centred=False)
            ruled_lines(c, x, top - label_h - LINE_PITCH, width, lines, LINE_PITCH,
                        self.palette.interior_rule, 0.5)
        return label_h + (lines or 0) * LINE_PITCH

    def _el_prompt(self, c, page, element, x, top, width, count) -> float:
        pal = self.palette
        prompt = page.data.get("prompt", "")
        if not prompt:
            raise RenderError(f"page {page.data.get('heading', '')!r}: a prompt box with "
                              f"no prompt to print")
        face = font("text", "italic")
        lines = wrap(prompt, face, 10.5, width - 20)
        height = 29.0 + (len(lines) - 1) * 13.5 + 9.0
        if c is None:
            return height
        c.setFillColor(hex_color(pal.accent_for_page, 0.07))
        c.rect(x, top - height, width, height, stroke=0, fill=1)
        rule(c, x, top - height, width, pal.accent_for_page, 0.8)
        label = element.get("label", "")
        if label:
            draw_tracked(c, label, x + 10, top - 14, font("sans", "bold"), 7,
                         pal.accent_for_page, 1.4, centred=False)
        draw_block(c, prompt, x + 10, top - 29, width - 20, face, 10.5, 13.5,
                   pal.interior_ink)
        return height

    def _paragraphs(self, c, paragraphs: list[str], x: float, top: float, width: float,
                    face: str, size: float, leading: float, gap: float,
                    colour: str) -> float:
        """Paragraphs one after another; returns the height they take."""
        if not paragraphs:
            return 0.0
        baseline = top - size
        for index, paragraph in enumerate(paragraphs):
            lines = wrap(paragraph, face, size, width)
            if c is not None:
                draw_block(c, paragraph, x, baseline, width, face, size, leading, colour)
            baseline -= (len(lines) - 1) * leading
            if index < len(paragraphs) - 1:
                baseline -= leading + gap
        return top - baseline + size * 0.3

    def _el_text(self, c, page, element, x, top, width, count) -> float:
        return self._paragraphs(c, element["paragraphs"], x, top, width,
                                font("text", "regular"), 9.5, 13.5, 4.0,
                                self.palette.interior_ink)

    def _el_note(self, c, page, element, x, top, width, count) -> float:
        return self._paragraphs(c, [element["text"]], x, top, width,
                                font("text", "italic"), 8.5, 11.5, 0.0,
                                self.palette.interior_soft)

    def _text_page(self, c: pdfcanvas.Canvas, page: PageSpec, n: int) -> None:
        """A reference page: heading, prose, and the sources under a rule.

        The body shrinks to fit, but not below 9pt — a page of rules a carer
        has to act on is not the place for small print. Past that it raises.
        """
        x, y, width, height = self.text_frame(n)
        pal = self.palette
        top = y + height
        heading = page.data.get("heading", "")
        display = font("display", "bold")
        heading_lines = wrap(heading, display, 21, width)
        body_top = top - 26 - (len(heading_lines) - 1) * 25 - 18
        paragraphs = page.data.get("paragraphs", [])
        notes = page.data.get("notes", [])
        body_face, note_face = font("text", "regular"), font("text", "italic")

        def measure(size: float) -> tuple[float, float]:
            body = self._paragraphs(None, paragraphs, x, body_top, width, body_face,
                                    size, size * 1.45, size * 0.55, pal.interior_ink)
            extra = (18 + self._paragraphs(None, notes, x, 0, width, note_face, 8, 11,
                                           3, pal.interior_soft)) if notes else 0.0
            return body, extra

        for size in (10.5, 10.0, 9.5, 9.0):
            body, extra = measure(size)
            if body_top - body - extra >= y:
                break
        else:
            raise RenderError(
                f"text page {n} ({heading!r}) does not fit a {self.plan.trim_size} page "
                f"even at 9pt ({body + extra:.0f}pt of text for {body_top - y:.0f}pt of "
                f"room). Split it into two reference pages or shorten it."
            )
        self._eyebrow(c, page.data.get("eyebrow", ""), x, top)
        draw_block(c, heading, x, top - 26, width, display, 21, 25, pal.interior_ink)
        self._paragraphs(c, paragraphs, x, body_top, width, body_face, size, size * 1.45,
                         size * 0.55, pal.interior_ink)
        if notes:
            cursor = body_top - body - 10
            rule(c, x, cursor, width * 0.14, pal.accent_for_page, 0.8)
            self._paragraphs(c, notes, x, cursor - 8, width, note_face, 8, 11, 3,
                             pal.interior_soft)


def render_interior(
    plan: InteriorPlan,
    config: EngineConfig,
    path: str | Path,
    palette: Palette | None = None,
    motif: str | None = None,
    seed: int = 0,
) -> Path:
    return InteriorRenderer(plan, config, palette, motif, seed).render(path)


__all__ = ["InteriorRenderer", "render_interior", "balanced", "wrap", "pdfmetrics"]
