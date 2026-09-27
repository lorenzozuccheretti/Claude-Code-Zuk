"""Settings for the daily Telegram agent.

The agent reads the same ``.env`` as the CLI. ``MIN_ODDS``, ``MAX_ODDS`` and
``MIN_EV`` are shared names, so values set there apply to both; only the
code defaults differ (the agent's odds window is 1.75-2.25 and its EV floor
3.5%).
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from src.config import Settings


class AgentSettings(Settings):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    min_odds: float = 1.75
    max_odds: float = 2.25
    min_ev: float = 0.035
    # The odds window already implies P > 0.46 at +3.5% EV, so no separate floor.
    min_model_prob: float = 0.0
    max_daily_picks: int = Field(2, ge=0, le=2)

    timezone: str = "Europe/Rome"
    run_time: str = "10:00"  # local time for the scheduler
    # Only fixtures kicking off between MIN_LEAD_MINUTES and PICK_HORIZON_HOURS from now.
    pick_horizon_hours: int = Field(36, gt=0)
    min_lead_minutes: int = Field(60, ge=0)
    notify_no_picks: bool = True

    # Reasoning: Claude writes it when a key is configured, otherwise a template does.
    anthropic_api_key: str = ""
    reasoning_model: str = "claude-opus-5"
    reasoning_language: str = "English"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        ZoneInfo(v)  # raises for unknown zones
        return v

    @field_validator("run_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        h, _, m = v.partition(":")
        if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
            raise ValueError("RUN_TIME must be HH:MM")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id
                    and self.telegram_bot_token != "your_telegram_bot_token")

    @property
    def llm_reasoning(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
