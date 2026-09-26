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
        """全过（filter None 然后 all）。"""
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
