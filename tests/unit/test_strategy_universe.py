"""strategy.universe 候选池构建测试。"""
from __future__ import annotations

import importlib
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from emotion_core.services.strategy import universe


class TestCodes:
    def test_empty_frame(self):
        assert universe._codes(pd.DataFrame()) == []

    def test_with_codes(self):
        df = pd.DataFrame({"code": ["600519", "000001"]})
        result = universe._codes(df)
        assert result == ["600519", "000001"]

    def test_padding(self):
        df = pd.DataFrame({"code": [1, 2]})
        result = universe._codes(df)
        assert result == ["000001", "000002"]

    def test_dropna(self):
        df = pd.DataFrame({"code": ["600519", None, "000001"]})
        result = universe._codes(df)
        assert None not in result


class TestBuildUniverse:
    def test_empty(self, monkeypatch):
        monkeypatch.setattr(universe, "query_df", lambda sql, params: pd.DataFrame())
        result = universe.build_universe(date(2024, 6, 15))
        assert result == []

    def test_limit_pool_only(self, monkeypatch):
        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519", "000001"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "600519" in result

    def test_hot_rank_only(self, monkeypatch):
        def mock_query(sql, params):
            if "hot_rank" in sql:
                return pd.DataFrame({"code": ["000001"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "000001" in result

    def test_dedup(self, monkeypatch):
        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519"]})
            if "hot_rank" in sql:
                return pd.DataFrame({"code": ["600519"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert result.count("600519") == 1

    def test_max_universe_cap(self, monkeypatch):
        """候选池不超过 STRATEGY_MAX_UNIVERSE。"""
        import dataclasses
        from emotion_core.utils.config import CONFIG as REAL_CONFIG

        codes = [f"{i:06d}" for i in range(100, 200)]
        df = pd.DataFrame({"code": codes})

        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=10, BOARD_PREFIXES=("0", "3", "6"))
        monkeypatch.setattr(universe, "CONFIG", conf)
        monkeypatch.setattr(universe, "query_df", lambda sql, params: df)
        result = universe.build_universe(date(2024, 6, 15))
        assert len(result) <= 10

    def test_board_filter(self, monkeypatch):
        """只保留主板前缀。"""
        import dataclasses
        from emotion_core.utils.config import CONFIG as REAL_CONFIG

        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519", "830001"]})
            return pd.DataFrame()

        conf = dataclasses.replace(REAL_CONFIG, BOARD_PREFIXES=("0", "3", "6"))
        monkeypatch.setattr(universe, "CONFIG", conf)
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "600519" in result
        assert "830001" not in result


class TestQueryOrderingIsDeterministic:
    """候选池查询必须带完全确定的 ORDER BY。

    结果会被 STRATEGY_MAX_UNIVERSE 截断，截断后的顺序决定 LLM 配额先烧
    哪些 (code, skill) 组合。缺 ORDER BY 时 PostgreSQL 不保证顺序稳定，
    同一交易日重复运行会得到不同候选池，让「跑过哪些组合」不可复现。
    """

    @staticmethod
    def _captured_sql(monkeypatch) -> dict[str, str]:
        seen: dict[str, str] = {}

        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                seen["pool"] = sql
                return pd.DataFrame({"code": ["600519"]})
            if "hot_rank" in sql:
                seen["hot"] = sql
                return pd.DataFrame({"code": ["000001"]})
            return pd.DataFrame()

        monkeypatch.setattr(universe, "query_df", mock_query)
        universe.build_universe(date(2024, 6, 15))
        return seen

    def test_pool_query_orders_by_code(self, monkeypatch):
        sql = self._captured_sql(monkeypatch)["pool"].lower()
        assert "order by" in sql
        assert sql.rstrip().endswith("order by code")

    def test_hot_query_has_deterministic_tiebreaker(self, monkeypatch):
        """仅 ORDER BY rank 时同名次并列的顺序不保证，需补 code 兜底。"""
        sql = self._captured_sql(monkeypatch)["hot"].lower()
        assert "order by rank, code" in sql


class TestPoolTypeFilter:
    """候选池只收涨停(ZT)/炸板(ZB)，把跌停(DT)挡在外面。

    ``limit_pool_em`` 是东财**涨停池**表，但同一张表里混着 DT 行（实测
    2026-09-29：ZT 57 / DT 10 / ZB 8）。不收窄时跌停股会进候选池、白烧
    LLM 配额，且与 docstring 声明的「当日涨停池」语义相悖。
    """

    @staticmethod
    def _captured(monkeypatch) -> tuple[str, tuple]:
        seen: dict = {}

        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                seen["sql"], seen["params"] = sql, params
            return pd.DataFrame()

        monkeypatch.setattr(universe, "query_df", mock_query)
        universe.build_universe(date(2024, 6, 15))
        return seen["sql"], seen["params"]

    def test_pool_query_filters_pool_type(self, monkeypatch):
        sql = self._captured(monkeypatch)[0].lower()
        assert "pool_type = any(" in sql, "候选池查询没有按 pool_type 收窄"

    def test_dt_is_not_observed(self):
        assert "DT" not in universe.OBSERVED_POOL_TYPES, "跌停股不应进候选池"
        assert "ZT" in universe.OBSERVED_POOL_TYPES

    def test_pool_types_reach_the_query_as_param(self, monkeypatch):
        """pool_type 走参数而非 SQL 字面量，列表由 psycopg 适配成 PG 数组。"""
        _sql, params = self._captured(monkeypatch)
        assert params[1] == list(universe.OBSERVED_POOL_TYPES)


@pytest.mark.parametrize("raw,expected", [
    ("ZT", ("ZT",)),
    ("zt,zb", ("ZT", "ZB")),
    (" ZT , zb ", ("ZT", "ZB")),
    ("zt,,zb", ("ZT", "ZB")),
    ("", ()),
])
def test_pool_types_env_parsing(monkeypatch, raw, expected):
    """EC_STRATEGY_POOL_TYPES 的解析：去空白、转大写、丢空段。"""
    monkeypatch.setenv("EC_STRATEGY_POOL_TYPES", raw)
    importlib.reload(universe)
    try:
        assert universe.OBSERVED_POOL_TYPES == expected
    finally:
        monkeypatch.delenv("EC_STRATEGY_POOL_TYPES", raising=False)
        importlib.reload(universe)

