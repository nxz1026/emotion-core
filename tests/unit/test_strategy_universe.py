"""strategy.universe 候选池构建测试。

2026-10-09 日志巡检 C：候选池口径改为「手动自选 ``watchlist`` + 热门池
``hot_rank``」，不再读 ``limit_pool_em``；自选 code 必须排在热门池之前。
"""

from __future__ import annotations

import dataclasses
import logging
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


def _mock(monkeypatch, *, watch=(), hot=(), tradable=None):
    """装 watchlist / hot_rank / daily_bar 三个查询的假连接，并记录 SQL。

    `tradable`（R67-3）：当日有行情的 code 集合。默认 = watch + hot 的全部
    code（即「全部可交易」），这样既有的候选池顺序/截断断言不受硬闸门影响；
    传 `[]` 可模拟整池停牌，传子集可模拟部分缺行情。
    """
    seen: dict[str, object] = {}
    have_bar = set(watch) | set(hot) if tradable is None else set(tradable)

    def mock_query(sql, params=()):
        if "watchlist" in sql:
            seen["watch"] = sql
            return pd.DataFrame({"code": list(watch)})
        if "hot_rank" in sql:
            seen["hot"] = sql
            return pd.DataFrame({"code": list(hot)})
        if "daily_bar" in sql:
            seen["tradable"] = sql
            seen["tradable_params"] = params
            return pd.DataFrame({"code": sorted(have_bar)})
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


class TestTradableCodes:
    """R67-3 第一层：``tradable_codes`` / ``drop_untradable``。

    事故背景：2026-10-13 ``strategy_signal`` 有一条 600825 的
    ``expectation_repricing`` **BUY** score 92，而 600825 自 10-13 起连续停牌。
    停牌日的顺延占位行 ``volume = 0``（判据同 ``data.bar_guard.is_padding_row``），
    ``volume > 0`` 一次查全市场即可把停牌/无 bar 的票排除。
    """

    def test_returns_codes_as_set(self, monkeypatch):
        _mock(monkeypatch, tradable=["600519", "000001"])
        assert universe.tradable_codes(date(2024, 6, 15)) == {"600519", "000001"}

    def test_empty_when_no_bar(self, monkeypatch):
        _mock(monkeypatch, tradable=[])
        assert universe.tradable_codes(date(2024, 6, 15)) == set()

    def test_sql_filters_padding_rows_and_boards(self, monkeypatch):
        """判据必须是 ``volume > 0``（停牌占位行的特征），且限定主板前缀。"""
        seen = _mock(monkeypatch, tradable=["600519"])
        universe.tradable_codes(date(2024, 6, 15))
        sql = str(seen["tradable"]).lower()
        assert "from daily_bar" in sql
        assert "volume > 0" in sql, "停牌占位行 volume=0，缺此判据会把停牌票当有行情"
        assert "left(code, 3) in" in sql, "必须限定 BOARD_PREFIXES，别把创业板算进来"
        assert seen["tradable_params"][0] == date(2024, 6, 15)

    def test_drop_untradable_preserves_order_and_keeps_known(self, monkeypatch):
        _mock(monkeypatch, tradable=["600519", "000001"])
        assert universe.drop_untradable(["600519", "600825", "000001"], date(2024, 6, 15)) == [
            "600519",
            "000001",
        ]

    def test_drop_untradable_all_suspended(self, monkeypatch):
        _mock(monkeypatch, tradable=[])
        assert universe.drop_untradable(["600825", "002343"], date(2024, 6, 15)) == []


class TestBuildUniverseTradableGate:
    """R67-3 第一层闸门接在 ``build_universe`` 里，过滤**在截断之后**。"""

    def test_drops_suspended_code(self, monkeypatch):
        _mock(monkeypatch, watch=["600825"], hot=["000001"], tradable=["000001"])
        assert universe.build_universe(date(2024, 6, 15)) == ["000001"]

    def test_truncation_happens_before_filter(self, monkeypatch):
        """锁定现状口径：先按 STRATEGY_MAX_UNIVERSE 截断，再剔除停牌票。

        因此停牌票会占用一个截断名额，末尾的可交易票**补不进来**——此处
        600521 进了池、000001 被截掉，600825 剔除后池里只剩 2 只（非 3 只）。
        这是已知取舍（STRATEGY_HOT_N=20 + 自选表通常远小于上限 80，截断一般
        不生效）；若哪天改成「先剔除再截断」，本断言必须同步改。
        """
        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=3)
        monkeypatch.setattr(universe, "CONFIG", conf)
        _mock(
            monkeypatch,
            watch=["600825", "600520"],
            hot=["600521", "000001"],
            tradable=["600520", "600521", "000001"],
        )
        result = universe.build_universe(date(2024, 6, 15))
        assert result == ["600520", "600521"]
        assert "600825" not in result

    def test_no_log_when_everything_tradable(self, monkeypatch, caplog):
        _mock(monkeypatch, watch=["600519"], hot=["000001"])
        with caplog.at_level(logging.INFO):
            universe.build_universe(date(2024, 6, 15))
        assert "剔除当日无行情" not in caplog.text

    def test_logs_dropped_codes(self, monkeypatch, caplog):
        _mock(monkeypatch, watch=["600825", "600519"], tradable=["600519"])
        with caplog.at_level(logging.INFO):
            universe.build_universe(date(2024, 6, 15))
        assert "剔除当日无行情标的 1 只" in caplog.text
        assert "600825" in caplog.text
