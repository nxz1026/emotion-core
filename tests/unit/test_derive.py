"""derive.py 衍生层参考实现测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from emotion_core.algorithms import derive
from emotion_core.utils.config import CONFIG


class TestStreaks:
    def test_empty(self):
        assert derive._streaks([]) == []

    def test_all_true(self):
        assert derive._streaks([True, True, True]) == [1, 2, 3]

    def test_all_false(self):
        assert derive._streaks([False, False, False]) == [0, 0, 0]

    def test_mixed(self):
        assert derive._streaks([True, True, False, True]) == [1, 2, 0, 1]

    def test_single(self):
        assert derive._streaks([True]) == [1]
        assert derive._streaks([False]) == [0]


class TestPctCase:
    def test_generates_case(self):
        result = derive._pct_case("code")
        assert result.startswith("CASE")
        assert "WHEN" in result
        assert "ELSE" in result

    def test_custom_col(self):
        result = derive._pct_case("b.code")
        assert "left(b.code, 2)" in result


class TestBseFilter:
    def test_excludes_bse(self):
        result = derive._bse_filter("s.code")
        assert "<>" in result
        # Should have AND for multiple prefixes
        assert "AND" in result

    def test_config_driven(self):
        result = derive._bse_filter("code")
        for prefix in CONFIG.BSE_EXCLUDED_PREFIXES:
            assert prefix in result


class TestFinite:
    def test_not_null_check(self):
        result = derive._finite("price")
        assert "IS NOT NULL" in result

    def test_nan_check(self):
        result = derive._finite("price")
        assert "<> 'NaN'::numeric" in result


class TestRenderSql:
    def test_replaces_all_placeholders(self):
        sql = derive.render_sql()
        assert "{pct}" not in sql
        assert "{bse}" not in sql
        assert "{finite_pre}" not in sql
        assert "{finite_close}" not in sql
        assert "{finite_high}" not in sql
        assert "{finite_low}" not in sql

    def test_contains_derived_insert(self):
        sql = derive.render_sql()
        assert "INSERT INTO derived_bar" in sql

    def test_contains_bse_filter(self):
        sql = derive.render_sql()
        assert "<>" in sql


class TestComputeDerived:
    def test_calls_execute_with_sql(self):
        with patch("emotion_core.algorithms.derive.execute") as mock_exec:
            mock_exec.return_value = 42
            result = derive.compute_derived(date(2024, 1, 1), date(2024, 6, 30))
            assert result == 42
            assert mock_exec.call_count == 1
            assert len(mock_exec.call_args[0][1]) == 2


class TestComputeContDays:
    def test_calls_execute(self):
        with patch("emotion_core.algorithms.derive.execute") as mock_exec:
            mock_exec.return_value = 10
            result = derive.compute_cont_days(date(2024, 1, 1), date(2024, 6, 30))
            assert result == 10


class TestZeroReset:
    def test_calls_execute(self):
        with patch("emotion_core.algorithms.derive.execute") as mock_exec:
            mock_exec.return_value = 5
            result = derive.zero_reset(date(2024, 1, 1), date(2024, 6, 30))
            assert result == 5


class TestDeriveRange:
    def test_calls_all_three(self):
        with patch("emotion_core.algorithms.derive.execute") as mock_exec:
            mock_exec.return_value = 0
            derive.derive_range(date(2024, 1, 1), date(2024, 6, 30))
            assert mock_exec.call_count == 3
