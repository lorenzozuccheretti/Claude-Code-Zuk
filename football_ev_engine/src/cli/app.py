"""Typer + Rich command-line interface.

Run ``python main.py --help`` for the command list.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from src import pipeline
from src.backtest.backtester import BacktestConfig
from src.config import LEAGUES, get_league, get_settings, parse_season, recent_seasons, resolve_leagues, season_label
from src.data.alias_matcher import export_aliases, import_aliases, set_manual_alias
from src.data.database import session
from src.data.ingest_odds_api import QuotaExhausted
from src.engine.value import ValueRules

app = typer.Typer(help="Football +EV engine: Dixon-Coles pricing, de-vigging, value detection, Kelly staking.",
                  no_args_is_help=True, add_completion=False)
console = Console()

LeagueOpt = Annotated[str, typer.Option("--league", "-l", help=f"One of: {', '.join(LEAGUES)}")]
LeaguesOpt = Annotated[Optional[str], typer.Option("--league", "-l", help="Comma-separated leagues, or 'all'")]


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False)])


def _fail(msg: str) -> None:
    console.print(f"[bold red]Error:[/] {msg}")
    raise typer.Exit(1)


def _pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


@app.command("init-db")
def init_db() -> None:
    """Create the DuckDB database and all tables."""
    s = get_settings()
    with session(s.db_file) as con:
        n = import_aliases(con, s.resolve(s.aliases_json))
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    console.print(f"[green]Database ready[/] at {s.db_file}")
    console.print("Tables: " + ", ".join(sorted(tables)))
    if n:
        console.print(f"Restored {n} team aliases from {s.aliases_json}")


@app.command("fetch-historical")
def fetch_historical(
    league: LeaguesOpt = None,
    seasons: Annotated[Optional[str], typer.Option(help="Comma-separated, e.g. 2023-2024,2024-2025")] = None,
    n_seasons: Annotated[Optional[int], typer.Option("--n-seasons", help="Current season plus N-1 before it")] = None,
) -> None:
    """Download match results and odds from football-data.co.uk."""
    s = get_settings()
    try:
        leagues = resolve_leagues(league)
        season_list = ([season_label(parse_season(x)[0]) for x in seasons.split(",")] if seasons
                       else recent_seasons(n_seasons or s.history_seasons))
    except (KeyError, ValueError) as exc:
        _fail(str(exc))
    with console.status(f"Downloading {len(leagues) * len(season_list)} files..."):
        with session(s.db_file) as con:
            results = pipeline.fetch_historical(con, s, leagues, season_list)
            total = con.execute("SELECT count(*) FROM matches").fetchone()[0]
    table = Table("League", "Season", "Matches", "Status", title="football-data.co.uk")
    for r in results:
        table.add_row(r.league, r.season, str(r.rows), "[green]ok[/]" if r.error is None else f"[yellow]{r.error}[/]")
    console.print(table)
    console.print(f"{total} matches in the database.")


@app.command("fetch-odds")
def fetch_odds(league: LeaguesOpt = None) -> None:
    """Fetch upcoming odds from The Odds API and map team names to history."""
    s = get_settings()
    try:
        leagues = resolve_leagues(league)
        with console.status("Calling The Odds API..."), session(s.db_file) as con:
            reports, quota = pipeline.fetch_odds(con, s, leagues)
            export_aliases(con, s.resolve(s.aliases_json))
    except (KeyError, ValueError, PermissionError, QuotaExhausted) as exc:
        _fail(str(exc))
    table = Table("League", "Events", "Prices", "Notes", title="The Odds API")
    for r in reports:
        notes = r.error or ""
        if r.unmatched:
            notes += f"[red]unmatched: {', '.join(r.unmatched)}[/] "
        if r.fuzzy:
            notes += "fuzzy: " + ", ".join(f"{a}->{b} ({sc:.0f})" for a, b, sc in r.fuzzy)
        table.add_row(r.league, str(r.events), str(r.prices), notes)
    console.print(table)
    if quota.remaining is not None:
        console.print(f"Credits: {quota.remaining} remaining, {quota.used} used (last call cost {quota.last_cost}).")
    if any(r.unmatched for r in reports):
        console.print("Fix unmatched names with: python main.py aliases --league <league> --set 'API Name=History Name'")


@app.command()
def train(
    league: LeaguesOpt = None,
    ml: Annotated[str, typer.Option(help="ML benchmark: logreg, gbm or none")] = "logreg",
) -> None:
    """Fit Dixon-Coles (and the ML benchmark) on stored history."""
    s = get_settings()
    try:
        leagues = resolve_leagues(league)
    except KeyError as exc:
        _fail(str(exc))
    table = Table("League", "Matches", "Teams", "Home adv", "rho", "Converged", "Holdout Brier DC / ML",
                  title="Dixon-Coles fit")
    with session(s.db_file) as con:
        for lg in leagues:
            try:
                with console.status(f"Fitting {lg.name}..."):
                    r = pipeline.train(con, s, lg, None if ml == "none" else ml)
            except ValueError as exc:
                table.add_row(lg.slug, "-", "-", "-", "-", f"[yellow]{exc}[/]", "-")
                continue
            h = r.ml_holdout
            holdout = f"{h['brier_dc']:.4f} / {h['brier_ml']:.4f} (n={h['n']:.0f})" if h else "-"
            table.add_row(lg.slug, str(r.matches), str(r.teams), f"{r.home_advantage:+.3f}", f"{r.rho:+.3f}",
                          "[green]yes[/]" if r.converged else "[red]no[/]", holdout)
    console.print(table)


@app.command()
def predict(
    league: LeagueOpt = "serie-a",
    bankroll: Annotated[Optional[float], typer.Option(help="Bankroll for stake sizing")] = None,
    min_ev: Annotated[Optional[float], typer.Option(help="Minimum EV, e.g. 0.03")] = None,
    model_weight: Annotated[Optional[float], typer.Option(help="Weight of Dixon-Coles vs the sharp price (0-1)")] = None,
    show_skipped: Annotated[bool, typer.Option(help="List fixtures that couldn't be priced")] = False,
) -> None:
    """Price upcoming fixtures and show the +EV bets."""
    s = get_settings()
    try:
        lg = get_league(league)
        rules = ValueRules.from_settings(s, min_ev=min_ev, model_weight=model_weight)
        bank = bankroll or s.bankroll
        with session(s.db_file) as con:
            result = pipeline.predict(con, s, lg, rules, bank)
    except (KeyError, ValueError) as exc:
        _fail(str(exc))

    console.print(f"[bold]{lg.name}[/]: {result.fixtures} upcoming fixtures, "
                  f"model weight {rules.model_weight:.2f}, min EV {_pct(rules.min_ev)}, "
                  f"odds {rules.min_odds:.2f}-{rules.max_odds:.2f}, P >= {rules.min_prob:.2f}")
    if result.bets.empty:
        console.print("[yellow]No bets pass the filters.[/] That is the normal outcome against efficient markets.")
    else:
        table = Table("Kick-off (UTC)", "Match", "Market", "Pick", "Book", "Odds", "P DC", "P Pin", "P model",
                      "EV", "Stake", title="+EV bets")
        for b in result.bets.itertuples():
            pick = b.outcome if b.market == "h2h" else f"{b.outcome} {b.point:g}"
            pin = _pct(b.p_sharp) + ("*" if b.sharp_source == "consensus" else "")
            table.add_row(f"{b.commence_time:%a %d %b %H:%M}", f"{b.home_team} v {b.away_team}", b.market, pick,
                          b.bookmaker, f"{b.price:.2f}", _pct(b.p_dc), pin, _pct(b.p_model),
                          f"[green]{_pct(b.ev)}[/]", f"{b.stake:.2f} ({_pct(b.stake_pct, 2)})")
        console.print(table)
        console.print(f"Total stake {result.bets['stake'].sum():.2f} of {bank:.2f}. "
                      "* = no Pinnacle price; soft-book consensus used instead.")
    if result.notes and show_skipped:
        for note in result.notes:
            console.print(f"  [dim]{note}[/]")
    elif result.notes:
        console.print(f"[dim]{len(result.notes)} fixtures/markets skipped (--show-skipped to list).[/]")


@app.command()
def backtest(
    league: LeagueOpt = "serie-a",
    seasons: Annotated[str, typer.Option(help="Test seasons, e.g. 2023-2024,2024-2025")] = "2023-2024,2024-2025",
    bankroll: Annotated[Optional[float], typer.Option()] = None,
    model_weight: Annotated[Optional[float], typer.Option(help="Weight of Dixon-Coles vs the sharp price (0-1)")] = None,
    min_ev: Annotated[Optional[float], typer.Option()] = None,
    refit_days: Annotated[int, typer.Option(help="Refit the model every N days")] = 7,
    totals: Annotated[bool, typer.Option(help="Also bet Over/Under 2.5")] = True,
    ml: Annotated[str, typer.Option(help="ML benchmark: logreg, gbm or none")] = "logreg",
) -> None:
    """Walk-forward out-of-sample simulation: Yield, ROI, Brier, drawdown."""
    s = get_settings()
    try:
        lg = get_league(league)
        season_list = [season_label(parse_season(x)[0]) for x in seasons.split(",")]
        rules = ValueRules.from_settings(s, min_ev=min_ev, model_weight=model_weight)
        config = BacktestConfig(seasons=season_list, rules=rules, bankroll=bankroll or s.bankroll,
                                refit_every_days=refit_days, training_window_days=s.training_window_days,
                                xi=s.decay_xi, max_goals=s.max_goals, include_totals=totals,
                                min_team_matches=s.min_team_matches,
                                ml_kind=None if ml == "none" else ml)
        with console.status(f"Backtesting {lg.name} {', '.join(season_list)}..."), session(s.db_file) as con:
            result = pipeline.backtest(con, s, lg, config)
    except (KeyError, ValueError) as exc:
        _fail(str(exc))

    m = result.metrics
    perf = Table("Metric", "Value", title=f"{lg.name} backtest {', '.join(season_list)}")
    rows = [
        ("Matches priced", f"{m['matches_priced']:.0f}"),
        ("Bets", f"{m['bets']:.0f}"),
        ("Total staked", f"{m['staked']:.2f}"),
        ("Profit", f"{m['profit']:+.2f}"),
        ("Yield (profit / staked)", f"{m['yield_pct']:+.2f}%"),
        ("ROI (bankroll growth)", f"{m['roi_pct']:+.2f}%"),
        ("Final bankroll", f"{m['final_bankroll']:.2f}"),
        ("Max drawdown", f"{m['max_drawdown_pct']:.2f}%"),
    ]
    for key, label in (("hit_rate_pct", "Hit rate"), ("avg_odds", "Average odds"),
                       ("avg_ev_pct", "Average predicted EV"), ("avg_clv_pct", "Average CLV vs Pinnacle close")):
        if key in m:
            fmt = {"avg_odds": "{:.2f}", "hit_rate_pct": "{:.2f}%"}.get(key, "{:+.2f}%")
            rows.append((label, fmt.format(m[key])))
    for label, value in rows:
        perf.add_row(label, value)
    console.print(perf)

    if "matches_scored" in m:
        cal = Table("Model", "Brier (1X2)", "Log loss", title=f"Probability quality on {m['matches_scored']:.0f} matches")
        names = {"dc": "Dixon-Coles", "mkt": "Pinnacle (de-vigged)", "blend": f"Blend (w={rules.model_weight:.2f})",
                 "ml": f"ML benchmark ({ml})"}
        for prefix, name in names.items():
            if f"brier_{prefix}" in m:
                cal.add_row(name, f"{m[f'brier_{prefix}']:.4f}", f"{m[f'logloss_{prefix}']:.4f}")
        console.print(cal)
        console.print("[dim]Lower is better. If Pinnacle beats Dixon-Coles, keep --model-weight low.[/]")


@app.command()
def aliases(
    league: LeagueOpt = "serie-a",
    set_: Annotated[Optional[str], typer.Option("--set", help="Pin a mapping: 'API Name=History Name'")] = None,
) -> None:
    """List team-name mappings, or pin one by hand."""
    s = get_settings()
    try:
        lg = get_league(league)
    except KeyError as exc:
        _fail(str(exc))
    with session(s.db_file) as con:
        if set_:
            raw, sep, canon = set_.partition("=")
            if not sep or not raw.strip() or not canon.strip():
                _fail("Use --set 'API Name=History Name'")
            known = {r[0] for r in con.execute(
                "SELECT home_team FROM matches WHERE league = ? UNION SELECT away_team FROM matches WHERE league = ?",
                [lg.fd_code, lg.fd_code]).fetchall()}
            if canon.strip() not in known:
                _fail(f"{canon.strip()!r} is not a team in {lg.name} history")
            set_manual_alias(con, lg.fd_code, raw.strip(), canon.strip())
            con.execute("UPDATE fixtures SET home_team = ? WHERE league = ? AND home_team_raw = ?",
                        [canon.strip(), lg.fd_code, raw.strip()])
            con.execute("UPDATE fixtures SET away_team = ? WHERE league = ? AND away_team_raw = ?",
                        [canon.strip(), lg.fd_code, raw.strip()])
            export_aliases(con, s.resolve(s.aliases_json))
            console.print(f"[green]Pinned[/] {raw.strip()} -> {canon.strip()}")
        rows = con.execute(
            "SELECT raw_name, canonical_name, method, score, verified FROM team_aliases WHERE league = ? "
            "ORDER BY verified, raw_name", [lg.fd_code]).fetchall()
    table = Table("API name", "History name", "Method", "Score", "Verified", title=f"{lg.name} aliases")
    for raw, canon, method, score, verified in rows:
        table.add_row(raw, canon, method, f"{score:.0f}", "yes" if verified else "[yellow]review[/]")
    console.print(table)


# Registered last: agent_app imports console and _fail from this module.
from src.cli.agent_app import agent_app  # noqa: E402

app.add_typer(agent_app, name="agent")
