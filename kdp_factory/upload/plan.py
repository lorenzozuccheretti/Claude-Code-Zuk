"""Station 5's artifact: exactly what to type, and where.

The checklist is specific about what a good upload run looks like: "It typed
from the listing file, not from memory. It read the form back before submitting.
It stopped at Publish." All three properties live in this plan — the driver just
executes it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

SELECTORS_PATH = Path(__file__).with_name("selectors.yaml")

# The one action this engine will not perform. Station 6 is a human.
FORBIDDEN_ACTIONS = ("publish", "submit for review", "approve proof")


@dataclass
class Field:
    screen: str
    name: str
    value: Any
    labels: list[str] = field(default_factory=list)
    css: list[str] = field(default_factory=list)
    kind: str = "text"  # text | textarea | select | radio | file | repeated


@dataclass
class UploadPlan:
    slug: str
    marketplace: str
    stops_before: str
    fields: list[Field]
    files: list[Field]
    read_back: list[str]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "marketplace": self.marketplace,
            "stops_before": self.stops_before,
            "forbidden_actions": list(FORBIDDEN_ACTIONS),
            "fields": [asdict(f) for f in self.fields],
            "files": [asdict(f) for f in self.files],
            "read_back": list(self.read_back),
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Upload run — {self.slug}",
            "",
            f"Marketplace: {self.marketplace}",
            f"**This run stops before: {self.stops_before}.**",
            "",
            "## Fields to type (from the listing file, not from memory)",
            "",
            "| Screen | Field | Value |",
            "| --- | --- | --- |",
        ]
        for item in self.fields:
            value = str(item.value)
            if len(value) > 80:
                value = value[:77] + "…"
            value = value.replace("|", r"\|")
            lines.append(f"| {item.screen} | {item.name} | {value} |")
        lines += ["", "## Files to upload", ""]
        lines += [f"- **{f.name}** → `{f.value}`" for f in self.files]
        lines += ["", "## Read back before leaving the form", ""]
        lines += [f"- {name}" for name in self.read_back]
        lines += ["", "## Notes", ""]
        lines += [f"- {note}" for note in self.notes]
        lines.append("")
        return "\n".join(lines)


def _load_selectors() -> dict[str, Any]:
    with SELECTORS_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_upload_plan(
    slug: str,
    listing: dict[str, Any],
    manuscript: Path,
    cover: Path,
    marketplace: str = "kdp.amazon.com",
) -> UploadPlan:
    selectors = _load_selectors()["screens"]
    author = str(listing.get("author", "")).strip()
    first, _, last = author.partition(" ")

    def spec(screen: str, name: str) -> dict[str, Any]:
        return selectors.get(screen, {}).get("fields", {}).get(name, {})

    fields: list[Field] = []

    def add(screen: str, name: str, value: Any, kind: str = "text") -> None:
        s = spec(screen, name)
        fields.append(
            Field(
                screen=screen,
                name=name,
                value=value,
                labels=list(s.get("labels", [])),
                css=list(s.get("css", [])),
                kind=kind,
            )
        )

    add("details", "title", listing["title"])
    add("details", "subtitle", listing["subtitle"])
    add("details", "author_first", first or author)
    add("details", "author_last", last or "")
    add("details", "description", listing["description_html"], kind="textarea")
    add("details", "language", listing.get("language", "english"), kind="select")
    for index, keyword in enumerate(listing["keywords"]):
        s = spec("details", "keywords")
        fields.append(
            Field(
                screen="details",
                name=f"keyword_{index + 1}",
                value=keyword,
                labels=[f"{label} {index + 1}" for label in s.get("labels", [])],
                css=[c.replace("{index}", str(index)) for c in s.get("css", [])],
                kind="repeated",
            )
        )

    print_settings = listing["print"]
    add("content", "trim_size", print_settings["trim_size"], kind="select")
    add("content", "paper_type", print_settings["paper"], kind="radio")
    add("content", "bleed", "no" if not print_settings.get("bleed") else "yes", kind="radio")
    add("content", "cover_finish", print_settings.get("cover_finish", "matte"), kind="radio")
    add("pricing", "list_price", f"{listing['price']['list_price_usd']:.2f}")

    uploads = selectors["content"]["uploads"]
    files = [
        Field(
            screen="content",
            name="manuscript",
            value=str(manuscript),
            labels=list(uploads["manuscript"]["labels"]),
            css=list(uploads["manuscript"]["css"]),
            kind="file",
        ),
        Field(
            screen="content",
            name="cover",
            value=str(cover),
            labels=list(uploads["cover"]["labels"]),
            css=list(uploads["cover"]["css"]),
            kind="file",
        ),
    ]

    notes = [
        "Categories are chosen in a modal on the details screen; the driver does not "
        "click through it. Set them by hand from the listing file: "
        + "; ".join(listing["categories"]),
        "KDP asks for an ISBN on the content screen. Choose a free KDP ISBN unless you "
        "have your own — the engine does not decide this for you.",
        "The driver logs in nowhere: sign in yourself in the browser window it opens.",
        f"The run stops before Publish. {FORBIDDEN_ACTIONS[0]!r} is not an action this "
        "engine performs.",
    ]
    return UploadPlan(
        slug=slug,
        marketplace=marketplace,
        stops_before="Publish Your Paperback Book",
        fields=fields,
        files=files,
        read_back=[f.name for f in fields if f.screen == "details"]
        + ["list_price", "trim_size", "paper_type"],
        notes=notes,
    )
