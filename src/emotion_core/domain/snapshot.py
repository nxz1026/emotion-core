"""展示层视图模型（VM）。展示层只读这些类型，不直接拼裸 DB 行。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class MarketSnapshot:
    """直观层首页数据。"""
    date: date
    phase_text: str          # 通俗语言（"上升期"）
    buy_window: str
    limit_up_count: int
    bomb_count: int
    max_height: int
    ecosystem_rating: str
    one_liner: str           # 市场情绪一句话
    sample_n: int = 0


@dataclass(frozen=True)
class LadderSnapshot:
    """逻辑层梯队数据。"""
    date: date
    rows: tuple = field(default_factory=tuple)  # LadderDay 列表


@dataclass(frozen=True)
class SignalSnapshot:
    """直观层推荐数据。"""
    code: str | None
    date: date
    cont_days: int
    is_exchange: bool
    checklist: dict
    odds: dict = field(default_factory=dict)  # 三口径赔率
    sample_n: int = 0


@dataclass(frozen=True)
class StockSnapshot:
    """个股诊断（R2）。"""
    code: str
    name: str
    date: date
    # A 现在是什么
    cont_days: int
    is_exchange: bool
    is_sole_top: bool
    # B 过去干过什么
    leader_history: tuple = field(default_factory=tuple)
    signal_history: tuple = field(default_factory=tuple)
    # C 用它赌值不值
    promotion_rate: float | None = None
    divergence: float | None = None
    sample_n: int = 0
