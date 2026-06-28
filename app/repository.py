"""Persistence (upserts/dedup) and read-side aggregations for the dashboard.

All functions take an explicit ``Session`` so the fetcher can batch a whole
pull in one transaction and the web layer can open short read transactions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, delete, select

from .models import (
    AppUser,
    Book,
    Bookmark,
    Chapter,
    DailyReadTime,
    PullRun,
    Recommendation,
    Review,
    ShelfItem,
    StatSnapshot,
)
from .utils import fmt_duration, month_label, parse_date, ts_to_date

# --------------------------------------------------------------------------
# Write side (used by the fetcher)
# --------------------------------------------------------------------------


def upsert_daily_read_times(session: Session, read_times: dict[str, Any]) -> int:
    """Store per-day reading seconds from a /readdata/detail readTimes map."""
    count = 0
    for ts, secs in (read_times or {}).items():
        secs = int(secs or 0)
        if secs <= 0:
            continue
        day = ts_to_date(int(ts))
        row = session.get(DailyReadTime, day)
        if row is None:
            session.add(DailyReadTime(date=day, seconds=secs))
        else:
            row.seconds = secs
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
        count += 1
    return count


def save_stat_snapshot(session: Session, pull_date: str, mode: str, data: dict[str, Any]) -> None:
    """Upsert one (pull_date, mode) statistics snapshot."""
    existing = session.exec(
        select(StatSnapshot).where(
            StatSnapshot.pull_date == pull_date, StatSnapshot.mode == mode
        )
    ).first()
    row = existing or StatSnapshot(pull_date=pull_date, mode=mode)
    row.total_read_time = int(data.get("totalReadTime", 0) or 0)
    row.read_days = int(data.get("readDays", 0) or 0)
    row.day_average = int(data.get("dayAverageReadTime", 0) or 0)
    row.read_rate = int(data.get("readRate", 0) or 0)
    row.wr_read_time = int(data.get("wrReadTime", 0) or 0)
    row.wr_listen_time = int(data.get("wrListenTime", 0) or 0)
    # Keep only the display-relevant breakdowns to avoid bloating the row.
    keep = {
        k: data.get(k)
        for k in (
            "preferCategory",
            "preferAuthor",
            "preferTime",
            "preferCategoryWord",
            "preferTimeWord",
            "readStat",
            "compare",
            "registTime",
        )
        if data.get(k) is not None
    }
    row.payload_json = json.dumps(keep, ensure_ascii=False)
    row.captured_at = datetime.now(timezone.utc)
    session.add(row)


def upsert_book(session: Session, book_id: str, **fields: Any) -> None:
    """Merge non-empty fields into the Book row (book_id required)."""
    if not book_id:
        return
    row = session.get(Book, book_id) or Book(book_id=book_id)
    for key, value in fields.items():
        if value in (None, ""):
            continue
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)


def save_shelf(session: Session, pull_date: str, items: list[dict[str, Any]]) -> int:
    """Replace shelf membership snapshot for a pull_date."""
    session.exec(delete(ShelfItem).where(ShelfItem.pull_date == pull_date))
    for it in items:
        session.add(
            ShelfItem(
                pull_date=pull_date,
                book_id=it["book_id"],
                archive_name=it.get("archive_name", ""),
                finish_reading=int(it.get("finish_reading", 0) or 0),
                secret=int(it.get("secret", 0) or 0),
                update_time=int(it.get("update_time", 0) or 0),
            )
        )
    return len(items)


def upsert_bookmarks(
    session: Session, updated: list[dict[str, Any]], removed: list[str], chapters: dict[int, str]
) -> int:
    """Upsert highlights and delete any in `removed`."""
    for bid in removed or []:
        existing = session.get(Bookmark, bid)
        if existing:
            session.delete(existing)
    n = 0
    for bm in updated or []:
        bid = bm.get("bookmarkId")
        if not bid:
            continue
        row = session.get(Bookmark, bid) or Bookmark(bookmark_id=bid)
        row.book_id = bm.get("bookId", row.book_id)
        row.chapter_uid = int(bm.get("chapterUid", 0) or 0)
        row.chapter_idx = int(bm.get("chapterIdx", 0) or 0)
        row.chapter_title = chapters.get(row.chapter_uid, row.chapter_title)
        row.mark_text = bm.get("markText", "")
        row.color_style = int(bm.get("colorStyle", 0) or 0)
        row.type = int(bm.get("type", 0) or 0)
        row.range = bm.get("range", "")
        row.create_time = int(bm.get("createTime", 0) or 0)
        session.add(row)
        n += 1
    return n


def upsert_reviews(session: Session, reviews: list[dict[str, Any]]) -> int:
    """Upsert personal thoughts. Each item is the nested `review` object."""
    n = 0
    for rv in reviews or []:
        rid = rv.get("reviewId")
        if not rid:
            continue
        row = session.get(Review, rid) or Review(review_id=rid)
        row.book_id = rv.get("bookId", row.book_id)
        row.chapter_uid = int(rv.get("chapterUid", 0) or 0)
        row.chapter_idx = int(rv.get("chapterIdx", 0) or 0)
        row.chapter_title = rv.get("chapterTitle") or rv.get("chapterName") or ""
        row.content = rv.get("content", "")
        row.abstract = rv.get("abstract", "")
        row.range = rv.get("range", "")
        row.type = int(rv.get("type", 0) or 0)
        row.is_private = int(rv.get("isPrivate", 0) or 0)
        row.create_time = int(rv.get("createTime", 0) or 0)
        session.add(row)
        n += 1
    return n


def book_ids_needing_info(session: Session) -> list[str]:
    """book_ids whose /book/info metadata hasn't been fetched yet (write-once)."""
    return list(session.exec(select(Book.book_id).where(Book.info_fetched == 0)).all())


