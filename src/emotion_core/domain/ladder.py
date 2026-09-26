"""梯队与龙头类型（ladder_day 表）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LadderDay:
    """梯队行。连板数（cont_days）由停牌断档不打断（C1）计算。"""
    date: date
    code: str
    cont_days: int          # 连板数
    is_exchange: bool       # 换手板（非一字）
    is_one_word: bool       # 一字板
    is_bomb: bool           # 炸板
    is_sole_top: bool       # 唯一最高板


@dataclass(frozen=True)
class LeaderIdentity:
    """龙头身份。唯一最高板 + 充分换手 = 龙头。"""
    date: date
    code: str
    cont_days: int
    is_exchange: bool
    is_sole_top: bool
