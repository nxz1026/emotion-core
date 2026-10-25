"""变体参数对象（P2 搜索空间 A 档，docs/20 §4）。

一个 `LabRules` = 一个可回放的变体：状态机阈值 + entry 门槛 + 档位权重。
**默认值逐项取自 `CONFIG` / `entry` 的现行常量**，所以默认变体必须复刻主线
（`tests/unit/test_lab_replay.py::test_baseline_matches_signal_table` 钉住）。

为什么不做成 `CONFIG` 的字段：`config_hash()` 是对 `asdict(CONFIG)` 的 sha256，
加字段等于让全库流水线指纹换代（pipeline_state / signal / eval_result 全变）。
所以 Lab 自持一套参数对象，主线零改动。

**不进搜索空间的项**（写在这里是为了避免以后被当成"漏掉的旋钮"）：

- c2 一字板过滤：`ladder_day` 只有换手板进得了最高组（`ladder.top_group` 过滤
  `is_exchange`），一字板根本没有可成交价，放开它等于回测一个买不到的价位。
- c1 唯一高标：候选定义就是它，不是参数。
- `window_of` 的相位→窗口映射：退潮/发酵 STANDARD、高潮 ENHANCED、冰点 NONE 是
  ADR 0002 D2 的定档结论，不是可搜参数（B 档「相位阈值」只允许观测）。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from emotion_core.algorithms import entry, state
from emotion_core.utils.config import CONFIG

#: 档位倍率键（与 entry.TIER_* 对应，frozen dataclass 不能用 dict 字段比较）
_TIER_MULT = {
    entry.TIER_STRICT: "tier_mult_strict",
    entry.TIER_INHERIT: "tier_mult_inherit",
    entry.TIER_NEWCOMER: "tier_mult_newcomer",
}


@dataclass(frozen=True)
class LabRules:
    """一个变体的全部可调参数。"""

    # —— 状态机（直接喂 `state.StateRules`）——
    ebb_zt_perf: float = CONFIG.EBB_ZT_PERF
    ebb_ld_min: int = CONFIG.EBB_LD_MIN
    ebb_ld_mult: float = CONFIG.EBB_LD_MULT
    climax_zt: int = CONFIG.CLIMAX_ZT
    climax_amplitude: float = CONFIG.CLIMAX_AMPLITUDE
    ferment_zt_perf: float = CONFIG.FERMENT_ZT_PERF
    ferment_min_days: int = CONFIG.MIN_LEADER_DAYS
    ice_max_days: int = CONFIG.ICE_MAX_DAYS
    ice_zt_max: int = CONFIG.ICE_ZT_MAX

    # —— entry 门槛 ——
    min_leader_days: int = CONFIG.MIN_LEADER_DAYS  # c4 + 唯一高标选择
    diverge_min_turnover: float = CONFIG.EXCHANGE_TURNOVER_MIN  # c5 换手补偿

    # —— 档位权重倍率（乘在 position_tier 的基准权重上，0.0 = 该档不参与）——
    tier_mult_strict: float = 1.0
    tier_mult_inherit: float = 1.0
    tier_mult_newcomer: float = 1.0

    # ---- 派生 ----

    def state_rules(self) -> state.StateRules:
        """投影成状态机参数对象（同名字段一一对应）。"""
        names = {f.name for f in fields(state.StateRules)}
        return state.StateRules(
            **{f.name: getattr(self, f.name) for f in fields(self) if f.name in names}
        )

    def is_open(self, phase: str, diverge: bool, tier: str) -> bool:
        """该 (相位, 分歧降级, 身份档) 是否进观察名单（= 主线 weight × 变体倍率 > 0）。"""
        w = entry.position_tier(phase, diverge, tier).weight
        return w * getattr(self, _TIER_MULT[tier]) > 0

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def replace(self, **kw: Any) -> LabRules:
        return replace(self, **kw)

    def key(self) -> tuple[tuple[str, Any], ...]:
        """可哈希的参数指纹（去重/缓存用）。"""
        return tuple(sorted(self.as_dict().items()))


#: 基线变体 = 主线现行口径
BASELINE = LabRules()

#: 搜索网格（A 档；每项都是 "改一个会动到哪" 说得清的量）
GRID: dict[str, tuple[Any, ...]] = {
    "min_leader_days": (3, 4, 5),
    "diverge_min_turnover": (3.0, 4.0, 5.0, 6.0),
    "climax_zt": (70, 80, 90),
    "ebb_zt_perf": (-3.0, -2.0, -1.0),
    "ferment_zt_perf": (1.0, 1.5, 2.0),
    "ice_zt_max": (30, 40, 50),
    "tier_mult_strict": (1.0, 0.0),
    "tier_mult_newcomer": (1.0, 0.5, 0.0),
}


def variants(limit: int | None = None) -> list[LabRules]:
    """基线 + 单点变体（一次只改一个参数），再按 `limit` 截断。

    单点变体是刻意的：A 档 8 个旋钮的组合爆炸（3·4·3·3·3·3·2·3 = 5832）里，
    绝大多数是「两个参数同时变」的噪声。先做一步爬坡，验证有效方向后再叠加。
    """
    out: list[LabRules] = [BASELINE]
    for name, values in GRID.items():
        for v in values:
            if getattr(BASELINE, name) == v:
                continue
            out.append(BASELINE.replace(**{name: v}))
    return out if limit is None else out[:limit]
