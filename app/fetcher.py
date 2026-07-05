"""Daily pull orchestration and historical backfill.

A run is intentionally resilient: a failure fetching one book's notes is logged
and counted, but does not abort the whole pull.

Design rule: **never hold a DB write transaction while making network calls.**
The main pull first fetches all API data, then persists it in one short transaction.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from . import repository as repo
from . import settings_store
from . import stats
from .client import WeReadClient
from .db import init_db, session_scope
from .models import PullRun
from .utils import today_str, tzinfo

logger = logging.getLogger(__name__)

# Single guard shared by every pull trigger — startup, scheduler, manual refresh —
# so the three can never overlap and write the DB concurrently.
_pull_lock = threading.Lock()


def pull_running() -> bool:
    """True while any pull holds the lock. Used by the web layer to report status."""
    return _pull_lock.locked()


def run_pull_locked(kind: str) -> bool:
    """Run a pull under the shared lock, swallowing errors. Skips if one is already running.

    Returns True if this call actually ran a pull, False if it was skipped because
    another pull held the lock.
    """
    if not _pull_lock.acquire(blocking=False):
        logger.info("pull already running; skipping %s", kind)
        return False
    try:
        run_daily_pull(kind=kind)
    except Exception:
        logger.exception("%s pull failed", kind)
    finally:
        _pull_lock.release()
    return True


def run_daily_pull(client: WeReadClient | None = None, kind: str = "daily") -> dict[str, int]:
    """Pull all data and persist it. Returns a counts summary.

    The pull_run row is opened first and finalized in its own transaction *after*
    every phase finishes, so its ok flag and counts reflect the whole run —
    enrichment and pruning included — and a failure is recorded rather than rolled
    back with the data.
    """
    init_db()
    owns = client is None
    client = client or WeReadClient()
    counts: dict[str, int] = {
        "stats": 0, "shelf": 0, "books": 0, "books_info": 0, "chapters": 0,
        "bookmarks": 0, "reviews": 0, "recs": 0, "pruned": 0, "cleaned": 0, "errors": 0,
    }
    pull_date = today_str()
    with session_scope() as session:
        run_id = repo.start_pull(session, kind).id

    error = ""
    try:
        _pull_stats(client, counts)
        _run_main_pull(client, pull_date, counts)
        _enrich_book_info(client, counts)
        _enrich_chapters(client, counts)
        _prune_old_data(counts)
        _clean_orphaned_chapters(counts)
    except Exception as exc:
        error = str(exc)
        logger.exception("%s pull failed", kind)
        raise
    finally:
        if owns:
            client.close()
        with session_scope() as session:
            repo.finish_pull(session, session.get(PullRun, run_id),
                             ok=not error, counts=counts, error=error)
        _wal_checkpoint()

    logger.info("pull complete: %s", counts)
    return counts


# ---------------------------------------------------------------------------
# Reading stats
# ---------------------------------------------------------------------------

def _pull_stats(client: WeReadClient, counts: dict[str, int]) -> None:
    """Fetch reading statistics into ``period_stat``.

    Every pull refreshes the *volatile* periods — ``overall`` (cumulative) plus the
    current and previous week/month/year. The first pull additionally backfills
    every immutable past period, then flips ``stats_backfilled``. Each period is its
    own short transaction; one period failing is logged and counted, not fatal.
    """
    overall = _fetch_period(client, "overall", 0, counts)
    for mode in ("weekly", "monthly", "annually"):
        for offset in (0, -1):
            _fetch_period(client, mode, stats.base_time_for(mode, offset), counts)

    if settings_store.get().stats_backfilled or overall is None:
        return

    reg_time = int(overall.get("registTime") or 0) or _earliest_read_ts(overall)
    complete = True
    for mode in ("weekly", "monthly", "annually"):
        targets = stats.historical_base_times(mode, reg_time)
        for base_time in targets:
            with session_scope() as session:
                if repo.has_period_stat(session, mode, base_time):
                    continue
            if _fetch_period(client, mode, base_time, counts) is None:
                complete = False
        logger.info("stats backfill: %s, %d periods", mode, len(targets))
    if complete:
        settings_store.save(stats_backfilled=True)


def _fetch_period(
    client: WeReadClient, mode: str, base_time: int, counts: dict[str, int]
) -> dict[str, Any] | None:
    """Fetch one (mode, base_time) period and store its normalized payload.

    Tolerant: a failure is logged + counted and returns None, so a flaky single
    period can't abort the whole pull. Returns the raw response on success.
    """
    try:
        base_arg = None if mode == "overall" else base_time
        data = client.read_data_detail(mode=mode, base_time=base_arg)
    except Exception as exc:
        logger.warning("stats fetch failed for %s@%s: %s", mode, base_time, exc)
        counts["errors"] += 1
        return None
    with session_scope() as session:
        repo.upsert_period_stat(session, mode, base_time, stats.normalize(mode, data))
    counts["stats"] += 1
    return data


def _earliest_read_ts(overall: dict[str, Any]) -> int:
    """Fallback registration bound: earliest bucket in overall's readTimes, else now."""
    times = [int(t) for t in (overall.get("readTimes") or {})]
    return min(times) if times else int(datetime.now(tzinfo()).timestamp())


