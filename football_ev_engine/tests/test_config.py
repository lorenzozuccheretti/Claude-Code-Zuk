from datetime import date

import pytest

from src.config import Settings, current_season_start, get_league, parse_season, recent_seasons, resolve_leagues, season_code


@pytest.mark.parametrize("label,expected", [("2024-2025", (2024, 2025)), ("2024-25", (2024, 2025)),
                                            ("2425", (2024, 2025)), ("1999-2000", (1999, 2000))])
def test_parse_season(label, expected):
    assert parse_season(label) == expected


@pytest.mark.parametrize("bad", ["2024-2026", "2024", "abcd"])
def test_parse_season_rejects(bad):
    with pytest.raises(ValueError):
        parse_season(bad)


def test_season_code():
    assert season_code("2024-2025") == "2425"
    assert season_code("1999-2000") == "9900"


def test_current_season_rolls_over_in_july():
    assert current_season_start(date(2026, 6, 30)) == 2025
    assert current_season_start(date(2026, 7, 1)) == 2026
    assert recent_seasons(3, date(2026, 9, 27)) == ["2024-2025", "2025-2026", "2026-2027"]


def test_league_lookup():
    assert get_league("serie-a").fd_code == "I1"
    assert get_league("E0").slug == "premier-league"
    assert get_league("epl").slug == "premier-league"
    assert get_league("soccer_france_ligue_one").slug == "ligue-1"
    assert len(resolve_leagues("all")) == 6
    assert [lg.slug for lg in resolve_leagues("serie-a,la-liga")] == ["serie-a", "la-liga"]
    with pytest.raises(KeyError):
        get_league("eredivisie")


def test_settings_validation():
    with pytest.raises(ValueError):
        Settings(_env_file=None, min_odds=5, max_odds=4)
    s = Settings(_env_file=None)
    assert s.min_ev == 0.03 and s.kelly_fraction == 0.25 and s.max_stake_pct == 0.025
    assert s.db_file.is_absolute()
