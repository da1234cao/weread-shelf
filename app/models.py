"""SQLModel table definitions.

Design centres on *dated snapshots* so long-term trends can be derived — the
value a daily pull adds over the WeRead app itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DailyReadTime(SQLModel, table=True):
    """Per-day reading seconds, the headline time series.

    Sourced from /readdata/detail `readTimes` maps (monthly mode gives per-day
    values for a month; historical months are backfilled via baseTime).
    """

    __tablename__ = "daily_read_time"

    date: str = Field(primary_key=True, description="YYYY-MM-DD (local day)")
    seconds: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)


class StatSnapshot(SQLModel, table=True):
    """A dated snapshot of /readdata/detail for a given mode.

    The rich preference breakdowns (category/author/hour/etc.) are kept verbatim
    as JSON so the dashboard can render them without a dedicated column each.
    """

    __tablename__ = "stat_snapshot"

    id: int | None = Field(default=None, primary_key=True)
    pull_date: str = Field(index=True, description="YYYY-MM-DD of the pull")
    mode: str = Field(index=True, description="weekly|monthly|annually|overall")
    total_read_time: int = 0
    read_days: int = 0
    day_average: int = 0
    read_rate: int = 0
    wr_read_time: int = 0
    wr_listen_time: int = 0
    payload_json: str = "{}"
    captured_at: datetime = Field(default_factory=_utcnow)


class Book(SQLModel, table=True):
    """Latest known metadata + per-book counts (upserted from every source)."""

    __tablename__ = "book"

    book_id: str = Field(primary_key=True)
    title: str = ""
    author: str = ""
    translator: str = ""
    cover: str = ""
    category: str = ""
    intro: str = ""
    publisher: str = ""
    publish_time: str = ""
    isbn: str = ""
    finished: int = 0
    rating: int = 0
    rating_count: int = 0
    # Per-book reading state (from notebooks / getprogress).
    reading_progress: int = 0
    note_count: int = 0
    bookmark_count: int = 0
    review_count: int = 0
    # Whether /book/info has already enriched the metadata (lazy, write-once).
    info_fetched: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)


class ShelfItem(SQLModel, table=True):
    """Dated snapshot of shelf membership (lets us track shelf changes)."""

    __tablename__ = "shelf_item"

    pull_date: str = Field(primary_key=True)
    book_id: str = Field(primary_key=True)
    archive_name: str = ""
    finish_reading: int = 0
    secret: int = 0
    update_time: int = 0


class Bookmark(SQLModel, table=True):
    """A highlight (划线) — incrementally upserted, honoring `removed`."""

    __tablename__ = "bookmark"

    bookmark_id: str = Field(primary_key=True)
    book_id: str = Field(index=True)
    chapter_uid: int = 0
    chapter_idx: int = 0
    chapter_title: str = ""
    mark_text: str = ""
    color_style: int = 0
    type: int = 0
    range: str = ""
    create_time: int = 0


class Review(SQLModel, table=True):
    """A personal thought/note (想法) — abstract is the quoted text."""

    __tablename__ = "review"

    review_id: str = Field(primary_key=True)
    book_id: str = Field(index=True)
    chapter_uid: int = 0
    chapter_idx: int = 0
    chapter_title: str = ""
    content: str = ""
    abstract: str = ""
    range: str = ""
    type: int = 0
    is_private: int = 0
    create_time: int = 0


class Recommendation(SQLModel, table=True):
    """Dated snapshot of personalized recommendations (latest = current)."""

    __tablename__ = "recommendation"

    pull_date: str = Field(primary_key=True)
    book_id: str = Field(primary_key=True)
    title: str = ""
    author: str = ""
    cover: str = ""
    category: str = ""
    intro: str = ""


class AppSettings(SQLModel, table=True):
    """Single-row (id=1) app configuration, editable from the admin page.

    Replaces what used to be environment variables: API key, skill version,
    timezone, schedule, module visibility, access control, and the session
    signing secret.
    """

    __tablename__ = "app_settings"

    id: int = Field(default=1, primary_key=True)
    api_key: str = ""
    skill_version: str = "1.0.3"
    # Version the gateway last suggested upgrading to (via upgrade_info).
    suggested_skill_version: str = ""
    show_overview: bool = True
    show_discover: bool = True
    require_user_auth: bool = False
    pull_interval_hours: int = 24
    timezone: str = "Asia/Shanghai"
    # Random secret for signing session cookies (generated on first run).
    session_secret: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)


class AppUser(SQLModel, table=True):
    """Dashboard accounts. The admin is just a user with is_admin=True."""

    __tablename__ = "app_user"

    username: str = Field(primary_key=True)
    password_hash: str = ""
    is_admin: bool = False
    # Force a credential change on first login (seeded admin starts True).
    must_change: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PullRun(SQLModel, table=True):
    """One fetch run — for observability and the 'last successful pull' badge."""

    __tablename__ = "pull_run"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    ok: bool = False
    kind: str = "daily"  # daily | backfill | manual
    error: str = ""
    counts_json: str = "{}"
