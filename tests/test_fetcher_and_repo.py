"""End-to-end fetcher test with a fake client, plus repository aggregations."""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime

import pytest
from sqlmodel import select

from app import repository as repo
from app.db import session_scope
from app.fetcher import run_daily_pull
from app.models import (
    Book,
    Bookmark,
    PeriodStat,
    PullRun,
    Recommendation,
    Review,
    ShelfItem,
)

from . import fixtures as fx


class FakeClient:
    """Stand-in for WeReadClient returning fixture payloads."""

    def __init__(self):
        self.calls = []
        # Registration ~5 weeks ago keeps the first-pull history backfill small.
        self.reg_time = int(time.time()) - 5 * 7 * 86400

    def read_data_detail(self, mode=None, base_time=None):
        self.calls.append((mode, base_time))
        if mode == "overall":
            data = dict(fx.OVERALL)
            data["registTime"] = self.reg_time
            return data
        return {"weekly": fx.WEEKLY, "monthly": fx.MONTHLY, "annually": fx.ANNUALLY}[mode]

    def shelf_sync(self):
        return fx.SHELF

    def notebooks(self, count=50, last_sort=None):
        return fx.NOTEBOOKS

    def bookmark_list(self, book_id):
        return fx.BOOKMARKS_B2 if book_id == "b2" else {"updated": [], "removed": [], "chapters": []}

    def my_reviews(self, book_id, count=50, synckey=None):
        return fx.REVIEWS_B2 if book_id == "b2" else {"reviews": []}

    def recommend(self, count=12, max_idx=None):
        return fx.RECOMMEND

    def book_info(self, book_id):
        return {"bookId": book_id, "publisher": "某出版社",
                "isbn": f"ISBN-{book_id}", "intro": "公开简介"}

    def chapter_info(self, book_id):
        # Each book reports a chapterUpdateTime matching its shelf updateTime, so
        # an unchanged rerun never re-fetches.
        return {
            "b1": {"bookId": "b1", "chapterUpdateTime": 100,
                   "chapters": [{"chapterUid": 1, "chapterIdx": 1, "title": "楔子",
                                 "level": 1, "wordCount": 500}]},
            "b2": fx.CHAPTERS_B2,
            "b3": {"bookId": "b3", "chapterUpdateTime": 50, "chapters": []},
        }.get(book_id, {"chapters": []})

    def close(self):
        pass


def test_run_daily_pull_persists_everything():
    counts = run_daily_pull(client=FakeClient())

    assert counts["shelf"] == 3
    assert counts["books"] == 2
    assert counts["bookmarks"] == 2
    assert counts["reviews"] == 1
    assert counts["recs"] == 1
    assert counts["stats"] > 4  # overall + current/prev week/month/year + history backfill
    assert counts["books_info"] == 4  # b1, b2, b3, r1 each enriched once
    assert counts["chapters"] == 4  # b1, b2, b3, r1 each get a TOC fetch
    assert counts["pruned"] == 0  # retention_days defaults to 0 (off)

    with session_scope() as s:
        # The cumulative overall row is stored and display-normalized.
        overall = repo.get_period_stat(s, "overall", 0)
        assert overall["total_read_time"] == 5844393
        assert any(st["stat"] == "读过" for st in overall["read_stat"])
        # overall distribution drops the leading zero years (2018/2019), keeps 2020.
        assert overall["distribution"] == [{"label": "2020", "seconds": 677539}]
        # The first pull backfilled history and set the flag.
        from app import settings_store
        assert settings_store.get().stats_backfilled is True

        shelf = repo.current_shelf(s)
        names = {g["name"] for g in shelf["groups"]}
        assert {"文学", "历史", "未分组"} <= names

        # "Finished" is the user's read-finished flag (shelf finishReading), NOT the
        # notebook's book.finished (= 已完结, book done being serialized). b2 has
        # finishReading=0 but book.finished=1, so it must stay unfinished; b1 has
        # finishReading=1 and stays finished.
        assert s.get(Book, "b2").finished == 0
        assert s.get(Book, "b1").finished == 1

        notes = repo.books_with_notes(s)
        assert notes[0]["book_id"] == "b2"  # only book with notes
        detail = repo.book_notes(s, "b2")
        assert detail["bookmark_count"] == 2
        assert detail["review_count"] == 1
        # chapters ordered by idx; chapter 6 (第二章) before chapter 8 (第四章)
        assert detail["chapters"][0]["title"] == "第二章"
        # /book/info enrichment ran during the pull (write-once).
        assert detail["book"].info_fetched == 1
        assert detail["book"].isbn == "ISBN-b2"
        assert detail["book"].publisher == "某出版社"

        # Full table of contents stored, ordered by chapterIdx, level preserved.
        toc = repo.book_chapters(s, "b2")
        assert [c.title for c in toc] == ["第一章", "第二章", "第二章·小节", "第四章"]
        assert toc[2].level == 2 and toc[0].level == 1
        assert s.get(Book, "b2").chapters_update_time == 200

        recs = repo.current_recommendations(s)
        assert recs[0].title == "枪炮、病菌与钢铁"

        last = repo.last_pull(s)
        assert last is not None and last.ok


