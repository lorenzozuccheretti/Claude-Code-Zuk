"""Publisher Agent: the Amazon listing, inside KDP's rules.

The model drafts title, subtitle, description, the seven backend keyword
boxes and two categories from the validated keyword and the finished
outline. Code then enforces what KDP enforces and what a listing needs to
be found: the primary keyword in the title or subtitle, KDP's length limits,
no words Amazon rejects in metadata ("bestseller", "gratis", other brands),
and backend keywords that repeat nothing already in the title. A draft that
breaks a rule is sent back once with the list of problems; what can be
fixed mechanically (too many keyword boxes, a box over 50 characters) is.
"""

from __future__ import annotations

import re

from ..llm import LLM
from ..models import ListingDraft, Outline, PersonaDraft

TITLE_MAX = 200  # title + subtitle, characters
BOX_MAX = 50  # one backend keyword box, characters
BOXES = 7
DESCRIPTION_MIN, DESCRIPTION_MAX = 600, 4000
BANNED = ["bestseller", "best seller", "gratis", "gratuito", "kindle", "amazon", "kdp", "n. 1", "numero 1",
          "il migliore", "offerta", "sconto", "nuovo di zecca"]

SYSTEM = """Sei il responsabile marketing di un editore italiano indipendente su Amazon KDP. \
Scrivi la scheda prodotto di un manuale pratico premium (19,90-29,90 €).
- title: breve e memorabile; title o subtitle contengono la keyword principale esatta.
- subtitle: il beneficio misurabile e il pubblico, in linguaggio naturale.
- description: 3-5 paragrafi separati da una riga vuota; apri con il problema del lettore, poi \
cosa trova nel libro (dai capitoli reali), per chi è, perché è aggiornato. Niente promesse di \
risultati garantiti, niente recensioni inventate, niente prezzi.
- backend_keywords: 7 frasi diverse che i lettori digitano, scelte tra le keyword validate e \
le loro varianti; non ripetere parole già nel titolo; massimo 50 caratteri ciascuna.
- categories: 2 percorsi di categoria Amazon.it (es. "Libri > Diritto > Diritto civile"), \
il più specifico possibile.
Vietato: bestseller, gratis, sconto, n. 1, nomi di marchi o di altri autori."""


def _low(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _banned(text: str) -> list[str]:
    return [b for b in BANNED if re.search(rf"(?<!\w){re.escape(b)}(?!\w)", text, re.I)]


def listing_problems(d: ListingDraft, primary: str) -> list[str]:
    out = []
    if primary and _low(primary) not in _low(f"{d.title} {d.subtitle}"):
        out.append(f"la keyword principale «{primary}» manca in titolo e sottotitolo")
    if len(d.title) + len(d.subtitle) > TITLE_MAX:
        out.append(f"titolo + sottotitolo = {len(d.title) + len(d.subtitle)} caratteri (max {TITLE_MAX})")
    if not DESCRIPTION_MIN <= len(d.description) <= DESCRIPTION_MAX:
        out.append(f"descrizione di {len(d.description)} caratteri (tra {DESCRIPTION_MIN} e {DESCRIPTION_MAX})")
    for field, text in (("titolo", d.title), ("sottotitolo", d.subtitle), ("descrizione", d.description),
                        ("keyword", " | ".join(d.backend_keywords))):
        if hits := _banned(text):
            out.append(f"{field}: termini non ammessi da Amazon {hits}")
    if len(d.backend_keywords) < BOXES:
        out.append(f"solo {len(d.backend_keywords)} keyword nascoste su {BOXES}")
    if len(d.categories) < 2:
        out.append("servono 2 categorie")
    return out


def tidy_keywords(d: ListingDraft) -> ListingDraft:
    """Mechanical fixes: drop boxes over the limit, with banned words or repeating the title;
    keep seven."""
    title_words = set(re.findall(r"\w+", _low(f"{d.title} {d.subtitle}")))
    boxes = []
    for kw in d.backend_keywords:
        kw = _low(kw)
        if not kw or len(kw) > BOX_MAX or kw in boxes or _banned(kw):
            continue
        if set(re.findall(r"\w+", kw)) <= title_words:
            continue  # every word already indexed through the title
        boxes.append(kw)
    return d.model_copy(update={"backend_keywords": boxes[:BOXES]})


class Publisher:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    def listing(self, primary: str, keywords: list[str], outline: Outline,
                persona: PersonaDraft | None, competitors: list[str] | None = None) -> tuple[ListingDraft, list[str]]:
        chapters = "\n".join(f"{c.number}. {c.title} - {c.goal}" for c in outline.chapters)
        brief = (f"Keyword principale (validata su Amazon.it): {primary}\n"
                 f"Altre keyword validate: {', '.join(keywords)}\n"
                 f"Titolo di lavoro: {outline.title} - {outline.subtitle}\n"
                 f"Promessa: {outline.value_proposition}\n\nCapitoli:\n{chapters}\n\n"
                 f"Lettore: {persona.model_dump_json() if persona else 'non disponibile'}\n"
                 f"Titoli concorrenti in catalogo: {'; '.join(competitors or []) or 'non disponibili'}")
        problems: list[str] = []
        draft = None
        for _ in range(2):
            prompt = brief + (f"\n\nLa bozza precedente aveva questi problemi, correggili: {'; '.join(problems)}"
                              if problems else "")
            draft = tidy_keywords(self.llm.structured(system=SYSTEM, prompt=prompt, schema=ListingDraft,
                                                      effort="medium"))
            problems = listing_problems(draft, primary)
            if not problems:
                break
        assert draft is not None
        return draft, problems
