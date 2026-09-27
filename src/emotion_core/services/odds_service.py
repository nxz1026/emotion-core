"""胶水层：买点口径统计服务。

职责：三口径统计（打板价 / 开盘价 / 回踩）。
参考 docs/07 §7 打板口径统计。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.odds_service")


def run(trade_date: date) -> dict:
    """三口径买点统计。

    Args:
        trade_date: 交易日。

    Returns:
        统计结果 dict。
    """
    # 打板价口径
    df = query_df(
        "SELECT s.code, s.confirm_date, b.close as signal_close,"
        " nb.open as next_open"
        " FROM signal s"
        " JOIN daily_bar b ON b.code = s.code AND b.date = s.confirm_date"
        " JOIN daily_bar nb ON nb.code = s.code AND nb.date = (s.confirm_date + 1)"
        " WHERE s.confirm_date = %s",
        (trade_date,),
    )
    if df.empty:
        return {"trade_date": trade_date, "n": 0}

    return {
        "trade_date": trade_date,
        "n": len(df),
        "avg_next_open_pct": round(
            float((df["next_open"] / df["signal_close"] - 1).mean() * 100), 2
        ) if not df.empty else None,
    }
