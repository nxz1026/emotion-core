"""胶水层：生态评级服务。

职责：调 algorithms.dragon_env.rate → 写入 market_stat 的
dragon_env / dragon_env_reasons / dragon_env_risks 三列。
封装 data/loader.py 中与生态评级相关的落库函数，
避免 algorithms 层直连 data 层（架构纪律：docs/11 §4）。

依赖方向：algorithms → services → data → domain
"""
from __future__ import annotations

import json
import logging
from datetime import date

from emotion_core.algorithms import accelerate, dragon_env
from emotion_core.data.loader import (
    update_market_stat_ecosystem as _update_market_stat_ecosystem,
)

log = logging.getLogger("emotion_core.ecosystem_service")


def update_market_stat_ecosystem(d: date, rating: str, reasons: str, risks: str) -> int:
    """写入生态评级到 market_stat（代理 data.loader.update_market_stat_ecosystem）。"""
    return _update_market_stat_ecosystem(d, rating, reasons, risks)


def run(trade_date: date) -> str:
    """计算并写入生态评级（评级 + 理由 + 风险，三列一次写全）。

    Args:
        trade_date: 交易日。

    Returns:
        评级词汇（FAVORABLE / NEUTRAL / UNFAVORABLE）。
    """
    # 获取加速事件结果（供评级使用）
    accel = accelerate.detect(trade_date)
    rating = dragon_env.rate(trade_date, accel)

    # 三列一次写全。原实现是内联「UPDATE market_stat SET dragon_env = %s」，
    # 把 rate() 已经算出的 goods/bads 当场丢掉 —— 于是 dragon_env_reasons /
    # dragon_env_risks 建库以来恒为 NULL，面板「生态评级」只剩光杆评级、
    # 既无理由也无风险。这里直接复用 persist() 的同一套序列化（_ser + json），
    # 走 data.loader 的唯一落库口，不再自己拼 UPDATE。
    _update_market_stat_ecosystem(
        trade_date,
        str(rating["rating"]),
        json.dumps(dragon_env._ser(rating.get("goods") or []), ensure_ascii=False),
        json.dumps(dragon_env._ser(rating.get("bads") or []), ensure_ascii=False),
    )

    log.info("ecosystem_service %s: %s", trade_date, rating["rating"])
    return str(rating["rating"])
