"""Async client for The Odds API (v4) with quota tracking.

Each odds call costs ``len(markets) * len(regions)`` credits. The API
reports the account's usage in the ``x-requests-used``,
``x-requests-remaining`` and ``x-requests-last`` headers on every response;
we log each one to the ``api_usage`` table and refuse to call again once the
remaining quota drops to ``Settings.odds_api_min_remaining``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import duckdb
import httpx
import pandas as pd
from pydantic import BaseModel

from src.config import League, Settings
from src.data.database import upsert_df

log = logging.getLogger(__name__)


class QuotaExhausted(RuntimeError):
    """Raised before a request that would dip below the configured credit floor."""


# --- Response schemas ------------------------------------------------------


class Outcome(BaseModel):
    name: str
    price: float
    point: float | None = None


class Market(BaseModel):
    key: str
    last_update: datetime | None = None
    outcomes: list[Outcome]


class Bookmaker(BaseModel):
    key: str
    title: str = ""
    last_update: datetime | None = None
    markets: list[Market] = []


class Event(BaseModel):
    id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[Bookmaker] = []


class Sport(BaseModel):
    key: str
    group: str = ""
    title: str = ""
    active: bool = True


# --- Quota ------------------------------------------------------------------


@dataclass
class Quota:
    used: int | None = None
    remaining: int | None = None
    last_cost: int | None = None

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "Quota":
        def num(key: str) -> int | None:
            value = headers.get(key)
            try:
                return int(float(value)) if value is not None else None
            except ValueError:
                return None

        return cls(num("x-requests-used"), num("x-requests-remaining"), num("x-requests-last"))


def last_known_remaining(con: duckdb.DuckDBPyConnection) -> int | None:
    row = con.execute(
        "SELECT requests_remaining FROM api_usage WHERE requests_remaining IS NOT NULL "
        "ORDER BY requested_at DESC LIMIT 1"
    ).fetchone()
    return None if row is None else int(row[0])


# --- Client -----------------------------------------------------------------


@dataclass
class OddsApiClient:
    settings: Settings
    con: duckdb.DuckDBPyConnection
    client: httpx.AsyncClient | None = None
    max_retries: int = 3
    backoff_seconds: float = 1.0
    quota: Quota = field(default_factory=Quota)

    def __post_init__(self) -> None:
        if not self.settings.odds_api_key or self.settings.odds_api_key == "your_key_here":
            raise ValueError("ODDS_API_KEY is not set; copy .env.example to .env and add your key")
        remaining = last_known_remaining(self.con)
        self.quota = Quota(remaining=remaining)

    async def __aenter__(self) -> "OddsApiClient":
        self._own_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.settings.odds_api_base_url, timeout=30)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._own_client and self.client is not None:
            await self.client.aclose()

    def _check_quota(self, cost: int) -> None:
        remaining = self.quota.remaining
        if remaining is not None and remaining - cost < self.settings.odds_api_min_remaining:
            raise QuotaExhausted(
                f"Only {remaining} Odds API credits left (floor {self.settings.odds_api_min_remaining}); "
                "lower ODDS_API_MIN_REMAINING or wait for the monthly reset"
            )

    def _record(self, endpoint: str, resp: httpx.Response) -> None:
        q = Quota.from_headers(resp.headers)
        if q.remaining is not None:
            self.quota = q
        self.con.execute(
            "INSERT INTO api_usage VALUES (?, ?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc).replace(tzinfo=None), endpoint, resp.status_code, q.used, q.remaining, q.last_cost],
        )

    async def _get(self, path: str, params: dict[str, Any], cost: int) -> Any:
        assert self.client is not None, "use 'async with OddsApiClient(...)'"
        self._check_quota(cost)
        query = {"apiKey": self.settings.odds_api_key, **params}
        url = f"{self.settings.odds_api_base_url.rstrip('/')}/{path.lstrip('/')}"
        for attempt in range(self.max_retries + 1):
            resp = await self.client.get(url, params=query)
            self._record(path, resp)
            # 429 = too many requests per second; back off and retry.
            if resp.status_code == 429 and attempt < self.max_retries:
                await asyncio.sleep(self.backoff_seconds * 2**attempt)
                continue
            if resp.status_code == 401:
                raise PermissionError("The Odds API rejected the key (401)")
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    async def sports(self) -> list[Sport]:
        """``GET /v4/sports`` (free: costs no credits)."""
        data = await self._get("sports", {}, cost=0)
        return [Sport.model_validate(s) for s in data]

    async def odds(self, sport_key: str) -> list[Event]:
        """``GET /v4/sports/{sport}/odds`` for the configured markets and regions."""
        markets = self.settings.odds_api_markets
        regions = self.settings.odds_api_regions
        cost = len(markets.split(",")) * len(regions.split(","))
        data = await self._get(
            f"sports/{sport_key}/odds",
            {"regions": regions, "markets": markets, "oddsFormat": "decimal", "dateFormat": "iso"},
            cost=cost,
        )
        return [Event.model_validate(e) for e in data]


# --- Flattening -------------------------------------------------------------


def _naive_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def events_to_frames(
    events: list[Event], league: League, fetched_at: datetime | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flatten events into ``fixtures`` and ``odds`` rows.

    h2h outcome names are the team names, so they are mapped to
    ``home``/``draw``/``away`` here; totals keep ``over``/``under`` plus the
    line in ``point``. Unknown outcome names are dropped.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc).replace(tzinfo=None)
    fixtures, odds = [], []
    for ev in events:
        fixtures.append(
            {
                "event_id": ev.id,
                "league": league.fd_code,
                "sport_key": ev.sport_key,
                "commence_time": _naive_utc(ev.commence_time),
                "home_team_raw": ev.home_team,
                "away_team_raw": ev.away_team,
                "fetched_at": fetched_at,
            }
        )
        side = {ev.home_team: "home", ev.away_team: "away", "Draw": "draw"}
        for bk in ev.bookmakers:
            for mk in bk.markets:
                for oc in mk.outcomes:
                    if mk.key == "h2h":
                        outcome, point = side.get(oc.name), 0.0
                    elif mk.key == "totals" and oc.point is not None:
                        outcome, point = oc.name.lower(), float(oc.point)
                    else:
                        continue
                    if outcome not in ("home", "draw", "away", "over", "under") or oc.price <= 1.0:
                        continue
                    odds.append(
                        {
                            "event_id": ev.id,
                            "bookmaker": bk.key,
                            "market": mk.key,
                            "outcome": outcome,
                            "point": point,
                            "price": oc.price,
                            "last_update": _naive_utc(mk.last_update or bk.last_update),
                            "fetched_at": fetched_at,
                        }
                    )
    return pd.DataFrame(fixtures), pd.DataFrame(odds)


def store_snapshot(con: duckdb.DuckDBPyConnection, fixtures: pd.DataFrame, odds: pd.DataFrame) -> None:
    """Replace the stored odds of these events with the fresh snapshot."""
    if fixtures.empty:
        return
    ids = fixtures["event_id"].tolist()
    placeholders = ", ".join("?" for _ in ids)
    con.execute(f"DELETE FROM odds WHERE event_id IN ({placeholders})", ids)
    upsert_df(con, "fixtures", fixtures)
    upsert_df(con, "odds", odds)
