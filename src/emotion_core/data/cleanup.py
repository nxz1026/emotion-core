"""历史数据清理（docs/08 §8）。

老库废弃后，新库（emotion-core）的业务事实不删。
只清理可再生的辅助数据，删除前记录 data_revision。

清理对象（可再生/辅助）：
- ingest_progress：归档或删除（一次性写入，不再更新）
- limit_pool_em：保留 30 天（东财只覆盖近 30 交易日）
- alert：保留 90 天（已 ack 的告警）
- llm_call_log：保留 1,000 条（日志类）
- eval_result：保留 50 条（评估报告）
- 快照文件：保留 30 天

不清理（业务事实/历史判定）：
- daily_bar / derived_bar / market_stat / ladder_day / promotion_day
- signal / signal_outcome / position / trade_event / review_report
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.cleanup")

# 清理策略（表名 → (时间列, 保留天数)；None = 全删/归档）
RETENTION: dict[str, tuple[str, int | None]] = {
    "limit_pool_em": ("date", 30),
    "alert": ("created_at", 90),
    "llm_call_log": ("id", None),   # 按条数保留，见 KEEP_LATEST
    "eval_result": ("id", None),    # 按条数保留，见 KEEP_LATEST
    "ingest_progress": ("done_at", None),  # 归档或删除
}

KEEP_LATEST: dict[str, int] = {
    "llm_call_log": 1000,
    "eval_result": 50,
}

# 不清理的表（业务事实/历史判定）
PROTECTED: tuple[str, ...] = (
    "daily_bar", "derived_bar", "market_stat",
    "ladder_day", "promotion_day",
    "signal", "signal_outcome",
    "position", "trade_event",
    "review_report",
    "data_revision",  # 清理记录本身不清理
)


def cleanup_dry_run() -> dict[str, int]:
    """预览清理（不实际删除）。返回各表预计删除行数。"""
    now = datetime.now()
    plan: dict[str, int] = {}
    with transaction() as conn:
        with conn.cursor() as cur:
            for table, (col, days) in RETENTION.items():
                if table in PROTECTED:
                    continue
                if days is None and table not in KEEP_LATEST:
                    # ingest_progress：全删
                    cur.execute(f"SELECT count(*) FROM {table}")
                    plan[table] = cur.fetchone()[0]
                elif days is not None:
                    cur.execute(
                        f"SELECT count(*) FROM {table} WHERE {col} < %s",
                        (now - timedelta(days=days),)
                    )
                    plan[table] = cur.fetchone()[0]
                elif table in KEEP_LATEST:
                    keep = KEEP_LATEST[table]
                    cur.execute(f"SELECT count(*) FROM {table}")
                    total = cur.fetchone()[0]
                    cur.execute(
                        f"SELECT count(*) FROM (SELECT id FROM {table}"
                        f" ORDER BY id DESC LIMIT {keep}) t"
                    )
                    keep_cnt = cur.fetchone()[0]
                    plan[table] = total - keep_cnt
    return plan


def cleanup() -> dict[str, int]:
    """执行清理（幂等）。返回各表删除行数。"""
    now = datetime.now()
    deleted: dict[str, int] = {}
    with transaction() as conn:
        with conn.cursor() as cur:
            for table, (col, days) in RETENTION.items():
                if table in PROTECTED:
                    continue
                if table == "ingest_progress":
                    # 归档或删除（一次性写入，不再更新）
                    cur.execute(f"DELETE FROM {table}")
                    deleted[table] = cur.rowcount
                elif days is not None:
                    cur.execute(
                        f"DELETE FROM {table} WHERE {col} < %s",
                        (now - timedelta(days=days),)
                    )
                    deleted[table] = cur.rowcount
                elif table in KEEP_LATEST:
                    keep = KEEP_LATEST[table]
                    cur.execute(
                        f"DELETE FROM {table} WHERE id NOT IN"
                        f" (SELECT id FROM {table} ORDER BY id DESC LIMIT {keep})"
                    )
                    deleted[table] = cur.rowcount
    log.info("cleanup: %s", deleted)
    return deleted
