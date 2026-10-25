"""胶水层：观察记录服务。

职责：调 algorithms.entry.check_signal（内部已包含 ladder+state+entry 逻辑）。

A 止血（2026-10-21 奎爷拍板）：口径B 打板的成交假设被证伪（触及即成交 →
每信号 −1.26%），本服务不再推送「买入信号简报」。R58-4 的 BUY 即时推送保留代码
但**默认关闭**（`notify.push_buy_signal` 读 EMOTION_PUSH_BUY，默认 0），等 B 轮
找到正期望边缘、或 C 轮盘中能力补齐后再开。
"""

from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import Action, Signal, SignalSource
from emotion_core.services import notify

log = logging.getLogger("emotion_core.signal_service")


def run(trade_date: date, source: SignalSource = SignalSource.LIVE) -> Signal | None:
    """生成当日观察记录。

    Args:
        trade_date: 交易日。
        source: 信号来源（live 或 replay）。

    Returns:
        观察记录对象，无记录返回 None。
    """
    sig = entry.check_signal(trade_date, source)
    if sig is not None:
        log.info("signal_service %s: %s %s", trade_date, sig.action.value, sig.code)
        # 仅 BUY 档（观察名单成员）走即时推送；SECONDARY/RECOMMEND 是降档线索，
        # 只进报告，不单独推送。
        if sig.action == Action.BUY:
            _push_watch_safe(sig)
    else:
        log.info("signal_service %s: 无观察记录", trade_date)
    return sig


def _push_watch_safe(sig: Signal) -> None:
    """观察记录推送包裹：默认关闭，异常吞掉，不影响主链。

    push_buy_signal 内部自带开关（PUSH_BUY 默认 0）与 best-effort；这里再包一层
    只为兜住 sig 字段意外缺失等结构性错误，不让它拖垮 signal_service.run。
    """
    try:
        notify.push_buy_signal(
            code=sig.code,
            name=getattr(sig, "name", None) or "",
            cont_days=int(getattr(sig, "cont_days", 0) or 0),
            buy_window="",  # window 不在 Signal 契约里；daily 主链 push() 仍带
            cl=sig.checklist,
            trade_date=sig.date,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("观察记录推送包装层异常（不影响落盘）：%s", exc)
