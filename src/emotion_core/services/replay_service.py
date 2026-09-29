"""胶水层：信号历史回填服务。

职责：逐日调 entry.check_signal(source=REPLAY) 回填历史 signal。
使用 trading_days 工具函数遍历交易日，避免日历日迭代。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import SignalSource
from emotion_core.utils.dates import trading_days

log = logging.getLogger("emotion_core.replay_service")


def run(start: date, end: date) -> int:
    """回填历史 signal。

    Args:
        start: 起始日期。
        end: 结束日期。

    Returns:
        回填信号数。
    """
    days = trading_days(start, end)
    count = 0
    for cur in days:
        sig = entry.check_signal(cur, SignalSource.REPLAY)
        if sig is not None:
            count += 1
    log.info("replay_service %s~%s: %d/%d 个信号", start, end, count, len(days))
    return count
