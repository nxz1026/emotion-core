"""胶水层：买入信号服务。

职责：调 algorithms.entry.check_signal（内部已包含 ladder+state+entry 逻辑）。

R58-4：当 action=BUY 时调 notify.push_buy_signal 立即推飞书（24h 节流），
SECONDARY/RECOMMEND 不触发即时推送（与日报摘要推送同纪律，仅 BUY 给
「第一时间看到」的独立时点）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import Action, Signal, SignalSource
from emotion_core.services import notify

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
        # R58-4：BUY 即时推送——与日报摘要推送并行，专注 BUY 信号简报。
        # SECONDARY/RECOMMEND 是观察信号，不触发即时推送。
        if sig.action == Action.BUY:
            _push_buy_safe(sig)
    else:
        log.info("signal_service %s: 无信号", trade_date)
    return sig


def _push_buy_safe(sig: Signal) -> None:
    """BUY 推送包裹：异常吞掉，不影响主链。

    push_buy_signal 内部已 best-effort（异常不抛），这里再包一层只是为
    兜住 sig 字段意外缺失等结构性错误（不应发生，但不让其拖垮
    signal_service.run）。
    """
    try:
        notify.push_buy_signal(
            code=sig.code,
            name=getattr(sig, "name", None) or "",
            cont_days=int(getattr(sig, "cont_days", 0) or 0),
            buy_window="",   # window 不在 Signal 契约里；daily 主链 push() 仍带
            cl=sig.checklist,
            trade_date=sig.date,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("BUY 即时推送包装层异常（不影响 BUY 落盘）：%s", exc)