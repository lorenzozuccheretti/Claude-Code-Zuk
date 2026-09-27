"""National-team results from martj42/international_results (GitHub, free, no key).

One CSV holds every men's international since 1872:
``date,home_team,away_team,home_score,away_score,tournament,city,country,neutral``.
Rows are stored in ``matches`` under league ``INT`` with the calendar year as
season. There are no shots or odds, so those columns stay NULL: the model
can be fitted, but a betting backtest on internationals is not possible.

The dataset is maintained by hand and usually lags real results by days or
weeks; settlement of international picks waits until it catches up.
"""

from __future__ import annotations

import io
from datetime import date

import httpx
import numpy as np
import pandas as pd

LEAGUE_CODE = "INT"

# Non-FIFA sides play among themselves; they only add disconnected noise.
_EXCLUDED_TOURNAMENTS = ("CONIFA", "Island Games", "Viva World Cup", "ELF Cup", "FIFI Wild Cup",
                         "Muratti Vase", "Niamh Challenge Cup", "Inter Games")


def parse_results(raw: bytes | str, since: date) -> pd.DataFrame:
    """Played matches on or after ``since``, shaped like the ``matches`` table."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text))
    required = {"date", "home_team", "away_team", "home_score", "away_score", "tournament", "neutral"}
    if missing := required - set(df.columns):
        raise ValueError(f"International results CSV lacks columns {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "home_score", "away_score"])
    df = df[df["date"] >= pd.Timestamp(since)]
    excluded = df["tournament"].astype(str).str.contains("|".join(_EXCLUDED_TOURNAMENTS), case=False)
    df = df[~excluded]
    out = pd.DataFrame({
        "league": LEAGUE_CODE,
        "season": df["date"].dt.year.astype(str),
        "match_date": df["date"].dt.date,
        "home_team": df["home_team"].astype(str).str.strip(),
        "away_team": df["away_team"].astype(str).str.strip(),
        "fthg": df["home_score"].astype(int),
        "ftag": df["away_score"].astype(int),
        "neutral": df["neutral"].astype(str).str.upper().eq("TRUE"),
    })
    out["ftr"] = np.select([out["fthg"] > out["ftag"], out["fthg"] < out["ftag"]], ["H", "A"], default="D")
    return out.drop_duplicates(subset=["match_date", "home_team", "away_team"], keep="last").reset_index(drop=True)


async def download_results(url: str, since: date, client: httpx.AsyncClient | None = None) -> pd.DataFrame:
    own = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return parse_results(resp.content, since)
    finally:
        if own:
            await client.aclose()
