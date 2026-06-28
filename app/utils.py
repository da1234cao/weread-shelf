"""Small shared helpers for time/date formatting."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def tzinfo() -> ZoneInfo:
    from .settings_store import get

    return ZoneInfo(get().timezone)


def today_str() -> str:
    return datetime.now(tzinfo()).strftime("%Y-%m-%d")


def ts_to_date(ts: int) -> str:
    """Convert an epoch second to a YYYY-MM-DD label in the configured tz."""
    return datetime.fromtimestamp(int(ts), tzinfo()).strftime("%Y-%m-%d")


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


def month_label(d: str) -> str:
    """'2026-06-12' -> '2026-06'."""
    return d[:7]


def parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()
