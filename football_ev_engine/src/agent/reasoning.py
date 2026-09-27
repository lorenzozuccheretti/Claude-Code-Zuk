"""Two-sentence rationale for a pick, built from facts the engine computed.

:func:`build_facts` gathers everything from the database and the fitted
model - recent form, Dixon-Coles expected goals, attack/defence ranks and
the shots-on-target trend (football-data has no xG, so the model's expected
goals and shots on target stand in for it). :func:`write_reasoning` turns the
facts into text: with an Anthropic key, Claude writes it under a strict
"use only these facts" instruction; otherwise, or if that call fails, a
deterministic template does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

import duckdb
import pandas as pd

from src.agent.selector import Pick
from src.models.dixon_coles import DixonColes

log = logging.getLogger(__name__)


@dataclass
class TeamForm:
    team: str
    last5: str  # e.g. "WWDLW", most recent last
    points: int
    goals_for: int
    goals_against: int
    sot_recent: float | None  # shots on target per game, last 5
    sot_season: float | None  # shots on target per game, last 19
    games: int  # league matches behind the season figure (<= 19)
    attack_rank: int
    defence_rank: int  # 1 = concedes least


@dataclass
class Facts:
    home: TeamForm
    away: TeamForm
    league: str
    selection: str
    xg_home: float
    xg_away: float
    p_model: float
    p_dc: float | None
    p_sharp: float | None
    price: float
    ev: float


def _team_rows(history: pd.DataFrame, team: str) -> pd.DataFrame:
    h = history[history["home_team"] == team].assign(
        gf=lambda d: d["fthg"], ga=lambda d: d["ftag"], sot=lambda d: d["hst"],
        res=lambda d: d["ftr"].map({"H": "W", "D": "D", "A": "L"}))
    a = history[history["away_team"] == team].assign(
        gf=lambda d: d["ftag"], ga=lambda d: d["fthg"], sot=lambda d: d["ast"],
        res=lambda d: d["ftr"].map({"A": "W", "D": "D", "H": "L"}))
    return pd.concat([h, a]).sort_values("match_date")


def _ranks(model: DixonColes, current_teams: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    p = model.params
    assert p is not None
    teams = [t for t in p.teams if t in current_teams] or p.teams
    attack = {t: i + 1 for i, t in enumerate(sorted(teams, key=lambda t: -p.attack[t]))}
    defence = {t: i + 1 for i, t in enumerate(sorted(teams, key=lambda t: p.defence[t]))}
    return attack, defence


def _form(rows: pd.DataFrame, team: str, attack: dict, defence: dict) -> TeamForm:
    last5 = rows.tail(5)
    season = rows.tail(19)
    sot = pd.to_numeric(last5["sot"], errors="coerce").dropna()
    sot_s = pd.to_numeric(season["sot"], errors="coerce").dropna()
    return TeamForm(
        team=team,
        last5="".join(last5["res"].tolist()),
        points=int(sum({"W": 3, "D": 1}.get(r, 0) for r in last5["res"])),
        goals_for=int(last5["gf"].sum()),
        goals_against=int(last5["ga"].sum()),
        sot_recent=round(float(sot.mean()), 1) if len(sot) else None,
        sot_season=round(float(sot_s.mean()), 1) if len(sot_s) else None,
        games=len(season),
        attack_rank=attack.get(team, 0),
        defence_rank=defence.get(team, 0),
    )


def build_facts(con: duckdb.DuckDBPyConnection, pick: Pick, model: DixonColes) -> Facts:
    history = con.execute(
        "SELECT match_date, season, home_team, away_team, fthg, ftag, ftr, hst, ast FROM matches "
        "WHERE league = ? AND match_date < ? ORDER BY match_date",
        [pick.league, pick.commence_time.date()],
    ).df()
    latest_season = history["season"].max() if not history.empty else None
    cur = history[history["season"] == latest_season]
    attack, defence = _ranks(model, set(cur["home_team"]) | set(cur["away_team"]))
    xg_h, xg_a = model.expected_goals(pick.home_team, pick.away_team)
    return Facts(
        home=_form(_team_rows(history, pick.home_team), pick.home_team, attack, defence),
        away=_form(_team_rows(history, pick.away_team), pick.away_team, attack, defence),
        league=pick.league_name, selection=pick.selection_label,
        xg_home=round(xg_h, 2), xg_away=round(xg_a, 2),
        p_model=pick.p_model, p_dc=pick.p_dc, p_sharp=pick.p_sharp, price=pick.price, ev=pick.ev,
    )


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _trend(f: TeamForm, games: int) -> str:
    if f.sot_recent is None:
        return ""
    if f.sot_season is None or games <= 5:
        return f", {f.sot_recent:g} shots on target per game"
    direction = "up from" if f.sot_recent > f.sot_season else "down from" if f.sot_recent < f.sot_season else "level with"
    return f", {f.sot_recent:g} shots on target per game ({direction} {f.sot_season:g})"


def template_reasoning(f: Facts) -> str:
    """Deterministic two-sentence summary; used when no LLM is configured."""
    h, a = f.home, f.away

    def side(t: TeamForm) -> str:
        form = f"{t.last5} ({t.points} pts, goals {t.goals_for}-{t.goals_against})" if t.last5 else "no recent games"
        rank = f", rank {_ordinal(t.defence_rank)} for defence" if t.defence_rank else ""
        return f"{form}{rank}{_trend(t, t.games)}"

    first = f"{h.team} come in on {side(h)}, while {a.team} are on {side(a)}."
    market = f" against {f.p_sharp:.0%} implied by Pinnacle" if f.p_sharp is not None else ""
    second = (
        f"The model projects {f.xg_home:.2f}-{f.xg_away:.2f} expected goals and rates {f.selection} at "
        f"{f.p_model:.0%}{market}, which at {f.price:.2f} is a {f.ev:+.1%} edge."
    )
    return f"{first} {second}"


SYSTEM_PROMPT = (
    "You write the rationale line for a football betting pick. Write exactly two sentences, at most "
    "70 words in total, in {language}. Use only the facts in the JSON the user sends; never invent "
    "injuries, news, streaks or numbers that are not there. Mention recent form, the model's expected "
    "goals and one defensive or shots-on-target detail, then say why the price looks generous. "
    "Plain text only: no markdown, no emojis, no hype, no promises about the result. "
    "In the JSON, defence_rank 1 means the side that concedes least; sot_* are shots on target per game; "
    "probabilities are between 0 and 1."
)


def claude_reasoning(facts: Facts, api_key: str, model: str, language: str, client: Any = None) -> str | None:
    """Ask Claude for the two sentences; ``None`` on any failure or refusal."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic is not installed; using the template")
        return None
    client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM_PROMPT.format(language=language),
            messages=[{"role": "user", "content": json.dumps(asdict(facts), sort_keys=True)}],
            output_config={"effort": "low"},
            # On a policy refusal the API retries on a fallback model inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        log.warning("Claude unreachable (%s); using the template", exc)
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude error %s (%s); using the template", exc.status_code, exc.message)
        return None
    if response.stop_reason == "refusal":
        log.warning("Claude declined the reasoning request; using the template")
        return None
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


def write_reasoning(facts: Facts, api_key: str = "", model: str = "claude-opus-5",
                    language: str = "English", client: Any = None) -> str:
    if api_key or client is not None:
        text = claude_reasoning(facts, api_key, model, language, client)
        if text:
            return text
    return template_reasoning(facts)
