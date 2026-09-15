"""One definition of "these two lines read the same".

The generator refuses to draw a line too close to one it already drew, and the
substance gate fails a book whose lines are too close together. If those two
used different tokenisers — one splitting "week's" into two words, the other
keeping it whole — the generator could pass itself and still fail the gate.
They both import from here.
"""

from __future__ import annotations

import re

WORD_RE = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> str:
    """Lowercased word sequence — the form comparisons are made on."""
    return " ".join(WORD_RE.findall(text.lower()))


def token_set(text: str) -> set[str]:
    return set(WORD_RE.findall(text.lower()))


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of two lines, between 0 and 1."""
    return token_similarity(token_set(a), token_set(b))


def token_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / (len(a) + len(b) - intersection)
