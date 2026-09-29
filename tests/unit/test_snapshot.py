"""presentation/snapshot.py 快照日期与日历视图测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.presentation import snapshot


@pytest.fixture(autouse=True)
def _clear_cache():
    snapshot.clear_cache()
    yield
    snapshot.clear_cache()


def _mock_df(dates_list):
    """Create a mock DataFrame that supports df['date'].tolist()."""
    df = MagicMock()
    col = MagicMock()
    col.tolist.return_value = dates_list
    df.__getitem__ = lambda self, k: col if k == "date" else MagicMock()
    return df


class TestAvailableDates:
    def test_returns_dates(self):
        mock_df = _mock_df([date(2024, 6, 15), date(2024, 6, 14)])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            result = snapshot.available_dates()
            assert result == [date(2024, 6, 15), date(2024, 6, 14)]


class TestLatestDate:
    def test_returns_first(self):
        mock_df = _mock_df([date(2024, 6, 15)])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            assert snapshot.latest_date() == date(2024, 6, 15)

    def test_none_when_empty(self):
        mock_df = _mock_df([])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            assert snapshot.latest_date() is None


class TestResolveDate:
    def test_empty_returns_fallback(self):
        with patch("emotion_core.presentation.snapshot.latest_date", return_value=date(2024, 6, 10)):
            assert snapshot.resolve_date("") == date(2024, 6, 10)

    def test_invalid_returns_fallback(self):
        with patch("emotion_core.presentation.snapshot.latest_date", return_value=date(2024, 6, 10)):
            assert snapshot.resolve_date("bad-date") == date(2024, 6, 10)

    def test_valid_trading_day(self):
        with patch("emotion_core.presentation.snapshot.is_trading_day", return_value=True):
            assert snapshot.resolve_date("2024-06-15") == date(2024, 6, 15)

    def test_non_trading_day_fallback(self):
        with patch("emotion_core.presentation.snapshot.latest_date", return_value=date(2024, 6, 10)), \
             patch("emotion_core.presentation.snapshot.is_trading_day", return_value=False):
            assert snapshot.resolve_date("2024-06-15") == date(2024, 6, 10)


class TestCalendarCtx:
    def test_basic_structure(self):
        mock_df = _mock_df([date(2024, 6, 15), date(2024, 6, 14)])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            result = snapshot.calendar_ctx(date(2024, 6, 15))
            assert "month_label" in result
            assert "weeks" in result
            assert "weekday_labels" in result
            assert result["month_days"] >= 0


class TestBannerCtx:
    def test_selected_is_latest(self):
        mock_df = _mock_df([date(2024, 6, 15)])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            result = snapshot.banner_ctx(date(2024, 6, 15))
            assert result["is_latest"] is True
            assert result["days_behind"] == 0


class TestRecentSnapshotsCtx:
    def test_returns_list(self):
        mock_df = _mock_df([date(2024, 6, 15), date(2024, 6, 14)])
        with patch("emotion_core.presentation.snapshot.query_df", return_value=mock_df):
            result = snapshot.recent_snapshots_ctx(None, limit=5)
            assert len(result) == 2
            assert result[0]["iso"] == "2024-06-15"


class TestDateLinks:
    def test_with_date(self):
        result = snapshot.date_links(date(2024, 6, 15))
        assert result["date_query"] == "?date=2024-06-15"

    def test_without_date(self):
        result = snapshot.date_links(None)
        assert result["date_query"] == ""
