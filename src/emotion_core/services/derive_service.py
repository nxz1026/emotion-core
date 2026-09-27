"""胶水层：判据 + 连板服务。

职责：从 daily_bar 取数据 → 调 algorithms.compute_derived → 写回 derived_bar。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from emotion_core.algorithms import indicators
from emotion_core.domain.bar import Bar
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.derive_service")


def _to_cents(v) -> int:
    """Decimal 元 → int 分。处理 NaN。"""
    if v is None:
        return 0
    f = float(v)
    if f != f:  # NaN check
        return 0
    return int(round(f * 100))


def _load_bars(trade_date: date) -> list[Bar]:
    """从 daily_bar 加载最近 30 天日线（用于连板计算）。

    只加载在 trade_date 有数据的股票的最近 30 天日线。
    """
    # 获取 trade_date 有数据的所有股票代码
    codes_df = query_df(
        "SELECT DISTINCT code FROM daily_bar WHERE date = %s", (trade_date,)
    )
    if codes_df.empty:
        return []
    codes = list(codes_df["code"])

    # 加载这些股票最近 30 天的日线
    start_date = trade_date - timedelta(days=45)  # 45 天窗口确保连板不丢
    df = query_df(
        "SELECT code, date, open, high, low, close, pre_close, volume, turnover_rate"
        " FROM daily_bar WHERE code = ANY(%s) AND date >= %s ORDER BY code, date",
        (codes, start_date),
    )
    return [
        Bar(
            code=str(r.code),
            date=r.date,
            open_cents=_to_cents(r.open),
            high_cents=_to_cents(r.high),
            low_cents=_to_cents(r.low),
            close_cents=_to_cents(r.close),
            pre_close_cents=_to_cents(r.pre_close),
            volume=int(r.volume) if r.volume else 0,
            turnover_rate=float(r.turnover_rate) if r.turnover_rate is not None else 0.0,
        )
        for r in df.itertuples()
    ]


def _filter_by_date(bars: list[Bar], trade_date: date) -> list[Bar]:
    """过滤指定日期的 Bar。"""
    return [b for b in bars if b.date == trade_date]


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
                r.code, r.date, r.is_limit_up, r.touched_limit, r.is_bomb,
                r.is_one_word, r.is_exchange, r.is_limit_down, r.cont_days,
                r.amplitude,
            )
            for r in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO derived_bar (code, date, is_limit_up, touched_limit,"
                " is_bomb, is_one_word, is_exchange, is_limit_down, cont_days,"
                " amplitude) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
    all_bars = _load_bars(trade_date)
    if not all_bars:
        log.warning("derive_service %s: daily_bar 无数据", trade_date)
        return 0
    derived = indicators.compute_derived(all_bars)
    # 只写入指定日期的结果
    today_derived = _filter_by_date(derived, trade_date)
    n = _persist_derived(today_derived)
    log.info("derive_service %s: %d 行", trade_date, n)
    return n
