"""algorithms/reconcile.py 衍生层对账测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.algorithms import reconcile


class TestReconcile:
    def test_returns_dataframe(self):
        mock_df = MagicMock()
        with patch("emotion_core.algorithms.reconcile.query_df", return_value=mock_df), \
             patch("emotion_core.algorithms.reconcile.CONFIG") as mock_config:
            mock_config.BOARD_PREFIXES = ("600", "601", "603")
            result = reconcile.reconcile(date(2024, 6, 15))
            assert result is mock_df

    def test_board_prefixes_in_query(self):
        """验证 BOARD_PREFIXES 被正确展开到 SQL 占位符。"""
        with patch("emotion_core.algorithms.reconcile.query_df") as mock_query, \
             patch("emotion_core.algorithms.reconcile.CONFIG") as mock_config:
            mock_config.BOARD_PREFIXES = ("600", "601", "603", "000")
            reconcile.reconcile(date(2024, 6, 15))
            call_args = mock_query.call_args
            sql = call_args[0][0]
            params = call_args[0][1]
            # 4 个 prefix → 4 个 %s
            assert sql.count("%s") == 5  # 1 date + 4 prefixes
            assert params == (date(2024, 6, 15), "600", "601", "603", "000")


class TestReconcileRange:
    def test_empty_returns_empty(self):
        mock_df = pd.DataFrame()
        with patch("emotion_core.algorithms.reconcile.query_df", return_value=mock_df), \
             patch("emotion_core.algorithms.reconcile.CONFIG") as mock_config:
            mock_config.BOARD_PREFIXES = ("600",)
            result = reconcile.reconcile_range(date(2024, 1, 1), date(2024, 6, 15))
            assert result.empty

    def test_computes_match_pct(self):
        mock_df = pd.DataFrame({
            "date": [date(2024, 6, 15)],
            "pool_mb": [10],
            "diff": [2],
            "height_diff": [1],
        })
        with patch("emotion_core.algorithms.reconcile.query_df", return_value=mock_df), \
             patch("emotion_core.algorithms.reconcile.CONFIG") as mock_config:
            mock_config.BOARD_PREFIXES = ("600",)
            result = reconcile.reconcile_range(date(2024, 6, 1), date(2024, 6, 15))
            assert result.iloc[0]["mismatch"] == 3  # 2 diff + 1 height_diff
            assert result.iloc[0]["match_pct"] == 70.0  # (10-3)/10 * 100

    def test_all_match_100_pct(self):
        mock_df = pd.DataFrame({
            "date": [date(2024, 6, 15)],
            "pool_mb": [10],
            "diff": [0],
            "height_diff": [0],
        })
        with patch("emotion_core.algorithms.reconcile.query_df", return_value=mock_df), \
             patch("emotion_core.algorithms.reconcile.CONFIG") as mock_config:
            mock_config.BOARD_PREFIXES = ("600",)
            result = reconcile.reconcile_range(date(2024, 6, 1), date(2024, 6, 15))
            assert result.iloc[0]["match_pct"] == 100.0
