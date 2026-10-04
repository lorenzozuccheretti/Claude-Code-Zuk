"""Review Miner Agent: what exactly do buyers of the competing books miss?

The model proposes themes and tags each critical review; code does the
counting, and every quote the model returns must appear verbatim in the
review it came from, or it is thrown away. A theme's weight is therefore a
fact about the reviews, not the model's impression of them.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ..llm import LLM
from ..models import GapReport, Review, ReviewThemesDraft, Theme, ThemeCount

SYSTEM = """Sei un analista editoriale. Leggi recensioni negative o tiepide (1-3 stelle) \
di libri italiani concorrenti e individui cosa manca ai libri esistenti.

Regole:
- Un tema descrive un difetto del libro (es. "contenuto non aggiornato alla riforma 2025", \
"troppo teorico, nessun esempio pratico", "traduzione automatica"), non un'emozione generica.
- Riusa gli id dei temi già esistenti quando il significato coincide; crea nuovi id brevi \
in snake_case solo per difetti nuovi.
- Per ogni recensione restituisci i temi che contiene (anche nessuno) e una citazione \
copiata parola per parola dal testo della recensione, senza modificarla. Se nessun passaggio \
è adatto, la citazione è una stringa vuota.
- missing_content: argomenti concreti che i lettori cercavano e non hanno trovato.
- opportunity_statements: come un nuovo libro può battere i concorrenti su questi punti."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_reviews_csv(path: str | Path) -> list[Review]:
    """CSV with at least ``rating`` and ``text`` columns (``title``, ``date``,
    ``asin`` optional), e.g. exported from any review scraper."""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return [
            Review(asin=row.get("asin", ""), rating=int(float(row["rating"])),
                   title=row.get("title", ""), text=row["text"], date=row.get("date", ""))
            for row in csv.DictReader(fh)
            if row.get("text") and row.get("rating")
        ]


class ReviewMiner:
    def __init__(self, llm: LLM, batch_size: int = 60, max_stars: int = 3) -> None:
        self.llm = llm
        self.batch_size = batch_size
        self.max_stars = max_stars

    def collect(self, source, asins: list[str], csv_path: str = "", per_asin: int = 150) -> list[Review]:
        reviews: list[Review] = load_reviews_csv(csv_path) if csv_path else []
        if source is not None:
            for asin in asins:
                reviews.extend(source.reviews(asin, per_asin))
        seen, unique = set(), []
        for r in reviews:  # the same review can arrive from CSV and scraper
            key = (r.asin, _norm(r.text)[:200])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def mine(self, reviews: list[Review]) -> GapReport:
        critical = [r for r in reviews if r.rating <= self.max_stars]
        themes: dict[str, Theme] = {}
        members: dict[str, set[int]] = {}
        quotes: dict[str, list[str]] = {}
        missing: list[str] = []
        opportunities: list[str] = []
        rejected = 0

        for start in range(0, len(critical), self.batch_size):
            batch = critical[start:start + self.batch_size]
            listing = "\n\n".join(
                f'<review index="{i}" stars="{r.rating}">\n{r.title}\n{r.text}\n</review>'
                for i, r in enumerate(batch)
            )
            known = "\n".join(f"- {t.id}: {t.label} — {t.description}" for t in themes.values()) or "(nessuno)"
            draft = self.llm.structured(
                system=SYSTEM,
                prompt=f"Temi già individuati:\n{known}\n\nRecensioni:\n{listing}",
                schema=ReviewThemesDraft,
                effort="medium",
            )
            for t in draft.themes:
                themes.setdefault(t.id, t)
            for label in draft.labels:
                if not 0 <= label.review_index < len(batch):
                    continue
                review = batch[label.review_index]
                for tid in label.theme_ids:
                    if tid not in themes:
                        continue
                    members.setdefault(tid, set()).add(start + label.review_index)
                    if label.quote and _norm(label.quote) in _norm(f"{review.title} {review.text}"):
                        if len(quotes.setdefault(tid, [])) < 5 and label.quote not in quotes[tid]:
                            quotes[tid].append(label.quote)
                    elif label.quote:
                        rejected += 1
            missing += [m for m in draft.missing_content if m not in missing]
            opportunities += [o for o in draft.opportunity_statements if o not in opportunities]

        total = len(critical) or 1
        counted = sorted(
            (ThemeCount(theme=themes[tid], count=len(idx), share=round(len(idx) / total, 3),
                        quotes=quotes.get(tid, []))
             for tid, idx in members.items()),
            key=lambda t: (-t.count, t.theme.id),
        )
        return GapReport(
            reviews_considered=len(critical), themes=counted, missing_content=missing,
            opportunity_statements=opportunities, rejected_quotes=rejected,
        )
