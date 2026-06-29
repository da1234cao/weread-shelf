"""Synthetic gateway responses matching the real shapes (no personal data)."""

# /readdata/detail — readTimes granularity differs by mode: weekly/monthly are
# per-day, annually is per-month, overall is per-year. readStat (读过/读完/阅读/
# 笔记) is absent for weekly. registTime bounds the first-pull backfill.

# monthly: per-day buckets
MONTHLY = {
    "readTimes": {
        "1780243200": 3600,   # 2026-06-01
        "1780329600": 1800,   # 2026-06-02
        "1780416000": 0,      # 2026-06-03 (zero day)
        "1780502400": 7200,   # 2026-06-04
    },
    "readDays": 3,
    "dayAverageReadTime": 4200,
    "totalReadTime": 12600,
    "readStat": [
        {"stat": "读过", "counts": "2本"}, {"stat": "读完", "counts": "0本"},
        {"stat": "阅读", "counts": "3天"}, {"stat": "笔记", "counts": "5条"},
    ],
    "preferCategory": [{"categoryTitle": "历史", "readingTime": 20521, "readingCount": 1}],
}

# annually: per-month buckets (12 in production; 2 here is enough)
ANNUALLY = {
    "readTimes": {"1767196800": 100000, "1769875200": 80000},
    "readDays": 50,
    "dayAverageReadTime": 3600,
    "totalReadTime": 180000,
    "readStat": [
        {"stat": "读过", "counts": "15本"}, {"stat": "读完", "counts": "3本"},
        {"stat": "阅读", "counts": "50天"}, {"stat": "笔记", "counts": "102条"},
    ],
    "preferCategory": [{"categoryTitle": "玄幻小说", "readingTime": 90000, "readingCount": 5}],
}

# overall: per-year buckets, with the leading zero years that normalize() trims.
OVERALL = {
    "readTimes": {"1514764800": 0, "1546300800": 0, "1577836800": 677539},  # 2018/2019/2020
    "readDays": 845,
    "totalReadTime": 5844393,
    "readRate": 79,
    "wrReadTime": 4648188,
    "wrListenTime": 1196205,
    "readStat": [
        {"stat": "读过", "counts": "118本"}, {"stat": "读完", "counts": "44本"},
        {"stat": "阅读", "counts": "845天"}, {"stat": "笔记", "counts": "2964条"},
    ],
    "preferCategory": [
        {"categoryTitle": "影视原著", "readingTime": 1110145, "readingCount": 18},
        {"categoryTitle": "历史", "readingTime": 900000, "readingCount": 9},
    ],
    "preferAuthor": [{"name": "江南", "count": 2, "readTime": "58小时18分钟"}],
    "preferTime": [96167] + [1000] * 23,
    "registTime": 1577836800,
}

# weekly: per-day buckets, and no readStat.
WEEKLY = {"readTimes": {"1782489600": 2432}, "readDays": 1, "totalReadTime": 2432,
          "dayAverageReadTime": 2432}

SHELF = {
    "books": [
        {"bookId": "b1", "title": "诛仙", "author": "萧鼎", "cover": "http://c/1.jpg",
         "finishReading": 1, "secret": 1, "updateTime": 100},
        {"bookId": "b2", "title": "透过地理看历史", "author": "李不白", "cover": "http://c/2.jpg",
         "finishReading": 0, "updateTime": 200},
        {"bookId": "b3", "title": "未分组的书", "author": "佚名", "cover": "", "updateTime": 50},
    ],
    "archive": [
        {"name": "文学", "bookIds": ["b1"], "albumIds": []},
        {"name": "历史", "bookIds": ["b2"], "albumIds": []},
    ],
}

NOTEBOOKS = {
    "totalBookCount": 2,
    "totalNoteCount": 11,
    "hasMore": 0,
    "books": [
        {
            "bookId": "b2", "readingProgress": 38, "noteCount": 2, "bookmarkCount": 0,
            "reviewCount": 1, "sort": 200,
            "book": {"bookId": "b2", "title": "透过地理看历史", "author": "李不白",
                     "cover": "http://c/2.jpg", "categories": [{"title": "历史-历史地理"}],
                     "publishTime": "2020-11-01", "finished": 1, "intro": "简介"},
        },
        {
            "bookId": "b1", "readingProgress": 100, "noteCount": 0, "bookmarkCount": 0,
            "reviewCount": 0, "sort": 100,
            "book": {"bookId": "b1", "title": "诛仙", "author": "萧鼎", "cover": "http://c/1.jpg"},
        },
    ],
}

BOOKMARKS_B2 = {
    "updated": [
        {"bookId": "b2", "bookmarkId": "b2_26_1", "chapterUid": 26, "chapterIdx": 6,
         "markText": "划线一", "colorStyle": 3, "type": 1, "range": "1-9", "createTime": 1781011906},
        {"bookId": "b2", "bookmarkId": "b2_28_2", "chapterUid": 28, "chapterIdx": 8,
         "markText": "划线二", "colorStyle": 1, "type": 1, "range": "20-40", "createTime": 1781011999},
    ],
    "removed": [],
    "chapters": [
        {"chapterUid": 26, "chapterIdx": 6, "title": "第二章"},
        {"chapterUid": 28, "chapterIdx": 8, "title": "第四章"},
    ],
    "book": {"bookId": "b2"},
}

REVIEWS_B2 = {
    "totalCount": 1,
    "reviews": [
        {"reviewId": "rv1", "review": {
            "reviewId": "rv1", "bookId": "b2", "chapterUid": 28, "chapterIdx": 8,
            "chapterName": "第四章", "content": "我的想法", "abstract": "被划的原文",
            "range": "5351-5395", "type": 1, "isPrivate": 0, "createTime": 1781616198}},
    ],
}

# /book/chapterinfo — full table of contents. chapterUpdateTime mirrors the
# shelf's per-book updateTime (200 for b2). level>1 marks a nested sub-section.
CHAPTERS_B2 = {
    "bookId": "b2",
    "synckey": 99,
    "chapterUpdateTime": 200,
    "chapters": [
        {"chapterUid": 24, "chapterIdx": 4, "title": "第一章", "level": 1, "wordCount": 1200},
        {"chapterUid": 26, "chapterIdx": 6, "title": "第二章", "level": 1, "wordCount": 3400},
        {"chapterUid": 27, "chapterIdx": 7, "title": "第二章·小节", "level": 2, "wordCount": 800},
        {"chapterUid": 28, "chapterIdx": 8, "title": "第四章", "level": 1, "wordCount": 2100},
    ],
}

RECOMMEND = {
    "books": [
        {"bookId": "r1", "title": "枪炮、病菌与钢铁", "author": "贾雷德·戴蒙德",
         "cover": "http://c/r1.jpg", "category": "社会文化-社科", "intro": "..."},
    ],
}
