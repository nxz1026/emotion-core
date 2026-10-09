"""策略观察候选池：手动自选 + 热门池，不触碰主链路。

Native emotion-core implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df


def _codes(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "code" not in frame:
        return []
    return [str(code).zfill(6) for code in frame["code"].dropna().tolist()]


def filter_codes(codes: Iterable[object], *, limit: int | None = None) -> list[str]:
    """按 ``BOARD_PREFIXES`` 过滤 + ``zfill(6)`` + 保序去重。

    ``limit`` 为 ``None`` 时不截断（显式补跑清单应全量保留，交多少跑多少）；
    ``build_universe`` 则传 ``STRATEGY_MAX_UNIVERSE`` 做截断。
    """
    allowed = tuple(CONFIG.BOARD_PREFIXES)
    seen: set[str] = set()
    result: list[str] = []
    for raw in codes:
        code = str(raw).zfill(6)
        if not code.startswith(allowed) or code in seen:
            continue
        seen.add(code)
        result.append(code)
        if limit is not None and len(result) >= limit:
            break
    return result


def build_universe(trade_date: date) -> list[str]:
    """返回「手动自选 + 热门池」的主板去重候选，**自选优先**。

    2026-10-09 日志巡检 C：用户拍板覆盖口径 = 手动自选
    （``public.watchlist``，code 升序）+ 热门池（``public.hot_rank`` 当日
    前 ``STRATEGY_HOT_N``，``ORDER BY rank, code``），**不再读
    ``limit_pool_em``**（涨停/炸板池）。涨停池与热门池高度重叠，且用户
    真正想看的票在自选表里，手动自选才是确定性输入。

    顺序即优先级：``watchlist`` 的 code 全部排在 ``hot_rank`` 之前。
    runner 按 ``(code, skill)`` 交错遍历、配额先烧到前面的 code，因此
    自选永远先于热门池被 LLM 覆盖。

    两条查询都必须给出**完全确定**的 ORDER BY。结果会被
    ``STRATEGY_MAX_UNIVERSE`` 截断，而截断后的先后顺序直接决定 LLM 配额
    先落在哪些票上。缺 ORDER BY 时 PostgreSQL 不保证返回顺序稳定，同一
    交易日重复运行会得到不同候选池，让「跑过哪些组合」不可复现。
    自选表按 ``code`` 升序；热门池按 ``rank, code``（rank 可并列，需
    code 兜底，否则并列名次的返回顺序不稳定）。
    """
    watch = query_df("SELECT code FROM watchlist ORDER BY code", ())
    hot = query_df(
        "SELECT code FROM hot_rank WHERE date=%s ORDER BY rank, code LIMIT %s",
        (trade_date, CONFIG.STRATEGY_HOT_N),
    )
    return filter_codes(_codes(watch) + _codes(hot), limit=CONFIG.STRATEGY_MAX_UNIVERSE)
