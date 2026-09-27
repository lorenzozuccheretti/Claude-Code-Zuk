"""Runtime configuration and the static league catalogue.

Settings come from environment variables (or a ``.env`` file in the project
folder) and are validated by pydantic. Every betting threshold lives here so
the CLI, the live engine and the backtester apply exactly the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class League:
    """One supported competition, keyed the way each data source names it."""

    slug: str  # CLI name, e.g. "serie-a"
    name: str
    fd_code: str  # football-data.co.uk division code
    odds_api_key: str  # The Odds API sport key


LEAGUES: dict[str, League] = {
    lg.slug: lg
    for lg in (
        League("serie-a", "Serie A", "I1", "soccer_italy_serie_a"),
        League("premier-league", "Premier League", "E0", "soccer_epl"),
        League("la-liga", "La Liga", "SP1", "soccer_spain_la_liga"),
        League("bundesliga", "Bundesliga", "D1", "soccer_germany_bundesliga"),
        League("ligue-1", "Ligue 1", "F1", "soccer_france_ligue_one"),
    )
}

_LEAGUE_ALIASES = {"epl": "premier-league", "serie_a": "serie-a", "laliga": "la-liga", "ligue1": "ligue-1"}


def get_league(name: str) -> League:
    """Resolve a CLI slug, football-data code or Odds API key to a :class:`League`."""
    key = name.strip().lower()
    key = _LEAGUE_ALIASES.get(key, key)
    if key in LEAGUES:
        return LEAGUES[key]
    for lg in LEAGUES.values():
        if key in (lg.fd_code.lower(), lg.odds_api_key):
            return lg
    raise KeyError(f"Unknown league {name!r}. Choose from: {', '.join(LEAGUES)}")


def resolve_leagues(name: str | None) -> list[League]:
    """``None`` or ``"all"`` means every league; otherwise a comma-separated list."""
    if not name or name.lower() == "all":
        return list(LEAGUES.values())
    return [get_league(part) for part in name.split(",") if part.strip()]


class Settings(BaseSettings):
    """Validated application settings."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    odds_api_regions: str = "eu"
    odds_api_markets: str = "h2h,totals"
    # Stop spending credits once the monthly quota falls to this many.
    odds_api_min_remaining: int = 20

    football_data_base_url: str = "https://www.football-data.co.uk/mmz4281"
    history_seasons: int = Field(5, ge=1, le=20)

    db_path: Path = Path("data/football.duckdb")
    models_dir: Path = Path("data/models")
    aliases_json: Path = Path("data/team_aliases.json")

    sharp_bookmaker: str = "pinnacle"
    # Keys The Odds API actually returns for regions=eu (checked Sep 2026).
    # Exchanges (commission) and offshore books are left out on purpose.
    soft_bookmakers: list[str] = [
        "williamhill", "marathonbet", "sport888", "betsson", "nordicbet", "tipico_de",
        "unibet_fr", "unibet_nl", "unibet_se", "betclic_fr", "winamax_fr", "winamax_de",
        "leovegas_se", "pmu_fr", "codere_it", "coolbet",
    ]

    # Value filters (section 3C of the spec).
    min_ev: float = 0.03
    min_model_prob: float = 0.35
    min_odds: float = 1.40
    max_odds: float = 4.50

    # Staking (section 3D).
    bankroll: float = Field(1000.0, gt=0)
    kelly_fraction: float = Field(0.25, gt=0, le=1)
    max_stake_pct: float = Field(0.025, gt=0, le=1)

    # Weight of the Dixon-Coles probability in the final estimate; the rest
    # comes from the de-vigged sharp price. 1.0 = model only, 0.0 = market only.
    model_weight: float = Field(0.35, ge=0, le=1)
    devig_method: str = "multiplicative"

    # Dixon-Coles.
    decay_xi: float = Field(0.005, ge=0)  # per day
    training_window_days: int = Field(1095, gt=0)
    max_goals: int = Field(10, ge=5)
    # Below this many matches in the fit, a team's ratings are mostly noise and
    # the model is ignored for its fixtures (the de-vigged market is used alone).
    min_team_matches: int = Field(10, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.min_odds >= self.max_odds:
            raise ValueError("MIN_ODDS must be below MAX_ODDS")
        if self.devig_method not in ("multiplicative", "power"):
            raise ValueError("DEVIG_METHOD must be 'multiplicative' or 'power'")
        return self

    def resolve(self, path: Path) -> Path:
        """Relative paths are anchored at the project folder, not the CWD."""
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def db_file(self) -> Path:
        return self.resolve(self.db_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def season_label(start_year: int) -> str:
    """2024 -> ``"2024-2025"``."""
    return f"{start_year}-{start_year + 1}"


def season_code(label: str) -> str:
    """``"2024-2025"`` -> football-data's ``"2425"``."""
    start, end = parse_season(label)
    return f"{start % 100:02d}{end % 100:02d}"


def parse_season(label: str) -> tuple[int, int]:
    """Accept ``2024-2025``, ``2024-25`` or ``2425``; return (2024, 2025)."""
    s = label.strip()
    if "-" in s or "/" in s:
        a, b = s.replace("/", "-").split("-", 1)
        start = int(a)
        end = int(b) if len(b) == 4 else (start // 100) * 100 + int(b)
    elif len(s) == 4 and s.isdigit() and int(s[2:]) == (int(s[:2]) + 1) % 100:
        start = 2000 + int(s[:2])
        end = start + 1
    else:
        raise ValueError(f"Unrecognised season {label!r}; use e.g. 2024-2025")
    if end != start + 1:
        raise ValueError(f"Season {label!r} must span consecutive years")
    return start, end


def current_season_start(today: date | None = None) -> int:
    """European seasons start in July/August: before July we're in last year's."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def recent_seasons(n: int, today: date | None = None) -> list[str]:
    """The current season plus the ``n - 1`` before it, oldest first."""
    cur = current_season_start(today)
    return [season_label(y) for y in range(cur - n + 1, cur + 1)]
