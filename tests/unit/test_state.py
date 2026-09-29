"""state.py 状态机测试。"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.algorithms.state import (
    DayMetrics,
    EmotionState,
    _rule_climax,
    _rule_ebb,
    _rule_ferment,
    _rule_ice,
    classify_series,
    window_of,
)


def make_metrics(
    d: date,
    limit_up_count: int = 30,
    bomb_rate: float | None = None,
    zt_performance: float | None = None,
    max_limit_days: int = 3,
    limit_down_count: int = 5,
    top_amplitude: float | None = None,
    top_broke: bool | None = None,
    bomb_threshold: float = 0.5,
    has_candidate: bool = True,
    tradable_max_days: int = 3,
    oneword_ratio: float | None = None,
) -> DayMetrics:
    return DayMetrics(
        date=d,
        limit_up_count=limit_up_count,
        bomb_rate=bomb_rate,
        zt_performance=zt_performance,
        max_limit_days=max_limit_days,
        limit_down_count=limit_down_count,
        top_amplitude=top_amplitude,
        top_broke=top_broke,
        bomb_threshold=bomb_threshold,
        has_candidate=has_candidate,
        tradable_max_days=tradable_max_days,
        oneword_ratio=oneword_ratio,
    )


class TestWindowOf:
    def test_ferment(self):
        assert window_of("发酵") == ("STANDARD", False)

    def test_climax(self):
        assert window_of("高潮") == ("ENHANCED", False)

    def test_ice(self):
        assert window_of("冰点") == ("NONE", False)

    def test_ebb(self):
        assert window_of("退潮") == ("NONE", True)


class TestRuleEbb:
    def test_two_day_broke(self):
        t = make_metrics(date(2024, 6, 15), top_broke=True)
        y = make_metrics(date(2024, 6, 14), top_broke=True)
        assert _rule_ebb(t, y, None) is True

    def test_perf_below_threshold(self):
        # EBB_ZT_PERF = -2.0, so need zt_performance < -2.0
        t = make_metrics(date(2024, 6, 15), zt_performance=-3.0)
        y = make_metrics(date(2024, 6, 14))
        assert _rule_ebb(t, y, None) is True

    def test_surge_limit_down(self):
        # EBB_LD_MIN = 15, EBB_LD_MULT = 2.0
        # Need t.limit_down_count > 15 AND t.limit_down_count > y * 2
        t = make_metrics(date(2024, 6, 15), limit_down_count=20)
        y = make_metrics(date(2024, 6, 14), limit_down_count=5)
        assert _rule_ebb(t, y, None) is True

    def test_normal(self):
        t = make_metrics(date(2024, 6, 15), limit_down_count=5)
        y = make_metrics(date(2024, 6, 14), limit_down_count=4)
        assert _rule_ebb(t, y, None) is False


class TestRuleClimax:
    def test_high_limit_up(self):
        t = make_metrics(date(2024, 6, 15), limit_up_count=200)
        assert _rule_climax(t, None, None) is True

    def test_high_bomb_rate(self):
        t = make_metrics(date(2024, 6, 15), bomb_rate=0.8, bomb_threshold=0.5)
        assert _rule_climax(t, None, None) is True

    def test_high_amplitude(self):
        t = make_metrics(date(2024, 6, 15), top_amplitude=20.0)
        assert _rule_climax(t, None, None) is True

    def test_normal(self):
        t = make_metrics(date(2024, 6, 15))
        assert _rule_climax(t, None, None) is False

    def test_none_amplitude(self):
        """None amplitude should not crash."""
        t = make_metrics(date(2024, 6, 15), top_amplitude=None)
        assert _rule_climax(t, None, None) is False


class TestRuleFerment:
    def test_valid_ferment(self):
        # MIN_LEADER_DAYS = 4, FERMENT_ZT_PERF = 1.5
        t = make_metrics(date(2024, 6, 15), max_limit_days=4, limit_up_count=50, zt_performance=2.0)
        y = make_metrics(date(2024, 6, 14), max_limit_days=4, limit_up_count=40, zt_performance=1.8)
        b = make_metrics(date(2024, 6, 13), max_limit_days=4, limit_up_count=30, zt_performance=1.6)
        assert _rule_ferment(t, y, b) is True

    def test_no_yesterday(self):
        t = make_metrics(date(2024, 6, 15))
        assert _rule_ferment(t, None, None) is False


class TestRuleIce:
    def test_base_ice(self):
        t = make_metrics(date(2024, 6, 15), max_limit_days=1, limit_up_count=5, has_candidate=False)
        assert _rule_ice(t, None, None) is True

    def test_reversal(self):
        t = make_metrics(date(2024, 6, 15), max_limit_days=1, limit_up_count=5, zt_performance=0.2)
        y = make_metrics(date(2024, 6, 14), zt_performance=-0.3)
        assert _rule_ice(t, y, None) is True

    def test_normal(self):
        t = make_metrics(date(2024, 6, 15))
        assert _rule_ice(t, None, None) is False


class TestClassifySeries:
    def test_empty(self):
        assert classify_series([]) == []

    def test_single_day_baseline(self):
        series = [make_metrics(date(2024, 6, 15), limit_up_count=30)]
        result = classify_series(series)
        assert len(result) == 1
        assert result[0].phase == "冰点"
        assert "首日基线" in result[0].reason

    def test_priority_ebb_over_climax(self):
        """退潮优先级 > 高潮（EBB_ZT_PERF = -2.0）。"""
        series = [
            make_metrics(date(2024, 6, 14), limit_up_count=30, zt_performance=0.3),
            make_metrics(date(2024, 6, 15), limit_up_count=200, top_broke=True, zt_performance=-3.0),
        ]
        result = classify_series(series)
        assert result[1].phase == "退潮"

    def test_inherit_previous(self):
        """无规则命中时继承昨日状态。"""
        series = [
            make_metrics(date(2024, 6, 14), max_limit_days=3, limit_up_count=50, zt_performance=0.3),
            make_metrics(date(2024, 6, 15), limit_up_count=30),
        ]
        result = classify_series(series)
        # 第一天是冰点（首日基线），第二天无命中继承
        assert result[1].phase == "冰点"

    def test_buy_window(self):
        series = [make_metrics(date(2024, 6, 15), limit_up_count=200)]
        result = classify_series(series)
        assert result[0].buy_window == "ENHANCED"
        assert result[0].force_liquidate is False

    def test_ebb_force_liquidate(self):
        series = [
            make_metrics(date(2024, 6, 14), top_broke=True, limit_down_count=20),
            make_metrics(date(2024, 6, 15), top_broke=True, limit_down_count=25),
        ]
        result = classify_series(series)
        assert result[1].force_liquidate is True
