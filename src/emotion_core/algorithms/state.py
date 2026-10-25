"""状态机（4 相 + 优先级 + buy_window）。语义逐字照搬 lkl/services/emotion.py。

规则（优先级 退潮 > 高潮 > 发酵 > 冰点，first match wins）：
- 退潮：两天最高板都断了 OR zt_perf < EBB_ZT_PERF OR 跌停激增（≥EBB_LD_MIN 且 >EBB_LD_MULT× 昨日）
- 高潮：limit_up_count > CLIMAX_COUNT OR bomb_rate > bomb_threshold OR top_amplitude > CLIMAX_AMPLITUDE
- 发酵：height ≥ FERMENT_MIN_DAYS AND zt_perf > FERMENT_ZT_PERF 连续两天 AND 涨停数连续两天上升
- 冰点：height ≤ ICE_MAX_DAYS AND count < ICE_COUNT_MAX AND (反转确认 OR 无唯一候选)
- 无命中 → 继承昨日；首日 seed 冰点

window_of: 发酵→STANDARD, 高潮→ENHANCED, 退潮→STANDARD（解禁，见下）, 冰点→NONE

W7′ 退潮解禁（2026-10-21 实测定档，_audit/2026-10-21-修正方案-实测定档.md）：
旧口径把「退潮」映射为 buy_window=NONE + force_liquidate=True（禁买 + 清仓）。全史
实测（口径B T+1封板价买→T+2开盘卖，费后）退潮 n=169 每信号期望 **+2.31%**、封板后
mean +5.35%、胜率 77%，比高潮 ENHANCED（+1.48%/68%）**更好**；退潮日次日连板高度
延续率 0.647（n=289）是全相位最高。且退潮→次日隔夜 +3.53%/73%（n=168）。
⇒ 退潮既不该禁买（砍掉 169 笔正期望），也不该清仓（清仓是反指）。本函数改为：
- 退潮 → ("STANDARD", False)：可买、不要求 ENHANCED 的强度补偿（c5 不适用）；
- force_liquidate 恒 False：不再有任何相位强制清仓（卖出只看断板/买点失效）；
- 冰点 → ("NONE", False)：唯一禁买相位（样本 n=3、封板率 0%，不参与）。
仓位差异不再由窗口承担，改由 entry.c3_tier（三档）+ phase 决定（见 entry.position_tier）。
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/state.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date

from emotion_core.utils.config import CONFIG


@dataclass(frozen=True)
class StateRules:
    """相位判定的可替换参数（P2：Lab 搜索用；主线不传就是用现行口径）。

    默认值逐项取自 `CONFIG`，所以 `classify_series(series)` 与改版前**逐字等价**
    （`tests/caliber/test_state_rules_default.py` 钉住）。Lab 用它物化变体：
    只换这一个对象，就能在内存里重跑 675 天的相位判定，既不碰 `CONFIG`
    （`config_hash()` 不变），也不写任何生产表。
    """

    ebb_zt_perf: float = CONFIG.EBB_ZT_PERF
    ebb_ld_min: int = CONFIG.EBB_LD_MIN
    ebb_ld_mult: float = CONFIG.EBB_LD_MULT
    climax_zt: int = CONFIG.CLIMAX_ZT
    climax_amplitude: float = CONFIG.CLIMAX_AMPLITUDE
    ferment_zt_perf: float = CONFIG.FERMENT_ZT_PERF
    ferment_min_days: int = CONFIG.MIN_LEADER_DAYS
    ice_max_days: int = CONFIG.ICE_MAX_DAYS
    ice_zt_max: int = CONFIG.ICE_ZT_MAX

    def replace(self, **kw: object) -> StateRules:
        """显式替换（frozen dataclass 用 dataclasses.replace 也行，这里省个 import）。"""
        from dataclasses import replace as _replace

        return _replace(self, **kw)

    def as_dict(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: 主线现行口径（默认值），Lab 的对照基线也用它
DEFAULT_RULES = StateRules()


def _lt(a: float | None, b: float) -> bool:
    return a is not None and a < b


def _gt(a: float | None, b: float) -> bool:
    return a is not None and a > b


@dataclass
class DayMetrics:
    """单日指标（从 derived_bar 计算）。"""

    date: date
    limit_up_count: int
    bomb_rate: float | None
    zt_performance: float | None
    max_limit_days: int
    limit_down_count: int
    top_amplitude: float | None
    top_broke: bool | None
    bomb_threshold: float
    has_candidate: bool
    tradable_max_days: int
    oneword_ratio: float | None


@dataclass
class EmotionState:
    """状态机输出。"""

    date: date
    phase: str  # 冰点/发酵/高潮/退潮
    buy_window: str  # STANDARD/ENHANCED/NONE
    force_liquidate: bool
    reason: str


def _rule_ebb(
    t: DayMetrics, y: DayMetrics | None, b: DayMetrics | None, r: StateRules = DEFAULT_RULES
) -> bool:
    """退潮：断板 + 跌停潮，或涨停表现转负，或跌停倍增。

    ★N4（2026-10-23）：`two_day` 原来只要求「最高板连着两天炸板」就成立，
    这是三条里最松的一条 —— 一次高标炸板就能拉响清仓式退潮（675 天里
    高潮↔退潮来回切 182 次）。现在加了「当天跌停 > ebb_ld_min」这道闸：
    炸板归炸板，没有跌停潮就不算退潮，只是「高标换了」。

    实测依据（677 天，`_audit/probe_n4_ebb.py` + `probe_n4_ebb2.py`）：
    旧口径退潮 292 天，收紧后 220 天，去掉的 72 天**全部**是「只有 two_day
    一条成立」的日子（当日跌停中位 7 家、涨停表现中位 +0.57%、次日高度不降率
    80.6%，反而高于仍判退潮那 220 天的 75.3%）—— 它们本来还算稳，是被这条
    最松的分支误标成退潮的。高潮↔退潮来回切从 180 次降到 149 次。
    代价如实记：这 72 天里有 4 天当日跌停 >15（涨停表现仍有 +0.9%），失去标签。
    另：全样本 151 天跌停 >15 里，新旧口径都各自漏判 35 天 / 39 天（ld 那条
    要求「比昨日翻倍」，单日跌停潮抓不到）—— 这是既存问题，不在 N4 范围。
    """
    two_day = (
        bool(t.top_broke)
        and y is not None
        and bool(y.top_broke)
        and t.limit_down_count > r.ebb_ld_min
    )
    perf = _lt(t.zt_performance, r.ebb_zt_perf)
    ld = (
        y is not None
        and t.limit_down_count > r.ebb_ld_min
        and t.limit_down_count > y.limit_down_count * r.ebb_ld_mult
    )
    return two_day or perf or ld


def _rule_climax(
    t: DayMetrics, y: DayMetrics | None, b: DayMetrics | None, r: StateRules = DEFAULT_RULES
) -> bool:
    """高潮：涨停家数 / 炸板率越阈 / 最高板振幅越阈。

    `top_amplitude` 用 `_gt` 护 None（当日无涨停股时 max(amplitude)=NULL →
    indicators 给 None）。此前裸比较 `None > 15.0` 会抛 TypeError——零涨停日
    直接把 emotion.run_range 打崩（本模块与 emotion.classify 共用 _RULES）。
    None 的语义是「没有最高板振幅可比」→ 该分支不触发。
    """
    return (
        t.limit_up_count > r.climax_zt
        or _gt(t.bomb_rate, t.bomb_threshold)
        or _gt(t.top_amplitude, r.climax_amplitude)
    )


def _rule_ferment(
    t: DayMetrics, y: DayMetrics | None, b: DayMetrics | None, r: StateRules = DEFAULT_RULES
) -> bool:
    return (
        t.max_limit_days >= r.ferment_min_days
        and y is not None
        and b is not None
        and _gt(t.zt_performance, r.ferment_zt_perf)
        and _gt(y.zt_performance, r.ferment_zt_perf)
        and t.limit_up_count > y.limit_up_count > b.limit_up_count
    )


def _rule_ice(
    t: DayMetrics, y: DayMetrics | None, b: DayMetrics | None, r: StateRules = DEFAULT_RULES
) -> bool:
    base = t.max_limit_days <= r.ice_max_days and t.limit_up_count < r.ice_zt_max
    rev = y is not None and _lt(y.zt_performance, 0) and _gt(t.zt_performance, 0)
    return base and (rev or not t.has_candidate)


_RULES = (
    (_rule_ebb, "退潮"),
    (_rule_climax, "高潮"),
    (_rule_ferment, "发酵"),
    (_rule_ice, "冰点"),
)


def window_of(phase: str) -> tuple[str, bool]:
    """相位 → (buy_window, force_liquidate)。

    W7′ 改版：退潮解禁为 STANDARD，force_liquidate 恒 False（详见模块文档）。
    冰点是唯一 NONE（禁买）。返回二元组形状不变，oracle 与调用方无需改签名。
    """
    if phase == "发酵":
        return "STANDARD", False
    if phase == "高潮":
        return "ENHANCED", False
    if phase == "退潮":
        return "STANDARD", False
    return "NONE", False  # 冰点：禁买，不清仓（无持仓可清）


def classify_series(
    series: list[DayMetrics], rules: StateRules | None = None
) -> list[EmotionState]:
    """按优先级逐日推进状态机（纯函数）。

    `rules=None` ⇒ 用 `DEFAULT_RULES`（= CONFIG 现值），与改版前逐字等价；
    Lab 传自己的 `StateRules` 即得到变体的相位序列（不读库、不写库）。
    """
    r = rules or DEFAULT_RULES
    result: list[EmotionState] = []
    for i, t in enumerate(series):
        y = series[i - 1] if i >= 1 else None
        b = series[i - 2] if i >= 2 else None
        fired = next(((rule, name) for rule, name in _RULES if rule(t, y, b, r)), None)
        phase = fired[1] if fired else (result[-1].phase if result else "冰点")
        buy_window, force_liq = window_of(phase)
        tag = (
            "·首日基线"
            if (fired is None and y is None)
            else ("·延续" if (fired is None and y is not None) else "")
        )
        result.append(
            EmotionState(
                date=t.date,
                phase=phase,
                buy_window=buy_window,
                force_liquidate=force_liq,
                reason=f"{phase}{tag}(涨停{t.limit_up_count} 最高{t.max_limit_days}板)",
            )
        )
    return result