def test_pull_run_persists_full_counts():
    """The stored pull_run counts include the post-main-pull phases (enrichment),
    not just what the main transaction had tallied when it committed."""
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        run = repo.latest_pull(s)
        stored = json.loads(run.counts_json)
    assert run.ok
    assert stored["books_info"] == 4  # enriched after the main txn — must be recorded
    assert stored["chapters"] == 4
    assert stored["bookmarks"] == 2


def test_failed_pull_is_recorded_not_rolled_back():
    """A pull that errors still leaves a pull_run row marked failed; the run record
    is committed separately from the rolled-back data transaction."""
    class Boom(FakeClient):
        def shelf_sync(self):
            raise RuntimeError("shelf boom")

    with pytest.raises(RuntimeError):
        run_daily_pull(client=Boom())
    with session_scope() as s:
        run = repo.latest_pull(s)
        assert run is not None and run.ok is False
        assert "shelf boom" in run.error
        assert repo.last_pull(s) is None  # no successful pull on record


def test_second_pull_skips_history_backfill():
    """Once stats_backfilled is set, a later pull only refreshes the volatile
    periods (overall + current/previous), not the whole history again."""
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        first = len(s.exec(select(PeriodStat)).all())

    c = FakeClient()
    run_daily_pull(client=c)
    with session_scope() as s:
        assert len(s.exec(select(PeriodStat)).all()) == first  # no new history rows
    # overall + (weekly/monthly/annually × current+previous) = 7 refreshes, no backfill.
    assert len(c.calls) == 7


def test_bookmark_removed_is_deleted():
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        assert s.get(Bookmark, "b2_26_1") is not None

    # Second pull where one bookmark is now removed.
    class C(FakeClient):
        def bookmark_list(self, book_id):
            if book_id == "b2":
                return {"updated": [], "removed": ["b2_26_1"],
                        "chapters": fx.BOOKMARKS_B2["chapters"]}
            return {"updated": [], "removed": [], "chapters": []}

    run_daily_pull(client=C())
    with session_scope() as s:
        assert s.get(Bookmark, "b2_26_1") is None
        assert s.get(Bookmark, "b2_28_2") is not None


def test_idempotent_rerun_does_not_duplicate():
    run_daily_pull(client=FakeClient())
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        shelf = repo.current_shelf(s)
        assert shelf["total"] == 3  # not 6
        detail = repo.book_notes(s, "b2")
        assert detail["bookmark_count"] == 2  # not 4
        # TOC isn't re-fetched on an unchanged rerun (shelf updateTime unchanged).
        assert len(repo.book_chapters(s, "b2")) == 4


def test_chapters_refetched_when_shelf_reports_update():
    """A serialized book whose shelf updateTime grows gets its TOC re-pulled."""
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        assert len(repo.book_chapters(s, "b2")) == 4
        assert s.get(Book, "b2").chapters_update_time == 200

    class C(FakeClient):
        def shelf_sync(self):
            shelf = copy.deepcopy(fx.SHELF)
            for b in shelf["books"]:
                if b["bookId"] == "b2":
                    b["updateTime"] = 300  # new chapter landed
            return shelf

        def chapter_info(self, book_id):
            if book_id == "b2":
                return {"bookId": "b2", "chapterUpdateTime": 300,
                        "chapters": fx.CHAPTERS_B2["chapters"] + [
                            {"chapterUid": 30, "chapterIdx": 9, "title": "第五章",
                             "level": 1, "wordCount": 900}]}
            return super().chapter_info(book_id)

    run_daily_pull(client=C())
    with session_scope() as s:
        toc = repo.book_chapters(s, "b2")
        assert len(toc) == 5
        assert toc[-1].title == "第五章"
        assert s.get(Book, "b2").chapters_update_time == 300


