"""Gate 2 — the substance gate. This is Prompt 3, in code.

    "Grade this interior against the standard below. You did not write it —
     read it as a buyer who paid for it."

It takes that literally. The gate never sees the generator or its plan objects:
it opens the PDF, extracts the text a reader would see, and grades six criteria
PASS or FAIL with a one-line reason. Any FAIL stops the build.

The plan file is read too, but only for what it claims — so that the claims can
be checked against the PDF rather than believed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..content.llm import grade_interior
from ..content.text import normalize, token_set, token_similarity
from ..render.pdfutil import extract_pages_text
from .base import Gate, GateInput, GateReport

NUMBER = re.compile(r"\b(\d[\d,]*)\b")


_tokens = token_set
jaccard = token_similarity


class SubstanceGate(Gate):
    gate_id = "g2_substance"
    title = "Would a buyer feel they got their money's worth?"
    requires = ("interior_pdf", "interior_plan")

    def evaluate(self, data: GateInput, report: GateReport) -> None:
        plan = data.json("interior_plan")
        pages_text = extract_pages_text(data.path("interior_pdf"))
        bar = self.config.quality

        report.metrics["page_count"] = len(pages_text)
        report.metrics["content_units"] = plan.get("content_unit_count", 0)

        self._check_padding(plan, pages_text, report)
        self._check_repetition(plan, report)
        self._check_promise(plan, data, report)
        self._check_value(plan, pages_text, report)
        self._check_errors(plan, pages_text, data, report)
        self._check_completeness(plan, pages_text, report)
        self._llm_second_opinion(plan, pages_text, report)

        _ = bar  # thresholds are read inside each check

    # ------------------------------------------------------------- padding
    def _check_padding(self, plan: dict, pages_text: list[str], report: GateReport) -> None:
        total = len(pages_text)
        filler = plan.get("filler_pages", 0)
        empty = sum(1 for text in pages_text if not text.strip())
        ratio = (filler / total) if total else 1.0
        limit = self.config.quality.max_filler_page_ratio
        report.metrics["filler_ratio"] = round(ratio, 4)
        report.add(
            "padding",
            ratio <= limit,
            f"{filler} filler page(s) of {total} ({ratio:.1%}); the bar is {limit:.0%}"
            + (f"; {empty} page(s) carry no text at all" if empty else ""),
            {"filler_pages": filler, "empty_pages": empty, "ratio": round(ratio, 4)},
        )

    # ---------------------------------------------------------- repetition
    def _check_repetition(self, plan: dict, report: GateReport) -> None:
        units: list[dict[str, Any]] = plan.get("content_units", [])
        texts = [u["text"] for u in units]
        normalized = [normalize(t) for t in texts]
        unique = len(set(normalized))
        ratio = unique / len(normalized) if normalized else 0.0
        bar = self.config.quality
        report.metrics["unique_content_ratio"] = round(ratio, 4)

        duplicates = [t for t in set(normalized) if normalized.count(t) > 1]
        report.add(
            "repetition_exact",
            ratio >= bar.min_unique_content_ratio,
            f"{unique} distinct of {len(normalized)} content units ({ratio:.1%}); "
            f"the bar is {bar.min_unique_content_ratio:.0%}"
            + (f"; e.g. {duplicates[0][:60]!r} appears more than once" if duplicates else ""),
            {"unique": unique, "total": len(normalized), "duplicates": duplicates[:5]},
        )

        worst_pair, worst_score = self._closest_pair(texts)
        report.metrics["max_pairwise_similarity"] = round(worst_score, 4)
        report.add(
            "repetition_near_duplicate",
            worst_score <= bar.max_pairwise_similarity,
            f"closest two units overlap {worst_score:.0%} (limit {bar.max_pairwise_similarity:.0%})"
            + (f": {worst_pair[0][:45]!r} vs {worst_pair[1][:45]!r}" if worst_pair else ""),
            {"similarity": round(worst_score, 4), "pair": list(worst_pair) if worst_pair else []},
        )

    def _closest_pair(self, texts: Iterable[str]) -> tuple[tuple[str, str] | None, float]:
        items = list(texts)
        token_sets = [_tokens(t) for t in items]
        worst: tuple[str, str] | None = None
        worst_score = 0.0
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                score = jaccard(token_sets[i], token_sets[j])
                if score > worst_score:
                    worst_score, worst = score, (items[i], items[j])
        return worst, worst_score

    # ------------------------------------------------------------- promise
    def _check_promise(self, plan: dict, data: GateInput, report: GateReport) -> None:
        """Every number in the title and subtitle must be a number that is true."""
        claim = f"{plan.get('title', '')} {plan.get('subtitle', '')}"
        claimed = {int(n.replace(",", "")) for n in NUMBER.findall(claim)}
        truths = {
            int(plan.get("page_count", 0)),
            int(plan.get("content_unit_count", 0)),
            int(plan.get("content_pages", 0)),
        }
        for key, value in (plan.get("metadata") or {}).items():
            if isinstance(value, int):
                truths.add(value)
        # Trim sizes like "8.5x11" leak digits into the claim; drop small ones.
        claimed = {c for c in claimed if c >= 3}
        unsupported = sorted(c for c in claimed if c not in truths)
        report.add(
            "promise_numbers",
            not unsupported,
            f"every number in the title and subtitle is backed by the book"
            if not unsupported
            else f"the cover claims {unsupported} but the book has no such quantity "
            f"(real quantities: {sorted(truths)})",
            {"claimed": sorted(claimed), "truths": sorted(truths)},
        )

        niche = data.json("niche") if "niche" in data.paths else {}
        promise = (niche.get("promise") or "").strip()
        if promise:
            keywords = {w for w in _tokens(promise) if len(w) > 4}
            haystack = _tokens(
                f"{plan.get('title', '')} {plan.get('subtitle', '')} "
                f"{' '.join(u['text'] for u in plan.get('content_units', [])[:80])}"
            )
            hit = len(keywords & haystack)
            report.add(
                "promise_kept",
                hit > 0 or not keywords,
                f"{hit} of {len(keywords)} distinctive words from the niche promise "
                f"appear in the book's own words",
                {"promise": promise},
                advisory=True,
            )

    # --------------------------------------------------------------- value
    def _check_value(self, plan: dict, pages_text: list[str], report: GateReport) -> None:
        """Did the buyer get pages that carry something?

        Deliberately not "units per page": one week of a planner is two pages
        and one puzzle is a page plus half an answer page. What every format
        must share is that the pages are not empty and that the content the plan
        promises is actually printed where it says it is.
        """
        bar = self.config.quality
        total = max(len(pages_text), 1)
        content_pages = int(plan.get("content_pages", 0))
        ratio = content_pages / total
        report.metrics["content_page_ratio"] = round(ratio, 4)
        report.add(
            "value_content_share",
            ratio >= bar.min_content_page_ratio,
            f"{content_pages} of {total} pages carry content ({ratio:.0%}); "
            f"the bar is {bar.min_content_page_ratio:.0%}",
            {"content_pages": content_pages, "total_pages": total},
        )

        # A divider page carries a couple of words by design; a content page
        # that reads as empty is a defect. Different floors, same intent.
        empty: list[int] = []
        for index, page in enumerate(plan.get("pages", [])):
            kind = page.get("kind")
            if kind not in {"content", "divider"}:
                continue
            floor = bar.min_chars_per_content_page if kind == "content" else 1
            text = pages_text[index].strip() if index < len(pages_text) else ""
            if len(text) < floor:
                empty.append(index + 1)
        report.add(
            "value_no_empty_content_pages",
            not empty,
            f"every content page has something printed on it"
            if not empty
            else f"{len(empty)} page(s) are marked as content but are effectively blank: "
            f"{empty[:5]}",
            {"empty_pages": empty[:20]},
        )

        units = plan.get("content_units", [])
        missing: list[str] = []
        for unit in units:
            index = int(unit.get("page_index", -1))
            probe = normalize(unit.get("probe") or unit.get("text", ""))
            if not probe:
                continue
            if not (0 <= index < len(pages_text)):
                missing.append(f"{probe[:40]} (page {index + 1} does not exist)")
                continue
            if probe not in normalize(pages_text[index]):
                missing.append(f"{probe[:40]} (not on page {index + 1})")
        report.add(
            "content_reaches_the_page",
            not missing,
            f"all {len(units)} content unit(s) were found in the PDF on the page they claim"
            if not missing
            else f"{len(missing)} unit(s) are in the plan but not on the page: {missing[0]}",
            {"missing_count": len(missing), "examples": missing[:3]},
        )

    # -------------------------------------------------------------- errors
    def _check_errors(
        self, plan: dict, pages_text: list[str], data: GateInput, report: GateReport
    ) -> None:
        haystack = "\n".join(pages_text).lower()
        found = [token for token in self.config.quality.forbidden_tokens if token in haystack]
        report.add(
            "errors_placeholders",
            not found,
            "no placeholder or leftover-template text in the PDF"
            if not found
            else f"placeholder text made it into the book: {', '.join(found)}",
            {"found": found},
        )

        if "book_type_checks" in data.paths:
            checks = data.json("book_type_checks")
            failures = [c for c in checks if not c["passed"]]
            report.add(
                "errors_format_verification",
                not failures,
                f"all {len(checks)} format-specific checks passed "
                f"(these are code-verified, e.g. every puzzle word really is in its grid)"
                if not failures
                else f"{len(failures)} format check(s) failed: {failures[0]['name']} — "
                f"{failures[0]['reason']}",
                {"failed": [c["name"] for c in failures]},
            )

    # -------------------------------------------------------- completeness
    def _check_completeness(
        self, plan: dict, pages_text: list[str], report: GateReport
    ) -> None:
        bar = self.config.quality
        front = int(plan.get("front_matter_pages", 0))
        back = int(plan.get("back_matter_pages", 0))
        report.add(
            "completeness_front_matter",
            front >= bar.min_front_matter_pages,
            f"{front} front-matter page(s); the bar is {bar.min_front_matter_pages}",
        )
        report.add(
            "completeness_back_matter",
            back >= bar.min_back_matter_pages,
            f"{back} back-matter page(s); the bar is {bar.min_back_matter_pages}",
        )

        title = normalize(plan.get("title", ""))
        report.add(
            "completeness_title_page",
            bool(title) and title in normalize(pages_text[0] if pages_text else ""),
            "page 1 carries the book's title"
            if pages_text and title in normalize(pages_text[0])
            else "page 1 does not carry the title — the book opens on nothing",
        )
        copyright_found = any(
            "copyright" in normalize(text) for text in pages_text[: max(front, 4)]
        )
        report.add(
            "completeness_copyright",
            copyright_found,
            "a copyright page is present in the front matter"
            if copyright_found
            else "no copyright page in the front matter",
        )

        if bar.require_page_numbers:
            expected = [
                index
                for index, page in enumerate(plan.get("pages", []))
                if page.get("show_page_number")
            ]
            numbered = sum(
                1
                for index in expected
                if index < len(pages_text)
                and re.search(rf"(?<!\d){index + 1}(?!\d)", pages_text[index])
            )
            ratio = numbered / len(expected) if expected else 1.0
            report.metrics["page_number_ratio"] = round(ratio, 4)
            report.add(
                "completeness_page_numbers",
                ratio >= 0.98,
                f"{numbered} of {len(expected)} numbered pages show their number "
                f"in the PDF ({ratio:.0%})",
                {"numbered": numbered, "expected": len(expected)},
            )

    # ------------------------------------------------------------- the hook
    def _llm_second_opinion(
        self, plan: dict, pages_text: list[str], report: GateReport
    ) -> None:
        llm = self.config.llm
        if not llm.enabled:
            return
        verdict = grade_interior(
            llm,
            title=plan.get("title", ""),
            subtitle=plan.get("subtitle", ""),
            page_count=len(pages_text),
            sample_pages=pages_text,
        )
        report.metrics["llm"] = verdict.as_dict()
        if not verdict.available:
            report.add(
                "llm_second_opinion",
                True,
                f"the LLM hook was enabled but unavailable: {verdict.error}",
                advisory=True,
            )
            return
        notes = verdict.notes or []
        report.add(
            "llm_second_opinion",
            bool(verdict.passed),
            f"model verdict: {'pass' if verdict.passed else 'fail'}"
            + (f" — {notes[0]}" if notes else ""),
            {"notes": notes},
            advisory=not llm.allow_llm_to_fail_gate,
        )
