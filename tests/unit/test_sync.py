"""data/sync.py 日更数据同步测试。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.data import sync
from emotion_core.utils.errors import DataError


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
        )), patch("emotion_core.data.sync.check_bars") as mock_guard:
            rows = [
                ("600000", date(2024, 6, 15), 10.0, 10.5, 9.5, 10.2,
                 10.0, 100000.0, 1020000.0, 0.05),
            ]
            result = sync.upsert_daily_bars(rows)
            assert result == 1
            mock_guard.assert_called_once_with(rows, allow_gap=False)

    def test_validate_false_skips_guard(self):
        """历史修复场景：显式关掉自检（形状也一起跳过）。"""
        with patch("emotion_core.data.sync.transaction", return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock()),
            __exit__=MagicMock(return_value=False),
        )), patch("emotion_core.data.sync.check_bars") as mock_guard:
            sync.upsert_daily_bars([("600000", date(2024, 6, 15), 10.0, 10.5,
                                     9.5, 10.2, 10.0, 1000.0, 1000.0, 0.05)],
                                   validate=False)
            mock_guard.assert_not_called()


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
             patch("emotion_core.data.providers.eastmoney.em_data_date",
                   return_value=date(2024, 6, 15)), \
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

    def test_rejects_snapshot_from_another_day(self):
        """源自带 f124=10-13，却要写 09-29 ⇒ 拒绝（2026-09-29 污染的根因）。"""
        ts = datetime(2026, 10, 13, 15, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()
        frame = pd.DataFrame({"f2": ["600000"], "f6": [10.0], "f124": [int(ts)]})
        with patch("emotion_core.data.providers.eastmoney._em_clist",
                   return_value=frame):
            with pytest.raises(DataError, match="快照自带日期"):
                sync.fetch_snapshot(date(2026, 9, 29))

    def test_accepts_snapshot_of_requested_day(self):
        ts = datetime(2026, 9, 29, 15, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()
        frame = pd.DataFrame({
            "f12": ["600000"], "f17": [10.0], "f15": [10.5], "f16": [9.5],
            "f2": [10.2], "f18": [10.0], "f5": [1000.0], "f6": [1.0],
            "f8": [0.05], "f124": [int(ts)],
        })
        with patch("emotion_core.data.providers.eastmoney._em_clist",
                   return_value=frame):
            rows = sync.fetch_snapshot(date(2026, 9, 29))
            assert rows and rows[0][1] == date(2026, 9, 29)


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
    @pytest.fixture(autouse=True)
    def _pin_data_date(self, monkeypatch):
        """sync_daily 现在以**源自己的数据日期**为 end（2026-10-17 修）。"""
        monkeypatch.setattr("emotion_core.data.providers.eastmoney.em_data_date",
                            lambda: date(2026, 9, 29))

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
