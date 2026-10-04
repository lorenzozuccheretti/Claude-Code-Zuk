"""The zero-cost stack: free model backends, free data providers, hand-off,
cover specification for Canva."""

from __future__ import annotations

import json

import httpx
import pytest
from pypdf import PdfReader

from kdp_intel.cover import check_cover, cover_spec, write_spec
from kdp_intel.graph import Runtime, run
from kdp_intel.llm import LLMError, ScriptedLLM, inline_refs, strict_schema
from kdp_intel.llm_free import CachedLLM, GeminiLLM, OpenAICompatLLM, PendingLLM, task_id
from kdp_intel.models import Outline, PersonaDraft
from kdp_intel.providers import Providers
from kdp_intel.providers.amazon_html import parse_search_results
from kdp_intel.providers.base import ProviderError
from kdp_intel.providers.fixtures import Fixtures
from kdp_intel.providers.free import DuckDuckGoSearch, SavedAmazonPages, Suggestions, TavilySearch, TrendsCSV
from kdp_intel.rag.embeddings import HashingEmbedder
from kdp_intel.rag.store import MemoryStore

from intel_support import FIXTURES, TODAY, brain, project

PERSONA = {"name": "Laura", "demographics": "48 anni", "competence_level": "base", "vocabulary": ["CAF"],
           "frustrations": ["costi"], "tried_and_failed": ["articoli"], "desired_outcome": "chiudere"}


def mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ model backends

def test_inlined_schema_has_no_references():
    schema = inline_refs(strict_schema(Outline))
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    chapter = schema["properties"]["chapters"]["items"]
    assert chapter["additionalProperties"] is False and "queries" in chapter["required"]


def test_handoff_writes_a_request_then_reads_the_answer(tmp_path):
    llm = CachedLLM(None, tmp_path)
    with pytest.raises(PendingLLM) as waiting:
        llm.structured(system="Sei un ricercatore.", prompt="Costruisci la persona.", schema=PersonaDraft)
    tid = waiting.value.task_ids[0]
    request = (tmp_path / f"{tid}.request.md").read_text()
    assert "Costruisci la persona." in request and '"desired_outcome"' in request
    assert llm.pending() == [tmp_path / f"{tid}.request.md"]
    (tmp_path / f"{tid}.json").write_text("```json\n" + json.dumps(PERSONA) + "\n```")
    answer = llm.structured(system="Sei un ricercatore.", prompt="Costruisci la persona.", schema=PersonaDraft)
    assert answer.name == "Laura" and llm.pending() == []


def test_handoff_rejects_an_answer_that_breaks_the_schema(tmp_path):
    llm = CachedLLM(None, tmp_path)
    tid = task_id(PersonaDraft, "s", "p")
    (tmp_path / f"{tid}.json").write_text('{"name": "solo il nome"}')
    with pytest.raises(LLMError, match="non rispetta"):
        llm.structured(system="s", prompt="p", schema=PersonaDraft)


def test_cache_never_asks_twice(tmp_path):
    inner = ScriptedLLM(lambda schema, s, p: PERSONA)
    llm = CachedLLM(inner, tmp_path)
    for _ in range(3):
        llm.structured(system="s", prompt="p", schema=PersonaDraft)
    assert len(inner.calls) == 1 and llm.hits == 2


def test_gemini_request_shape_and_rate_limit_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "3s"}]}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "ragiono...", "thought": True}, {"text": json.dumps(PERSONA)}]}}]})

    llm = GeminiLLM("key", model="gemini-3.8-flash", rpm=600, client=mock(handler))
    out = llm.structured(system="s", prompt="p", schema=PersonaDraft)
    assert out.name == "Laura" and len(seen) == 2
    body = json.loads(seen[-1].content)
    assert seen[-1].headers["x-goog-api-key"] == "key"
    assert "gemini-3.8-flash:generateContent" in str(seen[-1].url)
    fmt = body["generationConfig"]["responseFormat"]["text"]
    assert fmt["mimeType"] == "application/json" and "$ref" not in json.dumps(fmt["schema"])


