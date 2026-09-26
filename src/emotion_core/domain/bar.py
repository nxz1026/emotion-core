"""行情数据类型。价格一律用分（int cents），消灭浮点。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Bar:
    """原始日线。

    口径：
    - open_cents/high_cents/low_cents/close_cents/pre_close_cents：分（int）
    - volume：手（int），非股
    - turnover_rate：百分数（float），5.23 表示 5.23%
    - pre_close_cents：存列（不是 LAG 重算），除权日不漂移
    """
    code: str
    date: date
    open_cents: int
    high_cents: int
    low_cents: int
    close_cents: int
    pre_close_cents: int
    volume: int  # 手
    turnover_rate: float  # 百分数

    @property
    def is_new_listing(self) -> bool:
        """是否次新股（上市不足 N 天）。"""
        # 占位：实际由 data.db.is_new_listing 判断
        return False


@dataclass(frozen=True)
class DerivedBar:
    """判据输出（derived_bar 表）。

    三态质量：status ∈ {limit_up, not_limit_up, unknown}；「未知」≠「未涨停」。
    """
    code: str
    date: date
    # 判据
    is_limit_up: bool
    is_limit_down: bool
    is_one_word: bool      # 一字板（low == limit_up_price）
    is_exchange: bool      # 换手板（is_limit_up AND low < limit_up_price）
    is_bomb: bool          # 炸板（touched_limit AND NOT is_limit_up）
    touched_limit: bool    # 曾触板（high >= limit_up_price）
    # 连板
    cont_days: int         # 连板数（停牌断档不打断，C1）
    # 振幅
    amplitude: float
    # 质量
    quality: str = "valid"  # valid | unknown
