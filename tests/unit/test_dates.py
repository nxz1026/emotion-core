"""utils/dates.py 交易日历测试。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.utils import dates


class TestTodaySh:
    def test_returns_date(self):
        result = dates.today_sh()
        assert isinstance(result, date)

    def test_is_recent(self):
        result = dates.today_sh()
        now_utc = datetime.now(timezone.utc)
        # Should be UTC+8
        expected = (now_utc + timedelta(hours=8)).date()
        assert result == expected


class TestTradingDays:
    def test_returns_sorted_list(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (date(2024, 6, 14),),
            (date(2024, 6, 15),),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.trading_days(date(2024, 6, 1), date(2024, 6, 30), conn=mock_conn)
        assert result == [date(2024, 6, 14), date(2024, 6, 15)]

    def test_empty(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.trading_days(date(2024, 6, 1), date(2024, 6, 30), conn=mock_conn)
        assert result == []


class TestPrevTradingDay:
    def test_returns_previous(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (date(2024, 6, 14),)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.prev_trading_day(date(2024, 6, 15), conn=mock_conn)
        assert result == date(2024, 6, 14)

    def test_none_when_no_previous(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (None,)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.prev_trading_day(date(2024, 1, 1), conn=mock_conn)
        assert result is None


class TestNextTradingDay:
    def test_returns_next(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (date(2024, 6, 16),)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.next_trading_day(date(2024, 6, 15), conn=mock_conn)
        assert result == date(2024, 6, 16)

    def test_none_when_no_next(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (None,)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.next_trading_day(date(2025, 12, 31), conn=mock_conn)
        assert result is None


class TestRecentTradingDays:
    def test_returns_sorted(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (date(2024, 6, 15),),
            (date(2024, 6, 13),),
            (date(2024, 6, 14),),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = dates.recent_trading_days(3, conn=mock_conn)
        assert result == [date(2024, 6, 13), date(2024, 6, 14), date(2024, 6, 15)]
