"""The agents, offline: RAG, analyst, review miner, architect, fact-checker."""

from __future__ import annotations

from datetime import date

import pytest

from kdp_intel.agents import Analyst, FactChecker, ReviewMiner
from kdp_intel.agents.analyst import combine
from kdp_intel.agents.architect import OutlineError, validate_outline
from kdp_intel.agents.writer import lint
from kdp_intel.models import ChapterDraft, SourceSpec
from kdp_intel.providers import Providers
from kdp_intel.providers.fixtures import Fixtures
from kdp_intel.rag import retrieve
from kdp_intel.rag.embeddings import HashingEmbedder
from kdp_intel.rag.ingest import Ingestor, chunk_text, html_to_text, source_id
from kdp_intel.rag.store import ChromaStore, MemoryStore

from intel_support import (
    ADE, ADE_URL, FIXTURES, ISTAT, OLD, OLD_URL, PRESS, TODAY, block, chapter_draft, llm, outline, project,
)


@pytest.fixture()
def fixtures():
    return Fixtures(FIXTURES)


@pytest.fixture()
def store(fixtures, tmp_path):
    s = MemoryStore(HashingEmbedder())
    ing = Ingestor(s, tmp_path / "sources.json", fixtures, today=TODAY)
    ing.ingest_all([SourceSpec(url=u) for u in fixtures.data["pages"]])
    return s


def checker(store, **kw):
    return FactChecker(llm(**kw), store, project().quality, today=TODAY)


# ------------------------------------------------------------------ RAG

def test_html_cleaning_keeps_prose_and_drops_chrome(fixtures):
    title, text, published = html_to_text(fixtures.fetch(ADE_URL))
    assert title.startswith("Dichiarazione di successione")
    assert "entro 12 mesi" in text and "Home Cittadini" not in text and "Partita IVA" not in text
    assert published == date(2025, 2, 14)  # "Ultimo aggiornamento: 14 febbraio 2025"


def test_chunks_respect_size_and_keep_everything():
    text = "\n".join(f"Paragrafo {i}. " + "parola " * 60 for i in range(20))
    chunks = chunk_text(text, size=600, overlap=100)
    assert all(len(c) <= 700 for c in chunks)
    assert all(f"Paragrafo {i}." in " ".join(chunks) for i in range(20))


def test_ingest_classifies_and_dates_sources(fixtures, tmp_path):
    s = MemoryStore(HashingEmbedder())
    ing = Ingestor(s, tmp_path / "sources.json", fixtures, today=TODAY)
    ing.ingest_all([SourceSpec(url=u) for u in fixtures.data["pages"]])
    ing.ingest_thread(fixtures.threads("ItaliaPersonalFinance", "x")[0])
    m = ing.manifest
    assert m[ADE]["kind"] == "official" and m[ADE]["authority"] == 1
    assert m[PRESS]["kind"] == "press" and m[PRESS]["published"] == "2026-05-10"
    assert any(v["kind"] == "community" and v["authority"] == 3 for v in m.values())
    assert (tmp_path / "sources.json").exists()


def test_filters_age_out_press_but_never_official_pages(store):
    hits = retrieve(store, ["imposta di successione giorni versamento"], k=10, max_age_days=540, today=TODAY)
    ids = {h.chunk.source_id for h in hits}
    assert OLD not in ids  # 2019 press article
    assert ADE in ids  # official page dated 2025-02-14 is older than the window but stays
    assert all(h.chunk.authority <= 2 for h in store.query("eredi", 10, max_authority=2))


def test_hybrid_search_finds_the_rare_word(store):
    top = store.query("quanti decessi nel 2025", 1)[0]
    assert top.chunk.source_id == ISTAT and top.lexical > 0


def test_chroma_store_round_trip(tmp_path, fixtures):
    s = ChromaStore(str(tmp_path / "chroma"), HashingEmbedder())
    Ingestor(s, tmp_path / "sources.json", fixtures, today=TODAY).ingest_all(
        [SourceSpec(url=u) for u in fixtures.data["pages"]])
    assert s.count() >= 4
    hits = s.query("versamento entro 90 giorni F24", 3, source_ids=[ADE])
    assert hits and all(h.chunk.source_id == ADE for h in hits)
    assert s.query("decessi", 2, min_published=date(2026, 1, 1))[0].chunk.source_id == ISTAT


# ------------------------------------------------------------------ analyst

def test_analyst_scores_from_measurements_only(fixtures, store):
    analyst = Analyst(Providers.offline(fixtures), store, today=TODAY)
    c = analyst.assess("Successione", ["dichiarazione di successione", "imposta di successione"])
    assert set(c.scores) == {"demand", "momentum", "weak_competition", "proven_sales", "freshness"}
    assert c.scores["momentum"] > 5  # the fixture trend rises
    assert c.competitors[0].bsr == 15400
    assert 0 < c.total <= 10


def test_missing_measurements_are_not_free_points():
    assert combine({"demand": 8.0, "momentum": None}) == 8.0
    assert combine({}) == 0.0
    assert combine({"demand": None, "freshness": 9.0}) == 0.0  # fresh sources alone prove no market
    analyst = Analyst(Providers(), None, today=TODAY)
    c = analyst.assess("Vuota", ["niente"])
    assert c.total == 0.0 and any("non calcolabile" in n for n in c.notes)


