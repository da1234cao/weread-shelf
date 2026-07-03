"""Thin client over the WeRead (微信读书) Agent API Gateway.

All operations hit a single endpoint:

    POST https://i.weread.qq.com/api/agent/gateway
    Authorization: Bearer wrk-xxxx
    body: {"api_name": "/path", "skill_version": "<auto>", ...flat params}

If the gateway returns an ``upgrade_info`` block with a newer ``skill_version``,
the client auto-updates its version and persists it so the next call uses it.

Errors are returned as a JSON body with a non-zero ``errcode`` (often with an
HTTP status of 499). Reading-duration fields are always in *seconds*.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from . import settings_store
from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class WeReadError(RuntimeError):
    """A business-level error returned by the gateway (non-zero errcode)."""

    def __init__(self, errcode: int, errmsg: str, api_name: str):
        self.errcode = errcode
        self.errmsg = errmsg
        self.api_name = api_name
        super().__init__(f"{api_name} failed: errcode={errcode} errmsg={errmsg}")


class WeReadAuthError(WeReadError):
    """Authentication / API-key problem (expired, invalid, or unauthorized)."""


# errcodes that indicate the API key is no longer valid.
_AUTH_ERRCODES = {-2001, -2010, -2012}
# HTTP statuses worth retrying (transient).
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class WeReadClient:
    """Synchronous gateway client with retries and polite rate limiting."""

    def __init__(
        self,
        api_key: str | None = None,
        skill_version: str | None = None,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ):
        self.settings = settings or get_settings()
        app = settings_store.get()
        self.api_key = api_key or app.api_key
        self.skill_version = skill_version or app.skill_version
        if not self.api_key:
            raise RuntimeError(
                "API key not configured. Generate one at "
                "https://weread.qq.com/r/weread-skills and set it on the admin page (/admin)."
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.settings.request_timeout)
        self._last_call_ts = 0.0

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WeReadClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core --------------------------------------------------------------
    def call(self, api_name: str, **params: Any) -> dict[str, Any]:
        """Invoke a gateway ``api_name`` with flat top-level params."""
        body = {"api_name": api_name, "skill_version": self.skill_version}
        # Only include params that were actually provided (drop None).
        body.update({k: v for k, v in params.items() if v is not None})
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                resp = self._client.post(self.settings.weread_base_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt <= self.settings.max_retries:
                    self._backoff(attempt, reason=f"network error: {exc}")
                    continue
                raise WeReadError(-1, f"network error after retries: {exc}", api_name) from exc

            if resp.status_code in _RETRY_STATUSES and attempt <= self.settings.max_retries:
                self._backoff(attempt, reason=f"HTTP {resp.status_code}")
                continue

            data = self._parse(resp, api_name)
            self._check_errors(data, api_name)
            self._auto_update_version(data.get("upgrade_info"))
            return data

    # -- typed helpers -----------------------------------------------------
    def read_data_detail(self, mode: str | None = None, base_time: int | None = None) -> dict[str, Any]:
        """Reading statistics. mode in weekly|monthly|annually|overall."""
        return self.call("/readdata/detail", mode=mode, baseTime=base_time)

    def shelf_sync(self) -> dict[str, Any]:
        return self.call("/shelf/sync")

    def notebooks(self, count: int = 50, last_sort: int | None = None) -> dict[str, Any]:
        return self.call("/user/notebooks", count=count, lastSort=last_sort)

    def bookmark_list(self, book_id: str) -> dict[str, Any]:
        """User's highlights (划线) for a book."""
        return self.call("/book/bookmarklist", bookId=book_id)

    def my_reviews(self, book_id: str, count: int = 50, synckey: int | None = None) -> dict[str, Any]:
        """User's own reviews for a book — passage thoughts (想法) and the whole-book
        review (书评), distinguished by each item's chapterUid. Note: param is `bookid`."""
        return self.call("/review/list/mine", bookid=book_id, count=count, synckey=synckey)

    def recommend(self, count: int = 12, max_idx: int | None = None) -> dict[str, Any]:
        return self.call("/book/recommend", count=count, maxIdx=max_idx)

    def book_info(self, book_id: str) -> dict[str, Any]:
        """Public book metadata (intro/category/publisher/isbn)."""
        return self.call("/book/info", bookId=book_id)

    def chapter_info(self, book_id: str) -> dict[str, Any]:
        """Public table of contents (chapter list) for a book."""
        return self.call("/book/chapterinfo", bookId=book_id)

    # -- internals ---------------------------------------------------------
    def _throttle(self) -> None:
        # Spacing between gateway calls is user-tunable from the admin page so the
        # operator can find a rate that doesn't trip the gateway's limits.
        interval = settings_store.get().gateway_interval
        wait = interval - (time.monotonic() - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(2 ** attempt, 30)
        logger.warning("gateway retry %d (%s); sleeping %ss", attempt, reason, delay)
        time.sleep(delay)

    @staticmethod
    def _parse(resp: httpx.Response, api_name: str) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise WeReadError(
                resp.status_code, f"non-JSON response: {resp.text[:200]}", api_name
            ) from exc
        if not isinstance(data, dict):
            raise WeReadError(resp.status_code, f"unexpected response type: {type(data)}", api_name)
        return data

    @staticmethod
    def _check_errors(data: dict[str, Any], api_name: str) -> None:
        errcode = data.get("errcode")
        if errcode is not None and errcode != 0:
            errmsg = str(data.get("errmsg", ""))
            if errcode in _AUTH_ERRCODES:
                raise WeReadAuthError(errcode, errmsg, api_name)
            raise WeReadError(errcode, errmsg, api_name)

    def _auto_update_version(self, upgrade_info: Any) -> None:
        """If the gateway suggests a newer skill_version, adopt it immediately."""
        if not upgrade_info:
            return
        version = self._extract_version(upgrade_info)
        if version and version != self.skill_version:
            old = self.skill_version
            self.skill_version = version
            settings_store.save(skill_version=version)
            logger.info("skill_version auto-updated: %s -> %s", old, version)

    @staticmethod
    def _extract_version(upgrade_info: Any) -> str:
        """Pull a version string from the gateway's upgrade_info dict."""
        if isinstance(upgrade_info, dict):
            for key in ("latest_version", "skill_version", "version", "latest", "latestVersion", "suggest_version"):
                if upgrade_info.get(key):
                    return str(upgrade_info[key])
            return str(upgrade_info)[:40] if upgrade_info else ""
        return str(upgrade_info)[:40]
