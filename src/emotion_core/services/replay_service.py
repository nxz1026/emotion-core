"""胶水层：信号历史回填服务。

职责：逐日调 entry.check_signal(source=REPLAY) 回填历史 signal。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import SignalSource

log = logging.getLogger("emotion_core.replay_service")


def run(start: date, end: date) -> int:
    """回填历史 signal。

    Args:
        start: 起始日期。
        end: 结束日期。

    Returns:
        回填信号数。
    """
    count = 0
    cur = start
    while cur <= end:
        sig = entry.check_signal(cur, SignalSource.REPLAY)
        if sig is not None:
            count += 1
        cur = date(cur.year, cur.month, cur.day + 1)  # 简化，实际用 trading_days
    log.info("replay_service %s~%s: %d 个信号", start, end, count)
    return count
