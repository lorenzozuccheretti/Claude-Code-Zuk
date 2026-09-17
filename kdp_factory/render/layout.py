"""Text primitives both renderers share.

Extracted after a real defect: letterspacing set on a text object stays in the
PDF's text state after the object ends, so every string drawn afterwards was
rendered wider than ReportLab measured it — and a cover title silently crossed
the trim. The fix belongs in one place, not two.
"""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics

from .design import hex_color


def wrap(text: str, face: str, size: float, width: float) -> list[str]:
    """Greedy wrap. A single word wider than the measure gets its own line."""
    words = text.split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if pdfmetrics.stringWidth(candidate, face, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def balanced(text: str, face: str, size: float, width: float) -> list[str]:
    """Wrap into even lines instead of filling each one to the edge.

    Greedy wrapping leaves a long first line and a stub last one. Narrowing the
    measure until the line count would grow finds the tightest width that keeps
    the same number of lines, which is what evens them out.
    """
    lines = wrap(text, face, size, width)
    if len(lines) < 2:
        return lines
    target = len(lines)
    low = max(pdfmetrics.stringWidth(word, face, size) for word in text.split())
    high = width
    while high - low > 1.0:
        middle = (low + high) / 2
        if len(wrap(text, face, size, middle)) <= target:
            high = middle
        else:
            low = middle
    return wrap(text, face, size, high)


def fit_size(text: str, face: str, width: float, start: float,
             minimum: float, max_lines: int) -> float:
    """The largest size at which the text still fits in ``max_lines``."""
    size = start
    while size > minimum and len(wrap(text, face, size, width)) > max_lines:
        size -= 0.5
    return size


def tracked_width(text: str, face: str, size: float, tracking: float) -> float:
    caps = text.upper()
    return pdfmetrics.stringWidth(caps, face, size) + tracking * max(len(caps) - 1, 0)


def draw_tracked(c, text: str, x: float, y: float, face: str, size: float,
                 color: str, tracking: float = 1.6, centred: bool = True) -> float:
    """Letterspaced caps — the label voice.

    Character spacing is part of the PDF text state and survives BT/ET, so the
    save/restore is load-bearing: without it every later string is drawn wider
    than it was measured.
    """
    if not text:
        return 0.0
    caps = text.upper()
    width = tracked_width(caps, face, size, tracking)
    start = x - width / 2 if centred else x
    c.saveState()
    obj = c.beginText()
    obj.setFont(face, size)
    obj.setFillColor(hex_color(color))
    obj.setCharSpace(tracking)
    obj.setTextOrigin(start, y)
    obj.textOut(caps)
    c.drawText(obj)
    c.restoreState()
    return width


def draw_block(c, text: str, x: float, y: float, width: float, face: str,
               size: float, leading: float, color: str, centred: bool = False,
               balance: bool = False, on_line=None) -> float:
    """Draw a wrapped paragraph; returns the baseline below the last line."""
    c.setFont(face, size)
    c.setFillColor(hex_color(color))
    lines = balanced(text, face, size, width) if balance else wrap(text, face, size, width)
    for line in lines:
        line_width = pdfmetrics.stringWidth(line, face, size)
        left = x + (width - line_width) / 2 if centred else x
        if on_line is not None:
            on_line(left, left + line_width, y)
        if centred:
            c.drawCentredString(x + width / 2, y, line)
        else:
            c.drawString(x, y, line)
        y -= leading
    return y


def ruled_lines(c, x: float, top: float, width: float, count: int,
                spacing: float, color: str, weight: float = 0.5) -> float:
    """The lines a buyer writes on. Light enough not to fight their handwriting."""
    c.setStrokeColor(hex_color(color))
    c.setLineWidth(weight)
    y = top
    for _ in range(count):
        c.line(x, y, x + width, y)
        y -= spacing
    return y


def rule(c, x: float, y: float, width: float, color: str, weight: float = 0.8) -> None:
    c.setStrokeColor(hex_color(color))
    c.setLineWidth(weight)
    c.line(x, y, x + width, y)
