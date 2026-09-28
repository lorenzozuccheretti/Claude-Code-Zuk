"""Summary reporting on the picks the agent has sent.

:func:`build_report` aggregates ``sent_picks`` into a :class:`Report`,
:func:`format_report` renders it as one Telegram HTML message, and
:func:`export_csv` writes the full pick history for a spreadsheet.

Two P/L views are reported: flat 1-unit stakes (clean for judging the
picks) and the suggested Kelly stakes as a share of bankroll (what
following the bot to the letter would have done).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from src.config import LEAGUES

LEAGUE_NAMES = {lg.fd_code: lg.name for lg in LEAGUES.values()}
MIN_PICKS_FOR_VERDICT = 50


@dataclass
class Slice:
    label: str
    picks: int
    settled: int
    wins: int
    units: float

    @property
    def yield_pct(self) -> float:
        return 100 * self.units / self.settled if self.settled else 0.0


@dataclass
class Report:
    generated_at: datetime
    period_start: date
    period_end: date
    period: Slice
    overall: Slice
    pending: int
    avg_odds: float | None
    avg_ev: float | None
    avg_clv: float | None
    clv_count: int
    kelly_bankroll_pct: float  # compounded bankroll change following the Kelly stakes
    by_competition: list[Slice] = field(default_factory=list)
    by_market: list[Slice] = field(default_factory=list)
    by_month: list[Slice] = field(default_factory=list)
    recent: pd.DataFrame = field(default_factory=pd.DataFrame)


def _load(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute("SELECT * FROM sent_picks WHERE status = 'sent' ORDER BY commence_time").df()
    if df.empty:
        return df
    df["commence_time"] = pd.to_datetime(df["commence_time"])
    df["sent_at"] = pd.to_datetime(df["sent_at"])
    df["market_label"] = df["market"].map({"h2h": "1X2", "totals": "Over/Under"}).fillna(df["market"])
    df["competition"] = df["league"].map(LEAGUE_NAMES).fillna(df["league"])
    return df


def _slice(label: str, df: pd.DataFrame) -> Slice:
    done = df[df["won"].notna()]
    return Slice(label, len(df), len(done), int(done["won"].astype(bool).sum()),
                 float(done["profit_units"].sum()) if len(done) else 0.0)


def build_report(con: duckdb.DuckDBPyConnection, tz: ZoneInfo, days: int = 7,
                 now: datetime | None = None, recent: int = 10) -> Report:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    end = local_now.date()
    start = end - timedelta(days=days - 1)
    df = _load(con)
    if df.empty:
        empty = Slice("", 0, 0, 0, 0.0)
        return Report(now, start, end, empty, empty, 0, None, None, None, 0, 0.0)

    local_kick = df["commence_time"].dt.tz_localize("UTC").dt.tz_convert(tz).dt.date
    in_period = (local_kick >= start) & (local_kick <= end)
    settled = df[df["won"].notna()].sort_values("commence_time")
    growth = float(((1 + settled["stake_pct"] * settled["profit_units"]).prod() - 1) * 100) if len(settled) else 0.0
    clv = pd.to_numeric(df["clv"], errors="coerce").dropna()
    month = df["commence_time"].dt.tz_localize("UTC").dt.tz_convert(tz).dt.strftime("%Y-%m")
    return Report(
        generated_at=now, period_start=start, period_end=end,
        period=_slice(f"{start:%d %b}–{end:%d %b}", df[in_period]),
        overall=_slice("All time", df),
        pending=int(df["won"].isna().sum()),
        avg_odds=float(df["price"].mean()),
        avg_ev=float(df["ev"].mean()),
        avg_clv=float(clv.mean()) if len(clv) else None,
        clv_count=len(clv),
        kelly_bankroll_pct=growth,
        by_competition=[_slice(k, g) for k, g in df.groupby("competition")],
        by_market=[_slice(k, g) for k, g in df.groupby("market_label")],
        by_month=[_slice(k, g) for k, g in df.groupby(month)],
        recent=df.tail(recent).iloc[::-1],
    )


def _pick_label(r) -> str:
    if r.market == "h2h":
        return {"home": "1", "draw": "X", "away": "2"}[r.outcome]
    return f"{'O' if r.outcome == 'over' else 'U'}{r.point:g}"


def _result_icon(won) -> str:
    if pd.isna(won):
        return "⏳"
    return "✅" if won else "❌"


def _line(s: Slice) -> str:
    if not s.picks:
        return "no picks"
    record = f"{s.wins}/{s.settled} won" if s.settled else "none settled yet"
    units = f", {s.units:+.2f}u (yield {s.yield_pct:+.1f}%)" if s.settled else ""
    pending = f", {s.picks - s.settled} pending" if s.picks > s.settled else ""
    return f"{s.picks} picks · {record}{units}{pending}"


def _verdict(r: Report) -> str:
    if r.avg_clv is None or r.clv_count < MIN_PICKS_FOR_VERDICT:
        n = r.clv_count
        return (f"Too early to judge: {n} pick{'s' if n != 1 else ''} with closing-line data, "
                f"{MIN_PICKS_FOR_VERDICT} needed for a meaningful verdict.")
    if r.avg_clv > 0.01:
        return "Average CLV is positive: the bot is beating the closing line, the best sign of a real edge."
    if r.avg_clv < -0.01:
        return "Average CLV is negative: the bot is not beating the closing line; profits so far are likely luck."
    return "Average CLV is about zero: no clear edge over the closing line yet."


def format_report(r: Report, title: str = "Weekly report") -> str:
    e = lambda t: html.escape(str(t), quote=False)  # noqa: E731
    if r.overall.picks == 0:
        return f"📊 <b>{e(title)}</b>\n\nNo picks have been sent yet."
    lines = [
        f"📊 <b>{e(title)}</b> · {r.period_start:%d %b} – {r.period_end:%d %b %Y}",
        "",
        f"🗓 <b>This period:</b> {e(_line(r.period))}",
        f"📒 <b>All time:</b> {e(_line(r.overall))}",
        f"💰 Following the Kelly stakes: bankroll <b>{r.kelly_bankroll_pct:+.1f}%</b>",
        f"📊 Avg odds {r.avg_odds:.2f} · avg predicted EV {r.avg_ev:+.1%}"
        + (f" · avg CLV <b>{r.avg_clv:+.1%}</b> ({r.clv_count})" if r.avg_clv is not None else ""),
        "",
        "<b>By competition</b>",
        *[f"• {e(s.label)}: {e(_line(s))}" for s in r.by_competition],
        "<b>By market</b>",
        *[f"• {e(s.label)}: {e(_line(s))}" for s in r.by_market],
    ]
    if len(r.by_month) > 1:
        lines += ["<b>By month</b>", *[f"• {e(s.label)}: {e(_line(s))}" for s in r.by_month[-6:]]]
    lines += ["", f"<b>Last {len(r.recent)} picks</b>"]
    for row in r.recent.itertuples():
        lines.append(f"{_result_icon(row.won)} {row.commence_time:%d/%m} {e(row.home_team)}–{e(row.away_team)} "
                     f"{_pick_label(row)} @ {row.price:.2f}")
    lines += ["", f"🔎 <i>{e(_verdict(r))}</i>"]
    return "\n".join(lines)


CSV_COLUMNS = {
    "commence_time": "kickoff_utc", "competition": "competition", "home_team": "home", "away_team": "away",
    "market_label": "market", "outcome": "selection", "point": "line", "bookmaker": "bookmaker", "price": "odds",
    "p_model": "engine_prob", "ev": "expected_value", "stake_pct": "kelly_stake_pct", "won": "won",
    "profit_units": "profit_units", "clv": "clv", "sent_at": "sent_at_utc",
}


def export_csv(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Full pick history as CSV (opens directly in Excel / Google Sheets)."""
    df = _load(con)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[list(CSV_COLUMNS)].rename(columns=CSV_COLUMNS) if not df.empty else pd.DataFrame(columns=list(CSV_COLUMNS.values()))
    out.to_csv(path, index=False)
    return len(out)
