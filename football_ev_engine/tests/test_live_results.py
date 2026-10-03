"""Provisional results from The Odds API /scores."""

from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import respx

from src.agent.runner import run_daily
from src.data.database import connect, upsert_df
from src.data.live_results import (purge_superseded, results_from_scores, season_label, sports_to_refresh,
                                   store_results)
from tests.test_agent_runner import API, NOW, QUOTA, mock_odds, settings  # noqa: F401 - fixture

T0 = datetime(2030, 3, 20, 12)


def fixture(con, event_id, kick, home="Inter Milan", away="AC Milan", league="I1",
            sport="soccer_italy_serie_a", raw=None):
    raw_home, raw_away = raw or (home, away)
    upsert_df(con, "fixtures", pd.DataFrame([{
        "event_id": event_id, "league": league, "sport_key": sport, "commence_time": kick,
        "home_team_raw": raw_home, "away_team_raw": raw_away, "home_team": home, "away_team": away,
        "fetched_at": kick - timedelta(days=1)}]))


def score(event_id, home, away, hg, ag, completed=True):
    return {"id": event_id, "completed": completed, "home_team": home, "away_team": away,
            "scores": [{"name": home, "score": str(hg)}, {"name": away, "score": str(ag)}]}


def match(con, day, home="Inter Milan", away="AC Milan", provisional=False, league="I1"):
    upsert_df(con, "matches", pd.DataFrame([{
        "league": league, "season": season_label(league, day), "match_date": day, "home_team": home,
        "away_team": away, "fthg": 1, "ftag": 0, "ftr": "H", "provisional": provisional}]))


def test_season_label():
    assert season_label("I1", date(2026, 9, 20)) == "2026-2027"
    assert season_label("I1", date(2027, 3, 1)) == "2026-2027"
    assert season_label("INT", date(2026, 9, 29)) == "2026"


def test_results_use_canonical_names_and_skip_known_or_unfinished(con):
    fixture(con, "e1", T0 - timedelta(hours=20), raw=("Inter", "Milan"))
    fixture(con, "e2", T0 - timedelta(hours=20), home="AS Roma", away="SS Lazio")
    fixture(con, "e3", T0 - timedelta(hours=20), home="Napoli", away="Juventus")
    match(con, (T0 - timedelta(hours=20)).date(), "AS Roma", "SS Lazio")  # already in history
    scores = [score("e1", "Inter", "Milan", 2, 2), score("e2", "AS Roma", "SS Lazio", 0, 1),
              score("e3", "Napoli", "Juventus", 0, 0, completed=False), score("zz", "X", "Y", 1, 0)]
    df = results_from_scores(con, scores, T0)
    assert df[["home_team", "away_team", "fthg", "ftag", "ftr"]].values.tolist() == [
        ["Inter Milan", "AC Milan", 2, 2, "D"]]
    assert store_results(con, scores, T0) == 1
    assert con.execute("SELECT provisional, season FROM matches WHERE fthg = 2").fetchone() == (True, "2029-2030")


def test_official_row_supersedes_provisional_even_a_day_apart(con):
    match(con, date(2030, 3, 20), provisional=True)
    match(con, date(2030, 3, 20), "AS Roma", "SS Lazio", provisional=True)
    match(con, date(2030, 3, 21))  # local date differs from the UTC kick-off date
    assert purge_superseded(con) == 1
    left = con.execute("SELECT home_team, provisional FROM matches ORDER BY 1").fetchall()
    assert left == [("AS Roma", True), ("Inter Milan", False)]


def test_scores_are_fetched_once_the_round_is_over_or_before_they_expire(con):
    fixture(con, "e1", T0 - timedelta(hours=10))
    fixture(con, "e2", T0 + timedelta(hours=5), home="AS Roma", away="SS Lazio")  # round still going
    assert sports_to_refresh(con, T0, timedelta(hours=24)) == []
    assert sports_to_refresh(con, T0 + timedelta(hours=30), timedelta(hours=24)) == ["soccer_italy_serie_a"]
    # Oldest missing result is 40h+ old: fetch even with more games to come.
    fixture(con, "e3", T0 + timedelta(hours=40), home="Napoli", away="Juventus",
            sport="soccer_italy_serie_a")
    assert sports_to_refresh(con, T0 + timedelta(hours=29), timedelta(hours=24)) == []
    assert sports_to_refresh(con, T0 + timedelta(hours=31), timedelta(hours=24)) == ["soccer_italy_serie_a"]
    # Nothing to fetch once the result is stored, or for unmapped teams.
    match(con, (T0 - timedelta(hours=10)).date())
    match(con, (T0 + timedelta(hours=5)).date(), "AS Roma", "SS Lazio")
    assert sports_to_refresh(con, T0 + timedelta(hours=31), timedelta(hours=24)) == []


@respx.mock
def test_daily_run_fills_missing_results(settings):  # noqa: F811
    con = connect(settings.db_file)
    kick = NOW - timedelta(hours=30)
    fixture(con, "old1", kick, home="Napoli", away="Como")
    con.close()
    mock_odds()
    respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5}}))
    scores = respx.get(f"{API}/sports/soccer_italy_serie_a/scores").mock(return_value=httpx.Response(
        200, json=[score("old1", "Napoli", "Como", 3, 1)], headers=QUOTA))
    r = run_daily(settings, now=NOW, refresh=False)
    assert scores.called and r.live_results == 1
    assert r.data_through["I1"] == str(kick.date())
    con = connect(settings.db_file)
    row = con.execute("SELECT fthg, ftag, provisional FROM matches WHERE home_team = 'Napoli' "
                      "AND away_team = 'Como' AND provisional").fetchone()
    con.close()
    assert row == (3, 1, True)
