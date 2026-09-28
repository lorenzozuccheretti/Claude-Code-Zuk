"""``python main.py agent ...``: the daily Telegram agent."""

from __future__ import annotations

import asyncio
import html
import re
from datetime import timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from src.agent import store
from src.agent.report import build_report, export_csv, format_report
from src.agent.runner import RunReport, run_daily, send_report
from src.agent.scheduler import build_scheduler
from src.agent.settings import get_agent_settings
from src.agent.telegram import TelegramClient, TelegramError, format_record
from src.cli.app import _fail, console
from src.data.database import session

agent_app = typer.Typer(help="Daily value-pick agent that posts to Telegram.", no_args_is_help=True)


def _plain(html_text: str) -> str:
    """Telegram HTML -> terminal text for dry runs."""
    return escape(html.unescape(re.sub(r"</?[bi]>", "", html_text)))


def _print_report(r: RunReport) -> None:
    mode = "[yellow]DRY RUN[/] - nothing sent" if r.dry_run else f"{r.sent} message(s) sent"
    console.print(f"[bold]Agent run[/] {r.started_at:%Y-%m-%d %H:%M} UTC · {mode}")
    if r.skipped:
        console.print(f"Nothing to do: {r.skipped} (use --force to run again).")
        return
    credits = f", {r.credits_remaining} Odds API credits left" if r.credits_remaining is not None else ""
    console.print(f"History +{r.history_rows} rows · {r.settled} picks settled · models: {len(r.trained)} leagues · "
                  f"{r.odds_events} events priced{credits}")
    console.print(f"{r.scanned} fixtures in the window · {r.candidates} passed the value filters · "
                  f"{len(r.picks)} selected")
    for text in r.messages:
        console.rule()
        console.print(_plain(text))
    if r.errors:
        console.rule("[yellow]warnings")
        for e in r.errors:
            console.print(f"[yellow]•[/] {escape(e)}")


@agent_app.command("run")
def run(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Compute and print the picks without sending or recording")] = False,
    refresh: Annotated[bool, typer.Option(help="Refresh football-data history first")] = True,
    fetch: Annotated[bool, typer.Option(help="Fetch fresh odds (2 credits per league)")] = True,
    force: Annotated[bool, typer.Option("--force", help="Run even if today's run already completed")] = False,
) -> None:
    """Run the daily job once: refresh, train, fetch odds, pick, send."""
    s = get_agent_settings()
    try:
        report = run_daily(s, dry_run=dry_run, refresh=refresh, fetch=fetch, force=force)
    except ValueError as exc:
        _fail(str(exc))
    _print_report(report)
    if report.errors and not report.dry_run and report.sent == 0 and report.picks:
        raise typer.Exit(1)


@agent_app.command("schedule")
def schedule(
    run_now: Annotated[bool, typer.Option("--run-now", help="Also run once immediately")] = False,
) -> None:
    """Stay running and fire the daily job at RUN_TIME in TIMEZONE."""
    s = get_agent_settings()
    if not s.telegram_configured:
        _fail("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    def job() -> None:
        _print_report(run_daily(s))

    scheduler = build_scheduler(s, job)
    console.print(f"Scheduled daily at {s.run_time} {s.timezone}. Ctrl+C to stop.")
    if run_now:
        job()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("Stopped.")


@agent_app.command("test-telegram")
def test_telegram() -> None:
    """Send a test message to check the bot token and chat id."""
    s = get_agent_settings()
    if not s.telegram_configured:
        _fail("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    try:
        mid = asyncio.run(TelegramClient(s.telegram_bot_token, s.telegram_chat_id).send(
            "✅ <b>Football value agent</b> is connected. Daily picks will arrive here."))
    except TelegramError as exc:
        _fail(str(exc))
    console.print(f"[green]Sent[/] (message id {mid}).")


@agent_app.command("report")
def report(
    send: Annotated[bool, typer.Option("--send", help="Send it to Telegram with the CSV history attached")] = False,
    days: Annotated[int, typer.Option(help="Length of the 'this period' window in days")] = 7,
    csv: Annotated[Optional[Path], typer.Option(help="Also write the full history to this CSV file")] = None,
) -> None:
    """Summary of every pick sent so far: results, P/L, CLV, by competition and market."""
    s = get_agent_settings()
    if send and not s.telegram_configured:
        _fail("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    with session(s.db_file) as con:
        store.settle(con)
        title = "Weekly report" if days == 7 else f"Report ({days} days)"
        text = format_report(build_report(con, s.tz, days=days), title)
        console.print(_plain(text))
        if csv:
            console.print(f"Wrote {export_csv(con, csv)} picks to {csv}")
        if send:
            tg = TelegramClient(s.telegram_bot_token, s.telegram_chat_id)
            if not send_report(con, s, tg, store.utcnow(), days=days, title=title):
                _fail("Telegram send failed")
            console.print("[green]Report sent to Telegram.[/]")


@agent_app.command("history")
def history(limit: Annotated[int, typer.Option(help="Rows to show")] = 30) -> None:
    """Show dispatched picks and the running track record."""
    s = get_agent_settings()
    with session(s.db_file) as con:
        store.settle(con)
        rows = con.execute(
            "SELECT sent_at, home_team, away_team, market, outcome, point, bookmaker, price, ev, status, "
            "won, profit_units, clv FROM sent_picks ORDER BY sent_at DESC LIMIT ?", [limit]).df()
        record = store.track_record(con)
    table = Table("Sent (local)", "Match", "Pick", "Book", "Odds", "EV", "Status", "Result", "P/L u", "CLV",
                  title="Dispatched picks")
    for r in rows.itertuples():
        pick = r.outcome if r.market == "h2h" else f"{r.outcome} {r.point:g}"
        result = "-" if r.won is None or r.won != r.won else ("[green]won[/]" if r.won else "[red]lost[/]")
        pl = "-" if r.profit_units != r.profit_units or r.profit_units is None else f"{r.profit_units:+.2f}"
        clv = "-" if r.clv is None or r.clv != r.clv else f"{r.clv:+.1%}"
        sent = r.sent_at.replace(tzinfo=timezone.utc).astimezone(s.tz)
        table.add_row(f"{sent:%d %b %H:%M}", f"{r.home_team} v {r.away_team}", pick, r.bookmaker,
                      f"{r.price:.2f}", f"{r.ev:+.1%}", r.status, result, pl, clv)
    console.print(table)
    console.print(_plain(format_record(record)))
