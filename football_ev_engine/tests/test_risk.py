"""Risk overrides: extreme totals lines, big-match unders, sandbox leagues."""

from datetime import timedelta

import pytest
import respx

from src.agent import store
from src.agent.report import build_report, format_report
from src.agent.runner import run_daily
from src.backtest.backtester import BacktestConfig, run_backtest
from src.config import Settings
from src.data.database import connect
from src.engine.risk import NO_RISK, RiskRules, league_codes, top_attack
from src.engine.staking import stake_fraction
from src.engine.value import ValueRules, evaluate_market
from tests.test_agent_runner import NOW, mock_odds, settings, tg_ok  # noqa: F401 - fixture

# The live agent's rules; model_weight=1 so P_model is exactly the probability given.
AGENT = ValueRules(min_ev=0.035, min_prob=0.0, min_odds=1.75, max_odds=2.25, model_weight=1.0)
RISK = RiskRules(big_match_under_penalty=0.035)  # rule 3 is opt-in
TOP = {"Napoli", "Roma", "Inter", "Atalanta", "Milan", "Lazio", "Juventus"}


def totals_bets(p_over, point, over_price, under_price, home="A", away="B", top=frozenset(), risk=RISK):
    return evaluate_market("e", "totals", point, ("over", "under"), {"over": p_over, "under": 1 - p_over},
                           None, {"book": {"over": over_price, "under": under_price}}, AGENT,
                           risk.market_risk("totals", point, home, away, set(top)))


# --- Rule 1: extreme lines ----------------------------------------------------


@pytest.mark.parametrize("match,price,p", [
    ("Germany-Serbia", 2.00, 0.520),       # EV +4.0%
    ("England-Czech Rep", 1.97, 0.535),    # EV +5.4%
    ("Rennes-Strasbourg", 2.20, 0.509),    # EV +12.0% before rounding: 11.98%
])
def test_past_over_3_5_losers_are_filtered(match, price, p):
    assert totals_bets(p, 3.5, price, 1.80) == []
    # Without the override the same signal passed the default 3.5% floor.
    risk_off = RiskRules(extreme_line_min_ev=0)
    assert [b.outcome for b in totals_bets(p, 3.5, price, 1.80, risk=risk_off)] == ["over"], match


def test_extreme_line_still_passes_with_a_big_edge():
    bets = totals_bets(0.56, 3.5, 2.05, 1.80)  # EV +14.8%
    assert [(b.outcome, round(b.ev, 3)) for b in bets] == [("over", 0.148)]


def test_under_1_5_and_beyond_are_extreme_but_normal_lines_are_not():
    assert RISK.is_extreme("under", 1.5) and RISK.is_extreme("under", 0.5)
    assert RISK.is_extreme("over", 4.5) and not RISK.is_extreme("under", 3.5)
    assert not RISK.is_extreme("over", 2.5)
    # Over 2.5 at +5% EV is untouched.
    assert [b.outcome for b in totals_bets(0.525, 2.5, 2.00, 1.80)] == ["over"]


def test_h2h_is_never_overridden():
    assert RISK.market_risk("h2h", 0.0, "Napoli", "Roma", TOP) is NO_RISK


# --- Rule 3: big-match unders --------------------------------------------------


@pytest.mark.parametrize("home,away,price", [("Napoli", "Roma", 1.99), ("Milan", "Atalanta", 2.25)])
def test_big_match_under_2_5_loses_3_5_points(home, away, price):
    p_under = 0.535  # EV +6.5% at 1.99: passes on its own
    plain = totals_bets(1 - p_under, 2.5, 1.70, price)
    assert [b.outcome for b in plain] == ["under"]
    penalised = totals_bets(1 - p_under, 2.5, 1.70, price, home, away, TOP)
    if (p_under - 0.035) * price - 1 >= 0.035:
        (b,) = penalised
        assert b.p_model == pytest.approx(p_under - 0.035)
        # Kelly is sized on the penalised probability, so the stake shrinks too.
        assert b.stake_pct == pytest.approx(stake_fraction(p_under - 0.035, price, 0.25, 0.025))
        assert b.stake_pct <= plain[0].stake_pct  # equal only when both hit the 2.5% cap
    else:
        assert penalised == []