# ------------------------------------------------------------------ review miner

def test_review_miner_counts_in_code_and_rejects_invented_quotes(fixtures):
    miner = ReviewMiner(llm())
    reviews = miner.collect(fixtures, ["B0TEST0001"])
    gaps = miner.mine(reviews)
    assert gaps.reviews_considered == 3  # the 5-star review is ignored
    counts = {t.theme.id: t.count for t in gaps.themes}
    assert counts == {"non_aggiornato": 1, "troppo_teorico": 1, "manca_conto": 1}
    by_id = {t.theme.id: t for t in gaps.themes}
    assert by_id["non_aggiornato"].quotes == ["nulla sulla riforma del 2025"]
    assert by_id["manca_conto"].quotes == [] and gaps.rejected_quotes == 1


# ------------------------------------------------------------------ architect & writer lint

def test_outline_structure_is_enforced():
    validate_outline(outline())
    bad = outline()
    bad.parts = bad.parts[:3]
    with pytest.raises(OutlineError, match="parts"):
        validate_outline(bad)
    swapped = outline()
    swapped.chapters[0].part, swapped.chapters[1].part = swapped.chapters[1].part, swapped.chapters[0].part
    with pytest.raises(OutlineError):
        validate_outline(swapped)


def test_lint_catches_banned_phrases_unknown_sources_and_missing_tools():
    draft = ChapterDraft.model_validate({"title": "x", "blocks": [
        block("paragraph", "In un mondo in continua evoluzione la legge cambia [[S:inventata-abcdef]]."),
        block("callout", "testo"),
        block("table", header=["a", "b"], rows=[["1"]]),
    ]})
    issues = lint(draft, {ADE}, project().quality)
    text = " ".join(issues)
    assert "frase vietata" in text and "inventata-abcdef" in text and "callout senza tipo" in text
    assert "lunghezza" in text and "caso_pratico" in text and "checklist" in text
    good = ChapterDraft.model_validate(chapter_draft("ok"))
    assert lint(good, {ADE, ISTAT}, project().quality) == []


# ------------------------------------------------------------------ fact-checker

def _draft(*paragraphs: str) -> ChapterDraft:
    return ChapterDraft.model_validate({"title": "t", "blocks": [block("paragraph", p) for p in paragraphs]})


def test_supported_claims_pass_with_verbatim_quotes(store):
    report = checker(store).check(1, ChapterDraft.model_validate(chapter_draft("ok")))
    assert report.passed, [(v.status, v.note) for v in report.failures()]
    assert report.counts == {"supported": len(report.verdicts)}
    assert all(v.quote for v in report.verdicts)


def test_wrong_number_is_caught_without_asking_the_model(store):
    fc = checker(store)
    report = fc.check(1, _draft(f"Il versamento avviene entro 60 giorni dalla presentazione [[S:{ADE}]]."))
    assert [v.status for v in report.verdicts] == ["number_mismatch"]
    assert "60" in report.verdicts[0].note and not report.passed
    assert fc.llm.calls == []  # deterministic gate, no tokens spent


def test_uncited_community_and_stale_sources_fail(store, fixtures, tmp_path):
    thread = fixtures.threads("ItaliaPersonalFinance", "x")[0]
    forum = source_id(thread["url"])
    Ingestor(store, tmp_path / "s.json", fixtures, today=TODAY).ingest_thread(thread)
    report = checker(store).check(1, _draft(
        "Il CAF chiede circa 600 euro per la pratica.",
        f"Il CAF chiede circa 600 euro per la pratica [[S:{forum}]].",
        f"Nel 2019 si pagava entro 60 giorni [[S:{OLD}]].",
    ))
    assert [v.status for v in report.verdicts] == ["uncited", "weak_source", "weak_source"]
    assert "ufficiale" in report.verdicts[1].note and "anteriore" in report.verdicts[2].note


def test_quotes_the_source_never_said_are_not_support(store):
    report = checker(store, fabricate=True).check(1, _draft(
        f"Gli eredi hanno 12 mesi di tempo per la dichiarazione dalla data di apertura della successione [[S:{ADE}]]."))
    assert [v.status for v in report.verdicts] == ["unsupported"]
    assert "non compare" in report.verdicts[0].note


def test_web_cross_check_finds_the_source_to_cite(fixtures, tmp_path):
    s = MemoryStore(HashingEmbedder())
    ing = Ingestor(s, tmp_path / "sources.json", fixtures, today=TODAY)
    fc = FactChecker(llm(), s, project().quality, web=fixtures, ingestor=ing,
                     verify_domains=["agenziaentrate.gov.it"], today=TODAY)
    report = fc.check(1, _draft("La dichiarazione di successione si presenta entro 12 mesi."))
    v = report.verdicts[0]
    assert v.status == "uncited" and v.source_id == ADE and f"[[S:{ADE}]]" in v.note
    assert ADE_URL in fixtures.fetched and OLD_URL not in fixtures.fetched
