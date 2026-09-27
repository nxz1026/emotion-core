"""胶水层：市场状态机服务。

职责：`derived_bar` → `algorithms.emotion`（唯一情绪状态机实现）→ `market_stat`。

历史缺口（审核文档 §9 第 5 条，C7）：本模块曾自持 `_load_day_metrics` +
`state.classify_series`，与 `algorithms/emotion.py` 的 `_counts` 口径不一致——
① 无次新过滤（emotion 走 `accelerate._NEW_ISSUER_FILTER`）；
② `max(cont_days)` 取全板块，未限主板 `CONFIG.BOARD_PREFIXES`（20% 板能顶掉主板最高板，
直接喂给 ICE_MAX_DAYS / MIN_LEADER_DAYS 分支）；
③ `bomb_rate` / `top_broke` / `oneword_ratio` 恒 None（高潮的炸板率分支永不触发）。
结果是 `market_stat` 出现两条语义不同的写入路径，线上跑的是弱的那条，
而 661 天 oracle 对账过的 `emotion.py` 反而零调用。

现改为薄胶水：单日增量走 `emotion.run_range(d, d)`（内部自带 warm-up 前情、
显式续接昨日已落库 phase，只回写 [d, d]）。口径与 `dragon_env.py` 模块文档
「行须已由 emotion.run_range 产出」的既有假设一致。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import emotion

log = logging.getLogger("emotion_core.market_service")


def run(trade_date: date) -> int:
    """转发单日状态机：`derived_bar` → `market_stat`（实现见 emotion.run_range）。

    Args:
        trade_date: 交易日。

    Returns:
        写入行数（0 或 1；无行情日/未来日期为 0）。
    """
    n = emotion.run_range(trade_date, trade_date)
    log.info("market_service %s: %d 行（emotion 口径）", trade_date, n)
    return n
