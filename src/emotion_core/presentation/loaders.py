"""展示层数据加载器：只读查询 → domain.snapshot。

**默认日期不是 `date.today()`**：展示层的默认快照日是库里最后一个有快照的交易日
（`snapshot.latest_date()`）。曾经用 today()，结果 2026-09-28 那天打开直观层看到
的是「市场处于 未知 / 今日无推荐」——最后快照其实是 09-24。用户要在日历里选历史
日期回看，所以每个 loader 都必须能接受 `trade_date`。
"""
from __future__ import annotations

from datetime import date

from emotion_core.presentation import snapshot
from emotion_core.utils.db import query_df


def _d(trade_date: date | None) -> date | None:
    """默认取最新快照日（无数据时返回 None，下面查询自然查不到行）。"""
    return trade_date if trade_date is not None else snapshot.latest_date()


def load_market_snapshot(trade_date: date | None = None) -> dict:
    """加载市场快照（默认最新快照日）。"""
    trade_date = _d(trade_date)
    df = query_df("SELECT * FROM market_stat WHERE date = %s", (trade_date,))
    return df.iloc[0].to_dict() if not df.empty else {}


def load_ladder(trade_date: date | None = None) -> list[dict]:
    """加载梯队（默认最新快照日）。"""
    trade_date = _d(trade_date)
    df = query_df(
        "SELECT * FROM ladder_day WHERE date = %s ORDER BY cont_days DESC",
        (trade_date,),
    )
    return df.to_dict("records")


def load_signals(trade_date: date | None = None) -> list[dict]:
    """加载信号（默认最新快照日）。"""
    trade_date = _d(trade_date)
    df = query_df(
        "SELECT * FROM signal WHERE confirm_date = %s ORDER BY action",
        (trade_date,),
    )
    return df.to_dict("records")


def load_promotion(trade_date: date | None = None) -> list[dict]:
    """加载晋级矩阵（默认最新快照日）。"""
    trade_date = _d(trade_date)
    df = query_df(
        "SELECT * FROM promotion_day WHERE date = %s ORDER BY layer",
        (trade_date,),
    )
    return df.to_dict("records")


def load_top_themes(trade_date: date | None = None, limit: int = 3) -> list[dict]:
    """加载题材热度 TOP N（默认最新快照日）。"""
    trade_date = _d(trade_date)
    df = query_df(
        "SELECT * FROM theme_group WHERE date = %s ORDER BY highest_board DESC, member_count DESC LIMIT %s",
        (trade_date, limit),
    )
    return df.to_dict("records")


def load_hot_rank(trade_date: date | None = None, limit: int = 10) -> list[dict]:
    """加载人气榜 TOP N（默认最新快照日）。"""
    trade_date = _d(trade_date)
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


def load_top_ladder(trade_date: date | None = None, limit: int = 10) -> list[dict]:
    """当日最高板梯队（derived_bar 现算，PIT）。

    `ladder_day` 表当前 0 行（日更链尚未成功产出一轮），直观层要能显示"当日最强的票"，
    故用 derived_bar 现算这一份**数据视图**（不是决策输出，页面必须标注来源）。
    """
    trade_date = _d(trade_date)
    if trade_date is None:
        return []
    df = query_df(
        """SELECT v.code, s.name, v.cont_days, v.is_one_word, v.is_exchange,
                  v.amplitude, v.touched_limit
           FROM derived_bar v JOIN stock_basic s ON s.code = v.code
           WHERE v.date = %s AND v.is_limit_up
           ORDER BY v.cont_days DESC, v.code LIMIT %s""",
        (trade_date, limit))
    return df.to_dict("records")


def load_signal_counts(trade_date: date | None = None) -> dict:
    """当日/全表信号与梯队条数（含 0），用于页面如实说明"无推荐是没数据还是没信号"。"""
    trade_date = _d(trade_date)

    def _one(sql: str, params: tuple) -> int:
        df = query_df(sql, params)
        return int(df.iloc[0]["n"]) if not df.empty else 0

    return {
        "day": _one("SELECT count(*) AS n FROM signal WHERE confirm_date = %s",
                    (trade_date,)),
        "total": _one("SELECT count(*) AS n FROM signal", ()),
        "ladder_day": _one("SELECT count(*) AS n FROM ladder_day WHERE date = %s",
                           (trade_date,)),
        "ladder_total": _one("SELECT count(*) AS n FROM ladder_day", ()),
    }
