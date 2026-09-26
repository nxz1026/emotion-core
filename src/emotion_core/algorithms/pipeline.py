"""流水线状态（产品 A2-6）：每步成功/失败/耗时落 pipeline_state 表。

语义逐字照搬 lkl/services/pipeline.py（docs/07-算法层搬运方案.md §3.4，94 行）：
主链命令（fetch/derive/emotion/ladder/theme/review/trade-export/trade-import/
trade-sync）入库时打点；`lkl pipeline` 只读展示逐步状态 + 失败步骤的重跑命令。
守卫宏 mark_failed 由各命令 except 兜底调用。判定逻辑（RERUN 表、状态字符串
RUNNING/OK/FAILED、exit_code==0 判定、`[:500]`/`[:200]` 截断长度、耗时 round 1 位、
rerun=RERUN.get(step)、排序键）零改动。

与 lkl 的差异（全部是契约/IO 适配，不涉判定逻辑）：
1. 入口迁到本仓持久层：`from lkl.utils import db` → `from emotion_core.utils.db import
   execute, query_df`（`execute(sql, params=(), conn=None) -> int`、
   `query_df(sql, params=(), conn=None) -> pd.DataFrame`，与 lkl 同签名）。三条
   INSERT .. ON CONFLICT (step) DO UPDATE 与 overview 的 SELECT / ORDER BY 文本逐字未改
   （含 status='RUNNING' / for_date=EXCLUDED.for_date / finished_at=null / exit_code=null、
   finished_at=now() / exit_code=EXCLUDED.exit_code、`ORDER BY coalesce(finished_at,
   started_at) DESC`），参数顺序与元组内容逐字未改。
2. mark_failed 的告警入口由函数内延迟导入 `lkl.services.alerts` 改为
   `emotion_core.algorithms.alerts`（第 7 层同层内引用，仍**不在模块顶层导入**，避免
   告警模块的依赖影响本模块可导入性）；结构不变——同一 try/except Exception，
   失败仅 `log.debug("告警入队失败（不拦 FAILED 记录）")`，**不抛**（告警是旁路，
   FAILED 事实已落库）。
3. 日志名 `lkl.pipeline` → `emotion_core.pipeline`（本仓 logger 命名空间统一）。
4. 落库/告警截断与中文文案逐字保留：detail[:500]（库）、detail[:200]（告警）、
   f"步骤 {step} 失败：{detail[:200]}"、source="pipeline"、level="ERROR"。
   重跑命令文案（RERUN 的 9 个值）仍是 lkl 原字符串（`lkl <cmd>`），本轮不改——
   CLI 命令名改写不在搬运范围内。

真实库对账另跑（编排者阶段）：同输入下与 lkl.services.pipeline 逐字段一致；
离线用例见 tests/unit/test_pipeline.py（无 DB，DB 与告警全部桩掉）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.utils.db import execute, query_df

log = logging.getLogger("emotion_core.pipeline")

# 步骤名 -> 重跑命令（失败时给用户可复制的一行命令）
RERUN = {
    "fetch":      "lkl fetch sync",
    "derive":     "lkl derive",
    "emotion":    "lkl emotion",
    "ladder":     "lkl ladder <date>",
    "theme":      "lkl theme <date>",
    "review":     "lkl review <date>",
    "trade-export": "lkl trade export",
    "trade-import": "lkl trade import",
    "trade-sync": "lkl trade sync",
}


def mark_running(step: str, for_date: date | None = None,
                 detail: str = "") -> None:
    """步骤开始：status=RUNNING，记 started_at（幂等覆盖上次）。"""
    execute(
        "INSERT INTO pipeline_state (step, status, for_date, detail,"
        " started_at) VALUES (%s,'RUNNING',%s,%s, now())"
        " ON CONFLICT (step) DO UPDATE SET status='RUNNING',"
        " for_date=EXCLUDED.for_date, detail=EXCLUDED.detail,"
        " started_at=now(), finished_at=null, exit_code=null",
        (step, for_date, detail))


def mark_done(step: str, for_date: date | None = None,
              detail: str = "", exit_code: int = 0) -> None:
    """步骤结束：status=OK/FAILED，记 finished_at + exit_code + 耗时。"""
    status = "OK" if exit_code == 0 else "FAILED"
    execute(
        "INSERT INTO pipeline_state (step, status, for_date, detail,"
        " started_at, finished_at, exit_code)"
        " VALUES (%s,%s,%s,%s, now(), now(), %s)"
        " ON CONFLICT (step) DO UPDATE SET status=EXCLUDED.status,"
        " for_date=EXCLUDED.for_date, detail=EXCLUDED.detail,"
        " finished_at=now(), exit_code=EXCLUDED.exit_code",
        (step, status, for_date, detail, exit_code))


def mark_failed(step: str, detail: str,
                for_date: date | None = None) -> None:
    """步骤异常兜底：FAILED + 详情 + 入 alert（A2-7 闭环，不静默）。"""
    execute(
        "INSERT INTO pipeline_state (step, status, for_date, detail,"
        " finished_at, exit_code) VALUES (%s,'FAILED',%s,%s, now(), 1)"
        " ON CONFLICT (step) DO UPDATE SET status='FAILED',"
        " for_date=EXCLUDED.for_date, detail=EXCLUDED.detail,"
        " finished_at=now(), exit_code=1",
        (step, for_date, detail[:500]))
    try:
        from emotion_core.algorithms import alerts
        alerts.record("ERROR", "pipeline", f"步骤 {step} 失败：{detail[:200]}")
    except Exception:                            # noqa: BLE001
        log.debug("告警入队失败（不拦 FAILED 记录）")


def overview() -> list[dict]:
    """全部步骤状态（升序按最近活动），失败步骤附重跑命令。"""
    df = query_df(
        "SELECT step, status, for_date, detail, started_at, finished_at,"
        " exit_code FROM pipeline_state"
        " ORDER BY coalesce(finished_at, started_at) DESC")
    out = []
    for r in df.itertuples():
        dur = None
        if r.started_at is not None and r.finished_at is not None:
            dur = round((r.finished_at - r.started_at).total_seconds(), 1)
        out.append({"step": r.step, "status": r.status,
                    "for_date": r.for_date.isoformat() if r.for_date else None,
                    "detail": r.detail, "duration_s": dur,
                    "exit_code": r.exit_code,
                    "rerun": RERUN.get(r.step)})
    return out


def failures() -> list[dict]:
    """当前 FAILED 步骤（告警面板数据源：未恢复的失败）。"""
    return [s for s in overview() if s["status"] == "FAILED"]
