"""胶水层：生态评级服务。

职责：调 algorithms.dragon_env.rate → 写入 market_stat.dragon_env。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import dragon_env
from emotion_core.algorithms import accelerate
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.ecosystem_service")


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
