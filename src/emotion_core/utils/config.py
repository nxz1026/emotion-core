"""全部阈值常量。策略口径值逐字照 lkl config.py，禁止顺手调参。

纪律：
- 本文件是策略语义的唯一住所，改任何值必须记 ADR。
- config_hash() 每次运行计算，写入 pipeline_state / signal / eval_result。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
import os


@dataclass(frozen=True)
class Config:
    # ── 数据范围 ──────────────────────────────
    DATA_START: date = date(2024, 1, 1)  # 判定口径起点（数据层保留 2010 起）

    # ── 标的池（lkl config.py BOARD_PREFIXES / NEW_ISSUER_FILTER）──
    # D1/R3：梯队与龙头限主板 10%；情绪计数仍走全市场（不在本文件控制）。
    BOARD_PREFIXES: tuple[str, ...] = ("600", "601", "603", "605", "000", "001", "002")
    LIMIT_RATIO: float = 0.10
    LIMIT_PCT_BY_PREFIX: dict[str, int] = field(
        default_factory=lambda: {"68": 120, "30": 120})
    LIMIT_PCT_DEFAULT: int = 110       # round((1 + LIMIT_RATIO) * 100)
    NEW_ISSUER_MIN_DAYS: int = 90   # 次新排除：首个 bar 距目标日不足 90 自然日
    # 次新豁免地板 = **数据层最早 bar 日期**（不变式：地板 ≥ daily_bar 的 min(date)）。
    # 语义：first_bar_date <= GREATEST(目标日-90d, 地板) 视作老股豁免。
    # 原值 date(2023,11,26) 是 lkl 的数据起点，被逐字搬到窗口不同的本库
    # （daily_bar 最早 2024-01-02）→ 2024-01-02~2024-03-29 共 58 个交易日里
    # GREATEST(...) < first_bar_date，**每只股票都算次新**，情绪计数全 0
    # （实测 58 天 limit_up_count=0；2024-04-01 起 target-90d 超过数据起点，恢复 78）。
    # 取 EM 日线首日 2024-01-02：窗口首日入池的老股（无法区分上市日）全部豁免，
    # 之后上市的真次新仍按 90 自然日剔除。
    NEW_ISSUER_FLOOR: date = date(2024, 1, 2)
    # C7：北交所不参与情绪判定（数据层保留 daily_bar / stock_basic）。
    # 代码形态：6 位纯代码 4/83/87/88 开头、920 开头（新代码段），及 akshare 的 bj 前缀。
    BSE_EXCLUDED_PREFIXES: tuple[str, ...] = ("4", "8", "920", "bj")

    # ── 状态机（lkl emotion.py）──────────────
    CLIMAX_ZT: int = 80              # 高潮：涨停家数 >
    CLIMAX_AMPLITUDE: float = 15.0  # 高潮：最高板振幅 ≥(%)
    FERMENT_ZT_PERF: float = 1.5    # 发酵：昨涨停表现 ≥(%)
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

    # ── 状态机额外阈值（lkl emotion.py）──────────────
    EBB_ZT_PERF: float = -2.0              # 退潮：昨涨停表现 ≤ (%)
    EBB_LD_MIN: int = 15                   # 退潮：跌停数绝对下限
    EBB_LD_MULT: float = 2.0               # 退潮：跌停数 ≥ 昨日的 EBB_LD_MULT 倍
    ICE_MAX_DAYS: int = 3                  # 冰点持续上限
    DIVERGE_NEG_MIN: int = 3               # 高潮分歧降级：负反馈 ≥
    DIVERGE_WINDOW: str = "NONE"           # 分歧降级窗口

    # ── 开关 ──────────────────────────────────
    SECONDARY_EXPORT_ENABLED: bool = False  # 次级推荐影子模式
    TRADE_EXPORT_ENABLED: bool = False  # 交易桥默认不导出
    LLM_PROFILE: str | None = None  # None=关；'agnes'=启用

    # ── 个股诊断（展示层只读功能，不在信号链上）────────
    # 与 LLP_PROFILE 分开：信号链 LLM 仍默认关，个股页可单独开/关。
    # 'off'/'' → 只出规则层结论（页面明示 LLM 未启用）。
    STOCK_LLM_PROFILE: str = os.environ.get("EC_STOCK_LLM_PROFILE", "smart")
    STOCK_STAT_CACHE_SEC: int = int(os.environ.get("EC_STOCK_STAT_CACHE_SEC", "60"))
    STOCK_TREND_DAYS: int = int(os.environ.get("EC_STOCK_TREND_DAYS", "30"))
    STOCK_LLM_MAX_TOKENS: int = int(os.environ.get("EC_STOCK_LLM_MAX_TOKENS", "800"))

    # ── 策略观察台 ─────────────────────────────
    STRATEGY_ENABLED: bool = os.environ.get("EC_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes")
    STRATEGY_PROFILE: str = os.environ.get("EC_STRATEGY_PROFILE", "agnes")
    STRATEGY_MAX_TOKENS: int = int(os.environ.get("EC_STRATEGY_MAX_TOKENS", "8192"))
    STRATEGY_MAX_LLM: int = int(os.environ.get("EC_STRATEGY_MAX_LLM", "50"))
    # 注意：调用侧的定速/退避/熔断参数**故意不在这里**。config_hash() 哈希
    # asdict(CONFIG) 的全字段，任何新增键都会让 pipeline_state / signal /
    # eval_result 里的策略指纹换代（同 theme_service.py:31、notify.py:31 的告警）。
    # 这几个旋钮住在 services/strategy/runner.py 的模块级常量，env 同名覆盖。
    STRATEGY_MAX_UNIVERSE: int = int(os.environ.get("EC_STRATEGY_MAX_UNIVERSE", "80"))
    STRATEGY_HOT_N: int = int(os.environ.get("EC_STRATEGY_HOT_N", "20"))
    STRATEGY_VERSION: str = os.environ.get("EC_STRATEGY_VERSION", "emotion-core-dev")
    STRATEGY_REPORTS_DIR: str = os.environ.get("EC_STRATEGY_REPORTS_DIR", "/home/ubuntu/DSH/longkonglong/reports")


CONFIG = Config()


def config_hash() -> str:
    """每次运行写入 pipeline_state / signal / eval_result（审核文档 S9）。"""
    payload = json.dumps(asdict(CONFIG), default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
