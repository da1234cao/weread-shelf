"""Period math and response normalization for the reading-stats dashboard.

Pure functions only — no DB, no network. The fetcher uses these to turn each
/readdata/detail response into a display-ready row (stored in PeriodStat); the
web layer adds the time-relative bits (label / prev / next) at read time.

A period is identified by (mode, base_time): the normalized local start of the
week (Monday), month (1st), or year (Jan 1). `overall` is cumulative and uses
base_time 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .utils import fmt_duration, ts_to_date, tzinfo

MODES = ("weekly", "monthly", "annually", "overall")

# Distribution granularity per mode: day buckets render in minutes, the coarser
# month/year buckets in hours.
_MINUTE_MODES = ("weekly", "monthly")

# Runaway guard for historical enumeration (≈21 years of weeks).
_MAX_PERIODS = 1100


def _period_start_of(mode: str, ts: int) -> int:
    """Local period start (epoch seconds) of the period containing `ts`."""
    d = datetime.fromtimestamp(ts, tzinfo())
    if mode == "annually":
        start = datetime(d.year, 1, 1, tzinfo=tzinfo())
    elif mode == "monthly":
        start = datetime(d.year, d.month, 1, tzinfo=tzinfo())
    else:  # weekly — Monday 00:00
        monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start = monday
    return int(start.timestamp())


def base_time_for(mode: str, offset: int) -> int:
    """Normalized period start for `offset` periods before now (offset ≤ 0).

    Returns 0 for `overall`. The result doubles as both the API `baseTime` and the
    PeriodStat key, so it must be deterministic for a given (mode, offset, today).
    """
    if mode == "overall":
        return 0
    now = datetime.now(tzinfo())
    if mode == "annually":
        start = datetime(now.year + offset, 1, 1, tzinfo=tzinfo())
    elif mode == "monthly":
        total = now.year * 12 + (now.month - 1) + offset
        start = datetime(total // 12, total % 12 + 1, 1, tzinfo=tzinfo())
    else:  # weekly
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start = monday + timedelta(weeks=offset)
    return int(start.timestamp())


def historical_base_times(mode: str, reg_time: int) -> list[int]:
    """Every period start from now back to the registration period, recent first.

    Used by the first-pull backfill. Bounded by `_MAX_PERIODS` so a missing or
    bogus `reg_time` can't spin from 1970.
    """
    if mode == "overall":
        return [0]
    reg_start = _period_start_of(mode, reg_time)
    out: list[int] = []
    offset = 0
    while len(out) < _MAX_PERIODS:
        bt = base_time_for(mode, offset)
        if bt < reg_start:
            break
        out.append(bt)
        offset -= 1
    return out


def _dist_label(mode: str, ts: int) -> str:
    d = ts_to_date(ts)  # YYYY-MM-DD in local tz
    if mode == "overall":
        return d[:4]               # year
    if mode == "annually":
        return f"{int(d[5:7])}月"  # month
    return d[5:]                   # MM-DD


def normalize(mode: str, base_time: int, data: dict[str, Any]) -> dict[str, Any]:
    """Turn a /readdata/detail response into the stored, display-ready payload.

    Holds only time-independent data; the relative label and prev/next live-compute
    at read time (see web.api_stats).
    """
    read_times = data.get("readTimes") or {}
    dist = [
        {"label": _dist_label(mode, int(ts)), "seconds": int(sec or 0)}
        for ts, sec in sorted(read_times.items(), key=lambda kv: int(kv[0]))
    ]
    if mode == "overall":  # drop the leading run of zero years
        while dist and dist[0]["seconds"] == 0:
            dist.pop(0)

    total = int(data.get("totalReadTime", 0) or 0)
    payload: dict[str, Any] = {
        "total_read_time": total,
        "total_read_time_h": fmt_duration(total),
        "day_average": data.get("dayAverageReadTime"),  # may be null (overall)
        "read_days": int(data.get("readDays", 0) or 0),
        "read_stat": data.get("readStat") or [],
        "distribution": dist,
        "dist_unit": "minutes" if mode in _MINUTE_MODES else "hours",
    }
    if mode == "overall":  # long-term preferences power the fixed "总体偏好" section
        payload["prefer_category"] = data.get("preferCategory") or []
        payload["prefer_author"] = data.get("preferAuthor") or []
        payload["prefer_time"] = data.get("preferTime") or []
    return payload


_NOW_LABEL = {"weekly": "本周", "monthly": "本月", "annually": "今年"}


def period_label(mode: str, offset: int, base_time: int) -> str:
    """Human label for the period: relative for the current one, absolute for past."""
    if mode == "overall":
        return "总计"
    if offset == 0:
        return _NOW_LABEL[mode]
    d = datetime.fromtimestamp(base_time, tzinfo())
    if mode == "annually":
        return f"{d.year}年"
    if mode == "monthly":
        return f"{d.year}年{d.month}月"
    end = d + timedelta(days=6)  # weekly: Mon–Sun range
    return f"{d.strftime('%m-%d')} ~ {end.strftime('%m-%d')}"
