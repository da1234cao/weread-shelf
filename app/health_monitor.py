"""Self-healing health monitor: periodically probes a local endpoint and exits
the process after consecutive failures so the container runtime restarts it."""

from __future__ import annotations

import logging
import os
import threading
import urllib.request

from . import settings_store

logger = logging.getLogger(__name__)

INTERVAL = 60  # seconds between probes
MAX_FAILURES = 5  # consecutive failures before exit
ENDPOINT = "http://localhost:8765/api/shelf"
TIMEOUT = 10  # seconds

_stop = threading.Event()


def _check() -> bool:
    try:
        req = urllib.request.Request(ENDPOINT)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status < 500
    except Exception:
        return False


def _loop() -> None:
    failures = 0
    while not _stop.wait(timeout=INTERVAL):
        if not settings_store.get().health_monitor_enabled:
            failures = 0
            continue
        if _check():
            failures = 0
        else:
            failures += 1
            logger.warning("health probe failed (%d/%d)", failures, MAX_FAILURES)
            if failures >= MAX_FAILURES:
                logger.error("%d consecutive failures — exiting for restart", failures)
                os._exit(1)


def start() -> None:
    _stop.clear()
    t = threading.Thread(target=_loop, daemon=True, name="health-monitor")
    t.start()
    logger.info("health monitor started (interval=%ds, max_failures=%d)", INTERVAL, MAX_FAILURES)


def stop() -> None:
    _stop.set()
    logger.info("health monitor stopped")
