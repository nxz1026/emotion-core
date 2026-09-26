"""市场状态类型。状态机输出（market_stat 表）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Phase(str, Enum):
    """情绪周期 4 相。优先级：退潮 > 高潮 > 发酵 > 冰点。"""
    ICE = "冰点"
    FERMENT = "发酵"
    CLIMAX = "高潮"
    EBB = "退潮"


class BuyWindow(str, Enum):
    """买入窗口。"""
    STANDARD = "STANDARD"
    ENHANCED = "ENHANCED"
    NONE = "NONE"


@dataclass(frozen=True)
class MarketStat:
    """状态机输出（market_stat 表）。

    口径：
    - phase：4 相，优先级 退潮 > 高潮 > 发酵 > 冰点，无命中继承昨日
    - buy_window：发酵→STANDARD，高潮→ENHANCED，冰点/退潮→NONE
    - force_liquidate：退潮时为 True
    - limit_up_count：涨停家数（全市场剔 ST 次新，不含北交所 C7）
    """
    date: date
    phase: Phase
    buy_window: BuyWindow
    limit_up_count: int
    bomb_count: int
    limit_down_count: int
    max_height: int          # 最高板数
    force_liquidate: bool    # 退潮清仓
    # 生态评级
    dragon_env: str = ""    # G1-G4 / B1-B5
    dragon_env_reasons: str = ""
    dragon_env_risks: str = ""
    # 加速
    accel_reason: str = ""
    # 质量
    has_candidate: bool = False
    bomb_threshold: float = 0.42
    # 版本
    config_hash: str = ""
