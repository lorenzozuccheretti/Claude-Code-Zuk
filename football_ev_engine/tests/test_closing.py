"""Closing-line snapshots for CLV."""

from datetime import datetime, timedelta

import httpx
import pandas as pd
import pytest
import respx

from src.agent import store
from src.agent.closing import capture, closing_fair, due_picks
from src.config import Settings
from src.data.database import upsert_df
from src.data.ingest_odds_api import Event

API = "https://api.the-odds-api.com/v4"
QUOTA = {"x-requests-used": "3", "x-requests-remaining": "400", "x-requests-last": "1"}
T0 = datetime(2030, 3, 20, 18, 0)
SETTINGS = Settings(_env_file=None, odds_api_key="k")


def pick(con, key, kick, market="totals", outcome="over", point=3.5, price=2.00, status="sent",
         league="INT", sport="soccer_uefa_nations_league"):
    upsert_df(con, "fixtures", pd.DataFrame([{
        "event_id": key, "league": league, "sport_key": sport, "commence_time": kick,
        "home_team_raw": "Germany", "away_team_raw": "Serbia", "home_team": "Germany", "away_team": "Serbia",
        "fetched_at": kick - timedelta(hours=8)}]))
    store.record_sending(con, dict(
        fixture_key=key, event_id=key, league=league, home_team="Germany", away_team="Serbia",
        commence_time=kick, market=market, outcome=outcome, point=point, bookmaker="x", price=price,
        p_model=0.52, p_dc=0.6, p_sharp=0.5, ev=0.04, stake_pct=0.01, reasoning=""), status=status)
    if status == "sending":
        store.mark(con, key, "sent")


def event(markets, key="e1"):
    """One event with the given {bookmaker: [(market, name, price, point)]} quotes."""
    books = []
    for book, quotes in markets.items():
        by_market = {}
        for mk, name, price, point in quotes:
            by_market.setdefault(mk, []).append({"name": name, "price": price, "point": point})
        books.append({"key": book, "title": book, "markets": [
            {"key": mk, "outcomes": oc} for mk, oc in by_market.items()]})
    return {"id": key, "sport_key": "soccer_uefa_nations_league", "commence_time": "2030-03-20T18:00:00Z",
            "home_team": "Germany", "away_team": "Serbia", "bookmakers": books}


PIN_35 = {"pinnacle": [("totals", "Over", 2.10, 3.5), ("totals", "Under", 1.80, 3.5),
                       ("totals", "Over", 1.60, 2.5), ("totals", "Under", 2.40, 2.5)]}


def test_closing_fair_devigs_pinnacle_on_the_picks_line():
    price, prob, source = closing_fair(Event.model_validate(event(PIN_35)), "INT", "totals", "over", 3.5,
                                       "pinnacle", "multiplicative")
    implied = (1 / 2.10) / (1 / 2.10 + 1 / 1.80)
    assert (price, source) == (2.10, "pinnacle") and prob == pytest.approx(implied)


def test_closing_fair_falls_back_to_consensus_or_none():
    soft = {"unibet_fr": [("totals", "Over", 2.0, 3.5), ("totals", "Under", 1.8, 3.5)],
            "pinnacle": [("totals", "Over", 1.6, 2.5), ("totals", "Under", 2.4, 2.5)]}
    price, prob, source = closing_fair(Event.model_validate(event(soft)), "INT", "totals", "under", 3.5,
                                       "pinnacle", "multiplicative")
    assert price is None and source == "consensus" and prob == pytest.approx((1 / 1.8) / (1 / 2.0 + 1 / 1.8))
    assert closing_fair(Event.model_validate(event(soft)), "INT", "totals", "over", 4.5,
                        "pinnacle", "multiplicative") is None


def test_due_window_covers_sent_and_sandbox_picks_once(con):
    pick(con, "soon", T0 + timedelta(minutes=50), status="sending")
    pick(con, "paper", T0 + timedelta(minutes=90), status="sandbox")
    pick(con, "later", T0 + timedelta(hours=5), status="sending")
    pick(con, "started", T0 - timedelta(minutes=5), status="sending")
    assert set(due_picks(con, T0)["fixture_key"]) == {"soon", "paper"}


