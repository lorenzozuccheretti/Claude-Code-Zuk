"""Word search generation — and, more importantly, verification.

A puzzle book is the one low-content format where "is it correct?" is a
question code can settle completely: every listed word must appear in the grid
exactly where the solution says it does, and a word must not appear twice by
accident. ``verify_puzzle`` is what the substance gate runs; it does not trust
the generator that produced the grid.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any, Iterable

from .rng import StageRandom

# (dx, dy) — right, down, and the two diagonals, plus their reverses.
DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0), (0, 1), (1, 1), (1, -1), (-1, 0), (0, -1), (-1, -1), (-1, 1),
)
EASY_DIRECTIONS: tuple[tuple[int, int], ...] = ((1, 0), (0, 1))
MEDIUM_DIRECTIONS: tuple[tuple[int, int], ...] = ((1, 0), (0, 1), (1, 1), (1, -1))

ALPHABET = string.ascii_uppercase


class PuzzleGenerationError(RuntimeError):
    """The generator could not place every word — never silently drop one."""


@dataclass(frozen=True)
class Placement:
    word: str
    row: int
    col: int
    d_row: int
    d_col: int

    def cells(self) -> list[tuple[int, int]]:
        return [
            (self.row + i * self.d_row, self.col + i * self.d_col)
            for i in range(len(self.word))
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "row": self.row,
            "col": self.col,
            "d_row": self.d_row,
            "d_col": self.d_col,
        }


@dataclass
class Puzzle:
    number: int
    theme: str
    size: int
    grid: list[list[str]]
    words: list[str]
    placements: list[Placement] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "theme": self.theme,
            "size": self.size,
            "grid": ["".join(row) for row in self.grid],
            "words": list(self.words),
            "placements": [p.as_dict() for p in self.placements],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Puzzle":
        return cls(
            number=int(data["number"]),
            theme=str(data["theme"]),
            size=int(data["size"]),
            grid=[list(row) for row in data["grid"]],
            words=[str(w) for w in data["words"]],
            placements=[Placement(**p) for p in data.get("placements", [])],
        )


def normalize_word(word: str) -> str:
    """Grid letters only: uppercase A–Z, no spaces or punctuation."""
    return "".join(ch for ch in word.upper() if ch in ALPHABET)


def _fits(grid: list[list[str]], word: str, row: int, col: int, d_row: int, d_col: int) -> bool:
    size = len(grid)
    for i, letter in enumerate(word):
        r, c = row + i * d_row, col + i * d_col
        if not (0 <= r < size and 0 <= c < size):
            return False
        existing = grid[r][c]
        if existing not in ("", letter):
            return False
    return True


def generate_puzzle(
    number: int,
    theme: str,
    words: Iterable[str],
    size: int,
    rng: StageRandom,
    difficulty: str = "medium",
    max_attempts: int = 400,
) -> Puzzle:
    """Place every word or raise. A dropped word is a defect, not a variation."""
    directions = {
        "easy": EASY_DIRECTIONS,
        "medium": MEDIUM_DIRECTIONS,
        "hard": DIRECTIONS,
    }.get(difficulty, MEDIUM_DIRECTIONS)

    cleaned = [normalize_word(w) for w in words]
    cleaned = [w for w in cleaned if w]
    if not cleaned:
        raise PuzzleGenerationError(f"puzzle {number} ({theme}) has no usable words")
    too_long = [w for w in cleaned if len(w) > size]
    if too_long:
        raise PuzzleGenerationError(
            f"puzzle {number} ({theme}): {', '.join(too_long)} do not fit in a {size}x{size} grid"
        )

    grid = [["" for _ in range(size)] for _ in range(size)]
    placements: list[Placement] = []
    # Longest first: the hard ones get the empty grid.
    order = sorted(cleaned, key=len, reverse=True)
    place_rng = rng.stream(f"place/{number}")

    for word in order:
        placed = False
        for attempt in range(max_attempts):
            d_row, d_col = place_rng.choice(directions)
            row = place_rng.randint(0, size - 1)
            col = place_rng.randint(0, size - 1)
            if not _fits(grid, word, row, col, d_row, d_col):
                continue
            for i, letter in enumerate(word):
                grid[row + i * d_row][col + i * d_col] = letter
            placements.append(Placement(word, row, col, d_row, d_col))
            placed = True
            break
        if not placed:
            raise PuzzleGenerationError(
                f"puzzle {number} ({theme}): could not place {word!r} in {max_attempts} "
                f"attempts on a {size}x{size} grid — use fewer or shorter words, "
                f"or a bigger grid"
            )

    fill_rng = rng.stream(f"fill/{number}")
    for r in range(size):
        for c in range(size):
            if not grid[r][c]:
                grid[r][c] = fill_rng.choice(ALPHABET)

    return Puzzle(
        number=number,
        theme=theme,
        size=size,
        grid=grid,
        words=sorted(cleaned),
        placements=sorted(placements, key=lambda p: p.word),
    )


def find_occurrences(grid: list[list[str]], word: str) -> list[Placement]:
    """Every position and direction in which the word appears in the grid."""
    size = len(grid)
    found: list[Placement] = []
    for row in range(size):
        for col in range(size):
            for d_row, d_col in DIRECTIONS:
                end_r = row + (len(word) - 1) * d_row
                end_c = col + (len(word) - 1) * d_col
                if not (0 <= end_r < size and 0 <= end_c < size):
                    continue
                if all(
                    grid[row + i * d_row][col + i * d_col] == letter
                    for i, letter in enumerate(word)
                ):
                    found.append(Placement(word, row, col, d_row, d_col))
    return found


def verify_puzzle(puzzle: Puzzle) -> list[tuple[str, bool, str]]:
    """Independent verification — reads the grid, ignores the generator.

    Returns (check name, passed, one-line reason) triples.
    """
    size = puzzle.size
    results: list[tuple[str, bool, str]] = []

    square = len(puzzle.grid) == size and all(len(row) == size for row in puzzle.grid)
    results.append(
        (
            f"p{puzzle.number}_grid_shape",
            square,
            f"grid is {len(puzzle.grid)}x{len(puzzle.grid[0]) if puzzle.grid else 0}, expected {size}x{size}",
        )
    )
    if not square:
        return results

    letters_ok = all(cell in ALPHABET for row in puzzle.grid for cell in row)
    results.append(
        (
            f"p{puzzle.number}_grid_letters",
            letters_ok,
            "every cell holds a single A–Z letter" if letters_ok else "grid contains empty or non-letter cells",
        )
    )

    missing = [w for w in puzzle.words if not find_occurrences(puzzle.grid, w)]
    results.append(
        (
            f"p{puzzle.number}_words_findable",
            not missing,
            f"all {len(puzzle.words)} words are findable in the grid"
            if not missing
            else f"not in the grid at all: {', '.join(missing)}",
        )
    )

    solution_wrong = []
    for placement in puzzle.placements:
        cells = placement.cells()
        if any(not (0 <= r < size and 0 <= c < size) for r, c in cells):
            solution_wrong.append(placement.word)
            continue
        spelled = "".join(puzzle.grid[r][c] for r, c in cells)
        if spelled != placement.word:
            solution_wrong.append(placement.word)
    results.append(
        (
            f"p{puzzle.number}_solution_correct",
            not solution_wrong,
            "the solution key points at the right cells for every word"
            if not solution_wrong
            else f"solution key is wrong for: {', '.join(solution_wrong)}",
        )
    )

    solved_words = {p.word for p in puzzle.placements}
    results.append(
        (
            f"p{puzzle.number}_solution_complete",
            solved_words == set(puzzle.words),
            f"{len(solved_words)} of {len(puzzle.words)} words have a solution entry",
        )
    )
    return results
