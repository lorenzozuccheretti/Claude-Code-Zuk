"""One daily agent run, end to end.

1. Refresh the current season from football-data.co.uk (full history on first run).
2. Settle earlier picks whose results are now in.
3. Refit Dixon-Coles for every league.
4. Fetch fresh odds from The Odds API (2 credits per league).
5. Scan, select at most ``MAX_DAILY_PICKS``, write reasoning.
6. Send to Telegram and record, or print everything in dry-run mode.

A failing step is recorded in the report and the run carries on where that
still makes sense. The exception is odds: without a fresh snapshot there
are no picks, because stale prices would make the EV figures fiction.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import duckdb

from src import pipeline
from src.agent import store
from src.agent.reasoning import build_facts, write_reasoning
from src.agent.report import build_report, export_csv, format_report
from src.agent.selector import Pick, select, to_picks
from src.agent.settings import AgentSettings
from src.agent.telegram import TelegramClient, format_no_picks, format_pick, format_record
from src.config import LEAGUES, recent_seasons
from src.data.database import session
from src.engine.value import ValueRules, scan
from src.models.dixon_coles import DixonColes

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    started_at: datetime
    dry_run: bool
    history_rows: int = 0
    settled: int = 0
    trained: list[str] = field(default_factory=list)
    odds_events: int = 0
    credits_remaining: int | None = None
    scanned: int = 0
    candidates: int = 0
    picks: list[Pick] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    sent: int = 0  # picks delivered
    notices: int = 0  # other messages delivered (no-pick notice, weekly report)
    skipped: str = ""  # why the run stopped early, if it did
    report_sent: bool = False
    errors: list[str] = field(default_factory=list)


def _refresh_history(con: duckdb.DuckDBPyConnection, s: AgentSettings, report: RunReport, fd_client) -> None:
    have = {r[0] for r in con.execute("SELECT DISTINCT league FROM matches").fetchall()}
    new = [lg for lg in LEAGUES.values() if lg.fd_code not in have]  # full history, once
    known = [lg for lg in LEAGUES.values() if lg.fd_code in have]    # current season only
    for leagues, seasons in ((new, recent_seasons(s.history_seasons)), (known, recent_seasons(1))):
        if not leagues:
            continue
        results = pipeline.fetch_historical(con, s, leagues, seasons, client=fd_client)
        report.history_rows += sum(r.rows for r in results)
        for r in results:
            # The current season's file can 404 in July; that's not worth an alert.
            if r.error and not (r.season == seasons[-1] and "404" in r.error):
                report.errors.append(f"history {r.league} {r.season}: {r.error}")


def _candidates(con: duckdb.DuckDBPyConnection, s: AgentSettings, now: datetime,
                report: RunReport) -> tuple[list[Pick], dict[str, DixonColes]]:
    rules = ValueRules.from_settings(s)
    latest = now + timedelta(hours=s.pick_horizon_hours)
    picks: list[Pick] = []
    models: dict[str, DixonColes] = {}
    for lg in LEAGUES.values():
        params = pipeline.load_params(con, lg.fd_code)
        model = DixonColes.from_params(params, s.max_goals) if params else None
        if model:
            models[lg.fd_code] = model
        fixtures = con.execute(
            "SELECT * FROM fixtures WHERE league = ? AND commence_time > ? AND commence_time <= ?",
            [lg.fd_code, now, latest],
        ).df()
        if fixtures.empty:
            continue
        report.scanned += len(fixtures)
        odds = con.execute(
            f"SELECT * FROM odds WHERE event_id IN ({', '.join('?' for _ in fixtures['event_id'])})",
            list(fixtures["event_id"]),
        ).df()

        def price(home: str, away: str, m=model):
            return m.predict(home, away) if m and m.can_price(home, away, s.min_team_matches) else None

        bets, _ = scan(fixtures, odds, price, rules, s.sharp_bookmaker, s.soft_bookmakers)
        picks.extend(to_picks(bets, fixtures, lg.name))
    report.candidates = len(picks)
    return picks, models


def _reason(con, s: AgentSettings, pick: Pick, models: dict[str, DixonColes], llm_client) -> str:
    model = models.get(pick.league)
    if model is None or pick.p_dc is None:
        sharp = f" against {pick.p_sharp:.0%} implied by Pinnacle" if pick.p_sharp is not None else ""
        return (f"At least one side has too few league matches for the goal model, so this pick rests on the "
                f"de-vigged market alone. {pick.selection_label} is rated {pick.p_model:.0%}{sharp}, "
                f"and {pick.price:.2f} beats that by {pick.ev:+.1%}.")
    facts = build_facts(con, pick, model)
    return write_reasoning(facts, s.anthropic_api_key, s.reasoning_model, s.reasoning_language, llm_client)


def run_daily(
    settings: AgentSettings,
    now: datetime | None = None,
    dry_run: bool = False,
    refresh: bool = True,
    fetch: bool = True,
    telegram: TelegramClient | None = None,
    fd_client=None,
    odds_client=None,
    llm_client=None,
    force: bool = False,
) -> RunReport:
    s = settings
    now = now or store.utcnow()
    report = RunReport(started_at=now, dry_run=dry_run)
    local_today = now.replace(tzinfo=timezone.utc).astimezone(s.tz).date()
    if not dry_run and telegram is None:
        if not s.telegram_configured:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (or use --dry-run)")
        telegram = TelegramClient(s.telegram_bot_token, s.telegram_chat_id)

    with session(s.db_file) as con:
        # Backup runs (and accidental re-runs) stop here once the day's run is done.
        if not dry_run and not force and store.event_done(con, "daily_run", local_today):
            report.skipped = "today's run already completed"
            return report
        if refresh:
            try:
                _refresh_history(con, s, report, fd_client)
            except Exception as exc:  # noqa: BLE001 - stored history is still usable
                report.errors.append(f"history refresh failed: {exc}")
        report.settled = store.settle(con, now)
        if fetch:
            try:
                report.settled += pipeline.fetch_scores(con, s, store.unsettled_sports(con, now), client=odds_client)
            except Exception as exc:  # noqa: BLE001 - football-data will settle them later
                report.errors.append(f"live results unavailable: {exc}")

        for lg in LEAGUES.values():
            try:
                pipeline.train(con, s, lg, ml_kind=None)
                report.trained.append(lg.slug)
            except ValueError as exc:
                report.errors.append(f"train {lg.slug}: {exc}")

        if fetch:
            try:
                reports, quota = pipeline.fetch_odds(con, s, list(LEAGUES.values()), client=odds_client)
                report.odds_events = sum(r.events for r in reports)
                report.credits_remaining = quota.remaining
                report.errors += [f"odds {r.league}: {r.error}" for r in reports if r.error]
                report.errors += [f"odds {r.league}: unmatched {', '.join(r.unmatched)}"
                                  for r in reports if r.unmatched]
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"odds fetch failed, no picks today: {exc}")
                return report

        candidates, models = _candidates(con, s, now, report)
        sent_today = store.sent_count_on(con, local_today, s.tz)
        report.picks = select(candidates, now, store.blocked_fixtures(con), sent_today,
                              s.max_daily_picks, s.pick_horizon_hours, s.min_lead_minutes)

        for pick in report.picks:
            pick.reasoning = _reason(con, s, pick, models, llm_client)
        record_line = format_record(store.track_record(con))
        total = len(report.picks)
        for i, pick in enumerate(report.picks, start=1):
            text = format_pick(pick, s.tz, i, total)
            if i == total:
                text += "\n\n" + record_line
            report.messages.append(text)
            if dry_run:
                continue
            store.record_sending(con, pick.db_row())
            try:
                message_id = asyncio.run(telegram.send(text))
            except Exception as exc:  # noqa: BLE001
                store.mark(con, pick.fixture_key, "failed")
                report.errors.append(f"telegram send failed for {pick.home_team} v {pick.away_team}: {exc}")
                continue
            store.mark(con, pick.fixture_key, "sent", message_id)
            report.sent += 1

        if (not report.picks and s.notify_no_picks and sent_today == 0
                and not store.event_done(con, "no_picks_notice", local_today)):
            text = format_no_picks(now, s.tz, report.scanned, report.candidates) + "\n\n" + record_line
            report.messages.append(text)
            if not dry_run:
                try:
                    asyncio.run(telegram.send(text))
                    store.record_event(con, "no_picks_notice", local_today)
                    report.notices += 1
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"telegram send failed: {exc}")

        if s.report_weekday == local_today.weekday():
            week_start = local_today - timedelta(days=6)
            if not store.event_done_since(con, "weekly_report", week_start) or dry_run:
                report.report_sent = send_report(con, s, telegram, now, report, dry_run)
                if report.report_sent and not dry_run:
                    store.record_event(con, "weekly_report", local_today)
                    report.notices += 1

        # A failed Telegram send leaves the day open, so a backup run can retry it.
        if not dry_run and not any("telegram" in e for e in report.errors):
            store.record_event(con, "daily_run", local_today, f"sent={report.sent}")
    return report


def send_report(con, s: AgentSettings, telegram: TelegramClient | None, now: datetime,
                report: RunReport | None = None, dry_run: bool = False, days: int = 7,
                title: str = "Weekly report") -> bool:
    """Send the summary message plus the CSV history. Returns True on success."""
    text = format_report(build_report(con, s.tz, days=days, now=now), title)
    if report is not None:
        report.messages.append(text)
    if dry_run:
        return True
    csv_path = s.resolve(s.db_path).parent / "picks_history.csv"
    rows = export_csv(con, csv_path)
    try:
        asyncio.run(telegram.send(text))
        if rows:
            asyncio.run(telegram.send_document(csv_path, f"Full pick history · {rows} picks"))
        return True
    except Exception as exc:  # noqa: BLE001
        if report is not None:
            report.errors.append(f"telegram report failed: {exc}")
        return False

