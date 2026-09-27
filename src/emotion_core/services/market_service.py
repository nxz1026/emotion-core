"""胶水层：市场状态机服务。

职责：从 derived_bar 计算指标 → 调 algorithms.state.classify_series → 写回 market_stat。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from emotion_core.algorithms import state
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import prev_trading_day
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.market_service")


def _load_day_metrics(trade_date: date) -> Optional[state.DayMetrics]:
    """从 derived_bar 计算单日指标。"""
    df = query_df(
        "SELECT max(cont_days) as max_h,"
        " max(CASE WHEN is_exchange THEN cont_days END) as max_ex_h,"
        " count(*) FILTER (WHERE is_limit_up) as zt_count,"
        " count(*) FILTER (WHERE is_bomb) as bomb_count,"
        " count(*) FILTER (WHERE is_limit_down) as ld_count,"
        " max(CASE WHEN is_limit_up THEN amplitude END) as top_amp"
        " FROM derived_bar WHERE date = %s",
        (trade_date,),
    )
    if df.empty or df["max_h"].iloc[0] is None:
        return None
    r = df.iloc[0]
    max_h = int(r["max_h"])
    # 涨停性能 = 当日涨停股中位数振幅
    perf_df = query_df(
        "SELECT amplitude FROM derived_bar"
        " WHERE date = %s AND is_limit_up", (trade_date,)
    )
    zt_perf = float(perf_df["amplitude"].median()) if not perf_df.empty else None
    return state.DayMetrics(
        date=trade_date,
        limit_up_count=int(r["zt_count"]),
        max_limit_days=max_h,
        limit_down_count=int(r["ld_count"]),
        bomb_threshold=CONFIG.BOMB_THRESHOLD,
        has_candidate=max_h >= CONFIG.MIN_LEADER_DAYS,
        tradable_max_days=int(r["max_ex_h"]) if r["max_ex_h"] else 0,
        zt_performance=zt_perf,
        top_amplitude=float(r["top_amp"]) if r["top_amp"] else None,
        top_broke=None,
        oneword_ratio=None,
    )


def run(trade_date: date) -> int:
    """状态机：derived_bar → market_stat。

    Args:
        trade_date: 交易日。

    Returns:
        写入行数（0 或 1）。
    """
    series = []
    cur = trade_date
    # 取最近 30 天构建状态机序列
    for _ in range(30):
        dm = _load_day_metrics(cur)
        if dm is None:
            break
        series.insert(0, dm)
        prev = prev_trading_day(cur)
        if prev is None:
            break
        cur = prev

    if not series:
        log.warning("market_service %s: 无历史数据", trade_date)
        return 0

    states = state.classify_series(series)
    today_state = states[-1]

    with transaction() as conn:
        conn.execute(
            "INSERT INTO market_stat (date, phase, buy_window, force_liquidate,"
            " reason, max_height, zt_count, ld_count, tradable_max_days)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (date) DO UPDATE SET"
            " phase = EXCLUDED.phase, buy_window = EXCLUDED.buy_window,"
            " force_liquidate = EXCLUDED.force_liquidate, reason = EXCLUDED.reason",
            (
                trade_date, today_state.phase, today_state.buy_window,
                today_state.force_liquidate, today_state.reason,
                today_state.max_limit_days, today_state.limit_up_count,
                today_state.limit_down_count, today_state.tradable_max_days,
            ),
        )
    log.info("market_service %s: %s", trade_date, today_state.phase)
    return 1
