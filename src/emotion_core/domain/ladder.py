"""梯队与龙头类型（ladder_day 表）。

字段与 public.ladder_day 8 列一一对应；is_one_word/is_bomb 属于 derived_bar，
不是梯队口径（梯队的最高层只看 is_exchange 换手板），故不在此类型。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LadderDay:
    """梯队行。连板数（cont_days）由停牌断档不打断（C1）计算。

    is_top / is_sole_top 的判定只取换手板（is_exchange）最高身位：
    一字缩量板不进最高层竞争（lkl/services/ladder.py §1.4）。
    """
    date: date
    code: str
    cont_days: int              # 连板数
    is_exchange: bool           # 换手板（非一字）
    is_top: bool                # 当日换手板最高身位组成员
    is_sole_top: bool           # 唯一最高板（换手组仅 1 只且 >= MIN_LEADER_DAYS）
    y_top_group_count: int      # 昨日最高换手组只数（R2 淘汰赛分母）
    y_top_survivor_count: int   # 昨日最高换手组中今日仍换手的只数（R2 分子）


@dataclass(frozen=True)
class LeaderIdentity:
    """龙头身份。唯一最高板 + 充分换手 = 龙头。"""
    date: date
    code: str
    cont_days: int
    is_exchange: bool
    is_sole_top: bool
