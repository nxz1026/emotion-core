"""状态机（4 相 + 优先级 + buy_window）。语义逐字照搬 lkl/services/emotion.py。

规则（优先级 退潮 > 高潮 > 发酵 > 冰点，first match wins）：
- 退潮：两天最高板都断了 OR zt_perf < EBB_ZT_PERF OR 跌停激增（≥EBB_LD_MIN 且 >EBB_LD_MULT× 昨日）
- 高潮：limit_up_count > CLIMAX_COUNT OR bomb_rate > bomb_threshold OR top_amplitude > CLIMAX_AMPLITUDE
- 发酵：height ≥ FERMENT_MIN_DAYS AND zt_perf > FERMENT_ZT_PERF 连续两天 AND 涨停数连续两天上升
- 冰点：height ≤ ICE_MAX_DAYS AND count < ICE_COUNT_MAX AND (反转确认 OR 无唯一候选)
- 无命中 → 继承昨日；首日 seed 冰点

window_of: 发酵→STANDARD, 高潮→ENHANCED, 冰点/退潮→NONE（退潮还 force_liquidate）
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/state.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from emotion_core.utils.config import CONFIG


def _lt(a: Optional[float], b: float) -> bool:
    return a is not None and a < b


def _gt(a: Optional[float], b: float) -> bool:
    return a is not None and a > b


@dataclass
class DayMetrics:
    """单日指标（从 derived_bar 计算）。"""
    date: date
    limit_up_count: int
    bomb_rate: Optional[float]
    zt_performance: Optional[float]
    max_limit_days: int
    limit_down_count: int
    top_amplitude: Optional[float]
    top_broke: Optional[bool]
    bomb_threshold: float
    has_candidate: bool
    tradable_max_days: int
    oneword_ratio: Optional[float]


@dataclass
class EmotionState:
    """状态机输出。"""
    date: date
    phase: str  # 冰点/发酵/高潮/退潮
    buy_window: str  # STANDARD/ENHANCED/NONE
    force_liquidate: bool
    reason: str


def _rule_ebb(t: DayMetrics, y: Optional[DayMetrics], b: Optional[DayMetrics]) -> bool:
    two_day = bool(t.top_broke) and y is not None and bool(y.top_broke)
    perf = _lt(t.zt_performance, CONFIG.EBB_ZT_PERF)
    ld = (y is not None and t.limit_down_count > CONFIG.EBB_LD_MIN
          and t.limit_down_count > y.limit_down_count * CONFIG.EBB_LD_MULT)
    return two_day or perf or ld


def _rule_climax(t: DayMetrics, y: Optional[DayMetrics], b: Optional[DayMetrics]) -> bool:
    """高潮：涨停家数 / 炸板率越阈 / 最高板振幅越阈。

    `top_amplitude` 用 `_gt` 护 None（当日无涨停股时 max(amplitude)=NULL →
    indicators 给 None）。此前裸比较 `None > 15.0` 会抛 TypeError——零涨停日
    直接把 emotion.run_range 打崩（本模块与 emotion.classify 共用 _RULES）。
    None 的语义是「没有最高板振幅可比」→ 该分支不触发。
    """
    return (t.limit_up_count > CONFIG.CLIMAX_ZT
            or _gt(t.bomb_rate, t.bomb_threshold)
            or _gt(t.top_amplitude, CONFIG.CLIMAX_AMPLITUDE))


def _rule_ferment(t: DayMetrics, y: Optional[DayMetrics], b: Optional[DayMetrics]) -> bool:
    return (t.max_limit_days >= CONFIG.MIN_LEADER_DAYS and y is not None
            and b is not None
            and _gt(t.zt_performance, CONFIG.FERMENT_ZT_PERF)
            and _gt(y.zt_performance, CONFIG.FERMENT_ZT_PERF)
            and t.limit_up_count > y.limit_up_count > b.limit_up_count)


def _rule_ice(t: DayMetrics, y: Optional[DayMetrics], b: Optional[DayMetrics]) -> bool:
    base = (t.max_limit_days <= CONFIG.ICE_MAX_DAYS
            and t.limit_up_count < CONFIG.ICE_ZT_MAX)
    rev = y is not None and _lt(y.zt_performance, 0) and _gt(t.zt_performance, 0)
    return base and (rev or not t.has_candidate)


_RULES = (
    (_rule_ebb, "退潮"),
    (_rule_climax, "高潮"),
    (_rule_ferment, "发酵"),
    (_rule_ice, "冰点"),
)


def window_of(phase: str) -> tuple[str, bool]:
    if phase == "发酵":
        return "STANDARD", False
    if phase == "高潮":
        return "ENHANCED", False
    return "NONE", phase == "退潮"


def classify_series(series: list[DayMetrics]) -> list[EmotionState]:
    """按优先级逐日推进状态机（纯函数）。"""
    result: list[EmotionState] = []
    for i, t in enumerate(series):
        y = series[i - 1] if i >= 1 else None
        b = series[i - 2] if i >= 2 else None
        fired = next(((rule, name) for rule, name in _RULES if rule(t, y, b)), None)
        phase = fired[1] if fired else (result[-1].phase if result else "冰点")
        buy_window, force_liq = window_of(phase)
        tag = "·首日基线" if (fired is None and y is None) else (
            "·延续" if (fired is None and y is not None) else "")
        result.append(EmotionState(
            date=t.date, phase=phase, buy_window=buy_window,
            force_liquidate=force_liq,
            reason=f"{phase}{tag}(涨停{t.limit_up_count} 最高{t.max_limit_days}板)",
        ))
    return result