def _latest_shelf_update_times(session: Session) -> dict[str, int]:
    """Per-book `updateTime` from the most recent shelf snapshot.

    The shelf's updateTime equals a book's chapterUpdateTime, so it tells us — for
    free, no extra call — when a book's chapters changed since our last TOC fetch.
    """
    latest = session.exec(
        select(ShelfItem.pull_date).order_by(ShelfItem.pull_date.desc())
    ).first()
    if not latest:
        return {}
    items = session.exec(select(ShelfItem).where(ShelfItem.pull_date == latest)).all()
    return {it.book_id: it.update_time for it in items}


def book_chapter_fetch_targets(session: Session) -> list[str]:
    """book_ids whose table of contents should be (re)fetched.

    Fetched when never fetched (``chapters_update_time == 0``) or when the shelf
    reports a newer ``updateTime`` than our last fetch — so completed books are
    fetched once and serialized books refresh exactly when new chapters land.
    """
    shelf_upd = _latest_shelf_update_times(session)
    targets: list[str] = []
    for book in session.exec(select(Book)).all():
        if book.chapters_update_time == 0:
            targets.append(book.book_id)
        else:
            upd = shelf_upd.get(book.book_id)
            if upd is not None and upd > book.chapters_update_time:
                targets.append(book.book_id)
    return targets


def replace_chapters(session: Session, book_id: str, chapters: list[dict[str, Any]]) -> int:
    """Replace a book's stored table of contents with a fresh chapter list."""
    session.exec(delete(Chapter).where(Chapter.book_id == book_id))
    n = 0
    for ch in chapters or []:
        uid = ch.get("chapterUid")
        if uid is None:
            continue
        session.add(
            Chapter(
                book_id=book_id,
                chapter_uid=int(uid),
                chapter_idx=int(ch.get("chapterIdx", 0) or 0),
                title=ch.get("title", "") or "",
                level=max(int(ch.get("level", 1) or 1), 1),
                word_count=int(ch.get("wordCount", 0) or 0),
                update_time=int(ch.get("updateTime", 0) or 0),
            )
        )
        n += 1
    return n


def save_recommendations(session: Session, pull_date: str, books: list[dict[str, Any]]) -> int:
    session.exec(delete(Recommendation).where(Recommendation.pull_date == pull_date))
    for b in books or []:
        bid = b.get("bookId")
        if not bid:
            continue
        session.add(
            Recommendation(
                pull_date=pull_date,
                book_id=bid,
                title=b.get("title", ""),
                author=b.get("author", ""),
                cover=b.get("cover", ""),
                category=b.get("category", ""),
                intro=b.get("intro", ""),
            )
        )
    return len(books or [])


