"""为策略模型提供紧凑、只读的个股事实上下文。

Native emotion-core implementation.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from emotion_core.utils.db import query_df


def _value(row, key: str, default="—"):
    value = row.get(key, default) if hasattr(row, "get") else default
    return default if value is None or pd.isna(value) else value


def _daily_text(trade_date: date, code: str) -> list[str]:
    start = trade_date - timedelta(days=45)
    frame = query_df(
        "SELECT b.date,b.open,b.high,b.low,b.close,b.volume,b.turnover_rate,"
        " d.cont_days,d.is_one_word,"
        " CASE WHEN b.pre_close IS NOT NULL AND b.pre_close <> 0 "
        " THEN (b.close/b.pre_close-1)*100 END AS change_pct"
        " FROM daily_bar b LEFT JOIN derived_bar d ON d.code=b.code AND d.date=b.date"
        " WHERE b.code=%s AND b.date BETWEEN %s AND %s ORDER BY b.date DESC LIMIT 20",
        (code, start, trade_date))
    lines = []
    for row in frame.itertuples(index=False):
        values = row._asdict()
        lines.append(
            f"{_value(values, 'date')} 收{_value(values, 'close')} "
            f"涨{_value(values, 'change_pct')}% 换手{_value(values, 'turnover_rate')}% "
            f"连板{_value(values, 'cont_days', 0)} 一字{_value(values, 'is_one_word', False)}")
    return lines


def build_stock_context(trade_date: date, code: str) -> str:
    """拼接近20日日线、梯队、题材、热榜和市场情绪，最多约1500字。"""
    ladder = query_df(
        "SELECT cont_days,is_top FROM ladder_day WHERE date=%s AND code=%s",
        (trade_date, code))
    theme = query_df(
        "SELECT primary_theme FROM theme_tag WHERE date=%s AND code=%s",
        (trade_date, code))
    hot = query_df(
        "SELECT rank FROM hot_rank WHERE date=%s AND code=%s",
        (trade_date, code))
    stat = query_df(
        "SELECT phase,buy_window,limit_up_count,bomb_rate,zt_performance,max_limit_days"
        " FROM market_stat WHERE date=%s", (trade_date,))

    h = _value(ladder.iloc[0], "cont_days", 0) if not ladder.empty else 0
    top = _value(ladder.iloc[0], "is_top", False) if not ladder.empty else False
    topic = _value(theme.iloc[0], "primary_theme") if not theme.empty else "—"
    rank = _value(hot.iloc[0], "rank") if not hot.empty else "—"
    mood = stat.iloc[0].to_dict() if not stat.empty else {}

    header = (f"股票：{code}\n题材：{topic}；热榜名次：{rank}\n"
              f"当日连板：{h}；板高度标记：{top}\n"
              f"市场情绪：阶段={_value(mood, 'phase')}，窗口={_value(mood, 'buy_window')}，"
              f"涨停={_value(mood, 'limit_up_count')}，炸板率={_value(mood, 'bomb_rate')}，"
              f"涨停股表现={_value(mood, 'zt_performance')}，"
              f"最高板={_value(mood, 'max_limit_days')}\n"
              "近20日日线（新→旧）：\n")
    return (header + "\n".join(_daily_text(trade_date, code)))[:1500]
