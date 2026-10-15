"""胶水层：题材聚合服务。

职责：从 theme_source 取标签 → 调 algorithms.theme.aggregate → 写 theme_tag/theme_group；
同一份采集顺带写 theme_spot（当日概念板块热点榜）。NullProvider 降级不阻断。

2026-10-14 起生效源是 `SinaConceptProvider`（新浪概念，免费，0 Wind 额度），
激活方式见 `data/theme_source.py` 的模块文档（环境变量，不动 CONFIG/不动 systemd 单元）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import theme
from emotion_core.data.theme_source import get_provider
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.theme_service")

_TAG_COLS = ("date", "code", "primary_theme", "secondary_themes", "source")
_SPOT_COLS = ("date", "node", "theme", "member_count", "pct_chg", "amount",
              "leading_code", "leading_name", "source")


def _fetch_spots(provider, trade_date: date) -> list[dict]:
    """板块热点榜（可选能力，非 ThemeProvider 协议必需）。

    单独 try：热点榜是**同源的附加产物**，它挂了不该连题材标签一起丢掉
    （反之亦然——题材标签失败时 `run()` 在开事务前就返回，热点也不会写）。
    """
    fn = getattr(provider, "fetch_spots", None)
    if fn is None:
        return []
    try:
        return fn(trade_date)
    except Exception as exc:  # noqa: BLE001 —— 附加产物，不阻断主链
        log.warning("theme_spot %s：板块热点榜获取失败（题材标签照写）：%s",
                    trade_date, str(exc)[:120])
        return []


def _clean_tag_row(row: dict) -> dict:
    """写库前剔 STYLE_ONLY（含属性桶扩展）：primary/secondary 只留有效题材。

    保序：primary 在前、secondary 顺序不变。全部标签皆噪声 → `primary_theme=None`
    且 `secondary_themes=[]`——该票当日无有效题材，行仍保留以记录覆盖
    （`_row_themes` 对 NULL 主名展开零行，不会污染后续聚合）。
    """
    primary = row.get("primary_theme")
    secondary = list(row.get("secondary_themes") or [])
    valid = theme.theme_filter(
        [t for t in ([primary] if primary else []) + secondary if t])
    out = dict(row)
    out["primary_theme"] = valid[0] if valid else None
    out["secondary_themes"] = valid[1:]
    return out


def run(trade_date: date) -> int:
    """题材展开聚合 + 落库。

    Args:
        trade_date: 交易日。

    Returns:
        题材组数。
    """
    # 题材源未接入时走 NullProvider 空实现（见 data/theme_source.py），降级不阻断主链。
    # 原写法 CONFIG.get("THEME_PROVIDER") 属于旧 dict 配置时代的残留——本库 CONFIG 是
    # frozen dataclass（utils/config.py），没有 .get()，也没有 THEME_PROVIDER 字段，
    # 于是每天跑到 theme 就 AttributeError 崩（实测 2026-09-28 那次日更）。这里不做
    # 「补一个字段」的扩张：新增字段会改变 config_hash()，让审计里的策略指纹平白换代。
    provider = get_provider()
    tags = provider.fetch_tags(trade_date)
    if not tags:
        log.info("theme_service %s: 无题材数据", trade_date)
        return 0

    # 聚合仍吃**原始**标签：aggregate 内部自行 is_style 剔除（与 lkl 同位置同判据），
    # 提前过滤会改变 _row_themes 的展开口径取序（primary 由末位变首位），
    # 进而改动并列最高板 top_code 的取序（模块文档 ★）。
    result = theme.aggregate(tags)
    spots = _fetch_spots(provider, trade_date)
    # 落库行只留有效题材（2026-10-14 拍板）：provider 负责「如其所是地取回来」，
    # 判据单一真相在 algorithms/theme.py，服务层在写入口收口。
    rows = [_clean_tag_row(r) for r in tags]

    with transaction() as conn:
        conn.execute("DELETE FROM theme_group WHERE date = %s", (trade_date,))
        conn.execute("DELETE FROM theme_tag WHERE date = %s", (trade_date,))
        # 逐票标签：一股一行（primary + secondary 数组），与 theme_tag 的 (date, code) 主键同形。
        # 连接级没有 executemany，必须用 cursor（psycopg3 的 Connection 不提供该方法）。
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO theme_tag ({', '.join(_TAG_COLS)})"
                f" VALUES ({', '.join(['%s'] * len(_TAG_COLS))})",
                [(trade_date, r["code"], r.get("primary_theme"),
                  list(r.get("secondary_themes") or []),
                  r.get("source") or "unknown") for r in rows],
            )
        for theme_name, stats in result.items():
            conn.execute(
                "INSERT INTO theme_group (date, theme, highest_board, top_code,"
                " member_count, completeness, status, mid_count, low_count,"
                " first_board_count)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (trade_date, theme_name, stats["highest_board"],
                 stats["top_code"], stats["member_count"],
                 stats["completeness"], stats["status"], stats["mid_count"],
                 stats["low_count"], stats["first_board_count"]),
            )
        if spots:
            conn.execute("DELETE FROM theme_spot WHERE date = %s", (trade_date,))
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO theme_spot ({', '.join(_SPOT_COLS)})"
                    f" VALUES ({', '.join(['%s'] * len(_SPOT_COLS))})",
                    [tuple(s[c] for c in _SPOT_COLS) for s in spots],
                )

    log.info("theme_service %s: %d 票标签 / %d 个题材组 / %d 个板块热点",
             trade_date, len(tags), len(result), len(spots))
    return len(result)
