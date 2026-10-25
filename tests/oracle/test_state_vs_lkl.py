"""对账测试：Python 状态机规则 vs lkl market_stat.json。

验证 classify_series() 的规则引擎重现 lkl 的 661 天 phase 序列。
这是阶段 2a 的核心验收标准。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from emotion_core.algorithms import state
from emotion_core.algorithms.state import (
    DayMetrics,
    classify_series,
    window_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_market_stat() -> list[dict]:
    return json.load(open(FIXTURES / "market_stat.json", encoding="utf-8"))


def to_metrics(rows: list[dict]) -> list[DayMetrics]:
    out = []
    for r in rows:
        out.append(
            DayMetrics(
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
            )
        )
    return out


def test_window_of():
    assert window_of("发酵") == ("STANDARD", False)
    assert window_of("高潮") == ("ENHANCED", False)
    assert window_of("冰点") == ("NONE", False)
    # W7′ 有意偏离 lkl 原口径：退潮解禁（实测定档 _audit/2026-10-21-修正方案-实测定档.md）。
    # lkl 原文 ("NONE", True) 的依据是「退潮=亏钱效应」，但口径B 实测退潮日每信号期望
    # +2.31%/胜率 77%（优于高潮 ENHANCED +1.48%/68%），该前提不成立。
    assert window_of("退潮") == ("STANDARD", False)


def test_classify_series_basic():
    """基本规则命中。"""
    # 高潮：涨停 81 > 80
    series = [
        DayMetrics(
            date=date(2024, 1, 2),
            limit_up_count=50,
            bomb_rate=0.1,
            zt_performance=2.0,
            max_limit_days=4,
            limit_down_count=2,
            top_amplitude=10.0,
            top_broke=False,
            bomb_threshold=0.3,
            has_candidate=True,
            tradable_max_days=4,
            oneword_ratio=0.2,
        ),
        DayMetrics(
            date=date(2024, 1, 3),
            limit_up_count=81,
            bomb_rate=0.1,
            zt_performance=2.0,
            max_limit_days=4,
            limit_down_count=2,
            top_amplitude=10.0,
            top_broke=False,
            bomb_threshold=0.3,
            has_candidate=True,
            tradable_max_days=4,
            oneword_ratio=0.2,
        ),
    ]
    result = classify_series(series)
    assert result[0].phase == "冰点"  # 首日
    assert result[1].phase == "高潮"  # 81 > 80
    assert result[1].buy_window == "ENHANCED"


def test_priority_ebb_over_climax():
    """退潮优先级 > 高潮。"""
    series = [
        DayMetrics(
            date=date(2024, 1, 2),
            limit_up_count=85,
            bomb_rate=0.5,
            zt_performance=-3.0,
            max_limit_days=6,
            limit_down_count=20,
            top_amplitude=20.0,
            top_broke=True,
            bomb_threshold=0.4,
            has_candidate=False,
            tradable_max_days=6,
            oneword_ratio=0.5,
        ),
    ]
    result = classify_series(series)
    assert result[0].phase == "退潮"  # 退潮优先，即使涨停 85 > 80


def _old_ebb(t, y, b, r=state.DEFAULT_RULES):
    """N4 之前的退潮判据：两连炸板**不需要**跌停潮。仅用于证明分歧来源。"""
    two_day = bool(t.top_broke) and y is not None and bool(y.top_broke)
    perf = t.zt_performance is not None and t.zt_performance < r.ebb_zt_perf
    ld = (
        y is not None
        and t.limit_down_count > r.ebb_ld_min
        and t.limit_down_count > y.limit_down_count * r.ebb_ld_mult
    )
    return two_day or perf or ld


def test_reconciliation_with_lkl(monkeypatch):
    """对账：把退潮判据换回 N4 之前的口径，Python 必须逐日重现 lkl 661 天 phase。

    N4（2026-10-23）之后本仓的退潮判据有意收紧，默认口径**不再**与 lkl 逐日相等；
    那条分歧由下一个用例逐条解释。这里用旧判据证明「除 N4 外没有别的漂移」。
    """
    monkeypatch.setattr(
        state,
        "_RULES",
        tuple(
            ((_old_ebb if rule is state._rule_ebb else rule), name) for rule, name in state._RULES
        ),
    )
    rows = load_market_stat()
    metrics = to_metrics(rows)
    result = classify_series(metrics)

    mismatches = []
    for r, expected in zip(result, rows, strict=False):
        if r.phase != expected["phase"]:
            mismatches.append(
                {
                    "date": expected["date"],
                    "python": r.phase,
                    "lkl": expected["phase"],
                    "zt_perf": expected["zt_performance"],
                    "bomb_rate": expected["bomb_rate"],
                    "limit_up": expected["limit_up_count"],
                }
            )

    if mismatches:
        pytest.fail(
            f"对账失败 {len(mismatches)} 处:\n"
            + "\n".join(
                f"  {m['date']} python={m['python']} lkl={m['lkl']} "
                f"zt_perf={m['zt_perf']} bomb_rate={m['bomb_rate']} limit_up={m['limit_up']}"
                for m in mismatches[:20]
            )
        )


def test_n4_divergence_is_only_ebb_tightening():
    """N4 之后与 lkl 的唯一分歧：lkl 判退潮、本仓不判退潮（两连炸板但无跌停潮）。

    lkl 那套「两连炸板就退潮」在 677 天里误标了 72 天（当日跌停中位 7 家、
    涨停表现 +0.57%、次日高度不降率 80.6%），实测定档见 ADR 0002「N4」节。
    """
    rows = load_market_stat()
    metrics = to_metrics(rows)
    result = classify_series(metrics)

    bad = [(r.phase, m["phase"], m["date"]) for r, m in zip(result, rows, strict=False) if r.phase != m["phase"]]  # noqa: E501
    # 只允许「lkl 说退潮、我们现在不说退潮」这一种分歧
    wrong_way = [b for b in bad if b[1] != "退潮" or b[0] == "退潮"]
    assert wrong_way == [], f"出现了 N4 解释不了的分歧：{wrong_way[:5]}"
    # 分歧天数钉住（换判据就要显式改这一行并写理由）
    assert len(bad) == 85, f"分歧天数变了：{len(bad)}（费率/判据改动？先看 ADR 0002 N4）"
