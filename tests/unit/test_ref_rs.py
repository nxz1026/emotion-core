"""ref_rs 相对强弱排名服务测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services import ref_rs


class TestPeriodReturn:
    def test_basic(self):
        prices = [
            (date(2024, 1, 1), 10.0),
            (date(2024, 1, 2), 11.0),
            (date(2024, 1, 3), 12.0),
        ]
        result = ref_rs._period_return(prices, 3)
        assert result == pytest.approx(0.2)  # (12-10)/10

    def test_insufficient_data(self):
        prices = [(date(2024, 1, 1), 10.0)]
        assert ref_rs._period_return(prices, 2) is None

    def test_zero_start(self):
        prices = [
            (date(2024, 1, 1), 0.0),
            (date(2024, 1, 2), 10.0),
        ]
        assert ref_rs._period_return(prices, 2) is None

    def test_partial_period(self):
        """数据不足 period 天时用全部可用数据。"""
        prices = [
            (date(2024, 1, 1), 10.0),
            (date(2024, 1, 2), 11.0),
        ]
        result = ref_rs._period_return(prices, 5)
        assert result == pytest.approx(0.1)


class TestGetPrices:
    def test_empty(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur

        monkeypatch.setattr(ref_rs, "db_connect", lambda: mock_conn)
        result = ref_rs._get_prices(mock_conn, "600519", date(2024, 1, 1), date(2024, 1, 31))
        assert result == []

    def test_with_null_close(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            (date(2024, 1, 1), 100.0),
            (date(2024, 1, 2), None),
            (date(2024, 1, 3), 110.0),
        ]
        mock_conn.cursor.return_value = mock_cur

        monkeypatch.setattr(ref_rs, "db_connect", lambda: mock_conn)
        result = ref_rs._get_prices(mock_conn, "600519", date(2024, 1, 1), date(2024, 1, 31))
        assert len(result) == 2
        assert result[0] == (date(2024, 1, 1), 100.0)
        assert result[1] == (date(2024, 1, 3), 110.0)


class TestCalculateRs:
    def test_empty(self, monkeypatch):
        mock_conn = MagicMock()
        monkeypatch.setattr(ref_rs, "db_connect", lambda: mock_conn)

        # mock cursor for codes query
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur

        result = ref_rs.calculate_rs(as_of_date=date(2024, 6, 15))
        assert result == 0


class TestGetTopRs:
    def test_returns_list(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            ("600519", 1.5, 1),
            ("000001", 1.2, 2),
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(ref_rs, "db_connect", lambda: mock_conn)
        result = ref_rs.get_top_rs(as_of_date=date(2024, 6, 15), limit=2)
        assert len(result) == 2
        assert result[0]["code"] == "600519"
