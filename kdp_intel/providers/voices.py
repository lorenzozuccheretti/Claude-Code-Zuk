"""The reader's own words, from free sources that still answer from the cloud.

Reddit refuses anonymous requests from cloud networks and no longer hands out API
access on request, Quora and most Italian forums block crawlers, and Amazon.it answers
review pages with a captcha. Two sources remain, and both are what readers wrote:

* **Google autocomplete questions.** Google suggests only what many people typed:
  "amministratore di sostegno può essere anche badante", "quanto costa un
  amministratore di sostegno esterno". Asking with question stems after and before
  the keyword returns the worries, in the reader's vocabulary.
* **Forum and Q&A pages found by web search.** A search for "<keyword> forum" or
  "<keyword> esperienze" returns threads on open forums; they are fetched and archived
  as community evidence (tier 3), which the persona reads and the fact-checker never
  accepts for a figure or a rule.
"""

from __future__ import annotations

from typing import Any

from ..authority import classify, domain
from ..models import SourceSpec

# After the keyword: "<kw> come ...", "<kw> si può ...". Before it: "quanto costa <kw> ...".
STEMS_AFTER = ("come", "quanto", "chi", "cosa", "perché", "si può", "può", "quando", "conviene", "obbligatorio")
STEMS_BEFORE = ("come", "quanto costa", "chi può", "cosa succede se", "cosa fa", "perché")
FORUM_HINTS = ("forum", "esperienze", "consiglio")
# Pages that are not readers talking: shops, marketplaces, video, social logins.
_NOT_FORUMS = {"amazon.it", "amazon.com", "ibs.it", "feltrinelli.it", "mondadoristore.it", "youtube.com",
               "facebook.com", "instagram.com", "tiktok.com", "linkedin.com"}


def reader_questions(suggest: Any, keywords: list[str], limit: int = 60) -> list[str]:
    """Distinct autocomplete phrases that contain the keyword, in the order Google gives them."""
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords[:2]:
        low = kw.lower().strip()
        if not low:
            continue
        prefixes = [f"{low} {s}" for s in STEMS_AFTER] + [f"{s} {low}" for s in STEMS_BEFORE]
        for prefix in prefixes:
            try:
                found = suggest.google(prefix)
            except Exception:  # noqa: BLE001 - one refused prefix is not a missing source
                continue
            for phrase in found:
                key = " ".join(phrase.lower().split())
                if low in key and key != low and key not in seen:
                    seen.add(key)
                    out.append(key)
                    if len(out) >= limit:
                        return out
    return out


def forum_queries(keyword: str) -> list[str]:
    return [f"{keyword} {hint}" for hint in FORUM_HINTS]


def is_forum_candidate(url: str) -> bool:
    """Only pages that can hold readers' words: never official or press pages, never shops."""
    host = domain(url)
    if any(host == d or host.endswith("." + d) for d in _NOT_FORUMS):
        return False
    _kind, tier = classify(url)
    return tier == 3


def forum_specs(web_search: Any, queries: list[str], per_query: int = 5) -> list[SourceSpec]:
    """Search each query; keep forum-like hits as community sources.

    Hand-off searches raise ``PendingLLM``; every query of the round is asked at once."""
    from ..llm_free import PendingLLM  # noqa: PLC0415

    specs: dict[str, SourceSpec] = {}
    waiting: list[PendingLLM] = []
    for q in queries:
        try:
            results = web_search.search(q, num=per_query)
        except PendingLLM as pending:
            waiting.append(pending)
            continue
        for r in results:
            if is_forum_candidate(r.url):
                specs.setdefault(r.url, SourceSpec(url=r.url, title=r.title, kind="community"))
    if waiting:
        raise PendingLLM([t for w in waiting for t in w.task_ids], waiting[0].folder)
    return list(specs.values())
