import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from src.agent.selector import Pick
from src.agent.store import TrackRecord
from src.agent.telegram import TelegramClient, TelegramError, format_no_picks, format_pick, format_record

URL = "https://api.telegram.org/botTOKEN/sendMessage"


def pick(**kw):
    base = dict(fixture_key="k", event_id="e", league="I1", league_name="Serie A", home_team="Brighton & Hove",
                away_team="<Juve>", commence_time=datetime(2030, 7, 1, 18, 45), market="h2h", outcome="home",
                point=0.0, bookmaker="tipico_de", price=1.95, p_model=0.562, p_dc=0.6, p_sharp=0.54, ev=0.0959,
                stake_pct=0.015, reasoning="Form is \"good\" & improving.")
    base.update(kw)
    return Pick(**base)


def test_format_pick_matches_spec_and_escapes():
    text = format_pick(pick(), ZoneInfo("Europe/Rome"))
    assert "⚽ <b>Brighton &amp; Hove vs &lt;Juve&gt;</b> - Serie A" in text
    assert "20:45" in text  # 18:45 UTC in Rome summer time
    assert "Home Win (1)" in text and "1.95</b> (Tipico)" in text
    assert "56.2%</b> (fair odds 1.78)" in text and "+9.6%" in text and "1.5%</b> of bankroll" in text
    assert "💡 <i>Form is \"good\" &amp; improving.</i>" in text


def test_record_and_empty_messages():
    assert "none settled" in format_record(TrackRecord(2, 0, 0, 0.0, None))
    assert "3/5 won, +1.20u (yield +24.0%), avg CLV -1.5%" in format_record(TrackRecord(5, 5, 3, 1.2, -0.015))
    assert "12 fixtures scanned" in format_no_picks(datetime(2030, 1, 1, 9), ZoneInfo("Europe/Rome"), 12, 0)


@respx.mock
def test_send_ok_and_payload():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 7}}))
    assert asyncio.run(TelegramClient("TOKEN", "123").send("<b>hi</b>")) == 7
    body = route.calls.last.request.read()
    assert b'"parse_mode":"HTML"' in body.replace(b" ", b"") and b'"chat_id":"123"' in body.replace(b" ", b"")


@respx.mock
def test_send_retries_429_then_raises_without_token():
    respx.post(URL).mock(side_effect=[
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}),
        httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"}),
    ])
    with pytest.raises(TelegramError) as exc:
        asyncio.run(TelegramClient("TOKEN", "123").send("x"))
    assert "chat not found" in str(exc.value) and "TOKEN" not in str(exc.value)
