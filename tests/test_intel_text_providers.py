"""Deterministic pieces: Italian number handling and provider parsing."""

from __future__ import annotations

import json

import httpx
import pytest

from kdp_intel.authority import classify
from kdp_intel.providers.amazon_html import parse_bsr, parse_reviews
from kdp_intel.providers.apify import normalise
from kdp_intel.providers.base import ProviderError, parse_price
from kdp_intel.providers.dataforseo import DataForSEO
from kdp_intel.providers.helium10 import Helium10Export
from kdp_intel.providers.serpapi import SerpAPI
from kdp_intel.text import canonical_numbers_in, cites, law_refs, numbers, sentences, signals


def test_italian_numbers_are_canonical():
    assert numbers("La prestazione vale 1.401,53 euro al mese.") == ["1401.53"]
    assert numbers("Nel 2025 i decessi sono stati 652mila.") == ["2025", "652000"]
    assert numbers("oltre 1,2 milioni di caregiver") == ["1200000"]
    assert "652000" in canonical_numbers_in("essi sono stati 652mila, in linea")
    assert "16.4" in canonical_numbers_in("il 16,4% delle imprese")
    pdf = "la stabilisca entro 1 8 mesi; per le successioni aperte dal 1° gennaio 201 4"
    assert {"18", "2014"} <= canonical_numbers_in(pdf)


def test_prose_numbers_and_references_are_not_facts_to_match():
    assert numbers("I 3 errori più comuni") == []
    assert numbers("ai sensi dell'art. 13 della L. 132/2025") == []
    assert law_refs("art. 13 della L. 132/2025 e il D.Lgs. 139/2024") == ["art. 13", "L. 132/2025", "D.Lgs. 139/2024"]
    assert numbers("in 8 rate trimestrali") == ["8"]


def test_signals_and_sentences():
    text = "Dal 1° gennaio 2025 si paga entro 90 giorni [[S:agenziaentra-8bbe89]]. Poi si ricomincia."
    assert sentences(text)[0].endswith("[[S:agenziaentra-8bbe89]].")
    assert cites(text) == ["agenziaentra-8bbe89"]
    assert set(signals("Il 20% subito, ai sensi del D.Lgs. 139/2024, entro il 31 dicembre 2026")) == {
        "number", "percent", "law", "date"}
    assert signals("Il lettore deve capire cosa fare.") == []
    assert signals("Hai tre mesi per l'inventario.") == ["number_word"]  # quantities in words are facts
    assert signals("Se sei esonerato, firma qui.") == []  # "sei" as "you are"
    assert "law" in signals("Lo prevedono gli artt. 484 e 519 c.c.")
    assert sentences("Lo dice l'art. 13 del decreto. Poi altro.") == ["Lo dice l'art. 13 del decreto.", "Poi altro."]


def test_authority_tiers():
    assert classify("https://www.normattiva.it/x") == ("law", 1)
    assert classify("https://www.agenziaentrate.gov.it/x") == ("official", 1)
    assert classify("https://www.comune.bologna.gov.it/x") == ("official", 1)
    assert classify("https://www.regione.lazio.it/x") == ("official", 1)
    assert classify("https://asufc.sanita.fvg.it/x") == ("official", 1)
    assert classify("https://www.studiocataldi.it/x") == ("press", 2)
    assert classify("https://www.regionepiu.it/x") == ("other", 3)
    assert classify("https://www.ilsole24ore.com/x") == ("press", 2)
    assert classify("https://www.reddit.com/r/italy") == ("community", 3)
    assert classify("https://blog.example.com/x") == ("other", 3)


def test_prices():
    assert parse_price("19,90 €") == 19.9
    assert parse_price("1.234,56") == 1234.56
    assert parse_price(24.9) == 24.9
    assert parse_price("n.d.") is None


AMAZON_REVIEWS = """
<div id="cm_cr-review_list">
<div id="R1" data-hook="review" class="a-section review">
  <i data-hook="review-star-rating" class="a-icon"><span class="a-icon-alt">2,0 su 5 stelle</span></i>
  <a data-hook="review-title" href="#"><span>Non aggiornato</span></a> </div>
  <span data-hook="review-date">Recensito in Italia il 3 marzo 2025</span>
  <span data-hook="review-body" class="a-size-base"><span>Nulla sulla riforma del 2025.</span>
  </span> </div>
</div>
<div id="R2" data-hook="review" class="a-section review">
  <i data-hook="review-star-rating"><span class="a-icon-alt">3,0 su 5 stelle</span></i>
  <span data-hook="review-body"><span>Troppa teoria &amp; pochi esempi.</span>
  </span> </div>
</div>
</div>"""


def test_amazon_review_and_bsr_parsers():
    reviews = parse_reviews(AMAZON_REVIEWS, "B0X")
    assert [(r.rating, r.text) for r in reviews] == [(2, "Nulla sulla riforma del 2025."),
                                                     (3, "Troppa teoria & pochi esempi.")]
    page = ('<li><span class="a-text-bold">Posizione nella classifica Bestseller di Amazon:</span> '
            "n. 12.345 in Libri (Visualizza i Top 100 nella categoria Libri)</li>")
    assert parse_bsr(page) == 12345
    assert parse_bsr("<p>niente classifica</p>") is None


def test_apify_normalises_actor_aliases_and_drops_incomplete_records():
    out = normalise([
        {"ratingScore": 2, "reviewDescription": "Superficiale", "reviewTitle": "Mah"},
        {"rating": "3.0 out of 5 stars", "text": "Ok ma datato"},
        {"rating": None, "text": "senza voto"},
        {"stars": 1},
    ], asin="B0X")
    assert [(r.rating, r.text) for r in out] == [(2, "Superficiale"), (3, "Ok ma datato")]


def test_serpapi_pins_the_italian_market():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"interest_over_time": {"timeline_data": [
            {"date": "gen 2026", "values": [{"extracted_value": 42}]}]}})

    serp = SerpAPI("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    points = serp.interest_over_time("dichiarazione di successione")
    assert points[0].value == 42
    assert seen["geo"] == "IT" and seen["engine"] == "google_trends" and seen["hl"] == "it"


def test_dataforseo_reads_volumes_and_surfaces_task_errors():
    def ok(request):
        body = json.loads(request.content)
        assert body[0]["location_code"] == 2380 and body[0]["language_code"] == "it"
        return httpx.Response(200, json={"status_code": 20000, "tasks": [{"status_code": 20000, "result": [
            {"items": [{"keyword": "successione", "search_volume": 880}]}]}]})

    d = DataForSEO("u", "p", client=httpx.Client(transport=httpx.MockTransport(ok)))
    assert d.volumes(["successione"])[0].search_volume == 880

    def bad(request):
        return httpx.Response(200, json={"status_code": 20000, "tasks": [
            {"status_code": 40501, "status_message": "Invalid Field: 'location_code'."}]})

    d = DataForSEO("u", "p", client=httpx.Client(transport=httpx.MockTransport(bad)))
    with pytest.raises(ProviderError, match="location_code"):
        d.volumes(["successione"])


def test_helium10_export(tmp_path):
    csv = tmp_path / "cerebro.csv"
    csv.write_text("Keyword Phrase,Search Volume,Competing Products\n"
                   "dichiarazione di successione,\"2,900\",312\nsuccessione,880,>1000\n", encoding="utf-8")
    rows = Helium10Export(csv).volumes()
    assert [(r.keyword, r.search_volume) for r in rows] == [("dichiarazione di successione", 2900),
                                                            ("successione", 880)]
    bad = tmp_path / "other.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ProviderError, match="columns"):
        Helium10Export(bad).volumes()
