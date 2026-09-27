from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.agent import store
from src.data.database import upsert_df

ROME = ZoneInfo("Europe/Rome")


def row(key="I1|Inter|Milan|2030-01-10", **kw):
    base = dict(fixture_key=key, event_id="e1", league="I1", home_team="Inter", away_team="Milan",
                commence_time=datetime(2030, 1, 10, 19, 45), market="h2h", outcome="home", point=0.0,
                bookmaker="unibet_fr", price=2.0, p_model=0.55, p_dc=0.6, p_sharp=0.5, ev=0.1,
                stake_pct=0.02, reasoning="r")
    base.update(kw)
    return base


def test_fixture_key_uses_date():
    assert store.fixture_key("I1", "A", "B", datetime(2030, 1, 1, 20)) == "I1|A|B|2030-01-01"


def test_sending_blocks_and_failed_can_retry(con):
    store.record_sending(con, row())
    assert store.blocked_fixtures(con) == {"I1|Inter|Milan|2030-01-10"}
    store.mark(con, "I1|Inter|Milan|2030-01-10", "failed")
    assert store.blocked_fixtures(con) == set()
    store.record_sending(con, row())  # a failed pick can be retried
    store.mark(con, "I1|Inter|Milan|2030-01-10", "sent", 42)
    assert con.execute("SELECT status, telegram_message_id FROM sent_picks").fetchone() == ("sent", 42)


def test_sent_count_uses_local_day(con):
    store.record_sending(con, row())
    # 23:30 UTC on 9 Jan is already 10 Jan in Rome.
    con.execute("UPDATE sent_picks SET sent_at = TIMESTAMP '2030-01-09 23:30:00'")
    assert store.sent_count_on(con, date(2030, 1, 10), ROME) == 1
    assert store.sent_count_on(con, date(2030, 1, 9), ROME) == 0


def _match(con, fthg, ftag, **odds):
    res = "H" if fthg > ftag else "A" if fthg < ftag else "D"
    upsert_df(con, "matches", __import__("pandas").DataFrame([dict(
        league="I1", season="2029-2030", match_date=date(2030, 1, 10), home_team="Inter", away_team="Milan",
        fthg=fthg, ftag=ftag, ftr=res, **odds)]))


def test_settle_h2h_with_clv(con):
    store.record_sending(con, row())
    store.mark(con, row()["fixture_key"], "sent")
    _match(con, 2, 1, psch=1.90, pscd=3.6, psca=4.2)
    assert store.settle(con, now=datetime(2030, 1, 11)) == 1
    won, profit, clv = con.execute("SELECT won, profit_units, clv FROM sent_picks").fetchone()
    implied = [1 / 1.9, 1 / 3.6, 1 / 4.2]
    assert won and profit == pytest.approx(1.0)
    assert clv == pytest.approx(2.0 * implied[0] / sum(implied) - 1)
    rec = store.track_record(con)
    assert (rec.picks, rec.settled, rec.wins, rec.units) == (1, 1, 1, 1.0) and rec.yield_pct == 100


def test_settle_totals_loss_and_pending(con):
    store.record_sending(con, row(market="totals", outcome="over", point=2.5))
    store.mark(con, row()["fixture_key"], "sent")
    assert store.settle(con, now=datetime(2030, 1, 11)) == 0  # result not in history yet
    _match(con, 1, 1)
    assert store.settle(con, now=datetime(2030, 1, 11)) == 1
    assert con.execute("SELECT won, profit_units, clv FROM sent_picks").fetchone() == (False, -1.0, None)
