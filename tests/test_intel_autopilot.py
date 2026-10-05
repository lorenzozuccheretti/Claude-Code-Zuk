"""The autopilot: topic from seeds, gates, listing, book - offline and deterministic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kdp_intel.agents.publisher import listing_problems, tidy_keywords
from kdp_intel.agents.scout import Gates, Scout
from kdp_intel.autopilot import Autopilot, AutopilotProfile, slugify
from kdp_intel.config import Credentials
from kdp_intel.llm_free import CachedLLM, PendingLLM
from kdp_intel.models import ListingDraft
from kdp_intel.providers import Providers
from kdp_intel.providers.catalog import parse_ibs
from kdp_intel.providers.fixtures import Fixtures
from kdp_intel.providers.free import HandoffSearch

from intel_support import ADE_URL, FIXTURES, ISTAT_URL, PRESS_URL, TODAY, llm

TYPED = ["dichiarazione di successione", "dichiarazione di successione 2026", "dichiarazione di successione guida",
         "dichiarazione di successione libro", "dichiarazione di successione fai da te",
         "dichiarazione di successione online", "imposta di successione", "imposta di successione 2026",
         "imposta di successione calcolo", "imposta di successione rate", "imposta di successione franchigia"]


def market(**overrides) -> Fixtures:
    data = json.loads(Path(FIXTURES).read_text(encoding="utf-8"))
    data["suggest"] = {"amazon": TYPED, "google": ["dichiarazione di successione costo"]}
    data["catalog"] = {"dichiarazione di successione": {"total": 27, "books": [
        {"title": "Guida alla dichiarazione di successione", "publisher": "Il Sole 24 Ore", "year": 2010},
        {"title": "Dichiarazione di successione", "publisher": "Maggioli", "year": 2025}]}}
    rows = [{"title": "Dichiarazione di successione", "url": ADE_URL, "snippet": "entro 12 mesi"},
            {"title": "Indicatori demografici", "url": ISTAT_URL, "snippet": "decessi 2025"},
            {"title": "Sblocco del conto", "url": PRESS_URL, "snippet": "eredi"}]
    data["search"] = {"dichiarazione di successione": rows}
    data.update(overrides)
    return Fixtures(data)


def pilot(tmp_path, gates: Gates | None = None, **profile) -> Autopilot:
    prof = AutopilotProfile(name="test", author="Test Autore", publisher="Test Editore",
                            seeds=["dichiarazione di successione", "imposta di successione"],
                            verify_domains=["agenziaentrate.gov.it"], gates=gates or Gates(min_official_sources=1),
                            chapters=5, **profile)
    brain = llm()
    return Autopilot(prof, tmp_path, lambda folder: brain, Credentials(), TODAY, store="memory",
                     providers=Providers.offline(market()))


def test_autopilot_goes_from_seeds_to_a_printed_book(tmp_path):
    result = pilot(tmp_path).run()
    assert result["status"] == "printed", "\n".join(result["log"])
    assert Path(result["pdf"]).exists() and Path(result["listing"]).exists()
    assert result["cover"]  # the Canva guide for the final page count

    project = yaml.safe_load(Path(result["project"]).read_text(encoding="utf-8"))
    assert project["slug"] == "dichiarazione-di-successione-2026"
    assert project["book"]["title"] == "Dichiarazione di successione"  # from the listing
    assert {s["url"] for s in project["sources"]} == {ADE_URL, ISTAT_URL, PRESS_URL}  # tier 1-2, fetched
    assert project["research"]["niches"]["Successione dopo la riforma"][0] == "dichiarazione di successione"

    run_dir = tmp_path / ".kdp_intel" / "autopilot" / "test"
    assessments = json.loads((run_dir / "02_assessments.json").read_text(encoding="utf-8"))
    assert [a["idea"]["name"] for a in assessments] == ["Successione dopo la riforma"]  # «Cucina» never typed
    gates = {g["name"]: g["passed"] for g in assessments[0]["gates"]}
    assert gates == {"keyword": True, "competition": True, "sources": True}
    assert any("non digitate da nessuno" in line for line in result["log"])  # «successione senza notaio»

    listing = json.loads((Path(result["listing"]).parent / "07_listing.json").read_text(encoding="utf-8"))
    boxes = listing["listing"]["backend_keywords"]
    assert not any("bestseller" in b for b in boxes)  # dropped: Amazon rejects it
    assert "dichiarazione di successione" not in boxes  # dropped: already indexed through the title
    assert listing["problems"] == ["solo 6 keyword nascoste su 7"]  # said, not hidden


def test_autopilot_stops_when_no_topic_passes(tmp_path):
    result = pilot(tmp_path, gates=Gates(min_official_sources=99)).run()
    assert result["status"] == "no_topic"
    assert not (tmp_path / "intel_projects").exists()  # nothing written, nothing printed
    assert any("sources: NO" in line for line in result["log"])


def test_competition_gate_rejects_a_crowded_keyword():
    crowded = market(catalog={"dichiarazione di successione": {"total": 300, "books": [
        {"title": f"Libro {i}", "year": 2026} for i in range(20)]}})
    scout = Scout(llm(), crowded, crowded, gates=Gates(max_recent_titles=12), today=TODAY)
    idea = scout.propose(scout.harvest(["dichiarazione di successione", "imposta di successione"]), [])[0]
    a = scout.assess(idea, [], fetch_sources=False)
    assert a.primary_keyword == "dichiarazione di successione"
    assert not a.passed and "20 degli ultimi 2 anni" in a.gates[-1].reasons[0]


def test_keyword_gate_needs_amazon_and_a_long_tail():
    fx = market()
    scout = Scout(llm(), fx, fx, today=TODAY)
    ok = scout.check_keyword("dichiarazione di successione")
    assert ok.passed and ok.amazon_prefix.strip() == "dichiarazione" and ok.longtail >= 5
    google_only = scout.check_keyword("dichiarazione di successione costo")
    assert not google_only.passed and "non la suggerisce" in google_only.notes[0]


def test_listing_rules():
    good = ListingDraft(title="Dichiarazione di successione", subtitle="La guida 2026",
                        description="x" * 800, backend_keywords=[f"chiave {i}" for i in range(7)],
                        categories=["Libri > Diritto", "Libri > Fisco"])
    assert listing_problems(good, "dichiarazione di successione") == []
    bad = good.model_copy(update={"title": "Guida bestseller", "description": "corta"})
    problems = listing_problems(bad, "dichiarazione di successione")
    assert any("keyword principale" in p for p in problems)
    assert any("non ammessi" in p for p in problems) and any("descrizione" in p for p in problems)
    tidy = tidy_keywords(good.model_copy(update={"backend_keywords": ["successione", "x" * 60, "rate eredi"]}))
    assert tidy.backend_keywords == ["rate eredi"]  # repeats the title / too long


def test_handoff_search_asks_then_answers(tmp_path):
    cache = CachedLLM(None, tmp_path)
    search = HandoffSearch(cache)
    with pytest.raises(PendingLLM) as pending:
        search.search("dichiarazione di successione site:agenziaentrate.gov.it", 3)
    task = pending.value.task_ids[0]
    assert "site:agenziaentrate.gov.it" in (tmp_path / f"{task}.request.md").read_text(encoding="utf-8")
    (tmp_path / f"{task}.json").write_text(json.dumps({"results": [
        {"title": "ADE", "url": ADE_URL, "snippet": "", "published": ""},
        {"title": "non un URL", "url": "agenziaentrate", "snippet": "", "published": ""}]}), encoding="utf-8")
    assert [r.url for r in search.search("dichiarazione di successione site:agenziaentrate.gov.it", 3)] == [ADE_URL]


def test_ibs_catalogue_parser():
    total, books = parse_ibs((Path(FIXTURES).parent / "ibs_search.html").read_text(encoding="utf-8"))
    assert total == 27
    assert books[0].title == "Guida alla dichiarazione di successione. Con CD-ROM"
    assert (books[0].publisher, books[0].year, books[0].price_eur) == ("Il Sole 24 Ore", 2010, 64.6)
    assert all(b.url.startswith("https://www.ibs.it/") for b in books)


def test_profile_reads_numeric_seeds_as_text(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("seeds: [730, pensione]\nexclude: [104]\n", encoding="utf-8")
    profile = AutopilotProfile.load(path)
    assert profile.seeds == ["730", "pensione"] and profile.exclude == ["104"]


def test_slugify():
    assert slugify("Invalidità civile: la domanda") == "invalidita-civile-la-domanda"
