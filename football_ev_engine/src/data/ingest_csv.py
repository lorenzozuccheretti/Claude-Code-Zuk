"""Download and parse football-data.co.uk season CSVs.

URL pattern: ``{base}/{season_code}/{division}.csv``, e.g.
``https://www.football-data.co.uk/mmz4281/2425/I1.csv``.

The column set drifts between seasons (closing odds only exist from
2019-20, some files carry trailing empty columns, dates switch between
``dd/mm/yy`` and ``dd/mm/yyyy``), so parsing is tolerant: known columns are
mapped when present and left NULL otherwise.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from src.config import League, season_code

log = logging.getLogger(__name__)

# football-data column -> our column.
COLUMN_MAP: dict[str, str] = {
    "Date": "match_date",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "fthg",
    "FTAG": "ftag",
    "FTR": "ftr",
    "HS": "hs",
    "AS": "as_",
    "HST": "hst",
    "AST": "ast",
    "B365H": "b365h",
    "B365D": "b365d",
    "B365A": "b365a",
    "B365CH": "b365ch",
    "B365CD": "b365cd",
    "B365CA": "b365ca",
    "PSH": "psh",
    "PSD": "psd",
    "PSA": "psa",
    "PSCH": "psch",
    "PSCD": "pscd",
    "PSCA": "psca",
    "AvgH": "avgh",
    "AvgD": "avgd",
    "AvgA": "avga",
    "B365>2.5": "b365_over25",
    "B365<2.5": "b365_under25",
    "P>2.5": "ps_over25",
    "P<2.5": "ps_under25",
    "PC>2.5": "psc_over25",
    "PC<2.5": "psc_under25",
}
# Older files name the market average differently.
FALLBACK_COLUMNS: dict[str, str] = {"BbAvH": "avgh", "BbAvD": "avgd", "BbAvA": "avga"}
REQUIRED = ("match_date", "home_team", "away_team", "fthg", "ftag")
INT_COLUMNS = ("fthg", "ftag", "hs", "as_", "hst", "ast")


@dataclass
class FetchResult:
    league: str
    season: str
    rows: int
    error: str | None = None


def csv_url(base_url: str, league: League, season: str) -> str:
    return f"{base_url.rstrip('/')}/{season_code(season)}/{league.fd_code}.csv"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode CSV")  # latin-1 never fails; kept for type checkers


def parse_csv(raw: bytes | str, league_code: str, season: str) -> pd.DataFrame:
    """Turn one raw season file into rows shaped like the ``matches`` table.

    Unplayed or malformed rows (no score, no date) are dropped. Odds that are
    missing, non-numeric or ``<= 1`` are set to NaN.
    """
    text = _decode(raw) if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df.columns = [str(c).strip() for c in df.columns]

    rename = {src: dst for src, dst in COLUMN_MAP.items() if src in df.columns}
    for src, dst in FALLBACK_COLUMNS.items():
        if src in df.columns and dst not in rename.values():
            rename[src] = dst
    out = df[list(rename)].rename(columns=rename)

    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(f"{league_code} {season}: CSV lacks columns {missing}")

    out["match_date"] = pd.to_datetime(out["match_date"], dayfirst=True, format="mixed", errors="coerce")
    for col in INT_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=list(REQUIRED))
    out["home_team"] = out["home_team"].astype(str).str.strip()
    out["away_team"] = out["away_team"].astype(str).str.strip()
    out = out[(out["home_team"] != "") & (out["away_team"] != "")]

    for col in INT_COLUMNS:
        if col in out.columns:
            out[col] = out[col].astype("Int64")
    # Recompute the result from the score rather than trusting the FTR column.
    out["ftr"] = np.select(
        [out["fthg"] > out["ftag"], out["fthg"] < out["ftag"]], ["H", "A"], default="D"
    )
    odds_cols = [c for c in out.columns if c not in REQUIRED and c not in INT_COLUMNS and c != "ftr"]
    for col in odds_cols:
        values = pd.to_numeric(out[col], errors="coerce")
        out[col] = values.where(values > 1.0)

    out["league"] = league_code
    out["season"] = season
    out["match_date"] = out["match_date"].dt.date
    return out.drop_duplicates(subset=["match_date", "home_team", "away_team"], keep="last").reset_index(drop=True)


async def download_seasons(
    leagues: list[League],
    seasons: list[str],
    base_url: str,
    client: httpx.AsyncClient | None = None,
    concurrency: int = 4,
) -> tuple[list[pd.DataFrame], list[FetchResult]]:
    """Fetch every (league, season) file concurrently.

    A missing file (e.g. a season that hasn't started) is reported, not
    raised, so one gap doesn't abort the whole ingest.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(league: League, season: str) -> tuple[pd.DataFrame | None, FetchResult]:
        url = csv_url(base_url, league, season)
        async with sem:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                frame = parse_csv(resp.content, league.fd_code, season)
                return frame, FetchResult(league.slug, season, len(frame))
            except (httpx.HTTPError, ValueError, pd.errors.ParserError) as exc:
                log.warning("Skipping %s: %s", url, exc)
                return None, FetchResult(league.slug, season, 0, str(exc))

    try:
        results = await asyncio.gather(*(one(lg, s) for lg in leagues for s in seasons))
    finally:
        if own_client:
            await client.aclose()
    frames = [f for f, _ in results if f is not None and not f.empty]
    return frames, [r for _, r in results]