def test_gemini_daily_quota_stops_cleanly(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    llm = GeminiLLM("key", rpm=600, client=mock(lambda r: httpx.Response(
        429, text='{"error": {"message": "Quota exceeded for GenerateRequestsPerDayPerProjectPerModel"}}')))
    with pytest.raises(LLMError, match="domani"):
        llm.structured(system="s", prompt="p", schema=PersonaDraft)


def test_openai_compatible_falls_back_to_json_object_and_repairs(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    formats, replies = [], iter(['{"name": "incompleto"}', json.dumps(PERSONA)])

    def handler(request):
        body = json.loads(request.content)
        formats.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    llm = OpenAICompatLLM("https://openrouter.ai/api/v1", "openrouter/free", "k", client=mock(handler), rpm=600)
    assert llm.structured(system="s", prompt="p", schema=PersonaDraft).name == "Laura"
    assert formats == ["json_schema", "json_object", "json_object"]  # downgrade, then one repair


# ------------------------------------------------------------------ free data providers

def test_tavily_turns_site_filters_into_include_domains():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://www.inps.it/x",
                                                      "content": "c", "published_date": "2026-01-01"}]})

    results = TavilySearch("tvly-x", client=mock(handler)).search("prestazione universale site:inps.it", 3)
    assert seen["include_domains"] == ["inps.it"] and seen["query"] == "prestazione universale"
    assert seen["country"] == "italy" and results[0].url == "https://www.inps.it/x"


def test_duckduckgo_parses_results_and_refuses_challenge_pages():
    page = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.inps.it%2Fa&rut=x">'
            'INPS <b>accompagnamento</b></a><a class="result__snippet" href="#">Come si chiede</a>')
    ok = DuckDuckGoSearch(client=mock(lambda r: httpx.Response(200, text=page))).search("x")
    assert ok[0].url == "https://www.inps.it/a" and ok[0].title == "INPS accompagnamento"
    with pytest.raises(ProviderError):
        DuckDuckGoSearch(client=mock(lambda r: httpx.Response(202, text="challenge"))).search("x")


def test_autocomplete_is_a_free_demand_proxy(tmp_path):
    def handler(request):
        q = request.url.params.get("prefix") or request.url.params.get("q")
        if "amazon" in str(request.url):
            return httpx.Response(200, json={"suggestions": [{"value": q.strip()},
                                                             {"value": f"{q.strip()} libro"}]})
        return httpx.Response(200, json=[q, [q.strip(), f"{q.strip()} online", f"{q.strip()} modulo"]])

    s = Suggestions(client=mock(handler), folder=tmp_path)
    [m] = s.volumes(["dichiarazione di successione"])
    assert m.search_volume is None and m.on_amazon is True
    assert m.suggestions == 3  # libro, online, modulo
    assert list(tmp_path.glob("*.json"))  # cached: a rerun makes no request


def test_trends_csv_export_is_read_per_term(tmp_path):
    (tmp_path / "multiTimeline.csv").write_text(
        "Categoria: Tutte le categorie\n\nSettimana,dichiarazione di successione: (Italia),"
        "successione: (Italia)\n2025-10-05,45,80\n2025-10-12,<1,82\n", encoding="utf-8")
    t = TrendsCSV(tmp_path)
    assert [p.value for p in t.interest_over_time("Dichiarazione di successione")] == [45, 0]
    assert len(t.interest_over_time("successione")) == 2


SEARCH_PAGE = """<html><head><title>Amazon.it : dichiarazione di successione</title></head><body>
<div data-asin="B0AAAAAAA1" data-index="1" data-component-type="s-search-result" class="s-result-item">
 <h2 class="a-size-base-plus"><span>Successioni e donazioni 2026</span></h2>
 <span class="a-icon-alt">3,6 su 5 stelle</span>
 <a href="#" aria-label="41 valutazioni"><span class="s-underline-text">41</span></a>
 <span class="a-price"><span class="a-offscreen">24,90&nbsp;€</span></span>
</div>
<div data-asin="B0AAAAAAA2" data-index="2" data-component-type="s-search-result" class="s-result-item">
 <h2><span>Guida alla successione</span></h2><span class="a-icon-alt">4,1 su 5 stelle</span>
 <span class="s-underline-text">1.204</span><span class="a-offscreen">14,90 €</span>
</div></body></html>"""


