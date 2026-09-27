import asyncio

import httpx
import pytest
import respx

from src.config import get_league
from src.data.ingest_csv import csv_url, download_seasons, parse_csv
from tests.synthetic import to_football_data_csv

BASE = "https://www.football-data.co.uk/mmz4281"

OLD_STYLE = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,B365H,B365D,B365A,BbAvH,BbAvD,BbAvA,,\n"
    "I1,19/08/17,Juventus,Cagliari,3,0,H,20,5,8,1,1.20,6.5,15,1.21,6.3,14.0,,\n"
    "I1,20/08/17,Roma,Atalanta,0,1,A,,,,,2.1,3.4,3.6,2.05,3.3,3.5,,\n"
    "I1,,,,,,,,,,,,,,,,,,\n"
)


def test_parse_old_style_file():
    df = parse_csv(OLD_STYLE.encode("latin-1"), "I1", "2017-2018")
    assert len(df) == 2
    assert str(df.loc[0, "match_date"]) == "2017-08-19"
    assert df.loc[1, "ftr"] == "A" and df.loc[0, "avgh"] == pytest.approx(1.21)
    assert df["hs"].isna().sum() == 1
    assert "psh" not in df.columns  # absent columns stay absent (NULL on insert)


def test_parse_modern_file(synthetic_matches):
    season = synthetic_matches[synthetic_matches["season"] == "2024-2025"]
    df = parse_csv(to_football_data_csv(season), "I1", "2024-2025")
    assert len(df) == len(season)
    assert {"b365h", "psch", "ps_over25", "b365_under25"} <= set(df.columns)


def test_ftr_recomputed_and_bad_odds_nulled():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\nI1,01/09/2024,A,B,2,2,H,1.0,x,3.1\n"
    df = parse_csv(text, "I1", "2024-2025")
    assert df.loc[0, "ftr"] == "D"
    assert df["b365h"].isna().all() and df["b365d"].isna().all() and df.loc[0, "b365a"] == 3.1


def test_missing_required_columns():
    with pytest.raises(ValueError):
        parse_csv("Div,Date,HomeTeam\nI1,01/01/2024,A\n", "I1", "2024-2025")


def test_url():
    assert csv_url(BASE, get_league("serie-a"), "2024-2025") == f"{BASE}/2425/I1.csv"


@respx.mock
def test_download_reports_missing_seasons(synthetic_matches):
    season = synthetic_matches[synthetic_matches["season"] == "2024-2025"]
    respx.get(f"{BASE}/2425/I1.csv").mock(return_value=httpx.Response(200, text=to_football_data_csv(season)))
    respx.get(f"{BASE}/2526/I1.csv").mock(return_value=httpx.Response(404))
    frames, results = asyncio.run(download_seasons([get_league("I1")], ["2024-2025", "2025-2026"], BASE))
    assert len(frames) == 1 and len(frames[0]) == len(season)
    assert [r.error is None for r in results] == [True, False]
