"""胶水层：买入信号服务。

职责：调 algorithms.entry.check_signal（内部已包含 ladder+state+entry 逻辑）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import Signal, SignalSource

log = logging.getLogger("emotion_core.signal_service")


def run(trade_date: date, source: SignalSource = SignalSource.LIVE) -> Signal | None:
    """生成当日买入信号。

    Args:
        trade_date: 交易日。
        source: 信号来源（live 或 replay）。

    Returns:
        信号对象，无信号返回 None。
    """
    sig = entry.check_signal(trade_date, source)
    if sig is not None:
        log.info("signal_service %s: %s %s", trade_date, sig.action.value, sig.code)
    else:
        log.info("signal_service %s: 无信号", trade_date)
    return sig
