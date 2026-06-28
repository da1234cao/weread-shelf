"""Infrastructure config: DB location and HTTP-client tuning.

Everything user-facing (API key, skill version, timezone, schedule, module
visibility, access control) lives in the DB and is edited from the admin page —
see :mod:`app.settings_store`. There is no ``.env`` file; the container provides
``DB_PATH`` via the Dockerfile and the port via compose.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Storage. Container overrides this to /data/weread.db.
    db_path: str = "./data/weread.db"

    # WeRead gateway HTTP tuning (not user-configurable).
    weread_base_url: str = "https://i.weread.qq.com/api/agent/gateway"
    request_timeout: float = 20.0
    max_retries: int = 3
    # Polite delay (seconds) between successive gateway calls in a pull run.
    request_interval: float = 0.5

    # Web server port (used by `cli.py serve`; the container hardcodes 8765).
    port: int = 8765


@lru_cache
def get_settings() -> Settings:
    return Settings()
