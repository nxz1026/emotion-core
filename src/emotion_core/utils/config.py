"""全部阈值常量。策略口径值逐字照 lkl config.py，禁止顺手调参。

纪律：
- 本文件是策略语义的唯一住所，改任何值必须记 ADR。
- config_hash() 每次运行计算，写入 pipeline_state / signal / eval_result。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class Config:
    # ── 数据范围 ──────────────────────────────
    DATA_START: date = date(2024, 1, 1)  # 判定口径起点（数据层保留 2010 起）

    # ── 状态机（lkl emotion.py）──────────────
    CLIMAX_ZT: int = 80  # 高潮：涨停家数 >
    ICE_ZT_MAX: int = 40  # 冰点：涨停家数 < 且最高板 ≤ 3
    BOMB_RATE_FALLBACK: float = 0.42  # 自适应炸板阈值 fallback
    BOMB_RATE_WINDOW: int = 30  # 前 30 交易日 median+σ

    # ── 龙头与信号（lkl entry.py / ladder.py）──
    MIN_LEADER_DAYS: int = 4  # 唯一最高板门槛（可调 3，改了要记 ADR）
    SECONDARY_MIN_DAYS: int = 3  # 次级推荐放宽
    EXCHANGE_TURNOVER_MIN: float = 5.0  # c5 换手强度补偿（百分数）

    # ── 晋级率 ────────────────────────────────
    PROMOTION_MIN_DENOM: int = 3  # 分母不足 → None，不补零（F6）

    # ── 运维 ──────────────────────────────────
    MIN_COVERAGE: float = 0.90  # 覆盖率硬门槛（asel A12）

    # ── 开关 ──────────────────────────────────
    SECONDARY_EXPORT_ENABLED: bool = False  # 次级推荐影子模式
    TRADE_EXPORT_ENABLED: bool = False  # 交易桥默认不导出
    LLM_PROFILE: str | None = None  # None=关；'agnes'=启用


CONFIG = Config()


def config_hash() -> str:
    """每次运行写入 pipeline_state / signal / eval_result（审核文档 S9）。"""
    payload = json.dumps(asdict(CONFIG), default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
