"""Staying inside the Odds API free plan: pacing, free event checks, free closing lines."""

from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pytest
import respx

from src import pipeline
from src.agent import runner, store
from src.agent.closing import capture, due_picks
from src.agent.runner import RunReport
from src.config import LEAGUES, Settings
from src.data.budget import affordable, days_left_in_month, spare_credits
from src.data.database import upsert_df
from src.data.ingest_csv import parse_csv
from tests.test_closing import pick

API = "https://api.the-odds-api.com/v4"
QUOTA = {"x-requests-used": "100", "x-requests-remaining": "400", "x-requests-last": "0"}
NOW = datetime(2030, 3, 20, 9, 0)


def balance(con, remaining):
    con.execute("INSERT INTO api_usage VALUES (?, 'x', 200, 0, ?, 0)", [datetime.now(), remaining])


def test_pacing_keeps_a_daily_reserve_until_the_reset():
    assert days_left_in_month(date(2030, 3, 20)) == 12 and days_left_in_month(date(2030, 2, 28)) == 1
    assert spare_credits(124, date(2026, 10, 26), 12) == 124 - 6 * 12
    assert spare_credits(None, date(2030, 3, 1), 12) is None


def test_affordable_reads_the_last_known_balance(con):
    assert affordable(con, date(2030, 3, 20), 12, 2)  # unknown balance: allowed
    balance(con, 12 * 12 + 1)
    assert affordable(con, date(2030, 3, 20), 12, 1) and not affordable(con, date(2030, 3, 20), 12, 2)


@respx.mock
def test_odds_are_only_bought_for_leagues_playing_in_the_window(con):
    s = Settings(_env_file=None, odds_api_key="k")
    leagues = [LEAGUES["serie-a"], LEAGUES["premier-league"]]
    respx.get(f"{API}/sports").mock(return_value=httpx.Response(200, headers=QUOTA, json=[
        {"key": lg.odds_api_key, "active": True} for lg in leagues]))
    soon, later = NOW + timedelta(hours=10), NOW + timedelta(days=4)
    ev = lambda kick: [{"id": "x", "sport_key": "s", "commence_time": kick.isoformat() + "Z",  # noqa: E731
                        "home_team": "A", "away_team": "B"}]
    respx.get(f"{API}/sports/soccer_italy_serie_a/events").mock(return_value=httpx.Response(200, json=ev(soon), headers=QUOTA))
    respx.get(f"{API}/sports/soccer_epl/events").mock(return_value=httpx.Response(200, json=ev(later), headers=QUOTA))
    serie_a = respx.get(f"{API}/sports/soccer_italy_serie_a/odds").mock(return_value=httpx.Response(200, json=[], headers=QUOTA))
    epl = respx.get(f"{API}/sports/soccer_epl/odds").mock(return_value=httpx.Response(200, json=[], headers=QUOTA))
    reports, _ = pipeline.fetch_odds(con, s, leagues, window=(NOW + timedelta(hours=1), NOW + timedelta(hours=36)))
    assert serie_a.called and not epl.called
    assert [r.skipped for r in reports] == [False, True]
    # Without a window (the CLI), every active league is priced.
    pipeline.fetch_odds(con, s, leagues)
    assert epl.called


def fixture(con, event_id, kick, sport="soccer_italy_serie_a"):
    upsert_df(con, "fixtures", pd.DataFrame([{
        "event_id": event_id, "league": "I1", "sport_key": sport, "commence_time": kick,
        "home_team_raw": "A", "away_team_raw": "B", "home_team": "A", "away_team": "B", "fetched_at": kick}]))


def test_scores_calls_settle_first_and_respect_the_budget(con):
    s = Settings(_env_file=None, odds_api_daily_reserve=10)
    pick(con, "p1", NOW - timedelta(hours=20), market="h2h", outcome="home", point=0.0, league="I1",
         sport="soccer_epl", status="sending")
    fixture(con, "f1", NOW - timedelta(hours=50))  # missing result, old enough to fetch
    today = NOW.date()  # 12 days left -> 120 reserved
    report = RunReport(started_at=NOW, dry_run=True)
    balance(con, 124)
    assert runner._affordable_scores(con, s, NOW, today, report) == ["soccer_epl", "soccer_italy_serie_a"]
    balance(con, 122)
    assert runner._affordable_scores(con, s, NOW, today, report) == ["soccer_epl"]
    assert report.budget_skipped == 1
    balance(con, 100)
    assert runner._affordable_scores(con, s, NOW, today, report) == []


@respx.mock
def test_closing_snapshots_skip_free_lines_and_respect_the_budget(con):
    s = Settings(_env_file=None, odds_api_key="k")
    kick = NOW + timedelta(minutes=45)
    pick(con, "club", kick, market="totals", outcome="under", point=2.5, league="I1",
         sport="soccer_italy_serie_a", status="sending")
    pick(con, "club35", kick, market="totals", outcome="over", point=3.5, league="I1",
         sport="soccer_italy_serie_a", status="sending")
    pick(con, "intl", kick, market="h2h", outcome="home", point=0.0, status="sending")
    assert set(due_picks(con, NOW)["fixture_key"]) == {"club35", "intl"}
    balance(con, 12 * 12)  # exactly the reserve: nothing spare
    route = respx.get(url__regex=rf"{API}/sports/.*/events/.*/odds")
    r = capture(con, s, NOW)
    assert r.captured == 0 and "spare" in r.errors[0] and not route.called


def test_settlement_falls_back_to_the_market_average_close(con):
    kick = NOW - timedelta(days=1)
    pick(con, "k", kick, market="totals", outcome="over", point=2.5, price=2.10, league="I1",
         sport="soccer_italy_serie_a", status="sending")
    upsert_df(con, "matches", pd.DataFrame([{
        "league": "I1", "season": "2029-2030", "match_date": kick.date(), "home_team": "Germany",
        "away_team": "Serbia", "fthg": 2, "ftag": 1, "ftr": "H", "avgc_over25": 1.95, "avgc_under25": 1.85}]))
    assert store.settle(con, NOW) == 1
    clv = con.execute("SELECT clv FROM sent_picks").fetchone()[0]
    fair = (1 / 1.95) / (1 / 1.95 + 1 / 1.85)
    assert clv == pytest.approx(2.10 * fair - 1)


def test_csv_keeps_the_average_closing_columns():
    raw = ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,AvgCH,AvgCD,AvgCA,AvgC>2.5,AvgC<2.5\n"
           "I1,19/10/2026,Monza,Cagliari,4,0,H,2.54,3.25,3.04,2.03,1.81\n")
    row = parse_csv(raw, "I1", "2026-2027").iloc[0]
    assert (row["avgch"], row["avgca"], row["avgc_over25"], row["avgc_under25"]) == (2.54, 3.04, 2.03, 1.81)
