"""Book types the factory can build. Importing this package registers them."""

from .base import (
    BookType,
    ContentUnit,
    InteriorPlan,
    PageSpec,
    TitleProposal,
    available,
    get_book_type,
    is_registered,
    normalize,
    register,
)
from .journal import JournalBookType
from .planner import PlannerBookType
from .puzzle import WordSearchBookType

__all__ = [
    "BookType",
    "ContentUnit",
    "InteriorPlan",
    "PageSpec",
    "TitleProposal",
    "JournalBookType",
    "PlannerBookType",
    "WordSearchBookType",
    "available",
    "get_book_type",
    "is_registered",
    "normalize",
    "register",
]
