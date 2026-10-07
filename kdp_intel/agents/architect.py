"""Architect: buyer persona and book outline, from evidence only.

The persona is built from what readers wrote (forum threads, critical
reviews), the outline from the persona, the gap report and the official
sources. The book's skeleton is fixed by the brief — introduction, three
parts, conclusion — and checked in code after the model proposes it.
"""

from __future__ import annotations

from ..llm import LLM
from ..models import GapReport, Outline, PersonaDraft
from ..rag import VectorStore, format_evidence, retrieve

PERSONA_SYSTEM = """Sei un ricercatore di mercato editoriale per il mercato italiano. \
Costruisci la buyer persona SOLO da ciò che è scritto nelle evidenze (domande digitate su \
Google, thread di forum, recensioni, articoli). Le domande digitate su Google sono parole dei \
lettori: rivelano dubbi, paure e situazioni (chi chiede «per mia madre» è un figlio caregiver). \
Il campo vocabulary contiene parole ed espressioni copiate dalle evidenze, non parafrasi. Se un \
aspetto non emerge dalle evidenze, scrivi "non emerso"."""

OUTLINE_SYSTEM = """Sei l'architetto editoriale di un manuale non-fiction italiano di fascia \
premium (19,90-29,90 €). Struttura obbligatoria, in quest'ordine:
1. Introduzione: il problema con dati reali italiani/europei.
2. Parte 1 - Fondamenti strategici e demistificazione degli errori comuni.
3. Parte 2 - Il metodo passo-passo.
4. Parte 3 - Casi studio italiani, checklist e modelli pronti all'uso.
5. Conclusione: piano d'azione rapido.

Ogni capitolo risolve un problema preciso della persona o una lacuna dei libri concorrenti. \
Per ogni capitolo fornisci beats (i passaggi del capitolo, concreti) e queries (3-6 ricerche \
in italiano per recuperare dalle fonti norme, cifre e procedure necessarie). \
Il titolo contiene la parola chiave di ricerca principale; il sottotitolo il beneficio misurabile. \
Non usare frasi fatte."""

REQUIRED_PARTS = 5  # introduction, three parts, conclusion


class OutlineError(ValueError):
    pass


def validate_outline(outline: Outline) -> None:
    if len(outline.parts) != REQUIRED_PARTS:
        raise OutlineError(f"expected {REQUIRED_PARTS} parts (intro, 3 parts, conclusion), got {outline.parts}")
    if not outline.chapters:
        raise OutlineError("outline has no chapters")
    numbers = [c.number for c in outline.chapters]
    if numbers != list(range(1, len(numbers) + 1)):
        raise OutlineError(f"chapters must be numbered 1..n in order, got {numbers}")
    for c in outline.chapters:
        if c.part not in outline.parts:
            raise OutlineError(f"chapter {c.number} names unknown part {c.part!r}")
        if not c.queries:
            raise OutlineError(f"chapter {c.number} has no research queries")
    if outline.chapters[0].part != outline.parts[0] or outline.chapters[-1].part != outline.parts[-1]:
        raise OutlineError("the first chapter must be the introduction and the last the conclusion")
    order = [outline.parts.index(c.part) for c in outline.chapters]
    if order != sorted(order):
        raise OutlineError("chapters must follow the order of the parts")


def _bullets(lines: list[str] | None) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "(nessuna)"


class Architect:
    def __init__(self, llm: LLM, store: VectorStore) -> None:
        self.llm, self.store = llm, store

    def persona(self, niche: str, seeds: list[str], gaps: GapReport | None,
                questions: list[str] | None = None) -> PersonaDraft:
        # Official pages outrank forums on every query, so ask for many hits and keep the tier 3
        # ones: readers' threads first, then the other unranked pages.
        pool = [h for h in retrieve(self.store, seeds, k=40) if h.chunk.authority == 3]
        hits = sorted(pool, key=lambda h: h.chunk.kind != "community")[:12]
        gap_text = ""
        if gaps:
            gap_text = "\n".join(
                f"- {t.theme.label} ({t.count} recensioni): " + " | ".join(t.quotes)
                for t in gaps.themes
            )
        return self.llm.structured(
            system=PERSONA_SYSTEM,
            prompt=(f"Nicchia: {niche}\n\nLacune dalle recensioni negative:\n{gap_text or '(nessuna)'}"
                    f"\n\nDomande digitate su Google:\n{_bullets(questions)}"
                    f"\n\nEvidenze dalle community:\n{format_evidence(hits) or '(nessuna)'}"),
            schema=PersonaDraft,
            effort="medium",
        )

    def outline(self, niche: str, seeds: list[str], persona: PersonaDraft,
                gaps: GapReport | None, chapters: int = 12, questions: list[str] | None = None) -> Outline:
        hits = retrieve(self.store, seeds, k=8, max_authority=2)
        brief = (
            f"Nicchia: {niche}\nKeyword principali: {', '.join(seeds)}\n"
            f"Capitoli totali (inclusi introduzione e conclusione): circa {chapters}\n\n"
            f"Persona:\n{persona.model_dump_json(indent=2)}\n\n"
            f"Lacune dei concorrenti:\n{gaps.model_dump_json(indent=2) if gaps else '(nessuna)'}\n\n"
            + (f"Domande dei lettori (digitate su Google: ogni domanda frequente merita una risposta "
               f"nel libro):\n{_bullets(questions)}\n\n" if questions else "")
            + f"Fonti disponibili:\n{format_evidence(hits)}"
        )
        last_error = ""
        for _ in range(2):
            outline = self.llm.structured(
                system=OUTLINE_SYSTEM,
                prompt=brief + (f"\n\nLa proposta precedente era invalida: {last_error}" if last_error else ""),
                schema=Outline,
                effort="high",
            )
            try:
                validate_outline(outline)
                return outline
            except OutlineError as exc:
                last_error = str(exc)
        raise OutlineError(last_error)
