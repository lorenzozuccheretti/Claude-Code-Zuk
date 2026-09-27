from datetime import datetime, timedelta

from src.agent.selector import Pick, select

NOW = datetime(2030, 3, 1, 9, 0)


def pick(key="I1|A|B|2030-03-01", hours=10, ev=0.05, p=0.5, market="h2h", outcome="home", point=0.0):
    return Pick(fixture_key=key, event_id=key, league="I1", league_name="Serie A", home_team="A", away_team="B",
                commence_time=NOW + timedelta(hours=hours), market=market, outcome=outcome, point=point,
                bookmaker="x", price=2.0, p_model=p, p_dc=p, p_sharp=p, ev=ev, stake_pct=0.01)


def run(cands, blocked=(), sent_today=0, max_daily=2, horizon=36, lead=60):
    return select(cands, NOW, set(blocked), sent_today, max_daily, horizon, lead)


def test_ranks_by_ev_times_probability():
    a = pick("a", ev=0.10, p=0.45)   # 0.045
    b = pick("b", ev=0.08, p=0.60)   # 0.048
    c = pick("c", ev=0.04, p=0.50)   # 0.020
    assert [x.fixture_key for x in run([a, b, c])] == ["b", "a"]


def test_daily_cap_counts_picks_already_sent():
    cands = [pick("a"), pick("b"), pick("c")]
    assert len(run(cands, sent_today=1)) == 1
    assert run(cands, sent_today=2) == []
    assert run(cands, max_daily=0) == []


def test_one_pick_per_fixture():
    home = pick("a", ev=0.09, market="h2h", outcome="home")
    under = pick("a", ev=0.08, market="totals", outcome="under", point=2.5)
    other = pick("b", ev=0.04)
    chosen = run([home, under, other])
    assert [(c.fixture_key, c.market) for c in chosen] == [("a", "h2h"), ("b", "h2h")]


def test_dedup_and_time_window():
    cands = [pick("sent"), pick("soon", hours=0.5), pick("far", hours=48), pick("ok", hours=20)]
    assert [c.fixture_key for c in run(cands, blocked={"sent"})] == ["ok"]


def test_labels_and_fair_odds():
    assert pick(outcome="away").selection_label == "Away Win (2)"
    assert pick(market="totals", outcome="over", point=2.5).selection_label == "Over 2.5"
    assert abs(pick(p=0.562).fair_odds - 1.779) < 1e-3
