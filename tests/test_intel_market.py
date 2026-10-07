"""Amazon.it search exports (DeepView and similar) as competition and market data."""

from datetime import date
from pathlib import Path

import pytest

from kdp_intel.agents.scout import Gates, Scout
from kdp_intel.providers.base import ProviderError
from kdp_intel.providers.deepview import AmazonExports, is_about, parse_export
from intel_support import llm
from test_intel_autopilot import market

FOLDER = Path(__file__).parent / "fixtures_intel" / "market"
TODAY = date(2026, 10, 7)


def test_export_keeps_books_and_reads_numbers():
    keyword, books = parse_export(FOLDER / "deepview-dichiarazione-di-successione.csv")
    assert keyword == "dichiarazione di successione"  # from the URLs' keywords= parameter
    assert [b.asin for b in books] == ["8891600001", "8891600002", "B0SELFPUB1", "8891600004"]  # no t-shirt
    first, second = books[0], books[1]
    assert first.title == "La dichiarazione di successione"  # byline markup dropped
    assert first.price_eur == 24.9 and first.bsr == 80000 and first.est_sales_month == 9
    assert second.price_eur == 32.5  # Italian decimal comma
    assert books[2].self_published


def test_on_topic_needs_every_word_of_the_keyword():
    assert is_about("Successione e dichiarazione: guida pratica", "dichiarazione di successione")
    assert not is_about("Concorso Agenzia Entrate: manuale completo", "dichiarazione di successione")
    assert is_about("Manuale dell'amministrazione di sostegno", "amministratore di sostegno")


def test_summary_counts_only_on_topic_books():
    m = AmazonExports(FOLDER, TODAY).summary("Dichiarazione di successione")
    assert m.books == 4 and m.on_topic == 3  # the exam manual sells more but is another topic
    assert m.est_sales_month == 16 and m.est_royalty_month == 107
    assert m.median_price_eur == 24.9 and m.best_bsr == 80000
    assert m.recent_titles == 2 and m.self_published == 1


def test_scout_prefers_amazon_to_the_catalogue():
    fx = market()
    scout = Scout(llm(), fx, fx, gates=Gates(max_recent_titles=1), today=TODAY,
                  market=AmazonExports(FOLDER, TODAY))
    idea = scout.propose(scout.harvest(["dichiarazione di successione", "imposta di successione"]), [])[0]
    a = scout.assess(idea, [], fetch_sources=False)
    assert a.primary_keyword == "dichiarazione di successione"
    assert a.market is not None and a.market.on_topic == 3
    gate = a.gates[-1]
    assert gate.reasons[0].startswith("Amazon.it (deepview-dichiarazione-di-successione.csv): 3 titoli")
    assert "vendite stimate 16 copie/mese" in gate.reasons[1]
    assert not gate.passed  # two recent titles, one allowed


def test_a_file_that_is_not_an_export_fails_loudly(tmp_path):
    bad = tmp_path / "x.csv"
    bad.write_text("Keyword,Volume\nfoo,1\n", encoding="utf-8")
    with pytest.raises(ProviderError, match="colonne"):
        parse_export(bad)
