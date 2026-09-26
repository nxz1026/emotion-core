"""交易日历。从 daily_bar 派生（不维护独立日历表）。

口径来源：lkl/utils/dates.py。交易日历是 data 的事实，不是配置。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emotion_core.utils.db import connect_ro


def today_sh() -> date:
    """今天（Asia/Shanghai）。"""
    from datetime import datetime, timezone, timedelta as td
    return (datetime.now(timezone.utc) + td(hours=8)).date()


def trading_days(start: date, end: date, *, conn=None) -> list[date]:
    """从 daily_bar 派生交易日列表。

    Args:
        start: 起始日期（含）
        end: 结束日期（含）
        conn: 可选 DB 连接（默认 connect_ro）
    Returns:
        交易日列表（升序）
    """
    if conn is None:
        from emotion_core.utils.db import connect_ro
        conn = connect_ro()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM daily_bar "
            "WHERE date BETWEEN %s AND %s ORDER BY date",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def prev_trading_day(d: date, *, conn=None) -> date | None:
    """前一交易日。"""
    if conn is None:
        from emotion_core.utils.db import connect_ro
        conn = connect_ro()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(date) FROM daily_bar WHERE date < %s",
            (d,),
        )
        r = cur.fetchone()
        return r[0] if r and r[0] else None


def next_trading_day(d: date, *, conn=None) -> date | None:
    """下一交易日。"""
    if conn is None:
        from emotion_core.utils.db import connect_ro
        conn = connect_ro()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(date) FROM daily_bar WHERE date > %s",
            (d,),
        )
        r = cur.fetchone()
        return r[0] if r and r[0] else None


def recent_trading_days(n: int, *, conn=None) -> list[date]:
    """最近 N 个交易日。"""
    if conn is None:
        from emotion_core.utils.db import connect_ro
        conn = connect_ro()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM daily_bar ORDER BY date DESC LIMIT %s",
            (n,),
        )
        return sorted([r[0] for r in cur.fetchall()])
