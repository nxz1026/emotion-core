"""胶水层：晋级率服务。

职责：调 algorithms.promotion.promotion_matrix → 写 promotion_day。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import promotion

log = logging.getLogger("emotion_core.promotion_service")


def run(trade_date: date) -> int:
    """计算并写入晋级率矩阵。

    Args:
        trade_date: 交易日。

    Returns:
        写入行数（层数，最多 5 层）。
    """
    n = promotion.persist(trade_date)
    log.info("promotion_service %s: %d 层", trade_date, n)
    return n
