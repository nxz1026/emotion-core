"""信号类型（signal 表）。5 条件 checklist + 1 警告。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SECONDARY = "SECONDARY"
    SELL = "SELL"


class SignalSource(str, Enum):
    """信号来源。replay=历史回填，live=实盘。"""
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True)
class Checklist:
    """5 条件 + 1 警告。None 表示 UNKNOWN（不可核验）。"""
    c1_uniqueness: bool | None      # 唯一换手高标
    c2_exchange: bool | None         # 换手板非一字
    c3_elimination: bool | None      # 淘汰赛身份（集合相等判定）
    c4_min_days: bool | None         # 最低板数门槛
    c5_strength_diverge: bool | None # 强度与分歧补偿（仅 ENHANCED）
    w1_crowding: bool | None         # 警告：同身位扎堆（永不否决）

    @property
    def passed(self) -> bool:
        """无否决条件即通过（filter None 后 all True）。

        ⚠️ 口径：**UNKNOWN(None) 不否决**，五项全 None 时返回 ``True``。
        这不是「空真 bug」，是显式契约——`algorithms/entry.passed_of` 的
        docstring 写着「过滤 None 后全 True 才算过（与回测一致）」，而回测侧
        一直是同一口径；Rust 侧 `entry::passed_of` 亦然。三边必须一致，
        `tests/oracle/test_exit_rust_vs_python.py:89` 断言的就是这个：
        卖出信号的 checklist 六项恒 UNKNOWN，`passed is True`。
        改动前请先看这条。

        「不可核验」这个第三态由**上层**表达，不由本属性表达：
        `services/diagnose_service.py:280` 在构造不出 checklist 时给出
        ``None``，前端据此渲染「不可核验」而非「全过」。
        """
        vals = [self.c1_uniqueness, self.c2_exchange, self.c3_elimination,
                self.c4_min_days, self.c5_strength_diverge]
        return all(v is True for v in vals if v is not None)


@dataclass(frozen=True)
class Signal:
    """信号。"""
    code: str
    date: date
    action: Action
    checklist: Checklist
    source: SignalSource
    status: str = "SUGGESTED"  # SUGGESTED / ADOPTED / EXPORTED