def start_pull(session: Session, kind: str) -> PullRun:
    run = PullRun(kind=kind)
    session.add(run)
    session.flush()  # assign id
    return run


def finish_pull(session: Session, run: PullRun, ok: bool, counts: dict[str, int], error: str = "") -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.ok = ok
    run.error = error[:1000]
    run.counts_json = json.dumps(counts, ensure_ascii=False)
    session.add(run)


# --------------------------------------------------------------------------
# Read side (used by the web layer)
# --------------------------------------------------------------------------


def daily_trend(session: Session, days: int = 120) -> list[dict[str, Any]]:
    # Most recent `days` days, returned oldest-first for charting.
    rows = session.exec(
        select(DailyReadTime).order_by(DailyReadTime.date.desc()).limit(days)
    ).all()
    return [
        {"date": r.date, "seconds": r.seconds, "minutes": round(r.seconds / 60, 1)}
        for r in reversed(rows)
    ]


def monthly_trend(session: Session) -> list[dict[str, Any]]:
    rows = session.exec(select(DailyReadTime)).all()
    buckets: dict[str, int] = defaultdict(int)
    for r in rows:
        buckets[month_label(r.date)] += r.seconds
    return [
        {"month": m, "seconds": s, "hours": round(s / 3600, 1)}
        for m, s in sorted(buckets.items())
    ]


def latest_snapshot(session: Session, mode: str) -> StatSnapshot | None:
    return session.exec(
        select(StatSnapshot)
        .where(StatSnapshot.mode == mode)
        .order_by(StatSnapshot.pull_date.desc())
    ).first()


def _current_streak(rows: list[DailyReadTime]) -> int:
    """Consecutive days (ending at the most recent active day) with reading."""
    active = {r.date for r in rows if r.seconds > 0}
    if not active:
        return 0
    cur = max(parse_date(d) for d in active)
    streak = 0
    while cur.strftime("%Y-%m-%d") in active:
        streak += 1
        cur = cur - timedelta(days=1)
    return streak


def overview(session: Session) -> dict[str, Any]:
    overall = latest_snapshot(session, "overall")
    monthly = latest_snapshot(session, "monthly")
    rows = session.exec(select(DailyReadTime)).all()
    total_books = session.exec(select(Book)).all()
    last = last_pull(session)

    read_stat = []
    if overall:
        payload = json.loads(overall.payload_json)
        read_stat = payload.get("readStat", [])

    return {
        "total_read_time": overall.total_read_time if overall else 0,
        "total_read_time_h": fmt_duration(overall.total_read_time if overall else 0),
        "read_days": overall.read_days if overall else 0,
        "read_rate": overall.read_rate if overall else 0,
        "month_read_time": monthly.total_read_time if monthly else 0,
        "month_read_time_h": fmt_duration(monthly.total_read_time if monthly else 0),
        "month_read_days": monthly.read_days if monthly else 0,
        "streak": _current_streak(rows),
        "book_count": len(total_books),
        "read_stat": read_stat,
        "last_pull": last,
    }


def current_shelf(session: Session) -> dict[str, Any]:
    latest = session.exec(
        select(ShelfItem.pull_date).order_by(ShelfItem.pull_date.desc())
    ).first()
    if not latest:
        return {"pull_date": None, "groups": []}
    items = session.exec(select(ShelfItem).where(ShelfItem.pull_date == latest)).all()
    books = {b.book_id: b for b in session.exec(select(Book)).all()}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        b = books.get(it.book_id)
        groups[it.archive_name or "未分组"].append(
            {
                "book_id": it.book_id,
                "title": b.title if b else it.book_id,
                "author": b.author if b else "",
                "cover": b.cover if b else "",
                "progress": b.reading_progress if b else 0,
                # The shelf snapshot's finish_reading is the authoritative per-user
                # "已读完" flag (finishReading); don't fall back to Book.finished.
                "finished": it.finish_reading,
            }
        )
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return {
        "pull_date": latest,
        "total": len(items),
        "groups": [{"name": name, "books": bs} for name, bs in ordered],
    }


