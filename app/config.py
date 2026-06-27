"""Application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- WeRead Agent API Gateway ---
    weread_api_key: str = ""
    weread_base_url: str = "https://i.weread.qq.com/api/agent/gateway"
    skill_version: str = "1.0.3"
    request_timeout: float = 20.0
    max_retries: int = 3
    # Polite delay (seconds) between successive gateway calls in a pull run.
    request_interval: float = 0.5

    # --- Storage ---
    # Default to a writable local dir; the container overrides this to /data.
    db_path: str = "./data/weread.db"

    # --- Scheduling ---
    # Cron expression (minute hour day month weekday). Default: 03:00 daily.
    pull_cron: str = "0 3 * * *"
    tz: str = "Asia/Shanghai"
    # Pull once shortly after startup if the DB has no data yet.
    pull_on_startup: bool = True

    # --- Web ---
    port: int = 8765

    @property
    def has_api_key(self) -> bool:
        return bool(self.weread_api_key and self.weread_api_key.startswith("wrk-"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
