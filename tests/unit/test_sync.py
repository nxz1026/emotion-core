"""data/sync.py 日更数据同步测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.data import sync


class TestUpsertDailyBars:
    def test_empty_returns_zero(self):
        assert sync.upsert_daily_bars([]) == 0

    def test_writes_rows(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        with patch("emotion_core.data.sync.transaction", return_value=MagicMock(
            __enter__=MagicMock(return_value=mock_conn),
            __exit__=MagicMock(return_value=False),
        )):
            rows = [
                ("600000", date(2024, 6, 15), 10.0, 10.5, 9.5, 10.2,
                 10.0, 100000.0, 1020000.0, 0.05),
            ]
            result = sync.upsert_daily_bars(rows)
            assert result == 1


class TestFetchSnapshot:
    def test_filters_bj(self):
        mock_df = pd.DataFrame({
            "f2": ["600000", "000001", "bj920000"],
            "f3": [10.0, 11.0, 5.0],
            "f4": [10.5, 11.5, 5.2],
            "f5": [9.5, 10.5, 4.8],
            "f6": [10.2, 11.2, 5.0],
            "f7": [10.0, 11.0, 5.0],
            "f8": [100000, 200000, 50000],
            "f9": [1020000.0, 2240000.0, 250000.0],
            "f10": [0.05, 0.1, 0.02],
        })
        with patch("emotion_core.data.providers.eastmoney._em_clist", return_value=mock_df), \
             patch("emotion_core.data.providers.eastmoney._EM_FS_ALL_A", new=["f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"]), \
             patch("emotion_core.data.providers.eastmoney._SPOT_MAP", new={
                 "f2": "code", "f3": "open", "f4": "high", "f5": "low", "f6": "close",
                 "f7": "pre_close", "f8": "volume", "f9": "amount", "f10": "turnover_rate",
             }):
            result = sync.fetch_snapshot(date(2024, 6, 15))
            codes = [r[0] for r in result]
            assert "600000" in codes
            assert "000001" in codes
            assert "bj920000" not in codes  # 北交所被过滤


class TestFallbackRows:
    def test_empty_codes(self):
        result = sync.fallback_rows([], date(2024, 6, 1), date(2024, 6, 15))
        assert result == []

    def test_valid_frames(self):
        mock_frame = pd.DataFrame({
            "code": ["600000"],
            "date": [date(2024, 6, 15)],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "pre_close": [10.0],
            "volume": [100000.0],
            "amount": [1020000.0],
            "turnover_rate": [0.05],
        })
        with patch("emotion_core.data.providers.sina.SinaProvider.fetch_daily_bars",
                   return_value=mock_frame), \
             patch("emotion_core.data.providers.base.valid_frame", return_value=True):
            result = sync.fallback_rows(["600000"], date(2024, 6, 1), date(2024, 6, 15))
            assert len(result) == 1
            assert result[0][0] == "600000"

    def test_invalid_frames_skipped(self):
        with patch("emotion_core.data.providers.sina.SinaProvider.fetch_daily_bars",
                   return_value=pd.DataFrame()), \
             patch("emotion_core.data.providers.base.valid_frame", return_value=False):
            result = sync.fallback_rows(["600000"], date(2024, 6, 1), date(2024, 6, 15))
            assert result == []


class TestSyncDaily:
    def test_snapshot_path(self):
        with patch("emotion_core.data.sync.fetch_snapshot", return_value=[("600000",)]), \
             patch("emotion_core.data.sync.upsert_daily_bars", return_value=1) as mock_upsert:
            result = sync.sync_daily(5)
            assert result == 1
            mock_upsert.assert_called_once()

    def test_fallback_path(self):
        with patch("emotion_core.data.sync.fetch_snapshot", side_effect=Exception("502")), \
             patch("emotion_core.data.sync._all_codes", return_value=["600000"]), \
             patch("emotion_core.data.sync.fallback_rows", return_value=[("600000",)]), \
             patch("emotion_core.data.sync.upsert_daily_bars", return_value=1) as mock_upsert:
            result = sync.sync_daily(5)
            assert result == 1

    def test_snapshot_empty_fallback(self):
        with patch("emotion_core.data.sync.fetch_snapshot", return_value=[]), \
             patch("emotion_core.data.sync._all_codes", return_value=["600000"]), \
             patch("emotion_core.data.sync.fallback_rows", return_value=[("600000",)]), \
             patch("emotion_core.data.sync.upsert_daily_bars", return_value=1):
            result = sync.sync_daily(5)
            assert result == 1


class TestAllCodes:
    def test_returns_codes(self):
        mock_df = pd.DataFrame({"code": ["600000", "000001"]})
        with patch("emotion_core.utils.db.query_df", return_value=mock_df):
            result = sync._all_codes()
            assert result == ["600000", "000001"]
