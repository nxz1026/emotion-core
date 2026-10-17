"""策略观察候选池：手动自选 + 热门池，不触碰主链路。

Native emotion-core implementation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.strategy.universe")


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


def tradable_codes(trade_date: date) -> set[str]:
    """当日有真实行情的主板代码集（R67-3 第一层：``daily_bar.volume > 0``）。

    一次查全市场，别逐票查库。停牌日的顺延占位行 ``volume = 0``
    （判据同 ``data.bar_guard.is_padding_row``），会被排除；当日无 bar 的票
    自然不在集合里。

    事故背景：2026-10-13 ``strategy_signal`` 里有一条 600825
    ``expectation_repricing`` **BUY** score 92，而 600825 在 10-13/10-14/10-15
    连续停牌——LLM 拿不到当日行情却仍给了买入结论。
    """
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    df = query_df(
        f"SELECT code FROM daily_bar WHERE date = %s AND volume > 0"
        f" AND left(code, 3) IN ({ph})",
        (trade_date, *CONFIG.BOARD_PREFIXES),
    )
    return set(_codes(df))


def drop_untradable(codes: Iterable[str], trade_date: date) -> list[str]:
    """剔除当日无行情（停牌/无 bar）的代码，保序（R67-3 第一层闸门）。"""
    ok = tradable_codes(trade_date)
    return [code for code in codes if code in ok]


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
    codes = filter_codes(_codes(watch) + _codes(hot), limit=CONFIG.STRATEGY_MAX_UNIVERSE)
    # R67-3 第一层：当日停牌/无行情标的不进候选池。
    # ⚠️ 现状是「先按 STRATEGY_MAX_UNIVERSE 截断、再剔除」：停牌票会占用一个
    # 截断名额，末尾的可交易票补不进来（实际候选可能少于上限）。因为
    # STRATEGY_HOT_N(20) + 自选表通常远小于 STRATEGY_MAX_UNIVERSE(80)，
    # 截断一般不生效，故暂不改口径——若要改成「先剔除、再截断」，须同步
    # 本函数与 tests/unit/test_strategy_universe.py::TestBuildUniverseTradableGate。
    tradable = drop_untradable(codes, trade_date)
    if len(tradable) != len(codes):
        kept = set(tradable)
        log.info("候选池剔除当日无行情标的 %d 只（%s）：%s",
                 len(codes) - len(tradable), trade_date,
                 ",".join(c for c in codes if c not in kept))
    return tradable
