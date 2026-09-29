"""胶水层：生态评级服务。

职责：调 algorithms.dragon_env.rate → 写入 market_stat.dragon_env。
封装 data/loader.py 中与生态评级相关的落库函数，
避免 algorithms 层直连 data 层（架构纪律：docs/11 §4）。

依赖方向：algorithms → services → data → domain
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import dragon_env
from emotion_core.algorithms import accelerate
from emotion_core.data.loader import update_market_stat_ecosystem as _update_market_stat_ecosystem
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.ecosystem_service")


def update_market_stat_ecosystem(d: date, rating: str, reasons: str, risks: str) -> int:
    """写入生态评级到 market_stat（代理 data.loader.update_market_stat_ecosystem）。"""
    return _update_market_stat_ecosystem(d, rating, reasons, risks)


def run(trade_date: date) -> str:
    """计算并写入生态评级。

    Args:
        trade_date: 交易日。

    Returns:
        评级词汇（FAVORABLE / NEUTRAL / UNFAVORABLE）。
    """
    # 获取加速事件结果（供评级使用）
    accel = accelerate.detect(trade_date)
    rating = dragon_env.rate(trade_date, accel)

    # 写入 market_stat.dragon_env
    with transaction() as conn:
        conn.execute(
            "UPDATE market_stat SET dragon_env = %s WHERE date = %s",
            (rating["rating"], trade_date),
        )

    log.info("ecosystem_service %s: %s", trade_date, rating["rating"])
    return rating["rating"]