def test_napoli_roma_under_rejected_after_penalty():
    # 53.5% at 1.99 is +6.5% EV; 50.0% after the penalty is -0.5%: rejected.
    assert totals_bets(0.465, 2.5, 1.70, 1.99, "Napoli", "Roma", TOP) == []


def test_penalty_needs_both_sides_in_the_top_attacks_and_spares_the_over():
    assert [b.outcome for b in totals_bets(0.465, 2.5, 1.70, 1.99, "Napoli", "Lecce", TOP)] == ["under"]
    over = totals_bets(0.535, 2.5, 1.99, 1.70, "Napoli", "Roma", TOP)
    assert [(b.outcome, round(b.p_model, 3)) for b in over] == [("over", 0.535)]
    assert RISK.market_risk("totals", 3.5, "Napoli", "Roma", TOP).prob_shift == {}


def test_top_attack_ranks_within_the_current_season():
    attack = {"A": 0.5, "B": 0.4, "C": 0.3, "Relegated": 0.9}
    assert top_attack(attack, 2, pool={"A", "B", "C"}) == {"A", "B"}
    assert top_attack(attack, 2) == {"Relegated", "A"}
    assert top_attack(attack, 0) == set()


def test_rules_from_settings_and_league_names():
    s = Settings(_env_file=None, sandbox_leagues=["ligue-1", "E0"], extreme_line_min_ev=0.2, big_match_top_n=5)
    r = RiskRules.from_settings(s)
    assert r.sandbox_leagues == {"F1", "E0"} and r.extreme_line_min_ev == 0.2 and r.big_match_top_n == 5
    default = RiskRules.from_settings(Settings(_env_file=None))
    assert default.sandbox_leagues == set() and default.big_match_under_penalty == 0  # rules 2-3 opt-in
    assert default.extreme_line_min_ev == 0.12
    with pytest.raises(ValueError):
        league_codes(["ligue-2"])


def test_backtest_applies_the_risk_rules(synthetic_matches):
    base = dict(seasons=["2024-2025"], rules=ValueRules(model_weight=0.5), refit_every_days=28, ml_kind=None)
    plain = run_backtest(synthetic_matches, BacktestConfig(**base)).bets
    # A penalty this large on every under (top N covers the whole league) removes them all.
    harsh = RiskRules(big_match_under_penalty=0.5, big_match_top_n=100)
    risky = run_backtest(synthetic_matches, BacktestConfig(**base, risk=harsh)).bets
    unders = lambda b: b[(b["market"] == "totals") & (b["outcome"] == "under")]  # noqa: E731
    assert len(unders(plain)) > 0 and len(unders(risky)) == 0
    assert (risky["market"] == "h2h").sum() == (plain["market"] == "h2h").sum()


# --- Rule 2: sandbox leagues ------------------------------------------------------


@respx.mock
def test_sandbox_league_picks_are_recorded_not_sent(settings, con):  # noqa: F811
    settings = settings.model_copy(update={"sandbox_leagues": ["serie-a"], "notify_no_picks": False})
    mock_odds()
    tg = tg_ok()
    r = run_daily(settings, now=NOW, refresh=False)
    assert r.picks == [] and r.sent == 0 and tg.call_count == 0
    assert len(r.sandbox) == 2 and {p.league for p in r.sandbox} == {"I1"}
    db = connect(settings.db_file)
    statuses = db.execute("SELECT status FROM sent_picks").fetchall()
    assert statuses == [("sandbox",), ("sandbox",)]
    assert store.track_record(db).picks == 0  # paper picks stay out of the real record
    # Next run: never recorded twice, and no Telegram traffic.
    r2 = run_daily(settings, now=NOW + timedelta(minutes=5), refresh=False, force=True)
    assert r2.sandbox == [] and tg.call_count == 0
    # They settle like real picks and show up in their own report section.
    db.execute("UPDATE sent_picks SET won = true, profit_units = 1.0, clv = 0.03")
    text = format_report(build_report(db, settings.tz, now=NOW))
    db.close()
    assert "No picks have been sent yet" in text and "Sandbox" in text and "2/20 settled" in text


@respx.mock
def test_sandbox_does_not_take_real_slots(settings):  # noqa: F811
    settings = settings.model_copy(update={"sandbox_leagues": ["ligue-1"]})
    mock_odds()
    tg_ok()
    r = run_daily(settings, now=NOW, refresh=False)
    assert r.sent == 2 and r.sandbox == []
