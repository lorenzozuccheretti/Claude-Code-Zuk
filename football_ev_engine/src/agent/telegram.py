"""Telegram Bot API client (plain httpx) and message formatting.

Messages use ``parse_mode=HTML``; every dynamic value goes through
:func:`html.escape` because Telegram rejects the whole message on one stray
``<`` or ``&``.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from src.agent.selector import Pick
from src.agent.store import TrackRecord

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_LEN = 4096

BOOKMAKERS = {
    "williamhill": "William Hill", "marathonbet": "Marathonbet", "sport888": "888sport", "betsson": "Betsson",
    "nordicbet": "NordicBet", "tipico_de": "Tipico", "unibet_fr": "Unibet", "unibet_nl": "Unibet",
    "unibet_se": "Unibet", "unibet_eu": "Unibet", "betclic_fr": "Betclic", "winamax_fr": "Winamax",
    "winamax_de": "Winamax", "leovegas_se": "LeoVegas", "pmu_fr": "PMU", "codere_it": "Codere",
    "coolbet": "Coolbet", "pinnacle": "Pinnacle", "bet365": "Bet365",
}


def bookmaker_name(key: str) -> str:
    return BOOKMAKERS.get(key, key.replace("_", " ").title())


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None,
                 max_retries: int = 3) -> None:
        self.token = token
        self.chat_id = chat_id
        self.client = client
        self.max_retries = max_retries

    async def send(self, text: str) -> int:
        """Send one HTML message; returns Telegram's message_id."""
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 1] + "…"
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20)
        try:
            for attempt in range(self.max_retries + 1):
                resp = await client.post(
                    f"{API}/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                )
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 429 and attempt < self.max_retries:
                    wait = data.get("parameters", {}).get("retry_after", 2 ** attempt)
                    await asyncio.sleep(min(float(wait), 30))
                    continue
                if not data.get("ok"):
                    # Never log the token: it is part of the URL.
                    raise TelegramError(f"Telegram {resp.status_code}: {data.get('description', resp.text[:200])}")
                return int(data["result"]["message_id"])
            raise TelegramError("Telegram rate limit persisted after retries")
        finally:
            if own:
                await client.aclose()


    async def send_document(self, path: Path, caption: str = "") -> int:
        """Send a file (e.g. the CSV history); returns Telegram's message_id."""
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=60)
        try:
            resp = await client.post(
                f"{API}/bot{self.token}/sendDocument",
                data={"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"document": (path.name, path.read_bytes(), "text/csv")},
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if not data.get("ok"):
                raise TelegramError(f"Telegram {resp.status_code}: {data.get('description', resp.text[:200])}")
            return int(data["result"]["message_id"])
        finally:
            if own:
                await client.aclose()


def _local(ts: datetime, tz: ZoneInfo) -> datetime:
    return ts.replace(tzinfo=timezone.utc).astimezone(tz)


def format_pick(pick: Pick, tz: ZoneInfo, index: int | None = None, total: int | None = None) -> str:
    def e(text: str) -> str:
        return html.escape(text, quote=False)

    kick = _local(pick.commence_time, tz)
    header = "🔥 <b>Value Pick</b>" + (f" {index}/{total}" if index and total and total > 1 else "")
    lines = [
        header,
        "",
        f"⚽ <b>{e(pick.home_team)} vs {e(pick.away_team)}</b> - {e(pick.league_name)}",
        f"📅 {kick:%a %d %b %Y} · <b>{kick:%H:%M}</b> ({e(tz.key)})",
        f"🎯 Selection: <b>{e(pick.selection_label)}</b>",
        f"📊 Odds: <b>{pick.price:.2f}</b> ({e(bookmaker_name(pick.bookmaker))})",
        f"🧮 Engine probability: <b>{pick.p_model:.1%}</b> (fair odds {pick.fair_odds:.2f})",
        f"📈 Expected value: <b>{pick.ev:+.1%}</b>",
        f"💰 Kelly stake: <b>{pick.stake_pct:.1%}</b> of bankroll",
    ]
    if pick.reasoning:
        lines += ["", f"💡 <i>{e(pick.reasoning)}</i>"]
    return "\n".join(lines)


def format_record(record: TrackRecord) -> str:
    if record.settled == 0:
        return f"📒 Track record: {record.picks} picks sent, none settled yet."
    clv = f", avg CLV {record.avg_clv:+.1%}" if record.avg_clv is not None else ""
    return (f"📒 Track record: {record.wins}/{record.settled} won, {record.units:+.2f}u "
            f"(yield {record.yield_pct:+.1f}%){clv}")


def format_no_picks(now: datetime, tz: ZoneInfo, scanned: int, candidates: int) -> str:
    local = _local(now, tz)
    if candidates:
        reason = (f"{candidates} passed the value filters, but none survived the kick-off window, "
                  "dedup and daily limit.")
    else:
        reason = "none met the odds window and EV threshold."
    return f"🤖 <b>Daily scan</b> · {local:%a %d %b}\n\nNo pick today: {scanned} fixtures scanned, {reason}"