# ---------------------------------------------------------------------------
# Main pull: fetch *then* persist (never hold a write lock during network I/O)
# ---------------------------------------------------------------------------

def _shelf_items_and_finish_map(shelf: dict[str, Any]) -> tuple[list[dict], dict[str, int]]:
    """Parse the shelf response into upsert-ready items and a book->finished map."""
    archive: dict[str, str] = {}
    for arc in shelf.get("archive", []) or []:
        name = arc.get("name", "")
        for bid in arc.get("bookIds", []) or []:
            archive[bid] = name

    items: list[dict] = []
    finish_map: dict[str, int] = {}
    for b in shelf.get("books", []) or []:
        bid = b.get("bookId")
        if not bid:
            continue
        finished = int(b.get("finishReading", 0) or 0)
        finish_map[bid] = finished
        items.append({
            "book_id": bid,
            "title": b.get("title"), "author": b.get("author"), "cover": b.get("cover"),
            "archive_name": archive.get(bid, ""),
            "finish_reading": finished,
            "secret": int(b.get("secret", 0) or 0),
            "update_time": int(b.get("updateTime", 0) or 0),
        })
    return items, finish_map


def _category(book: dict[str, Any]) -> str:
    cats = book.get("categories")
    if isinstance(cats, list) and cats:
        return cats[0].get("title", "") if isinstance(cats[0], dict) else ""
    return ""


def _chapter_title_map(chapters: list[dict[str, Any]]) -> dict[int, str]:
    return {int(c["chapterUid"]): c.get("title", "") for c in chapters or [] if "chapterUid" in c}


def _run_main_pull(client: WeReadClient, pull_date: str, counts: dict[str, int]) -> None:
    """Fetch everything from the API first, then persist in one short transaction.

    The key invariant: **no network call happens inside a DB write transaction.**
    This keeps the SQLite write lock held for seconds, not minutes, so concurrent
    writes (like settings saves) never time out.
    """

    # -- phase 1: fetch all data (no DB lock held) --------------------------------

    shelf = client.shelf_sync()
    shelf_items, finish_map = _shelf_items_and_finish_map(shelf)

    notebook_books = _fetch_notebooks(client)
    counts["books"] = len(notebook_books)

    # Book meta + per-book highlights/reviews gathered during the fetch loop.
    book_metas: list[dict] = []               # kwargs for repo.upsert_book
    bookmark_datas: list[tuple[str, dict]] = []  # (book_id, raw API response)
    review_datas: list[tuple[str, list[dict]]] = []  # (book_id, parsed reviews)

    for nb in notebook_books:
        book = nb.get("book", {}) or {}
        bid = nb.get("bookId") or book.get("bookId")
        if not bid:
            continue

        book_metas.append({
            "book_id": bid,
            "title": book.get("title"), "author": book.get("author"), "cover": book.get("cover"),
            "category": _category(book), "publisher": book.get("publisher"),
            "publish_time": book.get("publishTime"), "intro": book.get("intro"),
            "finished": finish_map.get(bid, 0),
            "reading_progress": int(nb.get("readingProgress", 0) or 0),
            "note_count": int(nb.get("noteCount", 0) or 0),
            "review_count": int(nb.get("reviewCount", 0) or 0),
        })

        if int(nb.get("noteCount", 0) or 0) > 0:
            try:
                bookmark_datas.append((bid, client.bookmark_list(bid)))
            except Exception as exc:
                logger.warning("bookmarklist failed for %s: %s", bid, exc)
                counts["errors"] += 1

        if int(nb.get("reviewCount", 0) or 0) > 0:
            try:
                data = client.my_reviews(bid, count=100)
                reviews = [item.get("review", item) for item in data.get("reviews", []) or []]
                review_datas.append((bid, reviews))
            except Exception as exc:
                logger.warning("review/list/mine failed for %s: %s", bid, exc)
                counts["errors"] += 1

    # Recommendations (non-essential — failure is logged, not fatal).
    rec_books: list[dict] = []
    try:
        rec_books = (client.recommend(count=12).get("books") or []) or []
    except Exception as exc:
        logger.warning("recommendations failed: %s", exc)
        counts["errors"] += 1

    # -- phase 2: persist everything in one short transaction --------------------

    with session_scope() as session:
        # Shelf
        for it in shelf_items:
            repo.upsert_book(
                session, it["book_id"],
                title=it.pop("title"), author=it.pop("author"), cover=it.pop("cover"),
                finished=it["finish_reading"],
            )
        counts["shelf"] = repo.save_shelf(session, pull_date, shelf_items)

        # Notebook metadata
        for meta in book_metas:
            repo.upsert_book(session, meta.pop("book_id"), **meta)

        # Highlights
        for bid, data in bookmark_datas:
            chapters = _chapter_title_map(data.get("chapters", []))
            counts["bookmarks"] += repo.upsert_bookmarks(
                session, data.get("updated", []), data.get("removed", []), chapters)

        # Reviews
        for _bid, reviews in review_datas:
            counts["reviews"] += repo.upsert_reviews(session, reviews)

        # Recommendations
        for b in rec_books:
            if b.get("bookId"):
                repo.upsert_book(
                    session, b["bookId"], title=b.get("title"), author=b.get("author"),
                    cover=b.get("cover"), category=b.get("category"), intro=b.get("intro"),
                )
        counts["recs"] = repo.save_recommendations(session, pull_date, rec_books)


