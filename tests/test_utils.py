from app.utils import fmt_duration, ts_to_date


def test_fmt_duration():
    assert fmt_duration(0) == "0秒"
    assert fmt_duration(30) == "30秒"
    assert fmt_duration(60) == "1分钟"
    assert fmt_duration(3600) == "1小时"
    assert fmt_duration(3660) == "1小时1分"
    assert fmt_duration(5844393) == "1623小时26分"


def test_ts_to_date_china_tz():
    # 1780243200 is 2026-06-01 00:00 in UTC+8.
    assert ts_to_date(1780243200) == "2026-06-01"
