"""Form-based logbook: a daily page, a weekly review, records and reference.

A logbook is the opposite of a journal. Its pages are forms — label and rule,
a ruled table, a 0-10 scale, a row of checkboxes — and they are supposed to be
the same every day, because a care diary that changes its layout on day 12 is
one nobody can read back to a doctor. So the thing that makes a logbook worth
its price is not variety in the forms; it is that:

* the forms are the right ones, in the right order, for this reader — which is
  what a *pack* (``content/templates/logbook_<pack>.yaml``) decides;
* every daily, weekly and monthly page also carries one line written for the
  person filling it in, and those lines never repeat;
* the reference pages say something true, and say where it came from.

The pack holds every word, including the front and back matter, so a logbook
is entirely in its pack's language; the renderer only adds the locale's own
labels (the "how to use" eyebrow, "also from this press", page numbers).

Page count is solved, like every other type: front matter, reference, profile,
the daily cycle with its weekly and monthly pages, the extras and the closing
page land on ``target_pages`` by choosing the number of days.
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import EngineConfig
from ..content.locale import lookup, normalize_language, text
from ..content.packs import load_pack, select_diverse
from ..content.rng import StageRandom
from ..content.text import normalize, token_set, token_similarity
from ..errors import ConfigError
from ..niche import Niche
from ..spec.kdp import KDP_SPEC, even_up
from .base import BookType, ContentUnit, InteriorPlan, PageSpec, TitleProposal, register

log = logging.getLogger("kdp_factory")

DEFAULT_PACK = "example"

# Every form element the renderer can draw, and the keys each one may carry.
ELEMENT_KEYS: dict[str, set[str]] = {
    "fields": {"items", "columns"},
    "table": {"title", "columns", "rows"},
    "scale": {"title", "items", "min", "max"},
    "checks": {"title", "items", "columns"},
    "lines": {"label", "count"},
    "prompt": {"label"},
    "text": {"paragraphs"},
    "note": {"text"},
}
FILL = "fill"
FRONT_TEMPLATES = ("title_page", "copyright_page", "belongs_to_page", "how_to_use_page")


@dataclass(frozen=True)
class FormSpec:
    """One page design from the pack: what it is called and what is on it."""

    eyebrow: str
    heading: str
    elements: list[dict[str, Any]]
    count: int = 1          # extras: how many copies of this page
    every: int = 0          # weekly: after every Nth daily page
    every_weeks: int = 0    # monthly: after every Nth weekly page


@dataclass(frozen=True)
class TextSpec:
    """One reference page: prose, with the sources that back it."""

    eyebrow: str
    heading: str
    paragraphs: list[str]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LogbookPack:
    """A parsed, validated pack. Refuses to exist if the YAML is malformed."""

    name: str
    language: str
    title: str
    subtitle: str
    front: dict[str, Any]
    reference: list[TextSpec]
    profile: list[FormSpec]
    daily: FormSpec
    weekly: FormSpec | None
    monthly: FormSpec | None
    extras: list[FormSpec]
    daily_prompts: list[str]
    weekly_prompts: list[str]
    monthly_prompts: list[str]
    closing_lines: list[str]

    @property
    def extra_pages(self) -> int:
        return sum(spec.count for spec in self.extras)

    def fixed_pages(self, front: int, back: int) -> int:
        """Everything that does not depend on the number of days."""
        return front + len(self.reference) + len(self.profile) + self.extra_pages + back

    def cycle_pages(self, days: int) -> tuple[int, int, int]:
        """(daily, weekly, monthly) pages for a cycle of ``days`` days."""
        weeks = days // self.weekly.every if self.weekly else 0
        months = weeks // self.monthly.every_weeks if self.monthly else 0
        return days, weeks, months


# ------------------------------------------------------------------ loading
def resolve_pack_path(pack: str, base_dir: Path | None = None) -> Path | None:
    """A pack named by path rather than by name, if that is what was given.

    ``caregiver`` means ``templates/logbook_caregiver.yaml``; anything with a
    slash or a ``.yaml`` suffix is a file, tried next to the niche file first.
    That is what lets a test, or a press with a private pack, point at a pack
    outside the engine without copying it in.
    """
    if "/" not in pack and not pack.endswith((".yaml", ".yml")):
        return None
    path = Path(pack)
    if not path.is_absolute() and base_dir is not None and (base_dir / path).is_file():
        return base_dir / path
    return path


def load_logbook_pack(pack: str, base_dir: Path | None = None) -> LogbookPack:
    path = resolve_pack_path(pack, base_dir)
    if path is None:
        raw = load_pack(f"logbook_{pack}")
        where = f"logbook_{pack}.yaml"
    else:
        if not path.is_file():
            raise ConfigError(f"logbook pack not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise ConfigError(f"logbook pack {path} must contain a mapping")
        where = str(path)
    return parse_pack(raw, where)


def parse_pack(raw: dict[str, Any], where: str) -> LogbookPack:
    """Validate a pack's shape up front, so a typo is an error naming the key.

    The renderer can only discover a missing column width halfway through
    drawing page 87; the planner can say so before anything is drawn.
    """
    cycle = raw.get("cycle") or {}
    if not isinstance(cycle, dict) or not cycle.get("daily"):
        raise ConfigError(f"{where}: a logbook pack needs cycle.daily")

    def form(data: Any, label: str, prompts: int) -> FormSpec:
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: {label} must be a mapping")
        elements = [
            _element(e, f"{label}.elements[{i}]", where)
            for i, e in enumerate(data.get("elements") or [])
        ]
        if not elements:
            raise ConfigError(f"{where}: {label} has no elements")
        _check_fill(elements, label, where)
        found = sum(1 for e in elements if e["type"] == "prompt")
        if found != prompts:
            want = "exactly one prompt element" if prompts else "no prompt element"
            raise ConfigError(
                f"{where}: {label} must have {want} (found {found}); a prompt box "
                f"is filled from the cycle's prompt list, so only cycle pages carry one"
            )
        return FormSpec(
            eyebrow=str(data.get("eyebrow") or ""),
            heading=str(data.get("heading") or ""),
            elements=elements,
            count=_positive_int(data.get("count", 1), f"{label}.count", where),
            every=_positive_int(data.get("every", 7), f"{label}.every", where),
            every_weeks=_positive_int(data.get("every_weeks", 4), f"{label}.every_weeks", where),
        )

    reference = []
    for i, page in enumerate(raw.get("reference") or []):
        if not isinstance(page, dict) or not page.get("heading"):
            raise ConfigError(f"{where}: reference[{i}] needs a heading")
        paragraphs = [str(p) for p in (page.get("paragraphs") or []) if str(p).strip()]
        if not paragraphs:
            raise ConfigError(f"{where}: reference[{i}] ({page['heading']!r}) has no paragraphs")
        reference.append(TextSpec(
            eyebrow=str(page.get("eyebrow") or ""),
            heading=str(page["heading"]),
            paragraphs=paragraphs,
            notes=[str(n) for n in (page.get("notes") or []) if str(n).strip()],
        ))

    profile = [form(p, f"profile[{i}]", 0) for i, p in enumerate(raw.get("profile") or [])]
    extras = [form(p, f"extras[{i}]", 0) for i, p in enumerate(raw.get("extras") or [])]
    for spec, label in [(s, f"profile[{i}]") for i, s in enumerate(profile)] + [
        (s, f"extras[{i}]") for i, s in enumerate(extras)
    ]:
        if not spec.heading.strip():
            raise ConfigError(f"{where}: {label} needs a heading")

    front = raw.get("front") or {}
    if not isinstance(front, dict):
        raise ConfigError(f"{where}: front must be a mapping")
    language = str(raw.get("language") or "english")
    return LogbookPack(
        name=where,
        language=normalize_language(language),
        title=str(raw.get("title") or ""),
        subtitle=str(raw.get("subtitle") or ""),
        front=front,
        reference=reference,
        profile=profile,
        daily=form(cycle["daily"], "cycle.daily", 1),
        weekly=form(cycle["weekly"], "cycle.weekly", 1) if cycle.get("weekly") else None,
        monthly=form(cycle["monthly"], "cycle.monthly", 1) if cycle.get("monthly") else None,
        extras=extras,
        daily_prompts=_string_list(raw.get("daily_prompts"), "daily_prompts", where),
        weekly_prompts=_string_list(raw.get("weekly_prompts"), "weekly_prompts", where),
        monthly_prompts=_string_list(raw.get("monthly_prompts"), "monthly_prompts", where),
        closing_lines=_string_list(raw.get("closing_lines"), "closing_lines", where),
    )


def _string_list(value: Any, key: str, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return [str(v).strip() for v in value if str(v).strip()]


def _positive_int(value: Any, key: str, where: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: {key} must be a whole number, got {value!r}") from None
    if number < 1:
        raise ConfigError(f"{where}: {key} must be at least 1, got {number}")
    return number


def _element(raw: Any, label: str, where: str) -> dict[str, Any]:
    """One element, with its defaults filled in so the renderer guesses nothing."""
    if not isinstance(raw, dict) or raw.get("type") not in ELEMENT_KEYS:
        raise ConfigError(
            f"{where}: {label} must be a mapping with a type, one of "
            f"{', '.join(sorted(ELEMENT_KEYS))}"
        )
    kind = raw["type"]
    unknown = set(raw) - ELEMENT_KEYS[kind] - {"type"}
    if unknown:
        raise ConfigError(
            f"{where}: {label} ({kind}) has unknown key(s) {', '.join(sorted(unknown))}; "
            f"it takes {', '.join(sorted(ELEMENT_KEYS[kind]))}"
        )
    out: dict[str, Any] = {"type": kind}

    def items() -> list[str]:
        values = _string_list(raw.get("items"), f"{label}.items", where)
        if not values:
            raise ConfigError(f"{where}: {label} ({kind}) needs a non-empty items list")
        return values

    if kind == "fields":
        out.update(items=items(), columns=_positive_int(raw.get("columns", 2), f"{label}.columns", where))
    elif kind == "checks":
        out.update(title=str(raw.get("title") or ""), items=items(),
                   columns=_positive_int(raw.get("columns", 3), f"{label}.columns", where))
    elif kind == "scale":
        low, high = raw.get("min", 0), raw.get("max", 10)
        if not (isinstance(low, int) and isinstance(high, int) and low < high):
            raise ConfigError(f"{where}: {label} (scale) needs whole numbers min < max")
        out.update(title=str(raw.get("title") or ""), items=items(), min=low, max=high)
    elif kind == "table":
        columns = []
        for i, column in enumerate(raw.get("columns") or []):
            if not isinstance(column, dict) or "label" not in column:
                raise ConfigError(f"{where}: {label}.columns[{i}] needs a label")
            try:
                width = float(column.get("width", 1))
            except (TypeError, ValueError):
                width = 0.0
            if width <= 0:
                raise ConfigError(f"{where}: {label}.columns[{i}] needs a positive width")
            columns.append({"label": str(column["label"]), "width": width})
        if not columns:
            raise ConfigError(f"{where}: {label} (table) needs at least one column")
        rows = raw.get("rows", FILL)
        out.update(title=str(raw.get("title") or ""), columns=columns,
                   rows=FILL if rows == FILL else _positive_int(rows, f"{label}.rows", where))
    elif kind == "lines":
        count = raw.get("count", FILL)
        out.update(label=str(raw.get("label") or ""),
                   count=FILL if count == FILL else _positive_int(count, f"{label}.count", where))
    elif kind == "prompt":
        out.update(label=str(raw.get("label") or ""))
    elif kind == "text":
        paragraphs = _string_list(raw.get("paragraphs"), f"{label}.paragraphs", where)
        if not paragraphs:
            raise ConfigError(f"{where}: {label} (text) needs paragraphs")
        out.update(paragraphs=paragraphs)
    elif kind == "note":
        if not str(raw.get("text") or "").strip():
            raise ConfigError(f"{where}: {label} (note) needs text")
        out.update(text=str(raw["text"]).strip())
    return out


def _check_fill(elements: list[dict[str, Any]], label: str, where: str) -> None:
    """``fill`` takes what is left, so only the last element may ask for it."""
    for index, element in enumerate(elements):
        flexible = element.get("rows") == FILL or element.get("count") == FILL
        if flexible and index != len(elements) - 1:
            raise ConfigError(
                f"{where}: {label}.elements[{index}] ({element['type']}) uses 'fill' but "
                f"is not the last element; only the last element can take the space left"
            )


def _fill(template: str, where: str, **fields: Any) -> str:
    """``str.format`` that names the pack key when a placeholder is unknown."""
    names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    unknown = names - set(fields)
    if unknown:
        raise ConfigError(
            f"{where}: unknown placeholder(s) {', '.join('{' + n + '}' for n in sorted(unknown))}; "
            f"available here: {', '.join('{' + n + '}' for n in sorted(fields))}"
        )
    return template.format(**fields)


# --------------------------------------------------------------- book type
@register
class LogbookBookType(BookType):
    key = "logbook"
    label = "Form-based logbook"
    description = (
        "Pack-driven forms: a daily log page with its own prompt, weekly and monthly "
        "reviews, reference pages and record ledgers, undated."
    )
    default_options: dict[str, Any] = {
        "target_pages": 120,
        "pack": DEFAULT_PACK,
        "days": None,  # set this to pin the number of days instead of the page count
    }

    # ------------------------------------------------------------- planning
    def plan(
        self,
        niche: Niche,
        config: EngineConfig,
        rng: StageRandom,
        options: dict[str, Any] | None = None,
    ) -> InteriorPlan:
        opts = {**self.options_for(niche), **(options or {})}
        trim = niche.constraints.get("trim_size", config.brand.default_trim)
        paper = niche.constraints.get("paper", config.brand.default_paper)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        pack = self._pack(niche, opts)
        if pack.language != config.brand.language:
            # The pack's words and the locale's labels would disagree on the page.
            log.warning(
                "logbook pack %s is in %s but the imprint language is %s; the book's "
                "labels will be in %s", pack.name, pack.language,
                config.brand.language, config.brand.language,
            )

        days = self._solve_layout(target_pages, opts.get("days"), pack)
        _, weeks, months = pack.cycle_pages(days)
        headings = self._unit_headings(pack, config)
        daily, weekly, monthly = self._prompts(pack, opts, config, rng, days, weeks, months,
                                               headings)

        titles = self.named(niche, rng)
        fields = {"days": days, "weeks": weeks, "months": months}
        table = self._strings(config)
        pages: list[PageSpec] = self._front_matter(config, titles, pack, fields)
        units: list[ContentUnit] = []

        for spec in pack.reference:
            pages.append(PageSpec("text_page", kind="content", data={
                "eyebrow": spec.eyebrow or text(table, "interior.reference"),
                "heading": spec.heading,
                "paragraphs": list(spec.paragraphs),
                "notes": list(spec.notes),
            }))
            units.append(ContentUnit("reference", spec.heading, len(pages) - 1,
                                     probe=spec.heading))

        for spec in pack.profile:
            pages.append(self._form_page(spec, spec.eyebrow or text(table, "interior.record")))
            units.append(ContentUnit("record", spec.heading, len(pages) - 1, probe=spec.heading))

        week = month = 0
        for day in range(1, days + 1):
            eyebrow = _fill(pack.daily.eyebrow or lookup(table, "interior.day"),
                            f"{pack.name}: cycle.daily.eyebrow", n=day, **fields)
            pages.append(self._form_page(pack.daily, eyebrow, daily[day - 1], "daily", day))
            units.append(ContentUnit("daily_prompt", daily[day - 1], len(pages) - 1,
                                     probe=daily[day - 1]))
            if pack.weekly and day % pack.weekly.every == 0:
                week += 1
                eyebrow = _fill(pack.weekly.eyebrow or lookup(table, "interior.week"),
                                f"{pack.name}: cycle.weekly.eyebrow", n=week, **fields)
                pages.append(self._form_page(pack.weekly, eyebrow, weekly[week - 1],
                                             "weekly", week))
                units.append(ContentUnit("weekly_prompt", weekly[week - 1], len(pages) - 1,
                                         probe=weekly[week - 1]))
                if pack.monthly and week % pack.monthly.every_weeks == 0:
                    month += 1
                    eyebrow = _fill(
                        pack.monthly.eyebrow or lookup(table, "interior.month"),
                        f"{pack.name}: cycle.monthly.eyebrow", n=month, **fields)
                    pages.append(self._form_page(pack.monthly, eyebrow, monthly[month - 1],
                                                 "monthly", month))
                    units.append(ContentUnit("monthly_prompt", monthly[month - 1],
                                             len(pages) - 1, probe=monthly[month - 1]))

        for spec in pack.extras:
            # A ledger that runs to four pages is one record, not four: the
            # unit is the first page, the rest say "2 of 4" so they read on.
            first = len(pages)
            base = spec.eyebrow or text(table, "interior.record")
            for index in range(1, spec.count + 1):
                eyebrow = base if spec.count == 1 else (
                    f"{base} · {text(table, 'interior.page_of', n=index, count=spec.count)}")
                pages.append(self._form_page(spec, eyebrow))
            units.append(ContentUnit("record", spec.heading, first, probe=spec.heading))

        pages.extend(self._back_matter(config, pack))
        target = even_up(max(target_pages, int(KDP_SPEC["page_count"]["min"])))
        # The solver lands within a page or two of the target when a weekly
        # (or monthly) page would push the next day over it; that gap is
        # honest filler — a notes page — and gate 2 counts it as such.
        pages = self._pad_to(pages, target, text(table, "interior.notes"))
        pages = self._pad_to_even(pages)

        record_headings = [spec.heading for spec in [*pack.profile, *pack.extras]]
        return InteriorPlan(
            book_type=self.key,
            title=titles.title,
            subtitle=titles.subtitle,
            trim_size=trim,
            paper=paper,
            pages=pages,
            content_units=units,
            metadata={
                "pack": pack.name,
                "language": pack.language,
                "days": days,
                "weeks": weeks,
                "months": months,
                "every": pack.weekly.every if pack.weekly else 0,
                "every_weeks": pack.monthly.every_weeks if pack.monthly else 0,
                "reference_pages": len(pack.reference),
                "record_pages": len(pack.profile) + pack.extra_pages,
                "record_headings": record_headings,
                "target_pages": target_pages,
                "title_rationale": titles.rationale,
            },
        )

    def _pack(self, niche: Niche, opts: dict[str, Any]) -> LogbookPack:
        base = niche.source_path.parent if niche.source_path else None
        return load_logbook_pack(str(opts.get("pack") or DEFAULT_PACK), base)

    def _solve_layout(self, target_pages: int, pinned: Any, pack: LogbookPack) -> int:
        """How many days land on the target page count.

        total = front(4) + reference + profile + days + weekly + monthly
                + extras + back(1)

        Searched rather than solved, because a weekly page arrives with every
        seventh day and a monthly page with every fourth week: the total jumps
        by two or three at those days, and the largest day count that does not
        overshoot is the answer.
        """
        if pinned:
            return _positive_int(pinned, "options.days", "niche")
        fixed = pack.fixed_pages(len(FRONT_TEMPLATES), len(self.back_matter_templates))
        target = even_up(max(int(target_pages), int(KDP_SPEC["page_count"]["min"])))
        days = 0
        for candidate in range(1, target + 1):
            if fixed + sum(pack.cycle_pages(candidate)) > target:
                break
            days = candidate
        minimum = pack.weekly.every if pack.weekly else 7
        if days < minimum:
            raise ConfigError(
                f"a target of {target_pages} pages leaves room for only {days} day(s) after "
                f"{fixed} front, reference, record and closing page(s); raise target_pages "
                f"or remove extras from {pack.name}"
            )
        return days

    # --------------------------------------------------------------- prompts
    def _unit_headings(self, pack: LogbookPack, config: EngineConfig) -> list[str]:
        """Reference and record headings: units too, so they must not collide."""
        headings = [s.heading for s in pack.reference] + [
            s.heading for s in [*pack.profile, *pack.extras]]
        limit = config.quality.max_pairwise_similarity
        for i, first in enumerate(headings):
            for second in headings[i + 1:]:
                score = token_similarity(token_set(first), token_set(second))
                if normalize(first) == normalize(second) or score > limit:
                    raise ConfigError(
                        f"{pack.name}: the headings {first!r} and {second!r} read as the "
                        f"same page ({score:.0%} overlap, limit {limit:.0%}); give one of "
                        f"them a distinct heading"
                    )
        return headings

    def _prompts(
        self,
        pack: LogbookPack,
        opts: dict[str, Any],
        config: EngineConfig,
        rng: StageRandom,
        days: int,
        weeks: int,
        months: int,
        taken: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Daily, weekly and monthly prompts — distinct from each other and from
        every heading, because gate 2 compares all of a book's units at once."""
        limit = config.quality.max_pairwise_similarity
        chosen = list(taken)
        out = []
        for kind, pool, needed in (
            ("daily", pack.daily_prompts, days),
            ("weekly", pack.weekly_prompts, weeks),
            ("monthly", pack.monthly_prompts, months),
        ):
            if not needed:
                out.append([])
                continue
            distinct = list(dict.fromkeys(pool))
            distinct = self._without_used(distinct, opts, f"{kind} prompts") if distinct else []
            clear = [p for p in distinct if not _too_close(p, chosen, limit)]
            if len(clear) < needed:
                raise ConfigError(
                    f"{days} days need {needed} distinct {kind} prompts but {pack.name} has "
                    f"{len(clear)} usable ({len(distinct)} distinct, the rest too close to "
                    f"another prompt or heading). Add {needed - len(clear)} or more to "
                    f"{kind}_prompts, or lower target_pages."
                )
            picked = select_diverse(rng.stream(f"{kind}_prompts").shuffled(clear), needed,
                                    limit, f"{kind} prompts")
            chosen.extend(picked)
            out.append(picked)
        return out[0], out[1], out[2]

    # ------------------------------------------------------------ pages
    @staticmethod
    def _form_page(spec: FormSpec, eyebrow: str, prompt: str = "", cycle: str = "",
                   n: int = 0) -> PageSpec:
        data: dict[str, Any] = {
            "eyebrow": eyebrow,
            "heading": spec.heading,
            "elements": [dict(e) for e in spec.elements],
        }
        if cycle:
            data.update(cycle=cycle, n=n, prompt=prompt)
        return PageSpec("form_page", kind="content", data=data)

    def _front_matter(
        self, config: EngineConfig, titles: TitleProposal, pack: LogbookPack,
        fields: dict[str, int],
    ) -> list[PageSpec]:
        brand = config.brand
        table = self._strings(config)
        fallback = table["matter"]["logbook"]
        front = pack.front
        belongs = front.get("belongs_to") or {}
        how = front.get("how_to_use") or {}
        values = {
            "year": brand.copyright_year, "holder": brand.copyright_holder,
            "imprint": brand.imprint, "website": brand.website, "author": brand.author,
        }
        lines = []
        for line in front.get("copyright_lines") or []:
            if "{website}" in line and not brand.website:
                continue  # a line that only exists to carry the website
            lines.append(_fill(str(line), f"{pack.name}: front.copyright_lines", **values))
        if not lines:
            lines = self._copyright_lines(brand, table, "")
            lines = [line for line in lines if line]
        where = f"{pack.name}: front.how_to_use"
        return [
            PageSpec("title_page", kind="front_matter", data={
                "title": titles.title, "subtitle": titles.subtitle,
                "imprint": brand.imprint, "author": brand.author,
            }, show_page_number=False),
            PageSpec("copyright_page", kind="front_matter", data={"lines": lines},
                     show_page_number=False),
            PageSpec("belongs_to_page", kind="front_matter", data={
                "heading": str(belongs.get("heading") or fallback["belongs_to"]),
                "fields": [str(f) for f in (belongs.get("fields") or fallback["fields"])],
            }, show_page_number=False),
            PageSpec("how_to_use_page", kind="front_matter", data={
                "heading": str(how.get("heading") or fallback["how_to_use"]),
                "intro": _fill(str(how.get("intro") or fallback["intro"]), where, **fields),
                "bullets": [_fill(str(b), where, **fields) for b in how.get("bullets") or []],
            }, show_page_number=False),
        ]

    def _back_matter(self, config: EngineConfig, pack: LogbookPack) -> list[PageSpec]:
        brand = config.brand
        return [PageSpec("closing_page", kind="back_matter", data={
            "lines": list(pack.closing_lines),
            "imprint": brand.imprint,
            "also_by": list(brand.also_by),
            "note": brand.back_matter_note,
        }, show_page_number=False)]

    # --------------------------------------------------------------- titles
    def titles(self, niche: Niche, rng: StageRandom) -> TitleProposal:
        opts = self.options_for(niche)
        pack = self._pack(niche, opts)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        days = self._solve_layout(target_pages, opts.get("days"), pack)
        _, weeks, months = pack.cycle_pages(days)
        fields = {"days": days, "weeks": weeks, "months": months}
        title = _fill(pack.title, f"{pack.name}: title", **fields) or niche.niche.strip()
        subtitle = _fill(pack.subtitle, f"{pack.name}: subtitle", **fields)
        return TitleProposal(
            title=title,
            subtitle=subtitle,
            rationale=f"title and subtitle from {pack.name}; {days} days computed from "
                      f"the page plan",
        )

    # --------------------------------------------------------------- verify
    def verify(self, plan: InteriorPlan) -> list[tuple[str, bool, str]]:
        meta = plan.metadata
        cycle = [p for p in plan.pages if p.template == "form_page" and p.data.get("cycle")]
        daily = [p for p in cycle if p.data["cycle"] == "daily"]
        prompts = [u for u in plan.content_units if u.kind.endswith("_prompt")]
        normalized = [u.normalized for u in prompts]

        every, every_weeks = int(meta.get("every") or 0), int(meta.get("every_weeks") or 0)
        cadence_ok, cadence_reason = True, ""
        days_since = weeks_since = 0
        expected = {"daily": 1, "weekly": 1, "monthly": 1}
        for page in cycle:
            kind, n = page.data["cycle"], page.data["n"]
            if n != expected[kind]:
                cadence_ok, cadence_reason = False, f"{kind} page {n} out of sequence"
                break
            expected[kind] += 1
            if kind == "daily":
                days_since += 1
            elif kind == "weekly":
                if days_since != every:
                    cadence_ok = False
                    cadence_reason = f"weekly page {n} follows {days_since} day(s), not {every}"
                    break
                days_since, weeks_since = 0, weeks_since + 1
            elif kind == "monthly":
                if weeks_since != every_weeks or days_since:
                    cadence_ok = False
                    cadence_reason = f"monthly page {n} is not right after week {every_weeks}"
                    break
                weeks_since = 0
        weekly_pages = expected["weekly"] - 1
        monthly_pages = expected["monthly"] - 1
        if cadence_ok and every and weekly_pages != len(daily) // every:
            cadence_ok = False
            cadence_reason = f"{weekly_pages} weekly page(s) for {len(daily)} days"
        if cadence_ok and every_weeks and monthly_pages != weekly_pages // every_weeks:
            cadence_ok = False
            cadence_reason = f"{monthly_pages} monthly page(s) for {weekly_pages} weeks"

        target = even_up(max(int(meta.get("target_pages") or 0),
                             int(KDP_SPEC["page_count"]["min"])))
        return [
            (
                "every_day_has_a_prompt",
                len(daily) == int(meta.get("days") or 0)
                and all(p.data.get("prompt") for p in daily)
                and all(sum(1 for e in p.data["elements"] if e["type"] == "prompt") == 1
                        for p in cycle),
                f"{len(daily)} daily page(s) for {meta.get('days')} day(s), each with one "
                f"prompt box and its own prompt",
            ),
            (
                "prompts_unique",
                len(set(normalized)) == len(normalized),
                f"{len(set(normalized))} distinct prompts out of {len(normalized)}",
            ),
            (
                "weekly_cadence",
                cadence_ok,
                cadence_reason or f"a weekly page after every {every} days "
                f"({weekly_pages}), a monthly page after every {every_weeks} weeks "
                f"({monthly_pages})",
            ),
            (
                "lands_on_target",
                plan.page_count == target,
                f"{plan.page_count} pages against a target of {target}",
            ),
        ]


def _too_close(candidate: str, chosen: list[str], limit: float) -> bool:
    tokens = token_set(candidate)
    key = normalize(candidate)
    return any(
        key == normalize(other) or token_similarity(tokens, token_set(other)) > limit
        for other in chosen
    )


__all__ = ["LogbookBookType", "LogbookPack", "load_logbook_pack", "parse_pack"]
