"""Odds API credit pacing for the free plan (500 credits, reset on the 1st).

The daily odds fetch is what the agent exists for, so it always runs (down
to the client's hard floor). Everything else - live results, settlement
from scores, closing-line snapshots - is optional and only spends credits
the rest of the month's odds fetches won't need: ``daily_reserve`` credits
are kept back for every day left in the month, today included.
"""

from __future__ import annotations

import calendar
from datetime import date

import duckdb

from src.data.ingest_odds_api import last_known_remaining


def days_left_in_month(today: date) -> int:
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


def spare_credits(remaining: int | None, today: date, daily_reserve: int) -> int | None:
    """Credits free for optional calls; None when the balance is unknown."""
    if remaining is None:
        return None
    return remaining - days_left_in_month(today) * daily_reserve


def affordable(con: duckdb.DuckDBPyConnection, today: date, daily_reserve: int, cost: int) -> bool:
    spare = spare_credits(last_known_remaining(con), today, daily_reserve)
    return spare is None or spare >= cost
