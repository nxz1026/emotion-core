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
    """返回当日涨停池全量与热榜前 N 的主板去重候选。

    两条查询都必须给出**完全确定**的 ORDER BY。结果会被
    ``STRATEGY_MAX_UNIVERSE`` 截断，而截断后的先后顺序直接决定 LLM 配额
    先落在哪些票上（runner 按 ``(code, skill)`` 交错遍历，先到先烧额度）。
    缺 ORDER BY 时 PostgreSQL 不保证返回顺序稳定，同一交易日重复运行会
    得到不同的候选池，进而让「跑过哪些组合」不可复现。

    排序主键取 ``code`` 而非业务强度（连板高度等）：``limit_pool_em``
    同时含涨停(ZT)/跌停(DT)/炸板(ZB) 三种 pool_type，按强度排序会把跌停股
    排进前列。要改成「强势优先」需先决定 pool_type 的取舍，属独立决策。
    """
    pool = query_df(
        "SELECT code FROM limit_pool_em WHERE date=%s ORDER BY code",
        (trade_date,))
    hot = query_df(
        "SELECT code FROM hot_rank WHERE date=%s ORDER BY rank, code LIMIT %s",
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
