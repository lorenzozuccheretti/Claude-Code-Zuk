"""File naming is countable too — so code decides it, not a human at 1am.

Every artifact of a run lands under ``<output_root>/<slug>/`` in a numbered
station folder, so the folder listing reads as the build order.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_NON_SLUG = re.compile(r"[^a-z0-9]+")

STATION_DIRS = {
    1: "01_niche",
    2: "02_interior",
    3: "03_cover",
    4: "04_listing",
    5: "05_upload",
    6: "06_review",
}
GATES_DIR = "gates"


def slugify(value: str, max_length: int = 60) -> str:
    """A lowercase, hyphenated, ASCII-only slug. Stable for the same input."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", ascii_only).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    if not slug:
        raise ValueError(f"{value!r} does not contain any slug-able characters")
    return slug


def run_slug(niche_slug: str, book_type: str, seed: int) -> str:
    """The build's identity: niche + book type + seed, all of it in the name."""
    return f"{niche_slug}--{slugify(book_type)}--s{seed}"


def artifact_name(slug: str, kind: str, extension: str) -> str:
    return f"{slug}_{kind}.{extension.lstrip('.')}"


def sha256_file(path: Path, chunk_size: int = 1 << 16) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
