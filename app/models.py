"""SQLModel table definitions.

Design centres on *dated snapshots* so long-term trends can be derived — the
value a daily pull adds over the WeRead app itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PeriodStat(SQLModel, table=True):
    """Per-period reading stats from /readdata/detail, normalized for display.

    One row per (mode, base_time), where base_time is the period's normalized
    start (overall uses 0). Past periods are immutable and fetched once on the
    first pull; the current/previous period and `overall` refresh every pull.
    The payload is already display-ready (see app.stats.normalize), so the web
    layer just reads it — no live gateway call when browsing.
    """

    __tablename__ = "period_stat"

    mode: str = Field(primary_key=True, description="weekly|monthly|annually|overall")
    base_time: int = Field(primary_key=True, description="period start ts; 0 for overall")
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
    # Per-book counts from /user/notebooks. note_count is the 划线 (highlight)
    # count; review_count covers 想法 + 书评. (The API's bookmarkCount is always 0,
    # so it isn't stored — the real highlight total lives in note_count.)
    reading_progress: int = 0
    note_count: int = 0
    review_count: int = 0
    # Whether /book/info has already enriched the metadata (lazy, write-once).
    info_fetched: int = 0
    # chapterUpdateTime of the last TOC fetch (0 = never). The shelf's per-book
    # updateTime equals this, so we re-fetch chapters only when the shelf reports
    # a newer value — free change detection that handles serialized books.
    chapters_update_time: int = 0
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
    """A personal review. chapter_uid>0 is a passage thought (想法, abstract = the
    quoted text); chapter_uid==0 is a whole-book review (书评)."""

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


class Chapter(SQLModel, table=True):
    """A book's table-of-contents entry (from /book/chapterinfo, public data).

    Stored as a full snapshot per book (replaced on each fetch) so the detail
    page can show the real catalog, not just chapters that happen to have notes.
    """

    __tablename__ = "chapter"

    book_id: str = Field(primary_key=True, index=True)
    chapter_uid: int = Field(primary_key=True)
    chapter_idx: int = 0
    title: str = ""
    level: int = 1  # 1 = top level; >1 indents under its parent in the TOC.
    word_count: int = 0
    update_time: int = 0


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
    # Show the "built on weread-shelf" credit line in the page footer.
    show_footer_credit: bool = True
    require_user_auth: bool = False
    pull_interval_hours: int = 24
    # Snapshot retention window in days (0 = keep forever). Pulls older than this
    # are pruned from the append-only snapshot tables; each series' latest is always
    # kept. See repository.prune_snapshots.
    retention_days: int = 0
    # Seconds between successive gateway calls in a pull (politeness / rate limit).
    gateway_interval: float = 0.2
    # Flips True once the first full historical stats backfill has completed.
    stats_backfilled: bool = False
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
    kind: str = "daily"  # startup | daily | manual | backfill
    error: str = ""
    counts_json: str = "{}"
