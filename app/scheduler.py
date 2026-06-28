"""APScheduler integration: an interval job that runs the pull.

The interval (hours) comes from ``app_settings`` and can be changed from the
admin page without a restart via :func:`reschedule`. Interval triggers fire
every N hours from start, so they're timezone-independent.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import fetcher
from . import settings_store

logger = logging.getLogger(__name__)

_JOB_ID = "pull"
_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    if not settings_store.get().api_key:
        logger.info("scheduled pull skipped: API key not configured")
        return
    # Share the lock with startup/manual pulls so they never overlap.
    fetcher.run_pull_locked(kind="daily")


def _trigger() -> IntervalTrigger:
    hours = max(1, settings_store.get().pull_interval_hours)
    return IntervalTrigger(hours=hours)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler()
    sched.add_job(_job, _trigger(), id=_JOB_ID, replace_existing=True, max_instances=1)
    sched.start()
    logger.info("scheduler started: every %dh", settings_store.get().pull_interval_hours)
    _scheduler = sched
    return sched


def reschedule() -> None:
    """Apply a changed pull interval to the running job."""
    if _scheduler is not None:
        _scheduler.reschedule_job(_JOB_ID, trigger=_trigger())
        logger.info("scheduler rescheduled: every %dh", settings_store.get().pull_interval_hours)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
