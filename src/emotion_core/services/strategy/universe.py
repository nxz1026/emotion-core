"""策略观察候选池：仅读现有行情表，不触碰主链路。

Native emotion-core implementation.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df


def _codes(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "code" not in frame:
        return []
    return [str(code).zfill(6) for code in frame["code"].dropna().tolist()]


def build_universe(trade_date: date) -> list[str]:
    """返回当日涨停池全量与热榜前 N 的主板去重候选。"""
    pool = query_df("SELECT code FROM limit_pool_em WHERE date=%s", (trade_date,))
    hot = query_df(
        "SELECT code FROM hot_rank WHERE date=%s ORDER BY rank LIMIT %s",
        (trade_date, CONFIG.STRATEGY_HOT_N))
    allowed = tuple(CONFIG.BOARD_PREFIXES)
    seen: set[str] = set()
    result: list[str] = []
    for code in _codes(pool) + _codes(hot):
        if not code.startswith(allowed) or code in seen:
            continue
        seen.add(code)
        result.append(code)
        if len(result) >= CONFIG.STRATEGY_MAX_UNIVERSE:
            break
    return result
