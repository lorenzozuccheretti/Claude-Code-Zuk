"""Book types: the pluggable part of the factory.

Adding a second book type should be a new module and a decorator, not a fork
of the engine. A book type answers three questions:

* what pages does this book have, in order (``plan``)?
* what would it be called (``titles``)?
* what can code verify about it that is specific to this format (``verify``)?

Everything downstream — the renderer, the gates, the listing — works off the
``InteriorPlan`` a book type returns, so the engine itself never needs to know
what a word search is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..config import EngineConfig
from ..content.rng import StageRandom
from ..content.text import normalize
from ..errors import ConfigError
from ..niche import Niche
from ..spec.kdp import even_up

@dataclass(frozen=True)
class ContentUnit:
    """One distinct thing the buyer paid for.

    A journal prompt, a planner week's focus, a puzzle's word list. The
    substance gate counts these, compares them for repetition, and checks that
    each one really appears in the rendered PDF.
    """

    kind: str
    text: str
    page_index: int
    # The literal string that must appear on the rendered page. For a journal
    # prompt that is the whole prompt; for a puzzle it is the theme, because the
    # word list is scattered through a grid. The substance gate reads this out
    # of the PDF, so it is how a unit proves it was actually printed.
    probe: str = ""

    @property
    def normalized(self) -> str:
        return normalize(self.text)

    @property
    def normalized_probe(self) -> str:
        return normalize(self.probe or self.text)


@dataclass(frozen=True)
class PageSpec:
    """One page of the interior, as a renderer instruction."""

    template: str
    kind: str = "content"  # front_matter | content | divider | back_matter | filler
    data: dict[str, Any] = field(default_factory=dict)
    show_page_number: bool = True

    @property
    def is_filler(self) -> bool:
        return self.kind == "filler"

    @property
    def is_content(self) -> bool:
        return self.kind in {"content", "divider"}


@dataclass
class InteriorPlan:
    """The whole book, decided before a single point is drawn.

    Page count is a property of the plan, not an accident of rendering: the
    print gate later compares it against the PDF that actually exists.
    """

    book_type: str
    title: str
    subtitle: str
    trim_size: str
    paper: str
    pages: list[PageSpec]
    content_units: list[ContentUnit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def filler_pages(self) -> int:
        return sum(1 for p in self.pages if p.is_filler)

    @property
    def content_pages(self) -> int:
        return sum(1 for p in self.pages if p.is_content)

    @property
    def front_matter_pages(self) -> int:
        return sum(1 for p in self.pages if p.kind == "front_matter")

    @property
    def back_matter_pages(self) -> int:
        return sum(1 for p in self.pages if p.kind == "back_matter")

    def as_dict(self) -> dict[str, Any]:
        return {
            "book_type": self.book_type,
            "title": self.title,
            "subtitle": self.subtitle,
            "trim_size": self.trim_size,
            "paper": self.paper,
            "page_count": self.page_count,
            "front_matter_pages": self.front_matter_pages,
            "back_matter_pages": self.back_matter_pages,
            "content_pages": self.content_pages,
            "filler_pages": self.filler_pages,
            "content_unit_count": len(self.content_units),
            "pages": [asdict(p) for p in self.pages],
            "content_units": [asdict(u) for u in self.content_units],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class TitleProposal:
    """What this book would be called, and why."""

    title: str
    subtitle: str
    rationale: str = ""

    @property
    def combined(self) -> str:
        return f"{self.title}: {self.subtitle}" if self.subtitle else self.title


class BookType:
    """Base class for a format the factory knows how to build."""

    key: str = "book"
    label: str = "Book"
    description: str = ""
    default_options: dict[str, Any] = {}
    # Front/back matter templates every book type shares unless it overrides.
    front_matter_templates = ("title_page", "copyright_page", "belongs_to_page", "how_to_use_page")
    back_matter_templates = ("closing_page",)

    def options_for(self, niche: Niche) -> dict[str, Any]:
        """Merge the type's defaults with whatever the niche overrides."""
        options = dict(self.default_options)
        options.update(niche.options or {})
        return options

    def plan(
        self,
        niche: Niche,
        config: EngineConfig,
        rng: StageRandom,
        options: dict[str, Any] | None = None,
    ) -> InteriorPlan:  # pragma: no cover - interface
        raise NotImplementedError

    def titles(self, niche: Niche, rng: StageRandom) -> TitleProposal:  # pragma: no cover
        raise NotImplementedError

    def verify(self, plan: InteriorPlan) -> list[tuple[str, bool, str]]:
        """Format-specific checks code can settle. Empty by default."""
        return []

    # ------------------------------------------------------------- helpers
    def _without_used(
        self, pool: list[str], options: dict[str, Any], what: str = "lines"
    ) -> list[str]:
        """Drop anything previous runs already used, when the caller asked.

        Opt-in via ``options['avoid']`` (the CLI's ``--avoid``). Keeps the pool
        untouched otherwise, so a plain build stays reproducible from its seed
        alone.
        """
        avoid = options.get("avoid") or set()
        if not avoid:
            return pool
        kept = [line for line in pool if normalize(line) not in avoid]
        if not kept:
            raise ConfigError(
                f"every one of the {len(pool)} available {what} has been used by a "
                f"previous run. Add material to the template pack before publishing "
                f"another book in this niche."
            )
        return kept

    def _pad_to_even(self, pages: list[PageSpec]) -> list[PageSpec]:
        """A printed book has an even page count. Pad with one blank leaf."""
        if len(pages) % 2 == 1:
            pages.append(PageSpec("blank_page", kind="filler", show_page_number=False))
        return pages

    def _pad_to(self, pages: list[PageSpec], target: int) -> list[PageSpec]:
        """Bring a plan up to a minimum page count with notes pages.

        Notes pages are filler and count against the padding ratio in the
        substance gate — which is the point: padding you cannot hide.
        """
        target = even_up(target)
        while len(pages) < target:
            pages.insert(
                len(pages) - len(self.back_matter_templates),
                PageSpec("notes_page", kind="filler", data={"heading": "Notes"}),
            )
        return pages


TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "and", "with", "to", "in", "on", "your", "my",
})


def pick_title(patterns: Sequence[str], rng: StageRandom, **fields: object) -> str:
    """The first pattern that does not repeat a word once filled in.

    "Weekly: The Undated Weekly Planner" is what happens when a topic extracted
    from the niche collides with the words already in the pattern.
    """
    ordered = rng.shuffled(patterns)
    for pattern in ordered:
        candidate = pattern.format(**fields)
        words = [w for w in normalize(candidate).split() if w not in TITLE_STOPWORDS]
        if len(words) == len(set(words)):
            return candidate
    return ordered[0].format(**fields)


_REGISTRY: dict[str, BookType] = {}


def register(cls: type[BookType]) -> type[BookType]:
    """Class decorator: make a book type available to the engine."""
    instance = cls()
    if not instance.key:
        raise ConfigError(f"{cls.__name__} must define a non-empty key")
    _REGISTRY[instance.key] = instance
    return cls


def get_book_type(key: str) -> BookType:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ConfigError(
            f"unknown book type {key!r}; registered types: {', '.join(sorted(_REGISTRY)) or 'none'}"
        ) from None


def available() -> list[BookType]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def is_registered(key: str) -> bool:
    return key in _REGISTRY


def unique_texts(units: Iterable[ContentUnit]) -> set[str]:
    return {u.normalized for u in units}


def build_units(
    kind: str, texts: Iterable[str], page_index_of: Callable[[int], int]
) -> list[ContentUnit]:
    return [
        ContentUnit(kind=kind, text=text, page_index=page_index_of(i))
        for i, text in enumerate(texts)
    ]
