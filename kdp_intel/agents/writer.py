"""Writer Agent: one chapter at a time, from retrieved evidence only.

The writer sees the outline (for coherence), the persona (for voice and
vocabulary), the chapter's beats and the evidence retrieved for its queries.
It may cite only the source ids it was shown. After each draft a linter
checks what code can check — banned phrases, unknown citations, malformed
blocks — and those findings go back with the fact-checker's on revision.
"""

from __future__ import annotations

from datetime import date

from ..config import QualityBar
from ..llm import LLM
from ..models import ChapterDraft, ChapterSpec, ClaimVerdict, Hit, Outline, PersonaDraft
from ..rag import format_evidence
from ..text import cites, normalise_ws, strip_cites

SYSTEM = """Sei un consulente senior italiano che scrive un manuale pratico di fascia premium. \
Tono autorevole, concreto, empatico; frasi brevi; si entra subito nel merito.

Regole di contenuto, non negoziabili:
1. Ogni frase che contiene una cifra, una percentuale, un importo, una data, una scadenza o \
un riferimento normativo termina con il marcatore della fonte, ad esempio [[S:istat-3fa2c1]]. \
Usa solo i source_id presenti nelle evidenze di questo messaggio.
2. Cifre, norme e procedure vengono SOLO dalle evidenze. Se un dato utile manca nelle \
evidenze, non scriverlo: descrivi il passaggio senza la cifra.
3. Le evidenze con tier="3" (forum, recensioni) servono a capire il lettore: non usarle come \
fonte di cifre o regole.
4. Contesto italiano: istituzioni, piattaforme e moduli reali (es. Agenzia delle Entrate, \
INPS, SPID, F24) solo se compaiono nelle evidenze o sono di dominio comune.
5. Nei callout caso_pratico le cifre dello scenario (patrimonio, età, saldi) sono di fantasia e \
non si citano; ogni frase che applica una regola (aliquota, franchigia, scadenza, rate) la cita, \
e ogni cifra calcolata si ottiene con un solo passaggio (somma, differenza, prodotto, quota o \
percentuale) dalle cifre già scritte o da quelle della fonte, arrotondata all'euro.
6. Vietato: frasi fatte da IA ("In un mondo in continua evoluzione", "È fondamentale \
ricordare che", "In conclusione"), elenchi vuoti, promesse di risultati garantiti.

Formato: una lista di blocchi.
- heading: sottotitolo di sezione nel campo text (niente numerazione).
- paragraph: testo, con **grassetto** e *corsivo* se servono.
- bullets / numbered / checklist: voci in items.
- table: header e rows (ogni riga con lo stesso numero di celle dell'header), didascalia in title.
- callout: kind tra attenzione, caso_pratico, consiglio, dato_chiave; titolo in title; testo in text.
I campi non usati sono stringhe o liste vuote; kind vale "none" per i blocchi non callout.
Ogni capitolo contiene almeno: una tabella o uno schema, un callout "caso_pratico" \
ambientato in Italia e una checklist operativa."""


def lint(draft: ChapterDraft, allowed_sources: set[str], quality: QualityBar) -> list[str]:
    issues = []
    for i, block in enumerate(draft.blocks):
        texts = [block.text, block.title, *block.items, *block.header, *(c for r in block.rows for c in r)]
        for text in texts:
            low = normalise_ws(text)
            for phrase in quality.banned_phrases:
                if phrase in low:
                    issues.append(f"blocco {i}: frase vietata «{phrase}»")
            for sid in cites(text):
                if sid not in allowed_sources:
                    issues.append(f"blocco {i}: fonte [[S:{sid}]] non presente nelle evidenze")
        if block.type == "callout" and block.kind == "none":
            issues.append(f"blocco {i}: callout senza tipo")
        if block.type == "table":
            if not block.header or any(len(r) != len(block.header) for r in block.rows):
                issues.append(f"blocco {i}: tabella con righe di lunghezza diversa dall'intestazione")
        if block.type in ("bullets", "numbered", "checklist") and not block.items:
            issues.append(f"blocco {i}: elenco vuoto")
        if block.type in ("paragraph", "heading", "callout") and not strip_cites(block.text):
            issues.append(f"blocco {i}: testo vuoto")
    kinds = {(b.type, b.kind) for b in draft.blocks}
    if not any(t == "table" for t, _ in kinds):
        issues.append("manca una tabella o uno schema")
    if ("callout", "caso_pratico") not in kinds:
        issues.append("manca un callout caso_pratico")
    if not any(t == "checklist" for t, _ in kinds):
        issues.append("manca una checklist")
    return issues


def word_count(draft: ChapterDraft) -> int:
    words = 0
    for b in draft.blocks:
        for t in [b.text, *b.items, *(c for r in b.rows for c in r)]:
            words += len(strip_cites(t).split())
    return words


class Writer:
    def __init__(self, llm: LLM, quality: QualityBar, facts_as_of: date) -> None:
        self.llm, self.quality, self.facts_as_of = llm, quality, facts_as_of

    def write(self, spec: ChapterSpec, outline: Outline, persona: PersonaDraft | None,
              evidence: list[Hit], previous: ChapterDraft | None = None,
              failures: list[ClaimVerdict] | None = None, lint_issues: list[str] | None = None,
              ) -> ChapterDraft:
        toc = "\n".join(f"{c.number}. [{c.part}] {c.title} — {c.goal}" for c in outline.chapters)
        prompt = [
            f"Libro: {outline.title} — {outline.subtitle}",
            f"Promessa al lettore: {outline.value_proposition}",
            f"Dati aggiornati al: {self.facts_as_of.isoformat()}",
            f"Indice completo (per coerenza, non ripetere altri capitoli):\n{toc}",
        ]
        if persona:
            prompt.append(f"Lettore: {persona.model_dump_json()}")
        prompt += [
            f"Scrivi il capitolo {spec.number} «{spec.title}» ({spec.part}).",
            f"Obiettivo: {spec.goal}",
            "Passaggi:\n" + "\n".join(f"- {b}" for b in spec.beats),
            f"Lunghezza: circa {spec.target_words} parole.",
            f"Evidenze:\n{format_evidence(evidence)}",
        ]
        if previous is not None:
            problems = [
                f"- «{strip_cites(v.claim.text)}»: {v.status}. {v.note}".strip() for v in failures or []
            ] + [f"- {issue}" for issue in lint_issues or []]
            prompt.append(
                "Revisione. Questa è la bozza precedente:\n" + previous.model_dump_json()
                + "\n\nCorreggi questi problemi: riformula la frase sulle evidenze, cita la fonte "
                  "giusta o elimina l'affermazione. Lascia invariato il resto.\n" + "\n".join(problems)
            )
        draft = self.llm.structured(
            system=SYSTEM, prompt="\n\n".join(prompt), schema=ChapterDraft, effort="high",
            max_tokens=64000,
        )
        if not draft.title:
            draft.title = spec.title
        return draft
