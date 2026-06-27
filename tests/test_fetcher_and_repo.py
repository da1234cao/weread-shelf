"""End-to-end fetcher test with a fake client, plus repository aggregations."""

from __future__ import annotations

from app import repository as repo
from app.db import session_scope
from app.fetcher import run_daily_pull
from app.models import Bookmark

from . import fixtures as fx


class FakeClient:
    """Stand-in for WeReadClient returning fixture payloads."""

    def __init__(self):
        self.calls = []

    def read_data_detail(self, mode=None, base_time=None):
        self.calls.append(("readdata", mode))
        return {"overall": fx.OVERALL, "monthly": fx.MONTHLY, "weekly": fx.WEEKLY}[mode]

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

    def close(self):
        pass


def test_run_daily_pull_persists_everything():
    counts = run_daily_pull(client=FakeClient())

    assert counts["shelf"] == 3
    assert counts["books"] == 2
    assert counts["bookmarks"] == 2
    assert counts["reviews"] == 1
    assert counts["recs"] == 1
    assert counts["days"] >= 3  # monthly(3 non-zero) + weekly(1)

    with session_scope() as s:
        # Daily series: zero-second day is dropped.
        trend = repo.daily_trend(s)
        days = {d["date"]: d["seconds"] for d in trend}
        assert days["2026-06-01"] == 3600
        assert "2026-06-03" not in days  # 0 seconds skipped

        ov = repo.overview(s)
        assert ov["read_days"] == 845
        assert ov["total_read_time"] == 5844393
        assert ov["book_count"] >= 3
        assert any(st["stat"] == "读过" for st in ov["read_stat"])

        shelf = repo.current_shelf(s)
        names = {g["name"] for g in shelf["groups"]}
        assert {"文学", "历史", "未分组"} <= names

        notes = repo.books_with_notes(s)
        assert notes[0]["book_id"] == "b2"  # only book with notes
        detail = repo.book_notes(s, "b2")
        assert detail["bookmark_count"] == 2
        assert detail["review_count"] == 1
        # chapters ordered by idx; chapter 6 (第二章) before chapter 8 (第四章)
        assert detail["chapters"][0]["title"] == "第二章"

        recs = repo.current_recommendations(s)
        assert recs[0].title == "枪炮、病菌与钢铁"

        last = repo.last_pull(s)
        assert last is not None and last.ok


def test_monthly_trend_aggregates_by_month():
    run_daily_pull(client=FakeClient())
    with session_scope() as s:
        monthly = repo.monthly_trend(s)
    by_month = {m["month"]: m["seconds"] for m in monthly}
    # Both monthly (3600+1800+7200) and the weekly fixture day (2432) fall in
    # 2026-06 and are merged into the per-day series: 12600 + 2432 = 15032.
    assert by_month["2026-06"] == 15032


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
