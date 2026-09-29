"""胶水层：题材聚合服务。

职责：从 theme_source 取标签 → 调 algorithms.theme.aggregate → 写 theme_group/theme_tag。
NullProvider 降级不阻断。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import theme
from emotion_core.data.theme_source import get_provider
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.theme_service")


def run(trade_date: date) -> int:
    """题材展开聚合 + 落库。

    Args:
        trade_date: 交易日。

    Returns:
        题材组数。
    """
    # 题材源未接入：走 NullProvider 空实现（见 data/theme_source.py），降级不阻断主链。
    # 原写法 CONFIG.get("THEME_PROVIDER") 属于旧 dict 配置时代的残留——本库 CONFIG 是
    # frozen dataclass（utils/config.py），没有 .get()，也没有 THEME_PROVIDER 字段，
    # 于是每天跑到 theme 就 AttributeError 崩（实测 2026-09-28 那次日更）。这里不做
    # 「补一个字段」的扩张：新增字段会改变 config_hash()，让审计里的策略指纹平白换代。
    provider = get_provider()
    tags = provider.fetch_tags(trade_date)
    if not tags:
        log.info("theme_service %s: 无题材数据", trade_date)
        return 0

    result = theme.aggregate(tags)

    with transaction() as conn:
        conn.execute("DELETE FROM theme_group WHERE date = %s", (trade_date,))
        conn.execute("DELETE FROM theme_tag WHERE date = %s", (trade_date,))
        for theme_name, stats in result.items():
            conn.execute(
                "INSERT INTO theme_group (date, theme, highest_board, top_code,"
                " member_count, completeness, status)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (trade_date, theme_name, stats["highest_board"],
                 stats["top_code"], stats["member_count"],
                 stats["completeness"], stats["status"]),
            )
            # TODO: 写 theme_tag（如果有逐票标签数据）

    log.info("theme_service %s: %d 个题材", trade_date, len(result))
    return len(result)
