"""Offline doubles for kdp_intel tests: a project, the fixture providers and a
scripted model that answers like a careful (or, on request, careless) Claude."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from kdp_intel.config import BookMeta, Project, QualityBar, ResearchPlan
from kdp_intel.llm import ScriptedLLM
from kdp_intel.models import (
    ChapterDraft, ChapterSpec, EntailmentBatch, ListingDraft, NicheIdeas, Outline, PersonaDraft,
    ReviewThemesDraft, WebResults,
)
from kdp_intel.rag.ingest import source_id
from kdp_intel.text import canonical_numbers_in, numbers, sentences, strip_cites

FIXTURES = Path(__file__).with_name("fixtures_intel") / "market.json"
TODAY = date(2026, 10, 4)

ADE_URL = "https://www.agenziaentrate.gov.it/portale/successione"
ISTAT_URL = "https://www.istat.it/indicatori-2025"
PRESS_URL = "https://www.money.it/sblocco-conto"
OLD_URL = "https://www.money.it/vecchio"
ADE, ISTAT, PRESS, OLD = (source_id(u) for u in (ADE_URL, ISTAT_URL, PRESS_URL, OLD_URL))

PARTS = ["Introduzione", "Fondamenti", "Il metodo", "Casi e modelli", "Conclusione"]
QUERIES = ["termine dichiarazione di successione 12 mesi versamento rate", "decessi 2025 Italia"]


def outline() -> Outline:
    return Outline(
        title="Successione senza errori", subtitle="La guida pratica 2026 per gli eredi",
        value_proposition="Chiudere la successione in sei mesi senza sanzioni.",
        parts=PARTS,
        chapters=[ChapterSpec(number=i + 1, part=p, title=f"Capitolo su {p.lower()}", goal="Capire i termini",
                              beats=["termini", "versamento"], queries=QUERIES, target_words=400)
                  for i, p in enumerate(PARTS)],
    )


def project(with_outline: bool = True, **quality) -> Project:
    return Project(
        slug="test-successione", facts_as_of=TODAY,
        book=BookMeta(title="Successione senza errori", subtitle="La guida pratica 2026", author="Test Autore",
                      publisher="Test Editore", year=2026),
        research=ResearchPlan(
            niches={"Successione": ["dichiarazione di successione", "imposta di successione"]},
            competitor_asins=["B0TEST0001"], subreddits=["ItaliaPersonalFinance"],
            reddit_queries=["successione"], verify_domains=["agenziaentrate.gov.it"],
        ),
        sources=[{"url": u} for u in (ADE_URL, ISTAT_URL, PRESS_URL, OLD_URL)],
        index_terms=["dichiarazione di successione", "F24", "rate"],
        outline=outline() if with_outline else None,
        quality=QualityBar(**quality),
    )


def block(type_, text="", title="", kind="none", items=(), header=(), rows=()):
    return {"type": type_, "text": text, "title": title, "kind": kind, "items": list(items),
            "header": list(header), "rows": [list(r) for r in rows]}


def chapter_draft(title: str, days: int = 90) -> dict:
    return {"title": title, "blocks": [
        block("paragraph", f"Gli eredi presentano la dichiarazione entro 12 mesi dalla data di apertura della "
                           f"successione [[S:{ADE}]]. Ecco come muoversi senza ansia."),
        block("heading", "Il versamento"),
        block("paragraph", f"Il versamento avviene entro {days} giorni dal termine di presentazione, con "
                           f"modello **F24** [[S:{ADE}]]."),
        block("table", title="Tabella 1. Le scadenze", header=["Adempimento", "Termine"],
              rows=[["Dichiarazione", f"12 mesi [[S:{ADE}]]"], ["Versamento", f"90 giorni [[S:{ADE}]]"]]),
        block("callout", f"Marta, erede unica a Bologna, versa il 20 per cento subito e il resto in 8 rate "
                         f"trimestrali [[S:{ADE}]].", title="Il caso di Marta", kind="caso_pratico"),
        block("checklist", items=["Certificato di morte", "Dichiarazione sostitutiva dell'atto di notorietà"]),
        block("paragraph", f"Nel 2025 in Italia i decessi sono stati 652mila [[S:{ISTAT}]]."),
    ]}


def _evidence(prompt: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(
        r'<evidence source_id="([^"]+)"[^>]*>\n(.*?)\n</evidence>', prompt, re.S)}


def brain(wrong: dict[int, bool] | None = None, fabricate: bool = False):
    """A responder for ScriptedLLM.

    ``wrong`` maps a chapter number to whether its writer is stubborn: the
    first draft of each listed chapter says "60 giorni" (the source says 90);
    on revision a normal writer fixes it, a stubborn one does not.
    ``fabricate`` makes the fact-checker restate claims instead of quoting.
    """
    wrong = wrong or {}

    def respond(schema, system, prompt):
        if schema is ReviewThemesDraft:
            reviews = re.findall(r'<review index="(\d+)" stars="\d">\n(.*?)\n</review>', prompt, re.S)
            labels = []
            for idx, body in reviews:
                tid, quote = None, ""
                if "riforma" in body:
                    tid, quote = "non_aggiornato", "nulla sulla riforma del 2025"
                elif "esempio" in body:
                    tid, quote = "troppo_teorico", "nessun esempio pratico"
                elif "conto" in body:
                    tid, quote = "manca_conto", "una frase che il recensore non ha mai scritto"
                labels.append({"review_index": int(idx), "theme_ids": [tid] if tid else [], "quote": quote})
            return {"themes": [
                {"id": "non_aggiornato", "label": "Non aggiornato alla riforma", "description": "regole vecchie"},
                {"id": "troppo_teorico", "label": "Troppo teorico", "description": "niente esempi"},
                {"id": "manca_conto", "label": "Manca il conto corrente", "description": "sblocco del conto"}],
                "labels": labels, "missing_content": ["sblocco del conto corrente"],
                "opportunity_statements": ["Guida aggiornata alla riforma con esempi compilati"]}
        if schema is PersonaDraft:
            return {"name": "Laura, erede", "demographics": "48 anni, impiegata", "competence_level": "base",
                    "vocabulary": ["conto bloccato", "CAF"], "frustrations": ["costi del CAF"],
                    "tried_and_failed": ["articoli online datati"], "desired_outcome": "chiudere la pratica"}
        if schema is Outline:
            return outline().model_dump()
        if schema is ChapterDraft:
            n = int(re.search(r"Scrivi il capitolo (\d+)", prompt).group(1))
            title = re.search(r"Scrivi il capitolo \d+ «(.*?)»", prompt).group(1)
            evidence = _evidence(prompt)
            assert ADE in evidence and ISTAT in evidence, "the writer must be shown the sources it cites"
            revising = "Revisione" in prompt
            days = 60 if n in wrong and (not revising or wrong[n]) else 90
            return chapter_draft(title, days)
        if schema is EntailmentBatch:
            results = []
            blocks = re.findall(r'<claim id="([^"]+)"( esempio="si")?>\n(.*?)\n</claim>\n<evidence_for id="[^"]+">\n(.*?)'
                                r"\n</evidence_for>", prompt, re.S)
            for cid, example, claim, ev in blocks:
                evidence = _evidence(ev)
                quote, sid = "", ""
                wanted = numbers(claim)
                if example:  # judge the rule the step applies, not the scenario's own figures
                    wanted = [n for n in wanted if n in canonical_numbers_in(" ".join(evidence.values()))]
                for s, text in evidence.items():
                    for sentence in sentences(text.replace("\n", " ")):
                        if all(n in canonical_numbers_in(sentence) for n in wanted):
                            quote, sid = sentence, s
                            break
                    if quote:
                        break
                if fabricate:
                    quote = strip_cites(claim)  # the claim restated, not quoted from the source
                results.append({"claim_id": cid, "verdict": "supported" if quote else "not_enough_info",
                                "source_id": sid, "quote": quote, "note": ""})
            return {"results": results}
        if schema is NicheIdeas:  # one real topic, one the user never typed
            return {"ideas": [
                {"name": "Successione dopo la riforma", "reader": "eredi", "problem": "dichiarazione e imposta",
                 "why_now": "autoliquidazione dal 2025",
                 "keywords": ["dichiarazione di successione", "imposta di successione", "successione senza notaio"]},
                {"name": "Cucina veloce", "reader": "chi cucina", "problem": "poco tempo", "why_now": "-",
                 "keywords": ["ricette veloci", "torte facili"]}]}
        if schema is ListingDraft:
            return {"title": "Dichiarazione di successione",
                    "subtitle": "La guida 2026 per gli eredi: imposta in autoliquidazione, rate e scadenze",
                    "description": "Chi eredita ha dodici mesi e molte domande. " * 20,
                    "backend_keywords": ["imposta di successione rate", "eredità conto bloccato",
                                         "successione senza notaio", "voltura catastale eredi",
                                         "rinuncia eredità", "testamento olografo", "pensione reversibilità",
                                         "bestseller successione", "dichiarazione di successione"],
                    "categories": ["Libri > Diritto > Diritto civile", "Libri > Economia > Fisco"]}
        if schema is WebResults:
            return {"results": [{"title": "Dichiarazione di successione", "url": ADE_URL,
                                 "snippet": "entro 12 mesi", "published": ""}]}
        raise AssertionError(f"unexpected schema {schema.__name__}")

    return respond


def llm(**kwargs) -> ScriptedLLM:
    return ScriptedLLM(brain(**kwargs))
