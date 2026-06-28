"""Access to the single-row ``AppSettings`` config, with a small in-process cache.

The app runs as one process (web + scheduler), so a module-level cache of the
settings row is safe and lets hot paths (e.g. ``utils.tzinfo``) avoid a DB hit
per call. ``save`` is the only writer and refreshes the cache.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from .db import session_scope
from .models import AppSettings

_cache: AppSettings | None = None


def _load(session) -> AppSettings:
    """Get-or-create the row, ensuring a session secret exists."""
    row = session.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1, session_secret=secrets.token_hex(32))
        session.add(row)
    elif not row.session_secret:
        row.session_secret = secrets.token_hex(32)
        session.add(row)
    return row


def get() -> AppSettings:
    """Return the cached settings row (loading/seeding it on first use)."""
    global _cache
    if _cache is None:
        with session_scope() as session:
            _cache = _load(session)
    return _cache


def save(**changes: Any) -> AppSettings:
    """Apply field changes, persist, and refresh the cache."""
    global _cache
    with session_scope() as session:
        row = _load(session)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        _cache = row
    return _cache


def invalidate() -> None:
    """Drop the cache (used by tests switching DBs)."""
    global _cache
    _cache = None
