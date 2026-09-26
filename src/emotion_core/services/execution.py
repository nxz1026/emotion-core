"""T7 执行层反馈（ManualExecutor 协议/占位已清理，P4 接入时重建）。"""
from __future__ import annotations

from datetime import date

from emotion_core.utils.db import execute


def feedback(confirm_date: date, code: str, action: str,
             status: str, price: float | None = None) -> int:
    """人工回执：SUGGESTED → ADOPTED/DROPPED（评估期统计采纳率）。"""
    if status not in ("ADOPTED", "DROPPED"):
        raise ValueError("status 仅支持 ADOPTED/DROPPED")
    return execute(
        "UPDATE signal SET status=%s, feedback_price=%s, feedback_date=%s"
        " WHERE confirm_date=%s AND code=%s AND action=%s",
        (status, price, confirm_date, confirm_date, code, action))
