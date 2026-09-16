"""Station 4 — the listing pack.

Title, subtitle, description, seven keywords, three categories, price. All of
it derived from the niche evidence and the book that exists, written to a file
before anyone opens a browser — because a listing improvised at the upload
screen is the one that wastes keyword slots and misprices the book.

Every KDP limit is enforced here, in code. A title that cannot fit is an error,
not a truncation: silently cutting a title is how books ship called
"The Daily Gratitude Journal for New Mot".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..booktypes.base import InteriorPlan
from ..content.copy import description as build_description
from ..content.packs import load_pack, tokens
from ..errors import SpecViolation
from ..niche import Niche
from ..run.context import BuildContext
from ..spec.kdp import (
    listing_limits,
    min_list_price_usd,
    printing_cost_usd,
    royalty_usd,
)
from .base import Station


class ListingStation(Station):
    number = 4
    name = "listing pack"

    def run(
        self,
        ctx: BuildContext,
        niche: Niche | None = None,
        plan: InteriorPlan | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if niche is None or plan is None:
            raise ValueError("station 4 needs the niche and the interior plan")

        limits = listing_limits()
        page_count = int(ctx.manifest.facts.get("page_count", plan.page_count))

        title, subtitle = self._validated_titles(plan, limits)
        description = self._validated_description(plan, niche, limits)
        keywords = self._keywords(plan, niche, title, subtitle, limits)
        categories = self._categories(plan, niche, limits)
        price = self._price(plan, niche, page_count)

        listing = {
            "title": title,
            "subtitle": subtitle,
            "author": self.config.brand.author,
            "imprint": self.config.brand.imprint,
            "language": self.config.brand.language,
            "description_html": description,
            "keywords": keywords,
            "categories": categories,
            "price": price,
            "print": {
                "trim_size": plan.trim_size,
                "paper": plan.paper,
                "page_count": page_count,
                "interior_type": "black_and_white",
                "bleed": False,
                "cover_finish": "matte",
            },
            "evidence": {
                "niche": niche.niche,
                "audience": niche.audience,
                "keyword_seeds": list(niche.keywords_seed),
                "competitor": niche.competitor.as_dict(),
                "written_from": "niche evidence + the interior that exists, not improvised",
            },
        }

        ctx.write_json(4, "listing.json", listing, role="listing")
        ctx.write_text(4, "listing.md", self._markdown(listing), role="listing_readable")
        ctx.write_text(
            4, "keywords.txt", "\n".join(keywords) + "\n", role="listing_keywords"
        )
        ctx.fact("price_usd", price["list_price_usd"])
        ctx.fact("royalty_usd", price["royalty_usd"])
        return {"title": title, "keywords": len(keywords), "price": price["list_price_usd"]}

    # -------------------------------------------------------------- titles
    def _validated_titles(self, plan: InteriorPlan, limits: dict[str, int]) -> tuple[str, str]:
        title, subtitle = plan.title.strip(), plan.subtitle.strip()
        if len(title) > limits["title_chars"]:
            raise SpecViolation(
                f"title is {len(title)} characters; KDP allows {limits['title_chars']}"
            )
        combined = len(title) + len(subtitle)
        if combined > limits["title_plus_subtitle_chars"]:
            raise SpecViolation(
                f"title + subtitle is {combined} characters; KDP allows "
                f"{limits['title_plus_subtitle_chars']}. Shorten the subtitle pattern "
                f"for this book type rather than truncating at upload time."
            )
        return title, subtitle

    def _validated_description(
        self, plan: InteriorPlan, niche: Niche, limits: dict[str, int]
    ) -> str:
        description = build_description(plan, niche, self.config.brand)
        if len(description) > limits["description_chars"]:
            raise SpecViolation(
                f"description is {len(description)} characters; KDP allows "
                f"{limits['description_chars']}"
            )
        return description

    # ------------------------------------------------------------ keywords
    def _keywords(
        self,
        plan: InteriorPlan,
        niche: Niche,
        title: str,
        subtitle: str,
        limits: dict[str, int],
    ) -> list[str]:
        """Seven slots. A slot that only repeats title words is a wasted slot.

        Amazon already indexes the title and subtitle, so a keyword phrase earns
        its slot only if it brings at least one word they do not contain.
        """
        slots = int(limits["keyword_slots"])
        max_chars = int(limits["keyword_chars"])
        title_words = tokens(title, subtitle)

        pack = load_pack("categories")
        modifiers = list(pack["keyword_modifiers"].get(plan.book_type, []))
        modifiers += list(pack["keyword_modifiers"].get("generic", []))
        audience_short = self._audience_short(niche)

        candidates: list[str] = []
        candidates.extend(niche.keywords_seed)  # the evidence comes first
        candidates.extend(
            m.replace("{audience_short}", audience_short) for m in modifiers
        )

        chosen: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            phrase = " ".join(candidate.lower().split())
            if not phrase or phrase in seen or len(phrase) > max_chars:
                continue
            if not (tokens(phrase) - title_words):
                continue  # adds nothing the title does not already say
            seen.add(phrase)
            chosen.append(phrase)
            if len(chosen) == slots:
                break

        if len(chosen) < slots:
            raise SpecViolation(
                f"only {len(chosen)} of {slots} keyword slots could be filled with "
                f"phrases that add something the title does not already say. Add "
                f"keyword seeds to the niche file, or modifiers to categories.yaml."
            )
        return chosen

    # Words a phrase cannot end on: cutting here leaves "gift for women with".
    DANGLING = frozenset({
        "with", "for", "and", "or", "who", "that", "in", "on", "at", "under",
        "from", "to", "of", "facing", "living", "aged", "over", "about",
    })

    def _audience_short(self, niche: Niche) -> str:
        """A few words of the audience, cut where a phrase can actually end.

        Keyword slots are built as "gift for {this}", so a truncation that lands
        mid-thought spends a slot on nonsense.
        """
        words = [
            w for w in niche.audience_phrase.split()
            if w.lower() not in {"a", "an", "the"}
        ]
        if not words:
            return "adults"
        chosen = words[:4] if len(words) <= 4 else words[:3]
        while len(chosen) > 1 and chosen[-1].lower().strip(",") in self.DANGLING:
            chosen.pop()
        return " ".join(chosen).lower().strip(",")

    # ---------------------------------------------------------- categories
    def _categories(
        self, plan: InteriorPlan, niche: Niche, limits: dict[str, int]
    ) -> list[str]:
        pack = load_pack("categories")
        config = pack["book_types"].get(plan.book_type)
        if not config:
            raise SpecViolation(
                f"no categories defined for book type {plan.book_type!r}; add them to "
                f"categories.yaml"
            )
        words = tokens(niche.niche, niche.audience, niche.promise, *niche.keywords_seed)
        promoted = [
            entry["category"]
            for entry in config.get("tagged", [])
            if words & {t.lower() for t in entry.get("tags", [])}
        ]
        ordered = promoted + [c for c in config["defaults"] if c not in promoted]
        return ordered[: int(limits["categories"])]

    # --------------------------------------------------------------- price
    def _price(self, plan: InteriorPlan, niche: Niche, page_count: int) -> dict[str, Any]:
        """Derived from three constraints, not picked.

        1. Below ``printing cost / royalty rate`` every copy loses money.
        2. Below ``(min royalty + printing cost) / rate`` the book earns less
           per copy than the policy is willing to accept.
        3. A markup over the floor, so a cheap book is not priced at the floor.

        The price is the largest of those, rounded up to a charm ending. An
        incumbent's price can pull it *down*, but never below 1 or 2 — undercutting
        a competitor into a loss is not a strategy.
        """
        policy = self.config.price
        paper = plan.paper
        rate = Decimal("0.60")
        cost = printing_cost_usd(page_count, paper)
        floor = min_list_price_usd(page_count, paper)
        royalty_floor = (Decimal(str(policy.min_royalty_usd)) + cost) / rate
        hard_floor = max(floor, royalty_floor)

        override = niche.constraints.get("price")
        if override is not None:
            candidate = Decimal(str(override))
            basis = "pinned in the niche file"
        else:
            candidate = max(floor * Decimal(str(policy.markup_over_cost)), royalty_floor)
            basis = "max(markup over royalty floor, price that clears the royalty policy)"
            competitor = niche.competitor.price
            if competitor and policy.undercut_competitor_by:
                target = Decimal(str(competitor)) * (
                    Decimal("1") - Decimal(str(policy.undercut_competitor_by))
                )
                if target >= hard_floor:
                    candidate = min(candidate, target)
                    basis += f", capped at {policy.undercut_competitor_by:.0%} under the incumbent"
            candidate = self._round_to_charm(candidate, policy.round_to)

        if policy.max_price_usd is not None:
            candidate = min(candidate, Decimal(str(policy.max_price_usd)))

        if candidate < floor:
            raise SpecViolation(
                f"a list price of ${candidate} is below the ${floor} floor for a "
                f"{page_count}-page book on {paper} — every copy would lose money"
            )
        royalty = royalty_usd(candidate, page_count, paper)
        if royalty < Decimal(str(policy.min_royalty_usd)):
            raise SpecViolation(
                f"at ${candidate} the royalty is ${royalty}, below the "
                f"${policy.min_royalty_usd} floor in the price policy. Raise "
                f"price.max_price_usd, cut the page count, or lower price.min_royalty_usd."
            )
        return {
            "list_price_usd": float(candidate),
            "printing_cost_usd": float(cost),
            "min_list_price_usd": float(floor),
            "royalty_floor_price_usd": float(royalty_floor.quantize(Decimal("0.01"))),
            "royalty_usd": float(royalty),
            "royalty_rate": float(rate),
            "expanded_distribution_royalty_usd": float(
                royalty_usd(candidate, page_count, paper, expanded=True)
            ),
            "basis": basis,
        }

    def _round_to_charm(self, value: Decimal, ending: float) -> Decimal:
        """Round up to the next x.<ending> (e.g. 8.12 -> 8.99, 9.10 -> 9.99)."""
        ending_dec = Decimal(str(ending))
        whole = value.to_integral_value(rounding="ROUND_FLOOR")
        candidate = whole + ending_dec
        if candidate < value:
            candidate += Decimal("1")
        return candidate

    # ------------------------------------------------------------ readable
    def _markdown(self, listing: dict[str, Any]) -> str:
        price = listing["price"]
        lines = [
            f"# Listing — {listing['title']}",
            "",
            f"**Subtitle**: {listing['subtitle']}",
            f"**Author**: {listing['author']}  ·  **Imprint**: {listing['imprint']}",
            f"**Language**: {listing['language']}",
            "",
            "## Price",
            "",
            f"- List price: **${price['list_price_usd']:.2f}**",
            f"- Printing cost: ${price['printing_cost_usd']:.2f}",
            f"- Floor (royalty = 0): ${price['min_list_price_usd']:.2f}",
            f"- Floor for the policy's minimum royalty: "
            f"${price.get('royalty_floor_price_usd', 0):.2f}",
            f"- Royalty per copy: **${price['royalty_usd']:.2f}** "
            f"(expanded distribution: ${price['expanded_distribution_royalty_usd']:.2f})",
            "",
            "## Keywords (7 slots)",
            "",
        ]
        lines += [f"{i}. {kw}" for i, kw in enumerate(listing["keywords"], 1)]
        lines += ["", "## Categories (3)", ""]
        lines += [f"- {c}" for c in listing["categories"]]
        lines += [
            "",
            "## Print settings",
            "",
            f"- Trim: {listing['print']['trim_size']} in",
            f"- Paper: {listing['print']['paper']}",
            f"- Pages: {listing['print']['page_count']}",
            f"- Bleed: {'yes' if listing['print']['bleed'] else 'no'}",
            f"- Cover finish: {listing['print']['cover_finish']}",
            "",
            "## Description (as submitted)",
            "",
            "```html",
            listing["description_html"],
            "```",
            "",
            "## Evidence this was written from",
            "",
            f"- Niche: {listing['evidence']['niche']}",
            f"- Audience: {listing['evidence']['audience']}",
            f"- Keyword seeds: {', '.join(listing['evidence']['keyword_seeds']) or '—'}",
            "",
        ]
        return "\n".join(lines)
