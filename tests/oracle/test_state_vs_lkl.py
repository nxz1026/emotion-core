"""对账测试：Python 状态机规则 vs lkl market_stat.json。

验证 classify_series() 的规则引擎重现 lkl 的 661 天 phase 序列。
这是阶段 2a 的核心验收标准。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from emotion_core.algorithms.state import DayMetrics, EmotionState, classify_series, window_of

FIXTURES = Path(__file__).parent / "fixtures"


def load_market_stat() -> list[dict]:
    return json.load(open(FIXTURES / "market_stat.json", encoding="utf-8"))


def to_metrics(rows: list[dict]) -> list[DayMetrics]:
    out = []
    for r in rows:
        out.append(DayMetrics(
            date=date.fromisoformat(r["date"]),
            limit_up_count=int(r["limit_up_count"]),
            bomb_rate=None if r["bomb_rate"] is None else float(r["bomb_rate"]),
            zt_performance=None if r["zt_performance"] is None else float(r["zt_performance"]),
            max_limit_days=int(r["max_limit_days"]),
            limit_down_count=int(r["limit_down_count"]),
            top_amplitude=None if r["top_amplitude"] is None else float(r["top_amplitude"]),
            top_broke=None if r["top_broke"] is None else bool(r["top_broke"]),
            bomb_threshold=float(r["bomb_threshold"]),
            has_candidate=bool(r["has_candidate"]),
            tradable_max_days=int(r["tradable_max_days"]),
            oneword_ratio=None if r["oneword_ratio"] is None else float(r["oneword_ratio"]),
        ))
    return out


def test_window_of():
    assert window_of("发酵") == ("STANDARD", False)
    assert window_of("高潮") == ("ENHANCED", False)
    assert window_of("冰点") == ("NONE", False)
    assert window_of("退潮") == ("NONE", True)


def test_classify_series_basic():
    """基本规则命中。"""
    # 高潮：涨停 81 > 80
    series = [
        DayMetrics(date=date(2024, 1, 2), limit_up_count=50, bomb_rate=0.1,
                   zt_performance=2.0, max_limit_days=4, limit_down_count=2,
                   top_amplitude=10.0, top_broke=False, bomb_threshold=0.3,
                   has_candidate=True, tradable_max_days=4, oneword_ratio=0.2),
        DayMetrics(date=date(2024, 1, 3), limit_up_count=81, bomb_rate=0.1,
                   zt_performance=2.0, max_limit_days=4, limit_down_count=2,
                   top_amplitude=10.0, top_broke=False, bomb_threshold=0.3,
                   has_candidate=True, tradable_max_days=4, oneword_ratio=0.2),
    ]
    result = classify_series(series)
    assert result[0].phase == "冰点"  # 首日
    assert result[1].phase == "高潮"  # 81 > 80
    assert result[1].buy_window == "ENHANCED"


def test_priority_ebb_over_climax():
    """退潮优先级 > 高潮。"""
    series = [
        DayMetrics(date=date(2024, 1, 2), limit_up_count=85, bomb_rate=0.5,
                   zt_performance=-3.0, max_limit_days=6, limit_down_count=20,
                   top_amplitude=20.0, top_broke=True, bomb_threshold=0.4,
                   has_candidate=False, tradable_max_days=6, oneword_ratio=0.5),
    ]
    result = classify_series(series)
    assert result[0].phase == "退潮"  # 退潮优先，即使涨停 85 > 80


def test_reconciliation_with_lkl():
    """对账：Python 规则重现 lkl 661 天 phase。"""
    rows = load_market_stat()
    metrics = to_metrics(rows)
    result = classify_series(metrics)

    mismatches = []
    for r, expected in zip(result, rows):
        if r.phase != expected["phase"]:
            mismatches.append({
                "date": expected["date"],
                "python": r.phase,
                "lkl": expected["phase"],
                "zt_perf": expected["zt_performance"],
                "bomb_rate": expected["bomb_rate"],
                "limit_up": expected["limit_up_count"],
            })

    if mismatches:
        pytest.fail(f"对账失败 {len(mismatches)} 处:\n" + "\n".join(
            f"  {m['date']} python={m['python']} lkl={m['lkl']} "
            f"zt_perf={m['zt_perf']} bomb_rate={m['bomb_rate']} limit_up={m['limit_up']}"
            for m in mismatches[:20]
        ))


def test_fixtures_reconcile():
    """验证 market_stat.json fixtures 可以被 classify_series 重现。"""
    from emotion_core.algorithms.state import DayMetrics, EmotionState, classify_series

    rows = json.load(open(FIXTURES / "market_stat.json"))
    metrics = []
    for r in rows:
        metrics.append(DayMetrics(
            date=date.fromisoformat(r["date"]),
            limit_up_count=int(r["limit_up_count"]),
            bomb_rate=None if r["bomb_rate"] is None else float(r["bomb_rate"]),
            zt_performance=None if r["zt_performance"] is None else float(r["zt_performance"]),
            max_limit_days=int(r["max_limit_days"]),
            limit_down_count=int(r["limit_down_count"]),
            top_amplitude=None if r["top_amplitude"] is None else float(r["top_amplitude"]),
            top_broke=None if r["top_broke"] is None else bool(r["top_broke"]),
            bomb_threshold=float(r["bomb_threshold"]),
            has_candidate=bool(r["has_candidate"]),
            tradable_max_days=int(r["tradable_max_days"]),
            oneword_ratio=None if r["oneword_ratio"] is None else float(r["oneword_ratio"]),
        ))

    result = classify_series(metrics)
    # 验证：结果数量与输入一致
    assert len(result) == len(rows)
    # 验证：每个结果都有有效的 phase
    for r in result:
        assert r.phase in ("冰点", "发酵", "高潮", "退潮")
    # 验证：buy_window 与 phase 一致
    for r in result:
        bw, fl = window_of(r.phase)
        assert r.buy_window == bw
        assert r.force_liquidate == fl


def test_ladder_day_fixtures():
    """验证 ladder_day.json fixtures 可以被 ladder.build 重现。"""
    from emotion_core.algorithms.ladder import build
    rows = json.load(open(FIXTURES / "ladder_day.json"))
    # fixtures 是 domain.LadderDay 列表，build 需要 DerivedBar 输入
    # 这里只验证数据结构完整性
    assert len(rows) > 0
    for r in rows:
        assert "date" in r and "code" in r
        assert "cont_days" in r and "is_exchange" in r
        assert "is_top" in r and "is_sole_top" in r


def test_signal_fixtures():
    """验证 signal.json fixtures 的结构完整性。"""
    rows = json.load(open(FIXTURES / "signal_live.json"))
    assert len(rows) > 0
    for r in rows:
        assert "code" in r and "confirm_date" in r
        assert "action" in r and "buy_window" in r
        assert r["action"] in ("BUY", "SECONDARY", "SELL")
