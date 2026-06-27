"""Test fixtures: isolated temp SQLite DB and dummy settings per test."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WEREAD_API_KEY", "wrk-test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("REQUEST_INTERVAL", "0")
    monkeypatch.setenv("MAX_RETRIES", "2")
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    # Reset cached settings + engine so they pick up the temp env.
    from app import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db.init_db()
    yield
    db._engine = None
    config.get_settings.cache_clear()
