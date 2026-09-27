"""展示层数据加载器：只读查询 → domain.snapshot。"""
from __future__ import annotations

from datetime import date
from emotion_core.utils.db import query_df


def load_market_snapshot(trade_date: date | None = None) -> dict:
    """加载市场快照。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df("SELECT * FROM market_stat WHERE date = %s", (trade_date,))
    return df.iloc[0].to_dict() if not df.empty else {}


def load_ladder(trade_date: date | None = None) -> list[dict]:
    """加载梯队。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df(
        "SELECT * FROM ladder_day WHERE date = %s ORDER BY cont_days DESC",
        (trade_date,),
    )
    return df.to_dict("records")


def load_signals(trade_date: date | None = None) -> list[dict]:
    """加载信号。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df(
        "SELECT * FROM signal WHERE confirm_date = %s ORDER BY action",
        (trade_date,),
    )
    return df.to_dict("records")


def load_promotion(trade_date: date | None = None) -> list[dict]:
    """加载晋级矩阵。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df(
        "SELECT * FROM promotion_day WHERE date = %s ORDER BY layer",
        (trade_date,),
    )
    return df.to_dict("records")


def load_top_themes(trade_date: date | None = None, limit: int = 3) -> list[dict]:
    """加载题材热度 TOP N。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df(
        "SELECT * FROM theme_group WHERE date = %s ORDER BY highest_board DESC, member_count DESC LIMIT %s",
        (trade_date, limit),
    )
    return df.to_dict("records")


def load_hot_rank(trade_date: date | None = None, limit: int = 10) -> list[dict]:
    """加载人气榜 TOP N。"""
    if trade_date is None:
        trade_date = date.today()
    df = query_df(
        "SELECT * FROM hot_rank WHERE date = %s ORDER BY rank LIMIT %s",
        (trade_date, limit),
    )
    return df.to_dict("records")


def load_market_trend(days: int = 10) -> list[dict]:
    """加载近 N 日市场趋势。"""
    df = query_df(
        "SELECT * FROM market_stat WHERE date >= CURRENT_DATE - INTERVAL '%s days'"
        " ORDER BY date LIMIT %s",
        (days, days),
    )
    return df.to_dict("records")
