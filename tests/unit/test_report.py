"""orchestration/report.py 复盘报告 CLI 测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.orchestration import report


class TestListDates:
    def test_returns_dates(self):
        mock_df = MagicMock()
        mock_df.itertuples.return_value = [
            MagicMock(date=date(2024, 6, 15)),
            MagicMock(date=date(2024, 6, 14)),
        ]
        with patch("emotion_core.orchestration.report.query_df", return_value=mock_df):
            result = report._list_dates(5)
            assert result == [date(2024, 6, 15), date(2024, 6, 14)]

    def test_empty(self):
        mock_df = MagicMock()
        mock_df.itertuples.return_value = []
        with patch("emotion_core.orchestration.report.query_df", return_value=mock_df):
            result = report._list_dates(5)
            assert result == []


class TestLatest:
    def test_returns_latest(self):
        mock_df = MagicMock()
        mock_df.itertuples.return_value = [MagicMock(date=date(2024, 6, 15))]
        with patch("emotion_core.orchestration.report.query_df", return_value=mock_df):
            result = report._latest()
            assert result == date(2024, 6, 15)

    def test_none_when_empty(self):
        mock_df = MagicMock()
        mock_df.itertuples.return_value = []
        with patch("emotion_core.orchestration.report.query_df", return_value=mock_df):
            result = report._latest()
            assert result is None


class TestMain:
    def test_list_dates(self):
        with patch("emotion_core.orchestration.report._list_dates", return_value=[date(2024, 6, 15)]):
            result = report.main(["--list", "5"])
            assert result == 0

    def test_list_empty(self):
        with patch("emotion_core.orchestration.report._list_dates", return_value=[]):
            result = report.main(["--list", "5"])
            assert result == 1

    def test_latest_success(self):
        with patch("emotion_core.orchestration.report._latest", return_value=date(2024, 6, 15)), \
             patch("emotion_core.algorithms.review.publish") as mock_publish:
            mock_publish.return_value = "/path/to/report"
            result = report.main(["--latest"])
            assert result == 0

    def test_latest_no_data(self):
        with patch("emotion_core.orchestration.report._latest", return_value=None):
            result = report.main(["--latest"])
            assert result == 1

    def test_date_format_error(self):
        result = report.main(["--date", "bad-date"])
        assert result == 2

    def test_publish_failure(self):
        with patch("emotion_core.orchestration.report._latest", return_value=date(2024, 6, 15)), \
             patch("emotion_core.algorithms.review.publish") as mock_publish:
            mock_publish.side_effect = RuntimeError("data gap")
            result = report.main(["--date", "2024-06-15"])
            assert result == 1
