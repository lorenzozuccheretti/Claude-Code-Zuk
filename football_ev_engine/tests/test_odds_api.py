import asyncio
from datetime import datetime

import httpx
import pytest
import respx

from src.config import Settings, get_league
from src.data.ingest_odds_api import (
    Event, OddsApiClient, QuotaExhausted, events_to_frames, last_known_remaining, store_snapshot,
)
from tests.synthetic import odds_api_events

BASE = "https://api.the-odds-api.com/v4"
HEADERS = {"x-requests-used": "12", "x-requests-remaining": "488", "x-requests-last": "2"}


def settings(**kw):
    return Settings(_env_file=None, odds_api_key="k", **kw)


def run(coro):
    return asyncio.run(coro)


def test_requires_key(con):
    with pytest.raises(ValueError):
        OddsApiClient(Settings(_env_file=None, odds_api_key=""), con)


@respx.mock
def test_odds_request_params_and_quota_tracking(con):
    events = odds_api_events([("Inter Milan", "AC Milan")], datetime(2030, 1, 5, 18))
    route = respx.get(f"{BASE}/sports/soccer_italy_serie_a/odds").mock(
        return_value=httpx.Response(200, json=events, headers=HEADERS))

    async def go():
        async with OddsApiClient(settings(), con) as api:
            return await api.odds("soccer_italy_serie_a"), api.quota

    parsed, quota = run(go())
    params = route.calls.last.request.url.params
    assert params["regions"] == "eu" and params["markets"] == "h2h,totals"
    assert params["oddsFormat"] == "decimal" and params["dateFormat"] == "iso" and params["apiKey"] == "k"
    assert parsed[0].home_team == "Inter Milan"
    assert (quota.remaining, quota.used, quota.last_cost) == (488, 12, 2)
    assert last_known_remaining(con) == 488


@respx.mock
def test_refuses_below_floor(con):
    con.execute("INSERT INTO api_usage VALUES (now(), 'x', 200, 490, 10, 2)")
    route = respx.get(f"{BASE}/sports/soccer_epl/odds")

    async def go():
        async with OddsApiClient(settings(odds_api_min_remaining=20), con) as api:
            await api.odds("soccer_epl")

    with pytest.raises(QuotaExhausted):
        run(go())
    assert not route.called


@respx.mock
def test_retries_on_429(con):
    route = respx.get(f"{BASE}/sports").mock(side_effect=[
        httpx.Response(429), httpx.Response(200, json=[{"key": "soccer_epl", "active": True}], headers=HEADERS)])

    async def go():
        async with OddsApiClient(settings(), con, backoff_seconds=0) as api:
            return await api.sports()

    assert run(go())[0].key == "soccer_epl"
    assert route.call_count == 2
    assert con.execute("SELECT count(*) FROM api_usage").fetchone()[0] == 2


@respx.mock
def test_bad_key(con):
    respx.get(f"{BASE}/sports").mock(return_value=httpx.Response(401))

    async def go():
        async with OddsApiClient(settings(), con) as api:
            await api.sports()

    with pytest.raises(PermissionError):
        run(go())


def test_flatten_and_store(con):
    raw = odds_api_events([("Inter Milan", "AC Milan"), ("AS Roma", "SS Lazio")], datetime(2030, 1, 5, 18))
    events = [Event.model_validate(e) for e in raw]
    fixtures, odds = events_to_frames(events, get_league("serie-a"))
    assert len(fixtures) == 2 and fixtures["league"].eq("I1").all()
    assert fixtures["commence_time"].iloc[0] == datetime(2030, 1, 5, 18)
    # 2 events x 3 books x (3 h2h + 2 totals)
    assert len(odds) == 30
    assert set(odds["outcome"]) == {"home", "draw", "away", "over", "under"}
    assert odds.loc[odds["market"] == "h2h", "point"].eq(0).all()
    store_snapshot(con, fixtures, odds)
    store_snapshot(con, fixtures, odds.iloc[:5])  # a re-fetch replaces, not appends
    assert con.execute("SELECT count(*) FROM odds").fetchone()[0] == 5
    assert con.execute("SELECT count(*) FROM fixtures").fetchone()[0] == 2
