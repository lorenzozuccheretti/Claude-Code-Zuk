"""Closing-line snapshot for recorded picks, taken shortly before kick-off.

Settlement takes the closing line of club 1X2 and Over/Under 2.5 picks from
football-data for free. Picks on other lines (Over 3.5, Under 1.5) and every
national-team pick would never get a CLV. So ``agent closing`` runs every
hour through the match day; for each such pick (sent or sandbox) kicking off
within ``window``, and only when the month's spare credits allow, it fetches that
one event's market (1 credit, 2 if the line needs ``alternate_totals``),
de-vigs Pinnacle, or the soft-book consensus when Pinnacle doesn't quote the
line, and stores::

    clv = price_taken * closing_fair_probability - 1

The snapshot is taken up to ``window`` before kick-off, so it is a close
proxy for the closing line, not the final price. Settlement keeps this
value; football-data's true close only fills picks the snapshot missed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import duckdb
import httpx
import pandas as pd

from src.config import LEAGUES, Settings
from src.data.budget import affordable
from src.data.ingest_odds_api import Event, OddsApiClient, QuotaExhausted, events_to_frames
from src.engine.devig import devig
from src.engine.value import H2H_OUTCOMES, TOTALS_OUTCOMES, fair_from_books

DEFAULT_WINDOW = timedelta(minutes=100)


@dataclass
class ClosingReport:
    captured: int = 0
    due: int = 0
    credits_remaining: int | None = None
    errors: list[str] = field(default_factory=list)


def due_picks(con: duckdb.DuckDBPyConnection, now: datetime, window: timedelta = DEFAULT_WINDOW) -> pd.DataFrame:
    """Recorded picks without a snapshot that kick off within ``window``.

    Club 1X2 and Over/Under 2.5 picks are left out: football-data publishes
    their closing line for free, and settlement turns it into CLV.
    """
    return con.execute(
        "SELECT p.fixture_key, p.event_id, p.league, p.market, p.outcome, p.point, p.price, f.sport_key "
        "FROM sent_picks p JOIN fixtures f USING (event_id) "
        "WHERE p.status IN ('sent', 'sandbox') AND p.closing_at IS NULL "
        "AND (p.league = 'INT' OR (p.market = 'totals' AND p.point <> 2.5)) "
        "AND p.commence_time > ? AND p.commence_time <= ? ORDER BY p.commence_time",
        [now, now + window],
    ).df()


def closing_fair(event: Event, league_code: str, market: str, outcome: str, point: float,
                 sharp_book: str, method: str) -> tuple[float | None, float, str] | None:
    """(sharp price, fair probability, source) of ``outcome`` in ``event``, or None if unpriced."""
    league = next(lg for lg in LEAGUES.values() if lg.fd_code == league_code)
    for bk in event.bookmakers:
        for mk in bk.markets:
            if mk.key == "alternate_totals":
                mk.key = "totals"
    _, odds = events_to_frames([event], league)
    if odds.empty:
        return None
    rows = odds[(odds["market"] == market) & (odds["point"] == float(point))]
    outcomes = H2H_OUTCOMES if market == "h2h" else TOTALS_OUTCOMES
    sharp = rows[rows["bookmaker"] == sharp_book].drop_duplicates("outcome").set_index("outcome")["price"]
    if all(o in sharp.index for o in outcomes):
        fair = dict(zip(outcomes, devig([float(sharp[o]) for o in outcomes], method)))
        return float(sharp[outcome]), float(fair[outcome]), "pinnacle"
    books = {b: g.drop_duplicates("outcome").set_index("outcome")["price"].to_dict()
             for b, g in rows[rows["bookmaker"] != sharp_book].groupby("bookmaker")}
    consensus = fair_from_books(books, outcomes, method)
    if consensus is None:
        return None
    return None, float(consensus[outcome]), "consensus"


def store_closing(con: duckdb.DuckDBPyConnection, key: str, price_taken: float,
                  closing: tuple[float | None, float, str], at: datetime) -> None:
    sharp_price, prob, source = closing
    con.execute(
        "UPDATE sent_picks SET closing_price = ?, closing_prob = ?, closing_source = ?, closing_at = ?, "
        "clv = coalesce(clv, ?) WHERE fixture_key = ?",
        [sharp_price, prob, source, at, float(price_taken * prob - 1.0), key],
    )


def capture(con: duckdb.DuckDBPyConnection, settings: Settings, now: datetime,
            window: timedelta = DEFAULT_WINDOW, client: httpx.AsyncClient | None = None) -> ClosingReport:
    report = ClosingReport()
    due = due_picks(con, now, window)
    report.due = len(due)
    if due.empty:
        return report
    if not affordable(con, now.date(), settings.odds_api_daily_reserve, len(due)):
        report.errors.append("skipped: not enough spare Odds API credits this month")
        return report

    async def run() -> None:
        async with OddsApiClient(settings, con, client=client) as api:
            for p in due.itertuples(index=False):
                label = f"{p.fixture_key} {p.market} {p.outcome}"
                try:
                    event = await api.event_odds(p.sport_key, p.event_id, p.market)
                    found = closing_fair(event, p.league, p.market, p.outcome, p.point,
                                         settings.sharp_bookmaker, settings.devig_method)
                    if found is None and p.market == "totals":
                        event = await api.event_odds(p.sport_key, p.event_id, "alternate_totals")
                        found = closing_fair(event, p.league, p.market, p.outcome, p.point,
                                             settings.sharp_bookmaker, settings.devig_method)
                except httpx.HTTPStatusError as exc:
                    report.errors.append(f"{label}: HTTP {exc.response.status_code}")
                    continue
                except QuotaExhausted as exc:
                    report.errors.append(str(exc))
                    break
                if found is None:
                    report.errors.append(f"{label}: line no longer quoted")
                    continue
                store_closing(con, p.fixture_key, p.price, found, now)
                report.captured += 1
            report.credits_remaining = api.quota.remaining

    asyncio.run(run())
    return report
