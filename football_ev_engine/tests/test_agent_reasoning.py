from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from src.agent.reasoning import Facts, TeamForm, template_reasoning, write_reasoning


def facts():
    t = lambda name, rank: TeamForm(name, "WWDLW", 10, 8, 4, 5.2, 4.6, 19, 3, rank)  # noqa: E731
    return Facts(home=t("Inter", 1), away=t("Genoa", 12), league="Serie A", selection="Home Win (1)",
                 xg_home=1.9, xg_away=0.8, p_model=0.56, p_dc=0.6, p_sharp=0.54, price=1.95, ev=0.092)


def test_template_uses_only_the_facts():
    text = template_reasoning(facts())
    assert text.count(". ") == 1 and text.endswith(".")  # two sentences
    for piece in ("Inter", "WWDLW", "rank 1st for defence", "rank 12th", "1.90-0.80", "56%", "54%", "+9.2%"):
        assert piece in text


class FakeClient:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self.response, self.error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking"),
                                                      SimpleNamespace(type="text", text=text)])


def test_claude_path_request_shape():
    client = FakeClient(reply("Inter are flying. The price is generous."))
    out = write_reasoning(facts(), language="Italian", client=client)
    assert out == "Inter are flying. The price is generous."
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["output_config"] == {"effort": "low"}
    assert "Italian" in kw["system"] and '"selection": "Home Win (1)"' in kw["messages"][0]["content"]


@pytest.mark.parametrize("client", [
    FakeClient(reply("", "refusal")),
    FakeClient(reply("   ")),
    FakeClient(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))),
])
def test_claude_failures_fall_back_to_template(client):
    assert write_reasoning(facts(), client=client) == template_reasoning(facts())
