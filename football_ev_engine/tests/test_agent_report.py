import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
import respx

from src.agent import store
from src.agent.report import build_report, export_csv, format_report
from src.agent.telegram import TelegramClient

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2030, 3, 20, 9, 0)


def add(con, key, kick, won=None, price=2.0, market="h2h", outcome="home", point=0.0, league="I1",
        clv=None, stake=0.02, event_id=None):
    store.record_sending(con, dict(
        fixture_key=key, event_id=event_id or key, league=league, home_team=f"H{key}", away_team=f"A{key}",
        commence_time=kick, market=market, outcome=outcome, point=point, bookmaker="x", price=price,
        p_model=0.55, p_dc=0.6, p_sharp=0.5, ev=0.1, stake_pct=stake, reasoning=""))
    store.mark(con, key, "sent")
    if won is not None:
        con.execute("UPDATE sent_picks SET won = ?, profit_units = ?, clv = ? WHERE fixture_key = ?",
                    [won, price - 1 if won else -1.0, clv, key])


def test_empty_report(con):
    assert "No picks have been sent yet" in format_report(build_report(con, ROME, now=NOW))


def test_report_numbers(con):
    add(con, "a", datetime(2030, 2, 10, 19), won=True, price=2.2, clv=0.02)                 # last month
    add(con, "b", datetime(2030, 3, 15, 19), won=False, market="totals", outcome="over", point=2.5,
        league="INT", clv=-0.04)
    add(con, "c", datetime(2030, 3, 18, 19), won=True, price=1.9)
    add(con, "d", datetime(2030, 3, 21, 19))                                                 # pending
    r = build_report(con, ROME, days=7, now=NOW)
    assert (r.overall.picks, r.overall.settled, r.overall.wins) == (4, 3, 2)
    assert r.overall.units == pytest.approx(1.2 - 1 + 0.9)
    assert (r.period.picks, r.period.settled) == (2, 2)  # 15 and 18 March
    assert r.pending == 1 and r.avg_clv == pytest.approx(-0.01) and r.clv_count == 2
    assert r.kelly_bankroll_pct == pytest.approx(((1 + .02 * 1.2) * (1 - .02) * (1 + .02 * .9) - 1) * 100)
    assert {s.label for s in r.by_competition} == {"Serie A", "UEFA Nations League"}
    assert {s.label: s.picks for s in r.by_market} == {"1X2": 3, "Over/Under": 1}
    text = format_report(r)
    assert "2/3 won, +1.10u (yield +36.7%), 1 pending" in text
    assert "⏳" in text and "✅" in text and "❌" in text and "O2.5 @ 2.00" in text
    assert "Too early to judge: 2 picks" in text


def test_csv_export(con, tmp_path):
    add(con, "a", datetime(2030, 2, 10, 19), won=True)
    path = tmp_path / "h.csv"
    assert export_csv(con, path) == 1
    df = pd.read_csv(path)
    assert list(df.columns)[:4] == ["kickoff_utc", "competition", "home", "away"] and df.loc[0, "won"]


def test_settle_from_scores(con):
    add(con, "a", datetime(2030, 3, 19, 19), event_id="e1")
    add(con, "b", datetime(2030, 3, 19, 19), event_id="e2", market="totals", outcome="under", point=2.5)
    add(con, "c", datetime(2030, 3, 19, 19), event_id="e3")
    scores = [
        {"id": "e1", "completed": True, "home_team": "Ha", "away_team": "Aa",
         "scores": [{"name": "Ha", "score": "2"}, {"name": "Aa", "score": "1"}]},
        {"id": "e2", "completed": True, "home_team": "Hb", "away_team": "Ab",
         "scores": [{"name": "Hb", "score": "2"}, {"name": "Ab", "score": "1"}]},
        {"id": "e3", "completed": False, "home_team": "Hc", "away_team": "Ac", "scores": None},
    ]
    assert store.settle_from_scores(con, scores) == 2
    rows = dict(con.execute("SELECT fixture_key, won FROM sent_picks").fetchall())
    assert rows == {"a": True, "b": False, "c": None}


def test_grade():
    assert store.grade("h2h", "draw", 0, 1, 1)
    assert store.grade("totals", "over", 2.5, 2, 1) and not store.grade("totals", "under", 2.5, 2, 1)


def test_events(con):
    assert not store.event_done(con, "daily_run", date(2030, 1, 1))
    store.record_event(con, "daily_run", date(2030, 1, 1))
    assert store.event_done(con, "daily_run", date(2030, 1, 1))
    assert store.event_done_since(con, "daily_run", date(2029, 12, 26))
    assert not store.event_done_since(con, "daily_run", date(2030, 1, 2))


@respx.mock
def test_send_document(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("a,b\n1,2\n")
    route = respx.post("https://api.telegram.org/botT/sendDocument").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}))
    assert asyncio.run(TelegramClient("T", "1").send_document(f, "cap")) == 9
    body = route.calls.last.request.read()
    assert b'filename="h.csv"' in body and b"a,b" in body
