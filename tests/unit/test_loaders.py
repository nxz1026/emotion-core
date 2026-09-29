"""presentation/loaders.py 数据加载器测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.presentation import loaders


class TestD:
    def test_returns_provided_date(self):
        d = date(2024, 6, 15)
        assert loaders._d(d) == d

    def test_returns_latest_when_none(self):
        with patch("emotion_core.presentation.snapshot.latest_date", return_value=date(2024, 6, 10)):
            assert loaders._d(None) == date(2024, 6, 10)


class TestLoadMarketSnapshot:
    def test_returns_dict(self):
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iloc = [MagicMock(to_dict=lambda: {"phase": "发酵"})]
        with patch("emotion_core.presentation.loaders.query_df", return_value=mock_df), \
             patch("emotion_core.presentation.loaders._d", return_value=date(2024, 6, 15)):
            result = loaders.load_market_snapshot()
            assert result == {"phase": "发酵"}

    def test_empty_returns_empty_dict(self):
        mock_df = MagicMock()
        mock_df.empty = True
        with patch("emotion_core.presentation.loaders.query_df", return_value=mock_df), \
             patch("emotion_core.presentation.loaders._d", return_value=date(2024, 6, 15)):
            result = loaders.load_market_snapshot()
            assert result == {}


class TestLoadLadder:
    def test_returns_records(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [{"code": "600000"}]
        with patch("emotion_core.presentation.loaders.query_df", return_value=mock_df), \
             patch("emotion_core.presentation.loaders._d", return_value=date(2024, 6, 15)):
            result = loaders.load_ladder()
            assert result == [{"code": "600000"}]


class TestLoadSignals:
    def test_returns_records(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [{"action": "BUY"}]
        with patch("emotion_core.presentation.loaders.query_df", return_value=mock_df), \
             patch("emotion_core.presentation.loaders._d", return_value=date(2024, 6, 15)):
            result = loaders.load_signals()
            assert result == [{"action": "BUY"}]


class TestLoadMarketTrend:
    def test_returns_records(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [{"date": date(2024, 6, 15)}]
        with patch("emotion_core.presentation.loaders.query_df", return_value=mock_df):
            result = loaders.load_market_trend(10)
            assert result == [{"date": date(2024, 6, 15)}]


class TestLoadSignalCounts:
    def test_returns_counts(self):
        def _mock_query(sql, params):
            mock = MagicMock()
            if "signal" in sql and "confirm_date" in sql:
                mock.empty = False
                mock.iloc = [{"n": 5}]
            elif "signal" in sql:
                mock.empty = False
                mock.iloc = [{"n": 100}]
            elif "ladder_day" in sql and "date" in sql:
                mock.empty = False
                mock.iloc = [{"n": 3}]
            else:
                mock.empty = False
                mock.iloc = [{"n": 50}]
            return mock

        with patch("emotion_core.presentation.loaders.query_df", side_effect=_mock_query), \
             patch("emotion_core.presentation.loaders._d", return_value=date(2024, 6, 15)):
            result = loaders.load_signal_counts()
            assert result["day"] == 5
            assert result["total"] == 100
            assert result["ladder_day"] == 3
            assert result["ladder_total"] == 50
