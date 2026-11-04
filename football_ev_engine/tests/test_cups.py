"""European cups: scanned and priced from Pinnacle, with no goal model."""

from datetime import timedelta

import httpx
import pandas as pd
import respx

from src.agent.closing import due_picks
from src.agent.runner import run_daily
from src.config import FREE_CLOSE, MODELLED, get_league
from src.data.database import upsert_df
from src.data.live_results import sports_to_refresh
from tests.test_agent_runner import API, NOW, QUOTA, settings, tg_ok  # noqa: F401 - fixture
from tests.test_closing import pick

UCL = "soccer_uefa_champs_league"


def cup_event(kick, home="Inter Milan", away="Real Madrid", soft_home=2.20):
    def book(key, home_price, draw, away_price):
        return {"key": key, "title": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": home_price}, {"name": "Draw", "price": draw},
                                        {"name": away, "price": away_price}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.90, "point": 2.5},
                                           {"name": "Under", "price": 1.90, "point": 2.5}]}]}
    # Pinnacle's fair home price is 48.0%; 2.20 elsewhere is a +5.7% edge.
    return {"id": "ucl1", "sport_key": UCL, "commence_time": kick.isoformat() + "Z", "home_team": home,
            "away_team": away, "bookmakers": [book("pinnacle", 2.00, 3.60, 3.80),
                                              book("unibet_fr", soft_home, 3.40, 3.50)]}


def mock_cup(event):
    respx.get(f"{API}/sports").mock(return_value=httpx.Response(200, json=[{"key": UCL, "active": True}],
                                                                 headers=QUOTA))
    respx.get(f"{API}/sports/{UCL}/events").mock(return_value=httpx.Response(200, json=[
        {k: event[k] for k in ("id", "sport_key", "commence_time", "home_team", "away_team")}], headers=QUOTA))
    respx.get(f"{API}/sports/{UCL}/odds").mock(return_value=httpx.Response(200, json=[event], headers=QUOTA))


def agent_rules(s):
    return s.model_copy(update={"min_ev": 0.035, "min_odds": 1.75, "max_odds": 2.25})


def test_cups_are_market_priced_competitions():
    for slug in ("champions-league", "europa-league", "conference-league"):
        lg = get_league(slug)
        assert lg.source == "market" and lg.fd_code not in MODELLED and lg.fd_code not in FREE_CLOSE


@respx.mock
def test_cup_fixture_is_scanned_and_priced_from_pinnacle(settings):  # noqa: F811
    mock_cup(cup_event(NOW + timedelta(hours=10)))
    tg = tg_ok()
    r = run_daily(agent_rules(settings), now=NOW, refresh=False)
    assert r.scanned == 1 and r.sent == 1 and tg.call_count == 1
    (p,) = r.picks
    assert (p.league_name, p.home_team, p.away_team, p.selection_label) == (
        "Champions League", "Inter Milan", "Real Madrid", "Home Win (1)")
    assert p.p_dc is None and abs(p.p_model - 0.4803) < 1e-3 and abs(p.ev - 0.0567) < 1e-3
    assert "Pinnacle's de-vigged price alone" in p.reasoning
    assert not any("train champions-league" in e for e in r.errors)  # nothing to fit, no warning


@respx.mock
def test_no_pick_without_a_real_edge_over_pinnacle(settings):  # noqa: F811
    mock_cup(cup_event(NOW + timedelta(hours=10), soft_home=2.05))  # -1.5% against Pinnacle
    tg_ok()
    r = run_daily(agent_rules(settings).model_copy(update={"notify_no_picks": False}), now=NOW, refresh=False)
    assert r.scanned == 1 and r.picks == []


def test_cup_picks_get_paid_closing_lines_but_no_results_backfill(con):
    kick = NOW + timedelta(minutes=45)
    pick(con, "cup", kick, market="h2h", outcome="home", point=0.0, league="UCL", sport=UCL, status="sending")
    assert list(due_picks(con, NOW)["fixture_key"]) == ["cup"]
    # Finished cup fixtures are not worth a /scores call just to feed a model that doesn't exist.
    upsert_df(con, "fixtures", pd.DataFrame([{
        "event_id": "old", "league": "UCL", "sport_key": UCL, "commence_time": NOW - timedelta(hours=50),
        "home_team_raw": "A", "away_team_raw": "B", "home_team": "A", "away_team": "B", "fetched_at": NOW}]))
    assert sports_to_refresh(con, NOW, timedelta(hours=24)) == []


