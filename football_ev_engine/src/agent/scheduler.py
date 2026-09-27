"""Long-running daily scheduler (APScheduler) for local or Docker hosting.

For GitHub Actions or system cron, call ``python main.py agent run`` on a
schedule instead; this module is only for a process that stays up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agent.settings import AgentSettings

log = logging.getLogger(__name__)


def build_scheduler(settings: AgentSettings, job: Callable[[], None]) -> BlockingScheduler:
    hour, minute = (int(x) for x in settings.run_time.split(":"))
    scheduler = BlockingScheduler(timezone=settings.tz)

    def safe_job() -> None:
        try:
            job()
        except Exception:  # noqa: BLE001 - one bad day must not kill the scheduler
            log.exception("Daily agent run failed")

    scheduler.add_job(
        safe_job,
        CronTrigger(hour=hour, minute=minute, timezone=settings.tz),
        id="daily-picks",
        coalesce=True,  # after downtime, run once, not once per missed day
        misfire_grace_time=3 * 3600,
        max_instances=1,
    )
    return scheduler
