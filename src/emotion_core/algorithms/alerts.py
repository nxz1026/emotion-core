"""风险异常告警闭环：分级入库 + 幂等 + 确认 + 触达（第 7 层，逐字搬 lkl/services/alerts.py）。

语义逐字照搬 lkl/services/alerts.py（docs/07 §3.4）：level ∈ INFO/WARN/ERROR；ack 后
acked_at 置位（未 ack = 待处理队列）；去重按 **同 source 同 detail** 判「已有未确认」——
同一问题不重复轰炸、**不同问题不被静默吞掉**；webhook 只推未确认 WARN/ERROR，DB 是唯一
事实源。判定逻辑（level 白名单、去重键、未确认过滤、排序、推送过滤与截断）零改动。

三层职责（与 lkl 同名同序）：
- `record` / `record_dedup`：写入路径（pipeline 失败步骤 / health 断档 / compare 漂移 / review 缺数）
- `pending` / `ack`：Dashboard 告警面板数据源与人工确认
- `push_pending_webhook`：可选触达（默认关）

与 lkl 的差异（全部是 IO 适配，不涉判定逻辑）：
1. 写库走 utils.db；`record` 用 `INSERT .. RETURNING id` 取**实际插入 id**——SQL 与 lkl
   同句（lkl 也写了 RETURNING id，只是被 `db.execute` 的 rowcount 丢弃，再补一句
   `SELECT max(id)`）。单线程管道下返回值与 lkl 逐字相同；并发插入时 `max(id)` 会取到
   他人的 id，RETURNING 不会。
2. webhook 经 `_push` **延迟导入**第 10 层 notify（默认关、尚未搬运）：import 不在模块
   顶层，故本模块可独立导入/测试；notify 未就位时 `_push` 记 warning 并返回 False
   （**不抛**）——推送是可选触达，DB 是唯一事实源，缺层不得阻断 pipeline。
3. 表名 / 列名 / SQL 谓词与 lkl 一致；不新增字段、不建索引（去重是应用层语义）。

对账：真实库 alert 表差分（同输入下与 lkl.services.alerts 逐字段一致）与离线用例分离，
离线用例见 tests/unit/test_alerts.py（无 DB，DB 全部桩掉）。
"""
from __future__ import annotations

import logging
from typing import Any

from emotion_core.utils.db import execute, query_df

log = logging.getLogger("emotion_core.alerts")

LEVELS = ("INFO", "WARN", "ERROR")


def record(level: str, source: str, detail: str) -> int | None:
    """入告警（非法 level 抛错——告警不能被静默降级）；返回 id。"""
    if level not in LEVELS:
        raise ValueError(f"level 须为 {'/'.join(LEVELS)}，got {level}")
    df = query_df("INSERT INTO alert (level, source, detail) VALUES (%s,%s,%s)"
                  " RETURNING id", (level, source, detail))
    row = df["id"].iloc[0] if not df.empty else None
    log.warning("告警入队 [%s/%s] %s (id=%s)", level, source, detail, row)
    return int(row) if row is not None else None


def record_dedup(level: str, source: str, detail: str) -> int | None:
    """幂等入队：同 source **同 detail** 已有未确认告警 → 跳过（返回 None）。

    lkl 2026-09-18 巡检（P2-7）：`review._alert_usability` 此前直连 `record()`，同一问题
    重复入队（09-08「东财池缺失」一天内入 4 条且 9 天无人确认）。此处按 (source, detail)
    去重——同一问题不重复轰炸，**不同问题不被静默吞掉**（`health.record_once` 是按 source 去重）。
    """
    if level not in LEVELS:
        raise ValueError(f"level 须为 {'/'.join(LEVELS)}，got {level}")
    dup = query_df(
        "SELECT 1 FROM alert WHERE source=%s AND detail=%s AND acked_at IS NULL"
        " LIMIT 1", (source, detail))
    if not dup.empty:
        return None
    return record(level, source, detail)


def pending(limit: int = 100) -> list[dict]:
    """未确认告警（新→旧），Dashboard 告警面板数据源。"""
    df = query_df(
        "SELECT id, level, source, detail, created_at FROM alert"
        " WHERE acked_at IS NULL ORDER BY id DESC LIMIT %s", (limit,))
    return [{"id": int(r.id), "level": r.level, "source": r.source,
             "detail": r.detail,
             "created_at": r.created_at.isoformat()} for r in df.itertuples()]


def ack(ids: list[int]) -> int:
    """批量确认（人已处理）；不存在的 id 静默跳过，返回确认数。"""
    if not ids:
        return 0
    execute("UPDATE alert SET acked_at=now() WHERE id = ANY(%s)"
            " AND acked_at IS NULL", (ids,))
    df = query_df("SELECT count(*) n FROM alert WHERE id = ANY(%s)"
                  " AND acked_at IS NOT NULL", (ids,))
    return int(df["n"].iloc[0])


def _push(md: str, trade_date: Any = None) -> bool:
    """webhook 触达接缝（唯一知道 notify 的地方）：延迟导入，notify 未就位则静默跳过。

    notify（第 10 层，默认关）尚未搬运：此处 catch ImportError 返回 False 并记 warning，
    不把「可选触达缺层」升级成 pipeline 崩溃（lkl notify.push 的失败也是不抛的）。
    """
    try:
        from emotion_core.algorithms import notify
    except ImportError:
        log.warning("notify 未就位（第 10 层，默认关），webhook 推送跳过")
        return False
    return notify.push(md, trade_date)


def push_pending_webhook() -> None:
    """未确认 ERROR/WARN 推 webhook（未配置/提取空均静默）。"""
    items = [a for a in pending(20) if a["level"] in ("WARN", "ERROR")]
    if not items:
        return
    body = "\n".join(f"[{a['level']}] {a['source']}: {a['detail']}"
                     for a in items[:8])
    # notify._summary 只认「## ⓪ 速览」段——告警文本包一层可提取结构
    md = "## ⓪ 速览\n\n" + f"**未确认告警 {len(items)} 条**\n\n{body}\n"
    _push(md, None)
