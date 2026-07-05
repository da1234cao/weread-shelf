"""Access to the single-row ``AppSettings`` config, with a small in-process cache.

The module-level cache holds a detached copy (``model_copy()``) so it never
references a session and can't be poisoned by a failed commit.
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
    """Return a detached copy of the cached settings (loading/seeding on first use)."""
    global _cache
    if _cache is None:
        with session_scope() as session:
            _cache = _load(session).model_copy()
    return _cache


def save(**changes: Any) -> AppSettings:
    """Apply field changes, persist, and refresh the cache with a detached copy.

    The cache is only updated after a successful commit, so a transaction
    failure (e.g. a DB-lock timeout during a concurrent pull) can never leave
    the process-wide cache pointing to an expired object.
    """
    global _cache
    with session_scope() as session:
        row = _load(session)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
    _cache = row.model_copy()
    return _cache


def invalidate() -> None:
    """Drop the cache (used by tests switching DBs)."""
    global _cache
    _cache = None
