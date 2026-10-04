"""Fact-Checker Agent: no claim reaches the page without proof.

A *checkable* claim is any sentence (or table row) carrying a number, a
percentage, an amount, a date or a law reference. Each one goes through
gates in order, cheapest and most certain first:

1. **cited**      - it carries at least one ``[[S:id]]`` marker;
2. **in archive** - the cited source is in the vector store;
3. **authority**  - numbers and rules rest on a tier 1-2 source, and press
                    is not older than the quality bar allows;
4. **numbers**    - every number in the claim occurs in the cited source's
                    text (pure string check: no model involved);
5. **entailment** - a model judges whether the evidence states the claim,
                    and must quote it; a quote that is not a verbatim
                    substring of the evidence is treated as no support.

Uncited or unsupported claims are cross-checked on the web (official domains
first). What is found is ingested and reported as the source to cite; the
claim still fails, so the writer has to fix the text and the next pass
proves it.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..config import QualityBar
from ..llm import LLM
from ..models import (
    ChapterDraft, Claim, ClaimVerdict, EntailmentBatch, FactCheckReport, Hit, SourceSpec,
)
from ..rag import VectorStore, format_evidence
from ..text import canonical_numbers_in, cites, law_refs, normalise_ws, numbers, sentences, signals

SYSTEM = """Sei un fact-checker editoriale. Per ogni affermazione ricevi le evidenze \
delle fonti citate. Decidi se le evidenze AFFERMANO esplicitamente il contenuto:
- supported: le evidenze dicono la stessa cosa (stesse cifre, stesse condizioni, stessa norma);
- contradicted: le evidenze dicono qualcosa di incompatibile (cifra, data, soggetto o condizione diversi);
- not_enough_info: le evidenze non bastano.
Per supported e contradicted copia in quote il passaggio decisivo PAROLA PER PAROLA dalle \
evidenze; altrimenti quote è vuota. source_id è la fonte del passaggio. Non usare conoscenze \
esterne alle evidenze."""

FAILING = {"contradicted", "uncited", "number_mismatch", "weak_source"}


def extract_claims(chapter: int, draft: ChapterDraft) -> list[Claim]:
    claims: list[Claim] = []

    def add(block_index: int, text: str) -> None:
        sig = signals(text)
        if sig:
            claims.append(Claim(id=f"c{chapter}.{len(claims) + 1}", chapter=chapter,
                                block_index=block_index, text=text, cited=cites(text), signals=sig))

    for i, block in enumerate(draft.blocks):
        if block.type == "heading":
            continue
        if block.type == "table":
            for row in block.rows:  # a row is one claim, read with its header
                add(i, "; ".join(f"{h}: {c}" for h, c in zip(block.header, row)))
            continue
        for text in [block.text, *block.items]:
            for sentence in sentences(text):
                add(i, sentence)
    return claims


class FactChecker:
    def __init__(self, llm: LLM, store: VectorStore, quality: QualityBar, *, web=None,
                 ingestor=None, verify_domains: list[str] | None = None,
                 today: date | None = None, batch_size: int = 12) -> None:
        self.llm, self.store, self.quality = llm, store, quality
        self.web, self.ingestor = web, ingestor
        self.verify_domains = verify_domains or []
        self.today = today or date.today()
        self.batch_size = batch_size

    # ----------------------------------------------------------- deterministic gates

    def _evidence(self, claim: Claim) -> list[Hit]:
        return self.store.query(claim.text, 12, source_ids=claim.cited)

    def _gate(self, claim: Claim) -> tuple[ClaimVerdict | None, list[Hit]]:
        if not claim.cited:
            return ClaimVerdict(claim=claim, status="uncited",
                                note="affermazione verificabile senza fonte"), []
        hits = self._evidence(claim)
        found = {h.chunk.source_id for h in hits}
        absent = [s for s in claim.cited if s not in found]
        if not hits:
            return ClaimVerdict(claim=claim, status="unsupported",
                                note=f"fonti citate non presenti in archivio: {', '.join(absent)}"), []
        best_tier = min(h.chunk.authority for h in hits)
        if best_tier > self.quality.min_authority_for_numbers:
            return ClaimVerdict(claim=claim, status="weak_source", source_id=hits[0].chunk.source_id,
                                note="cifre e norme richiedono una fonte ufficiale o di stampa professionale"), hits
        oldest = self.today - timedelta(days=self.quality.max_source_age_days)
        if all(h.chunk.kind == "press" and h.chunk.published and date.fromisoformat(h.chunk.published) < oldest
               for h in hits):
            return ClaimVerdict(claim=claim, status="weak_source", source_id=hits[0].chunk.source_id,
                                note=f"fonte di stampa anteriore al {oldest.isoformat()}"), hits

        source_text = " ".join(h.chunk.text for h in hits)
        present = canonical_numbers_in(source_text)
        missing = [n for n in numbers(claim.text) if n not in present]
        for ref in law_refs(claim.text):
            ident = ref.split()[-1]
            if ident not in source_text and ident.split("/")[0] not in present:
                missing.append(ref)
        if missing:
            return ClaimVerdict(claim=claim, status="number_mismatch", source_id=hits[0].chunk.source_id,
                                note=f"assenti nella fonte citata: {', '.join(missing)}"), hits
        return None, hits

    # ----------------------------------------------------------- model gate

    def _entail(self, pending: list[tuple[Claim, list[Hit]]]) -> list[ClaimVerdict]:
        out: list[ClaimVerdict] = []
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start:start + self.batch_size]
            prompt = "\n\n".join(
                f'<claim id="{c.id}">\n{c.text}\n</claim>\n<evidence_for id="{c.id}">\n'
                f"{format_evidence(hits[:4])}\n</evidence_for>"
                for c, hits in batch
            )
            result = self.llm.structured(system=SYSTEM, prompt=prompt, schema=EntailmentBatch, effort="medium")
            by_id = {r.claim_id: r for r in result.results}
            for claim, hits in batch:
                r = by_id.get(claim.id)
                # Exactly what the model was shown: title line and passage of each hit.
                evidence = normalise_ws(" ".join(f"{h.chunk.title} {h.chunk.text}" for h in hits[:4]))
                if r is None:
                    out.append(ClaimVerdict(claim=claim, status="unsupported", note="nessun giudizio restituito"))
                elif r.verdict == "not_enough_info":
                    out.append(ClaimVerdict(claim=claim, status="unsupported", note=r.note))
                elif not r.quote or normalise_ws(r.quote) not in evidence:
                    out.append(ClaimVerdict(claim=claim, status="unsupported",
                                            note="la citazione proposta non compare nella fonte"))
                else:
                    status = "supported" if r.verdict == "supported" else "contradicted"
                    out.append(ClaimVerdict(claim=claim, status=status, source_id=r.source_id,
                                            quote=r.quote, note=r.note))
        return out

    # ----------------------------------------------------------- web cross-check

    def _cross_check(self, verdict: ClaimVerdict) -> ClaimVerdict:
        if self.web is None or self.ingestor is None or not self.verify_domains:
            return verdict
        query = " ".join(w for w in verdict.claim.text.split() if not w.startswith("[[S:"))[:200]
        specs = []
        for dom in self.verify_domains[:3]:
            specs += [SourceSpec(url=r.url, title=r.title) for r in self.web.search(f"{query} site:{dom}", num=2)]
        if not specs:
            return verdict
        self.ingestor.ingest_all(specs)
        hits = self.store.query(verdict.claim.text, 4, max_authority=self.quality.min_authority_for_numbers)
        present = canonical_numbers_in(" ".join(h.chunk.text for h in hits))
        if hits and all(n in present for n in numbers(verdict.claim.text)):
            sid = hits[0].chunk.source_id
            verdict.note = f"{verdict.note}; possibile fonte: [[S:{sid}]] ({hits[0].chunk.publisher})".lstrip("; ")
            verdict.source_id = sid
        return verdict

    # ----------------------------------------------------------- public

    def check(self, chapter: int, draft: ChapterDraft) -> FactCheckReport:
        claims = extract_claims(chapter, draft)
        verdicts: list[ClaimVerdict] = []
        pending: list[tuple[Claim, list[Hit]]] = []
        for claim in claims:
            verdict, hits = self._gate(claim)
            if verdict is None:
                pending.append((claim, hits))
            else:
                verdicts.append(verdict)
        verdicts += self._entail(pending) if pending else []
        verdicts = [self._cross_check(v) if v.status in ("uncited", "unsupported") else v for v in verdicts]
        order = {c.id: i for i, c in enumerate(claims)}
        verdicts.sort(key=lambda v: order[v.claim.id])

        counts: dict[str, int] = {}
        for v in verdicts:
            counts[v.status] = counts.get(v.status, 0) + 1
        unsupported_ratio = counts.get("unsupported", 0) / (len(verdicts) or 1)
        passed = not any(v.status in FAILING for v in verdicts) and (
            unsupported_ratio <= self.quality.max_unsupported_ratio
        )
        return FactCheckReport(chapter=chapter, verdicts=verdicts, passed=passed, counts=counts)
