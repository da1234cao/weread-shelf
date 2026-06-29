"""Small shared helpers for time/date formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def tzinfo() -> ZoneInfo:
    from .settings_store import get

    return ZoneInfo(get().timezone)


def today_str() -> str:
    return datetime.now(tzinfo()).strftime("%Y-%m-%d")


def ts_to_date(ts: int) -> str:
    """Convert an epoch second to a YYYY-MM-DD label in the configured tz."""
    return datetime.fromtimestamp(int(ts), tzinfo()).strftime("%Y-%m-%d")


def to_local(dt: datetime | None) -> datetime | None:
    """Convert a UTC datetime to the configured tz.

    Stored timestamps come back from SQLite naive (tzinfo stripped) but represent
    UTC, so a naive input is assumed UTC; an aware input is converted as-is.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo())


def fmt_local(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a UTC datetime as wall-clock time in the configured tz."""
    local = to_local(dt)
    return local.strftime(fmt) if local else ""


def fmt_duration(seconds: int | None) -> str:
    """Human-readable duration, e.g. 5844393 -> '1623小时26分'."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}秒"
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    return f"{minutes}分钟"