def _fetch_notebooks(client: WeReadClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_sort: int | None = None
    for _ in range(100):  # safety bound
        data = client.notebooks(count=50, last_sort=last_sort)
        books = data.get("books", []) or []
        out.extend(books)
        if not books or not data.get("hasMore"):
            break
        last_sort = books[-1].get("sort")
        if last_sort is None:
            break
    return out


# ---------------------------------------------------------------------------
# Enrichment (already fetch-then-persist per book — short transactions)
# ---------------------------------------------------------------------------

def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _enrich_book_info(client: WeReadClient, counts: dict[str, int]) -> None:
    """Fetch /book/info once per book (write-once), one short txn per book."""
    with session_scope() as session:
        pending = repo.book_ids_needing_info(session)
    for bid in pending:
        try:
            info = client.book_info(bid)
        except Exception as exc:
            logger.warning("book_info failed for %s: %s", bid, exc)
            counts["errors"] += 1
            continue
        with session_scope() as session:
            repo.upsert_book(
                session, bid,
                title=_str(info.get("title")), author=_str(info.get("author")),
                translator=_str(info.get("translator")), cover=_str(info.get("cover")),
                category=_str(info.get("category")), intro=_str(info.get("intro")),
                publisher=_str(info.get("publisher")), publish_time=_str(info.get("publishTime")),
                isbn=_str(info.get("isbn")), info_fetched=1,
            )
        counts["books_info"] += 1


def _enrich_chapters(client: WeReadClient, counts: dict[str, int]) -> None:
    """Fetch /book/chapterinfo per book, one short txn per book."""
    with session_scope() as session:
        pending = repo.book_chapter_fetch_targets(session)
    for bid in pending:
        try:
            info = client.chapter_info(bid)
        except Exception as exc:
            logger.warning("chapterinfo failed for %s: %s", bid, exc)
            counts["errors"] += 1
            continue
        stamp = max(int(info.get("chapterUpdateTime", 0) or 0), 1)
        with session_scope() as session:
            repo.replace_chapters(session, bid, info.get("chapters", []))
            repo.upsert_book(session, bid, chapters_update_time=stamp)
        counts["chapters"] += 1


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def _prune_old_data(counts: dict[str, int]) -> None:
    days = settings_store.get().retention_days
    if days <= 0:
        return
    cutoff_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with session_scope() as session:
        counts["pruned"] = repo.prune_snapshots(session, cutoff_dt)
    if counts["pruned"]:
        logger.info("pruned %d pull-run rows older than %d days", counts["pruned"], days)


def _clean_orphaned_chapters(counts: dict[str, int]) -> None:
    with session_scope() as session:
        n = repo.clean_orphaned_chapters(session)
    counts["cleaned"] = n
    if n:
        logger.info("cleaned %d orphaned chapter rows", n)


def _wal_checkpoint() -> None:
    from .db import get_engine

    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
    except Exception:
        logger.warning("WAL checkpoint failed", exc_info=True)
