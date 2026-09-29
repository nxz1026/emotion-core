"""数据修订标识（产品 A2-9）：回补/改写历史 → data_revision 留痕。

语义：upsert 覆盖已有行 = 「修订」——该交易日已有数据被替换，
下游旧报告/回测的依据已漂移。留痕 (table, trade_date, detail)；
`lkl revisions` 查受影响日期。重生成报告走独立 eval-* 文件名，
天然不覆盖旧版本。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date

from emotion_core.utils.db import execute, query_df, transaction

log = logging.getLogger(__name__)

# 表名白名单：拒绝拼接未注册的表名（防 SQL 注入）。
# 仅写路径（detect_and_log）使用，读路径走各自的参数化查询。
_ALLOWED_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ALLOWED_TABLES = frozenset({
    "daily_bar", "derived_bar", "stock_basic", "market_stat",
    "ladder_day", "signal", "signal_outcome", "promotion_day",
    "pipeline_state", "alert", "review_report", "position",
    "hot_rank", "cal", "limit_pool_em", "data_revision",
})


def _validate_table(table: str) -> str:
    """校验表名：仅允许小写字母/数字/下划线，且必须在白名单内。"""
    if not isinstance(table, str) or not _ALLOWED_TABLE_RE.match(table):
        raise ValueError(f"非法表名: {table!r}")
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"未注册的表名: {table!r}")
    return table


def detect_and_log(table: str, conflict_cols: Sequence[str],
                   rows: list[tuple], conn=None,
                   columns: Sequence[str] | None = None) -> None:
    """upsert 前调用：探测「已存在将被覆盖」的 date → 留痕一条。

    date 在行内的位置以 columns 为准（conflict_cols 是键名子集，
    行内位置由全列序决定）。冲突键含 date 才记（按日聚合）；
    stock_basic 等无 date 列的全量快照表不在本审计面。
    探测失败不拦写入（观测面非依赖）。
    """
    if "date" not in conflict_cols or not rows:
        return
    di = list(columns or conflict_cols).index("date")
    dates = sorted({r[di] for r in rows})
    try:
        _validate_table(table)
        existing = _existing_dates(table, dates, conn)
        for d in existing:
            note = f"upsert 覆盖 {len(rows)} 行（键 {list(conflict_cols)}）"
            sql = ("INSERT INTO data_revision (table_name, trade_date,"
                   " detail) VALUES (%s,%s,%s)")
            if conn is not None:
                execute(sql, (table, d, note), conn=conn)
            else:
                execute(sql, (table, d, note))
            log.info("数据修订留痕 %s %s", table, d)
    except Exception:                            # noqa: BLE001
        log.debug("修订探测失败（不拦 upsert）")


def _existing_dates(table: str, dates: list[date], conn) -> set:
    """表中已存在的 date 子集（一次查全，跨批次）。"""
    _validate_table(table)
    sql = f"SELECT DISTINCT date FROM {table} WHERE date = ANY(%s)"
    if conn is not None:
        return {r[0] for r in conn.execute(sql, (dates,)).fetchall()}
    with transaction() as c:
        return {r[0] for r in c.execute(sql, (dates,)).fetchall()}


def affected() -> list[dict]:
    """受修订影响日期清单（新→旧）——重生成报告的核对输入。"""
    df = query_df(
        "SELECT table_name, trade_date, detail, created_at FROM"
        " data_revision ORDER BY trade_date DESC, id DESC")
    return [{"table": r.table_name, "date": r.trade_date.isoformat(),
             "detail": r.detail, "revised_at": r.created_at.isoformat()}
            for r in df.itertuples()]