def test_saved_amazon_pages(tmp_path):
    comps = parse_search_results(SEARCH_PAGE)
    assert [(c.asin, c.rating, c.reviews, c.price_eur) for c in comps] == [
        ("B0AAAAAAA1", 3.6, 41, 24.9), ("B0AAAAAAA2", 4.1, 1204, 14.9)]
    (tmp_path / "ricerca.html").write_text(SEARCH_PAGE, encoding="utf-8")
    (tmp_path / "prodotto.html").write_text(
        "<p>ASIN B0AAAAAAA1</p><span>Posizione nella classifica Bestseller di Amazon: n. 23.456 in Libri</span>",
        encoding="utf-8")
    saved = SavedAmazonPages(tmp_path)
    assert len(saved.search_books("dichiarazione di successione")) == 2
    assert saved.search_books("badante convivente") == []  # a saved search for another keyword
    assert saved.bsr("B0AAAAAAA1") == 23456 and saved.bsr("B0AAAAAAA2") is None


# ------------------------------------------------------------------ pipeline, free mode

def _rt(tmp_path, llm, proj=None, **kw):
    return Runtime(project=proj or project(), llm=llm, providers=Providers.offline(Fixtures(FIXTURES)),
                   store=MemoryStore(HashingEmbedder()), workdir=tmp_path, today=TODAY, **kw)


def test_handoff_pipeline_waits_then_replays_for_free(tmp_path):
    cache = tmp_path / "llm_cache"
    first = run(_rt(tmp_path, CachedLLM(None, cache)))
    assert first["status"] == "waiting" and first["pending"]
    # Whoever answers the hand-off (here: the scripted model) fills the cache...
    answered = run(_rt(tmp_path, CachedLLM(ScriptedLLM(brain()), cache)))
    assert answered["status"] == "printed"
    # ...and from then on the whole book replays with no model at all.
    replay = CachedLLM(None, cache)
    assert run(_rt(tmp_path, replay))["status"] == "printed" and replay.calls == []


def test_outline_can_be_approved_before_writing(tmp_path):
    final = run(_rt(tmp_path, ScriptedLLM(brain()), project(with_outline=False), stop_after="architect"))
    assert final["status"] == "outline_ready"
    assert (tmp_path / "04_outline.json").exists() and not (tmp_path / "chapters").exists()


def test_placeholder_author_never_reaches_print(tmp_path):
    proj = project()
    proj.book.author = "Autore da definire"
    final = run(_rt(tmp_path, ScriptedLLM(brain()), proj))
    assert final["status"] == "proof" and any("book.author" in line for line in final["log"])


# ------------------------------------------------------------------ cover for Canva

def test_cover_spec_guide_and_check(tmp_path):
    run(_rt(tmp_path, ScriptedLLM(brain())))
    interior = tmp_path / "05_interior" / "interior.pdf"
    cs = cover_spec(interior)
    pages = len(PdfReader(str(interior)).pages)
    g = cs.geometry
    assert g.page_count == pages and abs(g.spine_width - pages * 0.002252) < 1e-9
    assert abs(g.width - (2 * 6 + g.spine_width + 2 * 0.125)) < 1e-9 and g.height == 9.25
    x, y, w, h = cs.barcode_box_in
    assert x + w <= g.spine_x and x >= g.back_panel_x  # on the back cover, clear of the spine
    paths = write_spec(cs, tmp_path / "06_cover")
    assert check_cover(paths["guide_pdf"], cs) == []  # the guide itself has the exact size
    spec_json = json.loads(paths["spec"].read_text())
    assert spec_json["canva"]["testo_sul_dorso"].startswith("vietato")  # < 100 pages
    from reportlab.pdfgen import canvas

    wrong = tmp_path / "wrong.pdf"
    c = canvas.Canvas(str(wrong), pagesize=((g.width + 1) * 72, (g.height + 1) * 72))
    c.showPage()
    c.save()
    assert "segni di taglio" in check_cover(wrong, cs)[0]