def books_with_notes(session: Session) -> list[dict[str, Any]]:
    books = session.exec(select(Book)).all()
    out = []
    for b in books:
        total = b.note_count + b.bookmark_count + b.review_count
        if total <= 0:
            continue
        out.append(
            {
                "book_id": b.book_id,
                "title": b.title,
                "author": b.author,
                "cover": b.cover,
                "progress": b.reading_progress,
                "note_count": b.note_count,
                "bookmark_count": b.bookmark_count,
                "review_count": b.review_count,
                "total": total,
            }
        )
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def book_notes(session: Session, book_id: str) -> dict[str, Any]:
    book = session.get(Book, book_id)
    bookmarks = session.exec(
        select(Bookmark).where(Bookmark.book_id == book_id)
    ).all()
    reviews = session.exec(select(Review).where(Review.book_id == book_id)).all()
    bookmarks.sort(key=lambda b: (b.chapter_idx, b.range))
    reviews.sort(key=lambda r: (r.chapter_idx, r.create_time))

    chapters: dict[int, dict[str, Any]] = {}
    for bm in bookmarks:
        ch = chapters.setdefault(
            bm.chapter_uid,
            {"title": bm.chapter_title, "idx": bm.chapter_idx, "bookmarks": [], "reviews": []},
        )
        ch["bookmarks"].append(bm)
    for rv in reviews:
        ch = chapters.setdefault(
            rv.chapter_uid,
            {"title": rv.chapter_title, "idx": rv.chapter_idx, "bookmarks": [], "reviews": []},
        )
        ch["reviews"].append(rv)
    ordered = sorted(chapters.values(), key=lambda c: c["idx"])
    return {"book": book, "chapters": ordered, "bookmark_count": len(bookmarks), "review_count": len(reviews)}


def book_chapters(session: Session, book_id: str) -> list[Chapter]:
    """A book's full table of contents, in reading order (empty if not fetched)."""
    rows = session.exec(select(Chapter).where(Chapter.book_id == book_id)).all()
    return sorted(rows, key=lambda c: c.chapter_idx)


def current_recommendations(session: Session) -> list[Recommendation]:
    latest = session.exec(
        select(Recommendation.pull_date).order_by(Recommendation.pull_date.desc())
    ).first()
    if not latest:
        return []
    return session.exec(
        select(Recommendation).where(Recommendation.pull_date == latest)
    ).all()


def last_pull(session: Session) -> PullRun | None:
    return session.exec(
        select(PullRun).where(PullRun.ok == True).order_by(PullRun.finished_at.desc())  # noqa: E712
    ).first()


def latest_pull(session: Session) -> PullRun | None:
    """Most recent run regardless of outcome — used to report refresh progress."""
    return session.exec(select(PullRun).order_by(PullRun.id.desc())).first()


# --------------------------------------------------------------------------
# Accounts (admin + normal users). Hashing lives in app.auth; we only store.
# --------------------------------------------------------------------------


def get_user(session: Session, username: str) -> AppUser | None:
    return session.get(AppUser, username)


def list_users(session: Session) -> list[AppUser]:
    return session.exec(select(AppUser).order_by(AppUser.username)).all()


def has_admin(session: Session) -> bool:
    return session.exec(
        select(AppUser).where(AppUser.is_admin == True)  # noqa: E712
    ).first() is not None


def create_user(
    session: Session, username: str, password_hash: str, is_admin: bool = False, must_change: bool = False
) -> AppUser:
    user = AppUser(
        username=username, password_hash=password_hash, is_admin=is_admin, must_change=must_change
    )
    session.add(user)
    return user


def set_password(
    session: Session, username: str, password_hash: str, must_change: bool = False
) -> AppUser | None:
    user = session.get(AppUser, username)
    if user is None:
        return None
    user.password_hash = password_hash
    user.must_change = must_change
    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    return user


def delete_user(session: Session, username: str) -> None:
    user = session.get(AppUser, username)
    if user is not None:
        session.delete(user)


def rename_user(session: Session, old: str, new: str) -> AppUser | None:
    """Move an account to a new username (the primary key), keeping its fields."""
    user = session.get(AppUser, old)
    if user is None:
        return None
    fresh = AppUser(
        username=new,
        password_hash=user.password_hash,
        is_admin=user.is_admin,
        must_change=user.must_change,
        created_at=user.created_at,
    )
    session.delete(user)
    session.flush()
    session.add(fresh)
    return fresh
