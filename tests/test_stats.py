"""Unit tests for the pure period math + normalization in app.stats."""

from __future__ import annotations

import time
from datetime import datetime

from app import stats
from app.utils import tzinfo

from . import fixtures as fx


def test_normalize_monthly_metrics_and_distribution():
    p = stats.normalize("monthly", stats.base_time_for("monthly", 0), fx.MONTHLY)
    assert p["total_read_time"] == 12600
    assert p["read_days"] == 3
    assert p["day_average"] == 4200
    assert p["dist_unit"] == "minutes"
    assert any(s["stat"] == "读过" for s in p["read_stat"])
    # Per-day buckets kept in chronological order (including the zero day).
    assert len(p["distribution"]) == 4
    assert p["distribution"][0]["seconds"] == 3600
    # Preference breakdowns are overall-only, never stored on month rows.
    assert "prefer_category" not in p


def test_normalize_weekly_has_no_readstat():
    p = stats.normalize("weekly", stats.base_time_for("weekly", 0), fx.WEEKLY)
    assert p["read_stat"] == []
    assert p["dist_unit"] == "minutes"


def test_normalize_annually_is_hours():
    p = stats.normalize("annually", stats.base_time_for("annually", 0), fx.ANNUALLY)
    assert p["dist_unit"] == "hours"
    assert len(p["distribution"]) == 2


def test_normalize_overall_trims_zero_years_and_keeps_prefs():
    p = stats.normalize("overall", 0, fx.OVERALL)
    assert p["distribution"] == [{"label": "2020", "seconds": 677539}]
    assert p["dist_unit"] == "hours"
    assert p["prefer_category"] and p["prefer_author"]
    assert len(p["prefer_time"]) == 24
    assert p["day_average"] is None  # overall provides no day-average


def test_base_time_for_is_period_start():
    bt = stats.base_time_for("monthly", 0)
    d = datetime.fromtimestamp(bt, tzinfo())
    assert d.day == 1 and d.hour == 0
    assert stats.base_time_for("monthly", -1) < bt  # previous month is earlier
    wk = datetime.fromtimestamp(stats.base_time_for("weekly", 0), tzinfo())
    assert wk.weekday() == 0 and wk.hour == 0  # weekly start is a Monday
    assert stats.base_time_for("overall", -3) == 0  # overall ignores offset


def test_historical_base_times_bounded_by_registration():
    reg = int(time.time()) - 5 * 7 * 86400  # ~5 weeks ago
    weeks = stats.historical_base_times("weekly", reg)
    assert weeks[0] == stats.base_time_for("weekly", 0)  # recent-first, current included
    assert 4 <= len(weeks) <= 8
    assert stats.historical_base_times("overall", reg) == [0]


def test_period_label():
    assert stats.period_label("overall", 0, 0) == "总计"
    assert stats.period_label("monthly", 0, stats.base_time_for("monthly", 0)) == "本月"
    assert stats.period_label("weekly", 0, stats.base_time_for("weekly", 0)) == "本周"
    bt = stats.base_time_for("annually", -1)
    yr = datetime.fromtimestamp(bt, tzinfo()).year
    assert stats.period_label("annually", -1, bt) == f"{yr}年"
