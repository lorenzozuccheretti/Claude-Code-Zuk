"""The reader's words without Reddit: Google questions and forum pages."""

from kdp_intel.agents.architect import Architect
from kdp_intel.llm_free import PendingLLM
from kdp_intel.models import GapReport, PersonaDraft, WebHit
from kdp_intel.providers.voices import forum_queries, forum_specs, is_forum_candidate, reader_questions


class FakeSuggest:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def google(self, prefix):
        self.asked.append(prefix)
        if prefix == "amministratore di sostegno chi":
            raise RuntimeError("429")
        return self.answers.get(prefix, [])


def test_reader_questions_keep_only_phrases_about_the_keyword():
    suggest = FakeSuggest({
        "amministratore di sostegno quanto": ["amministratore di sostegno quanto costa",
                                              "Amministratore di sostegno  quanto costa", "meteo roma"],
        "come amministratore di sostegno": ["come diventare amministratore di sostegno di mia madre"],
        "amministratore di sostegno si può": ["amministratore di sostegno"],
    })
    got = reader_questions(suggest, ["Amministratore di sostegno"])
    assert got == ["amministratore di sostegno quanto costa",
                   "come diventare amministratore di sostegno di mia madre"]
    assert "quanto costa amministratore di sostegno" in suggest.asked  # stems before the keyword too


def test_reader_questions_stop_at_the_limit():
    suggest = FakeSuggest({"tfr come": [f"tfr come {i}" for i in range(10)]})
    assert len(reader_questions(suggest, ["tfr"], limit=4)) == 4


def test_forum_candidates_exclude_official_press_and_shops():
    assert is_forum_candidate("https://www.alfemminile.com/forum/f123/amministratore.html")
    assert not is_forum_candidate("https://www.inps.it/x")
    assert not is_forum_candidate("https://www.altroconsumo.it/x")
    assert not is_forum_candidate("https://www.amazon.it/dp/123")
    assert not is_forum_candidate("https://www.youtube.com/watch?v=1")
    assert forum_queries("tfr") == ["tfr forum", "tfr esperienze", "tfr consiglio"]


class FakeSearch:
    def search(self, query, num=5):
        return [WebHit(title="Forum", url=f"https://forum.example.it/{query.split()[-1]}", snippet="", published=""),
                WebHit(title="INPS", url="https://www.inps.it/x", snippet="", published="")]


def test_forum_specs_are_community_sources():
    specs = forum_specs(FakeSearch(), ["tfr forum", "tfr esperienze"])
    assert [s.url for s in specs] == ["https://forum.example.it/forum", "https://forum.example.it/esperienze"]
    assert {s.kind for s in specs} == {"community"}


class HandoffSearch:
    def __init__(self, folder):
        self.folder = folder

    def search(self, query, num=5):
        raise PendingLLM([f"webresults-{query.split()[-1]}"], self.folder)


def test_forum_specs_ask_every_handoff_search_at_once(tmp_path):
    try:
        forum_specs(HandoffSearch(tmp_path), forum_queries("tfr"))
    except PendingLLM as pending:
        assert pending.task_ids == ["webresults-forum", "webresults-esperienze", "webresults-consiglio"]
    else:
        raise AssertionError("a hand-off search must wait")


class RecordingLLM:
    def __init__(self):
        self.prompts = []

    def structured(self, system, prompt, schema, effort="medium"):
        self.prompts.append((system, prompt))
        return PersonaDraft(name="n", demographics="d", competence_level="c", vocabulary=[],
                            frustrations=[], tried_and_failed=[], desired_outcome="o")


class EmptyStore:
    def query(self, *args, **kwargs):
        return []


def test_persona_reads_the_google_questions():
    llm = RecordingLLM()
    Architect(llm, EmptyStore()).persona("Nicchia", ["amministratore di sostegno"],
                                         GapReport(reviews_considered=0, themes=[], missing_content=[],
                                                   opportunity_statements=[]),
                                         ["amministratore di sostegno può essere anche badante"])
    system, prompt = llm.prompts[0]
    assert "domande digitate su" in system.lower()
    assert "- amministratore di sostegno può essere anche badante" in prompt


class StoreWithOfficialFirst:
    """Ten official chunks outrank the one forum thread on the query."""

    def query(self, text, k=8, **kwargs):
        from kdp_intel.models import Chunk, Hit
        def chunk(i, tier, kind):
            return Chunk(id=f"c{i}", source_id=f"s{i}", ordinal=0, text=f"testo {i}", url=f"https://x{i}.it",
                         title="t", publisher="p", kind=kind, authority=tier)
        hits = [Hit(chunk=chunk(i, 1, "official"), score=1.0 - i / 100) for i in range(10)]
        hits.append(Hit(chunk=chunk(99, 3, "community"), score=0.1))
        return hits[:k]


def test_persona_sees_forum_threads_even_when_official_pages_rank_higher():
    llm = RecordingLLM()
    Architect(llm, StoreWithOfficialFirst()).persona("Nicchia", ["kw"], None, [])
    assert "testo 99" in llm.prompts[0][1]
