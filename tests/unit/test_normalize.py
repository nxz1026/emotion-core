"""data/normalize.py 归一化测试。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from emotion_core.data.normalize import (
    DailyBar,
    _date,
    _decimal,
    _pick,
    normalize_daily_rows,
)


class TestPick:
    def test_direct_match(self):
        assert _pick({"open": 10.0}, ("open", "o")) == 10.0

    def test_fallback_keys(self):
        assert _pick({"开盘": 10.0}, ("open", "开盘")) == 10.0

    def test_none_value_skipped(self):
        assert _pick({"open": None, "o": 10.0}, ("open", "o")) == 10.0

    def test_empty_string_skipped(self):
        assert _pick({"open": "", "o": 10.0}, ("open", "o")) == 10.0

    def test_case_insensitive(self):
        assert _pick({"Open": 10.0}, ("open",)) == 10.0

    def test_no_match(self):
        assert _pick({"other": 10.0}, ("open",)) is None


class TestDecimal:
    def test_int(self):
        assert _decimal(10, "open") == Decimal("10")

    def test_float(self):
        assert _decimal(10.5, "open") == Decimal("10.5")

    def test_string(self):
        assert _decimal("10.5", "open") == Decimal("10.5")

    def test_invalid(self):
        with pytest.raises(ValueError, match="invalid"):
            _decimal("abc", "open")

    def test_infinity(self):
        with pytest.raises(ValueError, match="invalid"):
            _decimal(float("inf"), "open")


class TestDate:
    def test_date(self):
        assert _date(date(2024, 6, 15)) == date(2024, 6, 15)

    def test_datetime(self):
        assert _date(datetime(2024, 6, 15, 10, 0)) == date(2024, 6, 15)

    def test_string(self):
        assert _date("2024-06-15") == date(2024, 6, 15)

    def test_string_with_slash(self):
        assert _date("2024/06/15") == date(2024, 6, 15)

    def test_invalid(self):
        with pytest.raises(ValueError, match="invalid"):
            _date("not-a-date")


class TestNormalizeDailyRows:
    def test_basic(self):
        rows = [{
            "trade_date": "2024-06-15",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000.0,
        }]
        result = normalize_daily_rows(rows, "600519", "test")
        assert len(result) == 1
        bar = result[0]
        assert isinstance(bar, DailyBar)
        assert bar.code == "600519"
        assert bar.source == "test"
        assert bar.trade_date == date(2024, 6, 15)
        assert bar.open == Decimal("10.0")
        assert bar.quality == "valid"

    def test_multiple_rows(self):
        rows = [
            {"trade_date": "2024-06-14", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.5, "volume": 1000.0},
            {"trade_date": "2024-06-15", "open": 10.5, "high": 11.0, "low": 10.0, "close": 11.0, "volume": 1100.0},
        ]
        result = normalize_daily_rows(rows, "600519", "test")
        assert len(result) == 2

    def test_missing_field_raises(self):
        rows = [{"trade_date": "2024-06-15", "open": 10.0}]
        with pytest.raises(ValueError, match="missing required fields"):
            normalize_daily_rows(rows, "600519", "test")

    def test_not_mapping_raises(self):
        rows = ["not a mapping"]
        with pytest.raises(ValueError, match="must be a mapping"):
            normalize_daily_rows(rows, "600519", "test")

    def test_invalid_quality(self):
        """high < max(open, close, low) → invalid."""
        rows = [{"trade_date": "2024-06-15", "open": 10.0, "high": 9.0, "low": 9.5, "close": 10.5, "volume": 1000.0}]
        result = normalize_daily_rows(rows, "600519", "test")
        assert result[0].quality == "invalid"

    def test_amount_optional(self):
        rows = [{"trade_date": "2024-06-15", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000.0}]
        result = normalize_daily_rows(rows, "600519", "test")
        assert result[0].amount is None

    def test_chinese_keys(self):
        rows = [{"trade_date": "2024-06-15", "开盘": 10.0, "最高": 11.0, "最低": 9.5, "收盘": 10.5, "成交量": 1000.0}]
        result = normalize_daily_rows(rows, "600519", "test")
        assert len(result) == 1
        assert result[0].open == Decimal("10.0")
