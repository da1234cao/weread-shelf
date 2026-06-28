"""Daily pull orchestration and historical backfill.

A run is intentionally resilient: a failure fetching one book's notes is logged
and counted, but does not abort the whole pull.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from . import repository as repo
from . import settings_store
from .client import WeReadClient
from .db import init_db, session_scope
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


def _shelf_archive_map(shelf: dict[str, Any]) -> dict[str, str]:
    """book_id -> archive(folder) name."""
    mapping: dict[str, str] = {}
    for arc in shelf.get("archive", []) or []:
        name = arc.get("name", "")
        for bid in arc.get("bookIds", []) or []:
            mapping[bid] = name
    return mapping


def _chapter_title_map(chapters: list[dict[str, Any]]) -> dict[int, str]:
    return {int(c["chapterUid"]): c.get("title", "") for c in chapters or [] if "chapterUid" in c}


def run_daily_pull(client: WeReadClient | None = None, kind: str = "daily") -> dict[str, int]:
    """Pull all data and persist it. Returns a counts summary."""
    init_db()
    owns = client is None
    client = client or WeReadClient()
    counts: dict[str, int] = {
        "days": 0, "shelf": 0, "books": 0, "books_info": 0, "chapters": 0,
        "bookmarks": 0, "reviews": 0, "recs": 0, "errors": 0,
    }
    pull_date = today_str()

    try:
        _run_main_pull(client, kind, pull_date, counts)
        # Enrich book metadata (/book/info) *after* the main transaction commits,
        # one short transaction per book, so these per-book network calls never
        # hold the main write lock. (The book page used to fetch this lazily on
        # click, which intermittently caused "database is locked" 500s.)
        _enrich_book_info(client, counts)
        # Same write-once treatment for each book's table of contents.
        _enrich_chapters(client, counts)
    finally:
        if owns:
            client.close()

    _record_suggested_version(getattr(client, "upgrade_info", None))
    return counts


def _run_main_pull(client: WeReadClient, kind: str, pull_date: str, counts: dict[str, int]) -> None:
    """The bulk pull (stats, shelf, notebooks, notes, recommendations) in one txn."""
    with session_scope() as session:
        run = repo.start_pull(session, kind)
        try:
            # 1) Reading statistics (overall + current month + current week).
            for mode in ("overall", "monthly", "weekly"):
                data = client.read_data_detail(mode=mode)
                repo.save_stat_snapshot(session, pull_date, mode, data)
                if mode in ("monthly", "weekly"):
                    counts["days"] += repo.upsert_daily_read_times(session, data.get("readTimes", {}))

            # 2) Shelf. The shelf is the only source of the per-user "read-finished"
            # flag (finishReading); we record it per book to reuse in the notebook loop.
            shelf = client.shelf_sync()
            archive = _shelf_archive_map(shelf)
            shelf_items = []
            finish_map: dict[str, int] = {}
            for b in shelf.get("books", []) or []:
                bid = b.get("bookId")
                if not bid:
                    continue
                finished = int(b.get("finishReading", 0) or 0)
                finish_map[bid] = finished
                repo.upsert_book(
                    session, bid,
                    title=b.get("title"), author=b.get("author"), cover=b.get("cover"),
                    finished=finished,
                )
                shelf_items.append({
                    "book_id": bid,
                    "archive_name": archive.get(bid, ""),
                    "finish_reading": finished,
                    "secret": int(b.get("secret", 0) or 0),
                    "update_time": int(b.get("updateTime", 0) or 0),
                })
            counts["shelf"] = repo.save_shelf(session, pull_date, shelf_items)

            # 3) Notebooks (paginated) -> per-book counts + metadata.
            notebook_books = _fetch_all_notebooks(client)
            counts["books"] = len(notebook_books)
            for nb in notebook_books:
                book = nb.get("book", {}) or {}
                bid = nb.get("bookId") or book.get("bookId")
                if not bid:
                    continue
                category = ""
                cats = book.get("categories")
                if isinstance(cats, list) and cats:
                    category = cats[0].get("title", "") if isinstance(cats[0], dict) else ""
                # finished := finishReading from the shelf, NOT the notebook's
                # book.finished (= 已完结, the *book* is serialized to completion, not
                # that the user read it). Off-shelf books have no such evidence → 0,
                # which also clears stale flags written by the old behaviour.
                repo.upsert_book(
                    session, bid,
                    title=book.get("title"), author=book.get("author"), cover=book.get("cover"),
                    category=category, publisher=book.get("publisher"),
                    publish_time=book.get("publishTime"), intro=book.get("intro"),
                    finished=finish_map.get(bid, 0),
                    reading_progress=int(nb.get("readingProgress", 0) or 0),
                    note_count=int(nb.get("noteCount", 0) or 0),
                    bookmark_count=int(nb.get("bookmarkCount", 0) or 0),
                    review_count=int(nb.get("reviewCount", 0) or 0),
                )
                # 4) Highlights (划线) and thoughts (想法) per book.
                if int(nb.get("noteCount", 0) or 0) > 0:
                    counts["bookmarks"] += _pull_bookmarks(client, session, bid, counts)
                if int(nb.get("reviewCount", 0) or 0) > 0:
                    counts["reviews"] += _pull_reviews(client, session, bid, counts)

            # 5) Recommendations.
            try:
                rec = client.recommend(count=12)
                rec_books = rec.get("books", []) or []
                for b in rec_books:
                    if b.get("bookId"):
                        repo.upsert_book(
                            session, b["bookId"], title=b.get("title"), author=b.get("author"),
                            cover=b.get("cover"), category=b.get("category"), intro=b.get("intro"),
                        )
                counts["recs"] = repo.save_recommendations(session, pull_date, rec_books)
            except Exception as exc:  # recommendations are non-essential
                logger.warning("recommendations failed: %s", exc)
                counts["errors"] += 1

            repo.finish_pull(session, run, ok=True, counts=counts)
            logger.info("pull complete: %s", counts)
        except Exception as exc:
            logger.exception("pull failed")
            repo.finish_pull(session, run, ok=False, counts=counts, error=str(exc))
            raise


def _str(value: Any) -> str:
    """Coerce a gateway field to a string ('' for missing/non-string values)."""
    return value if isinstance(value, str) else ""


def _enrich_book_info(client: WeReadClient, counts: dict[str, int]) -> None:
    """Fetch /book/info once per book (write-once) and persist its metadata.

    Runs after the main pull transaction, one short transaction per book. A book
    is only fetched while ``info_fetched`` is 0, so steady-state daily pulls only
    enrich books that are new since the last pull.
    """
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
    """Fetch /book/chapterinfo and store each book's table of contents.

    Mirrors :func:`_enrich_book_info` (runs after the main transaction, one short
    transaction per book), but instead of write-once it re-fetches a book when the
    shelf reports newer chapters — see :func:`repo.book_chapter_fetch_targets`.
    """
    with session_scope() as session:
        pending = repo.book_chapter_fetch_targets(session)
    for bid in pending:
        try:
            info = client.chapter_info(bid)
        except Exception as exc:
            logger.warning("chapterinfo failed for %s: %s", bid, exc)
            counts["errors"] += 1
            continue
        # Stamp at least 1 so a book whose server omits chapterUpdateTime is still
        # marked fetched (won't be re-pulled unless the shelf updateTime grows).
        stamp = max(int(info.get("chapterUpdateTime", 0) or 0), 1)
        with session_scope() as session:
            repo.replace_chapters(session, bid, info.get("chapters", []))
            repo.upsert_book(session, bid, chapters_update_time=stamp)
        counts["chapters"] += 1


def _record_suggested_version(upgrade_info: Any) -> None:
    """If the gateway suggested a newer skill_version, store it for the admin hint."""
    if not upgrade_info:
        return
    suggested = ""
    if isinstance(upgrade_info, dict):
        for key in ("skill_version", "version", "latest", "latestVersion", "suggest_version"):
            if upgrade_info.get(key):
                suggested = str(upgrade_info[key])
                break
        suggested = suggested or str(upgrade_info)[:40]
    else:
        suggested = str(upgrade_info)[:40]
    if suggested and suggested != settings_store.get().suggested_skill_version:
        settings_store.save(suggested_skill_version=suggested)


def _fetch_all_notebooks(client: WeReadClient) -> list[dict[str, Any]]:
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


def _pull_bookmarks(client: WeReadClient, session, book_id: str, counts: dict[str, int]) -> int:
    try:
        data = client.bookmark_list(book_id)
    except Exception as exc:
        logger.warning("bookmarklist failed for %s: %s", book_id, exc)
        counts["errors"] += 1
        return 0
    chapters = _chapter_title_map(data.get("chapters", []))
    return repo.upsert_bookmarks(
        session, data.get("updated", []), data.get("removed", []), chapters
    )


def _pull_reviews(client: WeReadClient, session, book_id: str, counts: dict[str, int]) -> int:
    try:
        data = client.my_reviews(book_id, count=100)
    except Exception as exc:
        logger.warning("review/list/mine failed for %s: %s", book_id, exc)
        counts["errors"] += 1
        return 0
    # Each item nests the actual review under "review".
    reviews = [item.get("review", item) for item in data.get("reviews", []) or []]
    return repo.upsert_reviews(session, reviews)


def backfill_history(months: int = 13, client: WeReadClient | None = None) -> dict[str, int]:
    """Seed the daily time series with the last `months` months of per-day data.

    Calls /readdata/detail mode=monthly with a baseTime inside each past month;
    the server normalizes baseTime to the period start.
    """
    init_db()
    owns = client is None
    client = client or WeReadClient()
    total_days = 0
    now = datetime.now(tzinfo())
    try:
        with session_scope() as session:
            run = repo.start_pull(session, "backfill")
            year, month = now.year, now.month
            for _ in range(months):
                base = datetime(year, month, 1, 12, 0, 0, tzinfo=tzinfo())
                data = client.read_data_detail(mode="monthly", base_time=int(base.timestamp()))
                added = repo.upsert_daily_read_times(session, data.get("readTimes", {}))
                total_days += added
                logger.info("backfilled %s-%02d: %d days", year, month, added)
                month -= 1
                if month == 0:
                    month, year = 12, year - 1
            repo.finish_pull(session, run, ok=True, counts={"days": total_days})
    finally:
        if owns:
            client.close()
    return {"days": total_days}
