"""胶水层：判据 + 连板服务。

职责：从 daily_bar 取数据 → 调 algorithms.compute_derived → 写回 derived_bar。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import indicators
from emotion_core.domain.bar import Bar
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.derive_service")


def _load_bars(trade_date: date) -> list[Bar]:
    """从 daily_bar 加载当日原始日线。"""
    df = query_df(
        "SELECT code, date, open_cents, high_cents, low_cents, close_cents,"
        " pre_close_cents, volume, turnover_rate"
        " FROM daily_bar WHERE date = %s ORDER BY code, date",
        (trade_date,),
    )
    return [
        Bar(
            code=str(r.code),
            date=r.date,
            open_cents=int(r.open_cents),
            high_cents=int(r.high_cents),
            low_cents=int(r.low_cents),
            close_cents=int(r.close_cents),
            pre_close_cents=int(r.pre_close_cents),
            volume=int(r.volume),
            turnover_rate=float(r.turnover_rate) if r.turnover_rate is not None else 0.0,
        )
        for r in df.itertuples()
    ]


def _persist_derived(rows: list) -> int:
    """批量写入 derived_bar。"""
    if not rows:
        return 0
    with transaction() as conn:
        conn.execute(
            "DELETE FROM derived_bar WHERE date = %s", (rows[0].date,)
        )
        data = [
            (
                r.code, r.date, r.is_limit_up, r.is_limit_down, r.is_one_word,
                r.is_exchange, r.is_bomb, r.touched_limit, r.cont_days,
                r.amplitude, r.quality,
            )
            for r in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO derived_bar (code, date, is_limit_up, is_limit_down,"
                " is_one_word, is_exchange, is_bomb, touched_limit, cont_days,"
                " amplitude, quality) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                data,
            )
    return len(data)


def run(trade_date: date) -> int:
    """计算判据 + 连板并写入 derived_bar。

    Args:
        trade_date: 交易日。

    Returns:
        写入行数。
    """
    bars = _load_bars(trade_date)
    if not bars:
        log.warning("derive_service %s: daily_bar 无数据", trade_date)
        return 0
    derived = indicators.compute_derived(bars)
    n = _persist_derived(derived)
    log.info("derive_service %s: %d 行", trade_date, n)
    return n
