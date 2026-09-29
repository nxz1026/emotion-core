"""utils/dates.py 单元测试：today_sh 纯函数。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from emotion_core.utils.dates import today_sh


class TestTodaySh:
    """today_sh 返回 Asia/Shanghai 当天日期。"""

    def test_returns_date(self):
        result = today_sh()
        assert isinstance(result, date)

    def test_matches_shanghai_today(self):
        """与手动计算的 Asia/Shanghai 日期一致。"""
        expected = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        assert today_sh() == expected

    def test_not_utc_today(self):
        """UTC 日期可能与上海日期不同（跨午夜时）。"""
        result = today_sh()
        utc_today = datetime.now(timezone.utc).date()
        # 上海 = UTC+8，所以上海日期 >= UTC 日期
        assert result >= utc_today
        # 差距最多 1 天
        assert (result - utc_today).days <= 1
