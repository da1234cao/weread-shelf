"""The /api/stats read endpoint over a FakeClient-populated DB (no network)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import web
from app.fetcher import run_daily_pull

from .test_fetcher_and_repo import FakeClient


def test_api_stats_monthly_current():
    run_daily_pull(client=FakeClient())
    d = web.api_stats(mode="monthly", offset=0)
    assert d["period_label"] == "本月"
    assert d["has_next"] is False
    assert d["has_prev"] is True  # the previous month is refreshed every pull too
    assert any(s["stat"] == "读过" for s in d["read_stat"])
    assert d["distribution"] and d["dist_unit"] == "minutes"


def test_api_stats_weekly_has_no_readstat():
    run_daily_pull(client=FakeClient())
    assert web.api_stats(mode="weekly", offset=0)["read_stat"] == []


def test_api_stats_overall_has_preferences_and_no_nav():
    run_daily_pull(client=FakeClient())
    d = web.api_stats(mode="overall", offset=0)
    assert d["has_prev"] is False and d["has_next"] is False
    assert d["prefer_category"] and len(d["prefer_time"]) == 24


def test_api_stats_missing_period_is_empty():
    run_daily_pull(client=FakeClient())
    assert web.api_stats(mode="annually", offset=-99) == {
        "empty": True, "mode": "annually", "offset": -99,
    }


def test_api_stats_rejects_unknown_mode():
    with pytest.raises(HTTPException):
        web.api_stats(mode="bogus", offset=0)