@respx.mock
def test_capture_stores_snapshot_and_clv(con):
    pick(con, "e1", T0 + timedelta(minutes=45), status="sending")
    route = respx.get(f"{API}/sports/soccer_uefa_nations_league/events/e1/odds").mock(
        return_value=httpx.Response(200, json=event(PIN_35), headers=QUOTA))
    r = capture(con, SETTINGS, T0)
    assert (r.captured, r.due, r.credits_remaining) == (1, 1, 400)
    assert route.calls[0].request.url.params["markets"] == "totals"
    row = con.execute("SELECT closing_price, closing_prob, closing_source, closing_at, clv FROM sent_picks").fetchone()
    fair = (1 / 2.10) / (1 / 2.10 + 1 / 1.80)
    assert row[0] == 2.10 and row[1] == pytest.approx(fair) and row[2] == "pinnacle" and row[3] == T0
    # The Germany-Serbia over at 2.00 against a fair 46.2%: negative CLV.
    assert row[4] == pytest.approx(2.00 * fair - 1) and row[4] < 0
    # Captured once: the next hourly run makes no request.
    assert capture(con, SETTINGS, T0 + timedelta(minutes=10)).due == 0 and route.call_count == 1


@respx.mock
def test_capture_tries_alternate_totals_when_the_line_is_missing(con):
    pick(con, "e1", T0 + timedelta(minutes=45), status="sending")
    main_only = {"pinnacle": [("totals", "Over", 1.6, 2.5), ("totals", "Under", 2.4, 2.5)]}
    alt = {"pinnacle": [("alternate_totals", "Over", 2.10, 3.5), ("alternate_totals", "Under", 1.80, 3.5)]}
    respx.get(f"{API}/sports/soccer_uefa_nations_league/events/e1/odds").mock(side_effect=[
        httpx.Response(200, json=event(main_only), headers=QUOTA),
        httpx.Response(200, json=event(alt), headers=QUOTA)])
    assert capture(con, SETTINGS, T0).captured == 1
    assert con.execute("SELECT closing_price FROM sent_picks").fetchone()[0] == 2.10


@respx.mock
def test_capture_reports_failures(con):
    pick(con, "e1", T0 + timedelta(minutes=45), status="sending")
    respx.get(f"{API}/sports/soccer_uefa_nations_league/events/e1/odds").mock(
        return_value=httpx.Response(404, json={"message": "Event not found"}, headers=QUOTA))
    r = capture(con, SETTINGS, T0)
    assert r.captured == 0 and "HTTP 404" in r.errors[0]
    assert con.execute("SELECT clv FROM sent_picks").fetchone()[0] is None


def test_settlement_keeps_the_snapshot_clv_and_fills_missing_ones(con):
    kick = T0 - timedelta(days=1)
    for key, home in (("snap", "Inter"), ("none", "Roma")):
        pick(con, key, kick, market="h2h", outcome="home", point=0.0, price=2.0, status="sending", league="I1",
             sport="soccer_italy_serie_a")
        con.execute("UPDATE sent_picks SET home_team = ?, away_team = 'Milan' WHERE fixture_key = ?", [home, key])
        upsert_df(con, "matches", pd.DataFrame([{
            "league": "I1", "season": "2029-2030", "match_date": kick.date(), "home_team": home,
            "away_team": "Milan", "fthg": 1, "ftag": 0, "ftr": "H", "psch": 2.1, "pscd": 3.4, "psca": 3.6}]))
    con.execute("UPDATE sent_picks SET clv = 0.05, closing_at = ? WHERE fixture_key = 'snap'", [kick])
    assert store.settle(con, T0) == 2
    clv = dict(con.execute("SELECT fixture_key, clv FROM sent_picks").fetchall())
    implied = [1 / 2.1, 1 / 3.4, 1 / 3.6]
    assert clv["snap"] == 0.05 and clv["none"] == pytest.approx(2.0 * implied[0] / sum(implied) - 1)
