"""APScheduler integration: a daily cron job that runs the pull."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .fetcher import run_daily_pull

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    try:
        run_daily_pull(kind="daily")
    except Exception:
        logger.exception("scheduled pull failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    sched = BackgroundScheduler(timezone=settings.tz)
    trigger = CronTrigger.from_crontab(settings.pull_cron, timezone=settings.tz)
    sched.add_job(_job, trigger, id="daily_pull", replace_existing=True, max_instances=1)
    sched.start()
    logger.info("scheduler started: cron=%r tz=%s", settings.pull_cron, settings.tz)
    _scheduler = sched
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
