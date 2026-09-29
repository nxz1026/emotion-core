"""ladder_service 胶水层测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from emotion_core.services import ladder_service


def test_load_ladder_candidates_forwards():
    with patch("emotion_core.services.ladder_service._load_ladder_candidates") as mock:
        mock.return_value = [("600519", 3, True)]
        result = ladder_service.load_ladder_candidates(date(2024, 6, 15))
        assert result == [("600519", 3, True)]
        mock.assert_called_once_with(date(2024, 6, 15))


def test_load_exchange_codes_forwards():
    with patch("emotion_core.services.ladder_service._load_exchange_codes") as mock:
        mock.return_value = {"600519"}
        result = ladder_service.load_exchange_codes(date(2024, 6, 15))
        assert result == {"600519"}


def test_count_derived_rows_forwards():
    with patch("emotion_core.services.ladder_service._count_derived_rows") as mock:
        mock.return_value = 100
        result = ladder_service.count_derived_rows(date(2024, 6, 15))
        assert result == 100


def test_replace_ladder_day_forwards():
    rows = [("600519", 3, True)]
    with patch("emotion_core.services.ladder_service._replace_ladder_day") as mock:
        mock.return_value = 1
        result = ladder_service.replace_ladder_day(date(2024, 6, 15), rows)
        assert result == 1
        mock.assert_called_once_with(date(2024, 6, 15), rows)
