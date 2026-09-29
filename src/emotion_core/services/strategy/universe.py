"""策略观察候选池：仅读现有行情表，不触碰主链路。

Native emotion-core implementation.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

# 观察哪些 pool_type：ZT=涨停、ZB=炸板。DT=跌停被排除——本池语义是「当日涨停
# 池」，跌停是相反的极端，混进来既与语义相悖，又白烧 LLM 配额（实测 2026-09-29
# 该日 ZT 57 / DT 10 / ZB 8，即 13% 的候选被跌停股占据）。
#
# 放模块级常量而非 utils/config.py 的 Config：config_hash() 哈希 asdict(CONFIG)
# 全字段，往 Config 里加键会让 pipeline_state / signal / eval_result 的策略指纹
# 平白换代。env 同名 EC_STRATEGY_POOL_TYPES（逗号分隔，如 "ZT"）可覆盖。
OBSERVED_POOL_TYPES: tuple[str, ...] = tuple(
    item.strip().upper()
    for item in os.environ.get("EC_STRATEGY_POOL_TYPES", "ZT,ZB").split(",")
    if item.strip())


def _codes(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "code" not in frame:
        return []
    return [str(code).zfill(6) for code in frame["code"].dropna().tolist()]


def build_universe(trade_date: date) -> list[str]:
    """返回当日涨停/炸板池与热榜前 N 的主板去重候选。

    两条查询都必须给出**完全确定**的 ORDER BY。结果会被
    ``STRATEGY_MAX_UNIVERSE`` 截断，而截断后的先后顺序直接决定 LLM 配额
    先落在哪些票上（runner 按 ``(code, skill)`` 交错遍历，先到先烧额度）。
    缺 ORDER BY 时 PostgreSQL 不保证返回顺序稳定，同一交易日重复运行会
    得到不同的候选池，进而让「跑过哪些组合」不可复现。

    排序主键取 ``code`` 而非业务强度（连板高度等）：``limit_pool_em`` 内
    ``cont_days_em`` 只在涨停股上有意义，跌停行同样带值，按它排序会把跌停股
    排进前列。真要改成「强势优先」，必须先按 ``pool_type`` 收窄（本函数已做）
    并注意 DT 行的 ``first_seal`` 存的是空串而非 NULL，需 ``NULLIF(first_seal,'')``
    才不会把空串当成最早封板时间排到最前。
    """
    pool = query_df(
        "SELECT code FROM limit_pool_em WHERE date=%s AND pool_type = ANY(%s) "
        "ORDER BY code",
        (trade_date, list(OBSERVED_POOL_TYPES)))
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
