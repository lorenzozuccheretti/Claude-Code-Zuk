"""End-to-end daily run against mocked Odds API and Telegram."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from typer.testing import CliRunner

from src.agent.runner import run_daily
from src.agent.settings import AgentSettings, get_agent_settings
from src.data.database import connect, init_schema, upsert_df
from tests.synthetic import odds_api_events

API = "https://api.the-odds-api.com/v4"
TG = "https://api.telegram.org/botT/sendMessage"
QUOTA = {"x-requests-used": "2", "x-requests-remaining": "498", "x-requests-last": "2"}
NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
FIXTURES = [("Inter Milan", "AC Milan"), ("AS Roma", "SS Lazio"), ("Napoli", "Juventus"), ("Como", "Venezia")]


@pytest.fixture
def settings(tmp_path, synthetic_matches):
    db = tmp_path / "agent.duckdb"
    con = connect(db)
    init_schema(con)
    upsert_df(con, "matches", synthetic_matches)
    con.close()
    # Wide-open value rules so the synthetic prices always yield candidates.
    return AgentSettings(_env_file=None, db_path=db, aliases_json=tmp_path / "a.json", odds_api_key="k",
                         telegram_bot_token="T", telegram_chat_id="1", min_ev=-1.0, min_odds=1.01,
                         max_odds=100.0, timezone="Europe/Rome", report_weekday=-1)


def mock_odds(hours_ahead: int = 10):
    respx.get(f"{API}/sports").mock(return_value=httpx.Response(
        200, json=[{"key": "soccer_italy_serie_a", "active": True}], headers=QUOTA))
    events = odds_api_events(FIXTURES, NOW + timedelta(hours=hours_ahead),
                             books={"pinnacle": 0.025, "unibet_fr": 0.06, "williamhill": 0.05})
    respx.get(f"{API}/sports/soccer_italy_serie_a/odds").mock(
        return_value=httpx.Response(200, json=events, headers=QUOTA))


def tg_ok():
    return respx.post(TG).mock(return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5}}))


def picks_in_db(settings):
    con = connect(settings.db_file)
    rows = con.execute("SELECT fixture_key, status, telegram_message_id FROM sent_picks").fetchall()
    con.close()
    return rows


@respx.mock
def test_daily_run_sends_at_most_two_then_dedups(settings):
    mock_odds()
    tg = tg_ok()
    r = run_daily(settings, now=NOW, refresh=False)
    assert r.scanned == 4 and r.candidates >= 2
    assert len(r.picks) == 2 and r.sent == 2 and tg.call_count == 2
    assert len({p.fixture_key for p in r.picks}) == 2
    assert r.picks[0].score >= r.picks[1].score
    assert all(p.reasoning.count(". ") >= 1 for p in r.picks)
    assert "Track record" in r.messages[-1]
    assert {s for _, s, _ in picks_in_db(settings)} == {"sent"}
    # Unmatched history for the other four leagues is reported, not fatal.
    assert any("train premier-league" in e for e in r.errors)

    # Same day again: the daily cap is used up and no "no picks" message goes out.
    r2 = run_daily(settings, now=NOW + timedelta(minutes=5), refresh=False)
    assert r2.picks == [] and r2.messages == [] and tg.call_count == 2


@respx.mock
def test_next_day_never_resends_a_fixture(settings):
    mock_odds(hours_ahead=30)
    tg_ok()
    first = {p.fixture_key for p in run_daily(settings, now=NOW, refresh=False).picks}
    # One minute into the next local (Rome) day: the daily cap resets, dedup must not.
    rome = ZoneInfo("Europe/Rome")
    local_midnight = datetime.combine(NOW.replace(tzinfo=timezone.utc).astimezone(rome).date() + timedelta(days=1),
                                      datetime.min.time(), rome)
    later_now = local_midnight.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
    later = run_daily(settings, now=later_now, refresh=False)
    assert later.picks and not ({p.fixture_key for p in later.picks} & first)


@respx.mock
def test_dry_run_records_nothing(settings):
    mock_odds()
    tg = tg_ok()
    r = run_daily(settings, now=NOW, dry_run=True, refresh=False)
    assert len(r.picks) == 2 and len(r.messages) == 2
    assert tg.call_count == 0 and picks_in_db(settings) == []


@respx.mock
def test_failed_send_is_marked_and_retryable(settings):
    mock_odds()
    respx.post(TG).mock(return_value=httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked"}))
    r = run_daily(settings, now=NOW, refresh=False)
    assert r.sent == 0 and any("blocked" in e for e in r.errors)
    assert {s for _, s, _ in picks_in_db(settings)} == {"failed"}
    tg_ok()
    assert run_daily(settings, now=NOW + timedelta(minutes=1), refresh=False).sent == 2


@respx.mock
def test_odds_failure_means_no_picks(settings):
    respx.get(f"{API}/sports").mock(return_value=httpx.Response(401))
    tg = tg_ok()
    r = run_daily(settings, now=NOW, refresh=False)
    assert r.picks == [] and tg.call_count == 0
    assert any("odds fetch failed" in e for e in r.errors)


def test_real_rules_filter_odds_window(settings):
    strict = settings.model_copy(update={"min_ev": 0.035, "min_odds": 1.75, "max_odds": 2.25})
    with respx.mock:
        mock_odds()
        r = run_daily(strict, now=NOW, dry_run=True, refresh=False)
    assert all(1.75 <= p.price <= 2.25 and p.ev >= 0.035 for p in r.picks)


def test_requires_telegram_unless_dry_run(settings):
    with pytest.raises(ValueError):
        run_daily(settings.model_copy(update={"telegram_bot_token": ""}), refresh=False, fetch=False)


@respx.mock
def test_cli_dry_run(settings, monkeypatch):
    from src.cli import app as cli
    from src.cli.app import app

    for key, value in {"DB_PATH": str(settings.db_file), "ALIASES_JSON": str(settings.aliases_json),
                       "ODDS_API_KEY": "k", "MIN_EV": "-1", "MIN_ODDS": "1.01", "MAX_ODDS": "100",
                       "TELEGRAM_BOT_TOKEN": "", "ANTHROPIC_API_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli.console, "width", 200)
    get_agent_settings.cache_clear()
    mock_odds()
    result = CliRunner().invoke(app, ["agent", "run", "--dry-run", "--no-refresh"], catch_exceptions=False)
    get_agent_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output and "Value Pick 1/2" in result.output and "Engine probability" in result.output


def test_refresh_downloads_full_history_only_for_new_leagues(settings, monkeypatch):
    from src import pipeline
    from src.agent.runner import RunReport, _refresh_history
    from src.config import current_season_start

    calls = []
    monkeypatch.setattr(pipeline, "fetch_historical",
                        lambda con, s, leagues, seasons, client=None: calls.append(
                            (sorted(lg.fd_code for lg in leagues), seasons)) or [])
    con = connect(settings.db_file)
    _refresh_history(con, settings, RunReport(started_at=NOW, dry_run=True), None)
    con.close()
    new, known = calls
    assert "INT" in new[0] and "I1" not in new[0] and len(new[1]) == settings.history_seasons
    assert known[0] == ["I1"] and known[1] == [f"{current_season_start()}-{current_season_start() + 1}"]


@respx.mock
def test_backup_run_is_a_no_op_after_the_daily_run(settings):
    mock_odds()
    tg = tg_ok()
    run_daily(settings, now=NOW, refresh=False)
    calls = tg.call_count
    backup = run_daily(settings, now=NOW + timedelta(hours=2), refresh=False)
    assert backup.skipped and tg.call_count == calls
    forced = run_daily(settings, now=NOW + timedelta(hours=2), refresh=False, force=True)
    assert not forced.skipped and forced.picks == []  # daily cap still holds


@respx.mock
def test_no_pick_notice_only_once_a_day(settings):
    strict = settings.model_copy(update={"min_ev": 5.0})  # nothing can qualify
    mock_odds()
    tg = tg_ok()
    first = run_daily(strict, now=NOW, refresh=False)
    assert len(first.messages) == 1 and "No pick today" in first.messages[0] and tg.call_count == 1
    again = run_daily(strict, now=NOW + timedelta(hours=1), refresh=False, force=True)
    assert again.messages == [] and tg.call_count == 1


@respx.mock
def test_weekly_report_on_its_weekday_once(settings):
    local = NOW.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Rome"))
    s = settings.model_copy(update={"report_weekday": local.weekday()})
    mock_odds()
    tg = tg_ok()
    doc = respx.post("https://api.telegram.org/botT/sendDocument").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 8}}))
    r = run_daily(s, now=NOW, refresh=False)
    assert r.report_sent and doc.call_count == 1
    assert any("Weekly report" in m for m in r.messages) and tg.call_count == 3  # 2 picks + report
    run_daily(s, now=NOW + timedelta(hours=1), refresh=False, force=True)
    assert doc.call_count == 1


@respx.mock
def test_notices_are_counted(settings):
    strict = settings.model_copy(update={"min_ev": 5.0})
    mock_odds()
    tg_ok()
    r = run_daily(strict, now=NOW, refresh=False)
    assert (r.sent, r.notices) == (0, 1)
