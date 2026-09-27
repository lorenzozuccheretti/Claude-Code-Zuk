"""Synthetic league generator for tests.

Scores are drawn from a known Dixon-Coles process; Pinnacle prices are the
true probabilities with a small margin and Bet365 prices are noisier with a
bigger margin, mirroring how the real markets relate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd


TEAMS = [
    "Inter", "Milan", "Juventus", "Napoli", "Roma", "Lazio", "Atalanta", "Fiorentina", "Bologna", "Torino",
    "Genoa", "Verona", "Udinese", "Lecce", "Cagliari", "Empoli", "Monza", "Parma", "Como", "Venezia",
]


def true_params(teams: list[str], seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.3, len(teams))
    att -= att.mean()
    dfn = rng.normal(0, 0.2, len(teams))
    return {"attack": dict(zip(teams, att)), "defence": dict(zip(teams, dfn)), "gamma": 0.25, "rho": -0.08}


def _probs(lam: float, mu: float, rho: float, max_goals: int = 10) -> np.ndarray:
    from scipy.stats import poisson

    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


def _odds(p: np.ndarray, margin: float, noise: float, rng: np.random.Generator) -> np.ndarray:
    q = p * np.exp(rng.normal(0, noise, len(p)))
    q = q / q.sum() * (1 + margin)
    return np.round(1 / q, 2)


def generate_matches(
    seasons: list[str], league: str = "I1", teams: list[str] | None = None, seed: int = 7
) -> pd.DataFrame:
    """Double round-robin per season with results and pre-match/closing odds."""
    teams = teams or TEAMS
    rng = np.random.default_rng(seed)
    tp = true_params(teams, seed)
    rows = []
    for season in seasons:
        start = datetime(int(season[:4]), 8, 20)
        fixtures = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(fixtures)
        per_round = len(teams) // 2
        for k, (h, a) in enumerate(fixtures):
            date = start + timedelta(days=7 * (k // per_round) + int(rng.integers(0, 3)))
            lam = np.exp(tp["attack"][h] + tp["defence"][a] + tp["gamma"])
            mu = np.exp(tp["attack"][a] + tp["defence"][h])
            m = _probs(lam, mu, tp["rho"])
            flat = rng.choice(m.size, p=m.ravel())
            x, y = divmod(flat, m.shape[1])
            p1x2 = np.array([np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()])
            goals = np.add.outer(np.arange(m.shape[0]), np.arange(m.shape[1]))
            pou = np.array([m[goals > 2.5].sum(), m[goals < 2.5].sum()])
            ps, b365 = _odds(p1x2, 0.025, 0.02, rng), _odds(p1x2, 0.05, 0.08, rng)
            psc = _odds(p1x2, 0.02, 0.01, rng)
            ps_ou, b365_ou = _odds(pou, 0.025, 0.02, rng), _odds(pou, 0.05, 0.08, rng)
            rows.append({
                "league": league, "season": season, "match_date": date.date(), "home_team": h, "away_team": a,
                "fthg": int(x), "ftag": int(y), "ftr": "H" if x > y else "A" if x < y else "D",
                "hs": int(rng.poisson(lam * 5)), "as_": int(rng.poisson(mu * 5)),
                "hst": int(rng.poisson(lam * 2.5)), "ast": int(rng.poisson(mu * 2.5)),
                "b365h": b365[0], "b365d": b365[1], "b365a": b365[2],
                "psh": ps[0], "psd": ps[1], "psa": ps[2],
                "psch": psc[0], "pscd": psc[1], "psca": psc[2],
                "b365_over25": b365_ou[0], "b365_under25": b365_ou[1],
                "ps_over25": ps_ou[0], "ps_under25": ps_ou[1],
            })
    return pd.DataFrame(rows).sort_values(["match_date", "home_team"]).reset_index(drop=True)


CSV_COLUMNS = {
    "match_date": "Date", "home_team": "HomeTeam", "away_team": "AwayTeam", "fthg": "FTHG", "ftag": "FTAG",
    "ftr": "FTR", "hs": "HS", "as_": "AS", "hst": "HST", "ast": "AST", "b365h": "B365H", "b365d": "B365D",
    "b365a": "B365A", "psh": "PSH", "psd": "PSD", "psa": "PSA", "psch": "PSCH", "pscd": "PSCD", "psca": "PSCA",
    "b365_over25": "B365>2.5", "b365_under25": "B365<2.5", "ps_over25": "P>2.5", "ps_under25": "P<2.5",
}


def to_football_data_csv(df: pd.DataFrame) -> str:
    """Render rows the way football-data.co.uk publishes them."""
    out = df[list(CSV_COLUMNS)].rename(columns=CSV_COLUMNS).copy()
    out.insert(0, "Div", df["league"].iloc[0])
    out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%d/%m/%Y")
    return out.to_csv(index=False)


def odds_api_events(
    fixtures: list[tuple[str, str]], kickoff: datetime, sport_key: str = "soccer_italy_serie_a",
    books: dict[str, float] | None = None, seed: int = 3,
) -> list[dict]:
    """Odds API ``/odds`` payload for the given (home, away) API names."""
    rng = np.random.default_rng(seed)
    books = books or {"pinnacle": 0.025, "unibet_eu": 0.06, "marathonbet": 0.055}
    events = []
    for i, (home, away) in enumerate(fixtures):
        p = rng.dirichlet([4, 2.5, 3])
        pou = np.array([0.52, 0.48])
        bks = []
        for key, margin in books.items():
            o = _odds(p, margin, 0.0 if key == "pinnacle" else 0.1, rng)
            ou = _odds(pou, margin, 0.0 if key == "pinnacle" else 0.1, rng)
            bks.append({
                "key": key, "title": key, "last_update": kickoff.isoformat() + "Z",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": home, "price": float(o[0])}, {"name": "Draw", "price": float(o[1])},
                        {"name": away, "price": float(o[2])}]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "price": float(ou[0]), "point": 2.5},
                        {"name": "Under", "price": float(ou[1]), "point": 2.5}]},
                ],
            })
        events.append({
            "id": f"evt{i}", "sport_key": sport_key, "sport_title": "Serie A",
            "commence_time": (kickoff + timedelta(hours=i)).isoformat() + "Z",
            "home_team": home, "away_team": away, "bookmakers": bks,
        })
    return events
