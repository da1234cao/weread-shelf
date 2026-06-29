"""Test fixtures: isolated temp SQLite DB and seeded settings per test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAX_RETRIES", "2")

    # Reset cached settings/engine/store so they pick up the temp DB.
    from app import config, db, settings_store

    config.get_settings.cache_clear()
    db._engine = None
    settings_store.invalidate()
    db.init_db()
    # Seed the DB-backed config the way the admin page would (interval 0 = no throttle).
    settings_store.save(
        api_key="wrk-test", skill_version="1.0.3", timezone="Asia/Shanghai", gateway_interval=0.0
    )
    yield
    db._engine = None
    config.get_settings.cache_clear()
    settings_store.invalidate()
