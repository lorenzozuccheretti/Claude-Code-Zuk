"""Typesetting and the whole LangGraph pipeline, offline."""

from __future__ import annotations

import json

import pytest
from pypdf import PdfReader

from kdp_intel.config import BookMeta
from kdp_intel.graph import Runtime, run
from kdp_intel.models import ChapterDraft
from kdp_intel.providers import Providers
from kdp_intel.providers.fixtures import Fixtures
from kdp_intel.rag.embeddings import HashingEmbedder
from kdp_intel.rag.store import MemoryStore
from kdp_intel.typeset import BookContent, typeset
from kdp_intel.typeset.common import geometry, inline_tokens
from kdp_intel.typeset.typst_engine import TypstWriter

from intel_support import ADE, FIXTURES, ISTAT, TODAY, block, chapter_draft, llm, outline, project

SOURCES = {
    ADE: {"url": "https://www.agenziaentrate.gov.it/portale/successione", "title": "Dichiarazione di successione",
          "publisher": "Agenzia delle Entrate", "published": "2025-02-14", "retrieved": "2026-10-04",
          "authority": 1},
    ISTAT: {"url": "https://www.istat.it/indicatori-2025", "title": "Indicatori demografici", "publisher": "ISTAT",
            "published": "2026-03-31", "retrieved": "2026-10-04", "authority": 1},
}


def book(extra_text: str = "") -> BookContent:
    o = outline()
    chapters = []
    for spec in o.chapters:
        d = chapter_draft(spec.title)
        d["blocks"].append(block("paragraph", "Testo lungo per riempire la pagina. " * 120 + extra_text))
        chapters.append(ChapterDraft.model_validate(d))
    return BookContent(meta=BookMeta(title="Successione senza errori", subtitle="La guida", author="Test Autore",
                                     publisher="Test Editore", year=2026),
                       outline=o, chapters=chapters, sources=SOURCES, facts_as_of="2026-10-04",
                       index_terms=["F24", "dichiarazione di successione"])


def test_gutter_grows_with_page_count():
    assert geometry("6x9", 120).spec_gutter == 0.375
    assert geometry("6x9", 420).spec_gutter == 0.625
    assert geometry("6x9", 420).inside >= 0.625 + 0.3
    assert geometry("6x9", 10).planned_pages == 24  # KDP minimum


def test_inline_markup_is_inert_in_typst():
    tokens = inline_tokens(f"Un **grassetto** e *corsivo* con # e // e $ [[S:{ADE}]].")
    assert [k for k, _ in tokens] == ["text", "strong", "text", "emph", "text", "cite", "text"]
    w = TypstWriter(book())
    out = w.inline('Attenzione: #set page(width: 1in) e "virgolette" // non un commento')
    assert out.startswith('#"Attenzione: #set page(width: 1in) e \\"virgolette\\" // non un commento"')


@pytest.mark.parametrize("engine", ["typst", "weasyprint"])
def test_book_is_print_ready(tmp_path, engine):
    result = typeset(book('Testo con caratteri speciali: # * _ $ @ <x> [y] // fine.'), tmp_path, engine=engine)
    pf = result.preflight
    assert pf.ok, pf.problems
    assert pf.size_in == (6.0, 9.0) and pf.pages % 2 == 0 and pf.pages >= 24
    reader = PdfReader(str(result.pdf))
    text = [p.extract_text() or "" for p in reader.pages]
    whole = "\n".join(text)
    assert "Indice" in text[2] and "Successione senza errori" in text[0]
    assert "Fonti e riferimenti" in whole and "Agenzia delle Entrate" in whole
    assert "Indice analitico" in whole
    assert "@ <x> [y]" in whole  # special characters printed, not interpreted
    # Running heads: the book title on a verso of the main matter, a chapter title on a recto.
    assert any("SUCCESSIONE SENZA ERRORI" in t for t in text[5:])
    assert any("CAPITOLO SU FONDAMENTI" in t for t in text[5:])
    meta = json.loads((tmp_path / "preflight.json").read_text())
    assert meta["engine"] == engine and meta["ok"]


def _runtime(tmp_path, **brain):
    return Runtime(project=project(), llm=llm(**brain), providers=Providers.offline(Fixtures(FIXTURES)),
                   store=MemoryStore(HashingEmbedder()), workdir=tmp_path, today=TODAY)


def test_pipeline_revises_a_wrong_chapter_and_prints(tmp_path):
    final = run(_runtime(tmp_path, wrong={2: False}))
    assert final["status"] == "printed", final["log"]
    assert (tmp_path / "05_interior" / "interior.pdf").exists()
    ch2 = json.loads((tmp_path / "chapters" / "02.check.json").read_text())
    assert ch2["passed"]
    assert any("cap. 2 rev. 1" in line for line in final["log"])  # it needed one revision
    for name in ("01_niche_report.json", "02_gap_report.json", "03_persona.json", "04_outline.json",
                 "sources.json", "run_log.json"):
        assert (tmp_path / name).exists(), name
    gaps = json.loads((tmp_path / "02_gap_report.json").read_text())
    assert gaps["reviews_considered"] == 3


def test_pipeline_blocks_a_book_it_cannot_prove(tmp_path):
    final = run(_runtime(tmp_path, wrong={3: True}))
    assert final["status"] == "blocked"
    assert json.loads((tmp_path / "blocked.json").read_text()) == {"chapters": [3]}
    assert not (tmp_path / "05_interior").exists()
    report = json.loads((tmp_path / "chapters" / "03.check.json").read_text())
    assert any(v["status"] == "number_mismatch" for v in report["verdicts"])


def test_resume_skips_verified_chapters(tmp_path):
    run(_runtime(tmp_path))
    rt = _runtime(tmp_path)
    final = run(rt)
    assert final["status"] == "printed"
    written = [name for name, _ in rt.llm.calls if name == "ChapterDraft"]
    assert written == []  # every chapter was already verified
