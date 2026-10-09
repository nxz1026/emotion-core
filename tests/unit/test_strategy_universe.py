"""strategy.universe 候选池构建测试。

2026-10-09 日志巡检 C：候选池口径改为「手动自选 ``watchlist`` + 热门池
``hot_rank``」，不再读 ``limit_pool_em``；自选 code 必须排在热门池之前。
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pandas as pd

from emotion_core.services.strategy import universe
from emotion_core.utils.config import CONFIG as REAL_CONFIG


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


def _mock(monkeypatch, *, watch=(), hot=()):
    """装 watchlist / hot_rank 两个查询的假连接，并记录 SQL。"""
    seen: dict[str, str] = {}

    def mock_query(sql, params=()):
        if "watchlist" in sql:
            seen["watch"] = sql
            return pd.DataFrame({"code": list(watch)})
        if "hot_rank" in sql:
            seen["hot"] = sql
            return pd.DataFrame({"code": list(hot)})
        raise AssertionError(f"意外的查询：{sql}")

    monkeypatch.setattr(universe, "query_df", mock_query)
    return seen


class TestBuildUniverse:
    def test_empty(self, monkeypatch):
        _mock(monkeypatch)
        assert universe.build_universe(date(2024, 6, 15)) == []

    def test_watchlist_only(self, monkeypatch):
        _mock(monkeypatch, watch=["600519", "000001"])
        assert universe.build_universe(date(2024, 6, 15)) == ["600519", "000001"]

    def test_hot_rank_only(self, monkeypatch):
        _mock(monkeypatch, hot=["000001"])
        assert universe.build_universe(date(2024, 6, 15)) == ["000001"]

    def test_dedup(self, monkeypatch):
        _mock(monkeypatch, watch=["600519"], hot=["600519"])
        assert universe.build_universe(date(2024, 6, 15)) == ["600519"]

    def test_max_universe_cap(self, monkeypatch):
        """候选池不超过 STRATEGY_MAX_UNIVERSE。"""
        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=10)
        monkeypatch.setattr(universe, "CONFIG", conf)
        _mock(monkeypatch, hot=[f"6005{i:02d}" for i in range(100)])
        assert len(universe.build_universe(date(2024, 6, 15))) == 10

    def test_board_filter(self, monkeypatch):
        _mock(monkeypatch, watch=["600519", "830001"])
        result = universe.build_universe(date(2024, 6, 15))
        assert "600519" in result
        assert "830001" not in result

    def test_no_longer_reads_limit_pool(self, monkeypatch):
        """2026-10-09 用户拍板：不再读涨停/炸板池 limit_pool_em。"""
        seen = _mock(monkeypatch, watch=["600519"], hot=["000001"])
        universe.build_universe(date(2024, 6, 15))
        assert "limit_pool_em" not in seen["watch"]
        assert "limit_pool_em" not in seen["hot"]
        assert not hasattr(universe, "OBSERVED_POOL_TYPES"), "旧池类型常量应已删除"


class TestPriorityOrder:
    """顺序即优先级：自选 code 全部排在热门池之前（runner 先烧前面的 code）。"""

    def test_watchlist_codes_come_first(self, monkeypatch):
        _mock(monkeypatch, watch=["000001"], hot=["600519", "600520"])
        assert universe.build_universe(date(2024, 6, 15)) == ["000001", "600519", "600520"]

    def test_watchlist_survives_truncation(self, monkeypatch):
        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=2)
        monkeypatch.setattr(universe, "CONFIG", conf)
        _mock(monkeypatch, watch=["600519", "600520"], hot=["000001"])
        assert universe.build_universe(date(2024, 6, 15)) == ["600519", "600520"]

    def test_duplicate_in_hot_does_not_consume_slot(self, monkeypatch):
        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=2)
        monkeypatch.setattr(universe, "CONFIG", conf)
        _mock(monkeypatch, watch=["600519"], hot=["600519", "000001"])
        assert universe.build_universe(date(2024, 6, 15)) == ["600519", "000001"]


class TestQueryOrderingIsDeterministic:
    """候选池查询必须带完全确定的 ORDER BY。

    结果会被 STRATEGY_MAX_UNIVERSE 截断，截断后的顺序决定 LLM 配额先烧
    哪些 (code, skill) 组合。缺 ORDER BY 时 PostgreSQL 不保证顺序稳定，
    同一交易日重复运行会得到不同候选池，让「跑过哪些组合」不可复现。
    """

    def test_watchlist_query_orders_by_code(self, monkeypatch):
        seen = _mock(monkeypatch, watch=["600519"])
        universe.build_universe(date(2024, 6, 15))
        assert seen["watch"].lower().rstrip().endswith("order by code")

    def test_hot_query_has_deterministic_tiebreaker(self, monkeypatch):
        """仅 ORDER BY rank 时同名次并列的顺序不保证，需补 code 兜底。"""
        seen = _mock(monkeypatch, hot=["000001"])
        universe.build_universe(date(2024, 6, 15))
        sql = seen["hot"].lower()
        assert "order by rank, code" in sql
        assert "limit %s" in sql


class TestFilterCodes:
    """filter_codes：BOARD_PREFIXES 过滤 + zfill(6) + 保序去重。"""

    def test_filters_dedups_and_pads(self):
        assert universe.filter_codes(["600519", 1, "830001", "600519", "2"]) == [
            "600519",
            "000001",
            "000002",
        ]

    def test_limit_none_keeps_everything(self):
        codes = [f"6005{i:02d}" for i in range(150)]
        assert len(universe.filter_codes(codes)) == 150

    def test_limit_truncates(self):
        codes = [f"6005{i:02d}" for i in range(150)]
        assert len(universe.filter_codes(codes, limit=80)) == 80