def test_book_review_split_from_thoughts():
    """A chapter-less review (chapter_uid==0) is a 书评, kept out of the chapter
    grouping and counted separately on both the detail and list pages."""
    run_daily_pull(client=FakeClient())  # b2 gets one chapter thought (rv1, chapter 28)
    with session_scope() as s:
        s.add(Review(review_id="rv_book", book_id="b2", chapter_uid=0,
                     content="整本书的评价", type=4, create_time=1781616300))
        s.commit()

        detail = repo.book_notes(s, "b2")
        assert detail["review_count"] == 1  # 想法: only the chapter-bound rv1
        assert detail["book_review_count"] == 1  # 书评: the chapter-less one
        assert [rv.review_id for rv in detail["book_reviews"]] == ["rv_book"]
        # The book review must not create an empty "未命名章节" chapter group.
        assert all(ch["title"] for ch in detail["chapters"])

        row = next(b for b in repo.books_with_notes(s) if b["book_id"] == "b2")
        assert row["review_count"] == 1 and row["book_review_count"] == 1


def _seed_snapshots(s, dates):
    """Add one shelf + recommendation snapshot per pull_date."""
    for d in dates:
        s.add(ShelfItem(pull_date=d, book_id="b1"))
        s.add(Recommendation(pull_date=d, book_id="r1", title="t"))


def test_prune_snapshots_deletes_old_keeps_latest_and_exempt():
    with session_scope() as s:
        _seed_snapshots(s, ("2026-01-01", "2026-03-01", "2026-06-01"))
        # Exempt data that must never be pruned.
        s.add(PeriodStat(mode="overall", base_time=0, payload_json="{}"))
        s.add(Bookmark(bookmark_id="bm1", book_id="b1", create_time=0))
        s.add(Review(review_id="rv1", book_id="b1", create_time=0))
        # pull_run log: an old run plus a recent one (finish_pull always sets
        # finished_at, which last_pull orders by).
        s.add(PullRun(id=1, kind="daily", ok=True,
                      started_at=datetime(2026, 1, 1), finished_at=datetime(2026, 1, 1)))
        s.add(PullRun(id=2, kind="daily", ok=True,
                      started_at=datetime(2026, 6, 1), finished_at=datetime(2026, 6, 1)))
        s.commit()

        removed = repo.prune_snapshots(s, "2026-05-01", datetime(2026, 5, 1))
        s.commit()
        assert removed > 0

        # Snapshots before the cutoff are gone; the newest pull_date survives.
        assert {x.pull_date for x in s.exec(select(ShelfItem)).all()} == {"2026-06-01"}
        assert {x.pull_date for x in s.exec(select(Recommendation)).all()} == {"2026-06-01"}
        # pull_run: old one pruned, recent kept.
        assert {r.id for r in s.exec(select(PullRun)).all()} == {2}
        # Exempt tables untouched.
        assert s.get(PeriodStat, ("overall", 0)) is not None
        assert s.get(Bookmark, "bm1") is not None
        assert s.get(Review, "rv1") is not None


def test_prune_snapshots_keeps_latest_even_when_all_stale():
    """If no recent pull exists, the single newest snapshot is still preserved."""
    with session_scope() as s:
        _seed_snapshots(s, ("2026-01-01", "2026-02-01"))
        s.commit()
        # Cutoff far in the future: every row is "expired".
        repo.prune_snapshots(s, "2030-01-01", datetime(2030, 1, 1))
        s.commit()
        assert {x.pull_date for x in s.exec(select(ShelfItem)).all()} == {"2026-02-01"}
        assert {x.pull_date for x in s.exec(select(Recommendation)).all()} == {"2026-02-01"}
