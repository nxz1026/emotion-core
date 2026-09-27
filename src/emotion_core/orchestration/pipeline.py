"""编排层的流水线状态打点：mark_running / mark_done / mark_failed（docs/08 §3.3、§3.5）。

语义：latest-state（UPSERT 覆盖，`pipeline_state.step` 主键）——运维一眼看到每步**当前**状态，
不是 append-only 跑批历史。失败即入 `alert` 表（告警闭环，docs/08 §3.5「不静默」）。

本文件是**门面，不是第二份实现**：写库 SQL 与告警入队只有一处
（`algorithms/pipeline.py`，逐字搬 lkl/services/pipeline.py，锁定于
tests/unit/test_pipeline.py）。同表两个写者 = 两套截断口径/告警文案，迟早漂移；
编排层需要的只是**更窄的调用面**，故在此收敛后转发：

| 编排层（本文件）        | 实现（algorithms/pipeline）              | 差异 |
|------------------------|------------------------------------------|------|
| `mark_running(step, d)`| `mark_running(step, d, detail="")`       | 编排不需要起步 detail |
| `mark_done(step, d, detail)` | `mark_done(step, d, detail, exit_code=0)` | 编排层只表达「跑完」，退出码非 0 的场景走 mark_failed |
| `mark_failed(step, d, error)` | `mark_failed(step, error, d)`        | 编排层用 `error` 关键字名（与运行结果语义对齐），实参位次不同 |

告警：`mark_failed` 经 `algorithms.alerts.record("ERROR", "pipeline", ...)` 入队——它带 level
白名单校验与 `INSERT .. RETURNING id`，列名是 **(level, source, detail)**，不是 message
（`data/schema.py` 的 alert DDL 即如此；写 message 会 UndefinedColumn）。
告警是旁路：入队失败只 debug，不拦 FAILED 事实落库。

表归属：`pipeline_state` / `alert` 建在 emotion_core 库（`utils/db.py` 的 `_DBNAME`），
DDL 见 `data/schema.py`，与 lkl 同名同列，仅库不同。
"""
from __future__ import annotations

from datetime import date

from emotion_core.algorithms import pipeline as _impl


def mark_running(step: str, for_date: date | None = None) -> None:
    """标记步骤为 RUNNING 状态（幂等覆盖上次记录，清 finished_at/exit_code）。"""
    _impl.mark_running(step, for_date)


def mark_done(step: str, for_date: date | None = None, detail: str = "") -> None:
    """标记步骤为 OK 状态（记 finished_at 与耗时起点差）。"""
    _impl.mark_done(step, for_date, detail)


def mark_failed(step: str, for_date: date | None = None, error: str = "") -> None:
    """标记步骤为 FAILED 状态 + 入 alert 表（告警入队失败不抛，只 debug）。"""
    _impl.mark_failed(step, error, for_date)
