"""流水线状态（mark_running / mark_done / mark_failed / overview / failures）：不连库。

语义来源 lkl/services/pipeline.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 pipeline.py 模块文档）：写走 utils.db.execute、读走 utils.db.query_df、
mark_failed 的告警入口为函数内延迟导入 emotion_core.algorithms.alerts。

IO 全部桩掉（pipeline.execute、pipeline.query_df 记录 (sql, params) 并按帧返回；
alerts.record 换成 spy/抛错桩），真实库对账另跑（编排者阶段：同输入下与
lkl.services.pipeline 逐字段一致，含 SQL 文本与参数元组）。
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

import pandas as pd
import pytest

from emotion_core.algorithms import alerts as alerts_mod
from emotion_core.algorithms import pipeline

D0 = date(2026, 9, 26)
COLS = ["step", "status", "for_date", "detail",
        "started_at", "finished_at", "exit_code"]


class FakeDB:
    """execute / query_df 替身：记录 (sql, params)，query_df 返回预置帧。"""

    def __init__(self, frame: pd.DataFrame | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.frame = frame if frame is not None else pd.DataFrame(columns=COLS)

    def execute(self, sql: str, params=(), conn=None) -> int:
        self.calls.append((sql, tuple(params)))
        return 1

    def query_df(self, sql: str, params=(), conn=None) -> pd.DataFrame:
        self.calls.append((sql, tuple(params)))
        return self.frame


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(pipeline, "execute", fake.execute)
    monkeypatch.setattr(pipeline, "query_df", fake.query_df)
    return fake


def assert_sql(sql: str, *frags: str) -> None:
    for f in frags:
        assert f in sql, f"SQL 缺少 {f!r}:\n{sql}"


def one_call(db: FakeDB) -> tuple[str, tuple]:
    assert len(db.calls) == 1, db.calls
    return db.calls[0]


# ── mark_running ────────────────────────────────────────────
def test_mark_running_sql_params_and_reset(db):
    assert pipeline.mark_running("fetch", D0, "拉取中") is None
    sql, params = one_call(db)
    assert_sql(
        sql,
        "INSERT INTO pipeline_state (step, status, for_date, detail, started_at)",
        "VALUES (%s,'RUNNING',%s,%s, now())",          # 状态字面量 + started_at=now()
        "ON CONFLICT (step) DO UPDATE SET status='RUNNING'",   # 幂等覆盖上次
        "for_date=EXCLUDED.for_date",
        "detail=EXCLUDED.detail",
        "started_at=now()",                            # 重跑重置开始时刻
        "finished_at=null",                            # 清上次结束时刻
        "exit_code=null",                              # 清上次退出码
    )
    assert sql.count("%s") == 3
    assert params == ("fetch", D0, "拉取中")


def test_mark_running_defaults(db):
    pipeline.mark_running("derive")
    sql, params = one_call(db)
    assert params == ("derive", None, "")
    assert "status='RUNNING'" in sql and "now()" in sql


# ── mark_done ───────────────────────────────────────────────
@pytest.mark.parametrize("exit_code,status", [(0, "OK"), (1, "FAILED"),
                                              (2, "FAILED"), (7, "FAILED")])
def test_mark_done_status_by_exit_code(db, exit_code, status):
    assert pipeline.mark_done("emotion", D0, "done", exit_code) is None
    sql, params = one_call(db)
    assert_sql(
        sql,
        "INSERT INTO pipeline_state (step, status, for_date, detail,"
        " started_at, finished_at, exit_code)",
        "VALUES (%s,%s,%s,%s, now(), now(), %s)",      # 两端 now()
        "ON CONFLICT (step) DO UPDATE SET status=EXCLUDED.status",
        "for_date=EXCLUDED.for_date",
        "detail=EXCLUDED.detail",
        "finished_at=now()",
        "exit_code=EXCLUDED.exit_code",
    )
    assert sql.count("%s") == 5
    assert params == ("emotion", status, D0, "done", exit_code)


def test_mark_done_defaults(db):
    pipeline.mark_done("ladder")
    assert one_call(db)[1] == ("ladder", "OK", None, "", 0)


# ── mark_failed ─────────────────────────────────────────────
def test_mark_failed_truncates_and_enqueues_alert(db, monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(alerts_mod, "record",
                        lambda *a: seen.append(a))
    long_detail = "e" * 600
    pipeline.mark_failed("review", long_detail, D0)

    sql, params = one_call(db)
    assert_sql(
        sql,
        "INSERT INTO pipeline_state (step, status, for_date, detail,"
        " finished_at, exit_code) VALUES (%s,'FAILED',%s,%s, now(), 1)",
        "ON CONFLICT (step) DO UPDATE SET status='FAILED'",
        "for_date=EXCLUDED.for_date",
        "detail=EXCLUDED.detail",
        "finished_at=now()",
        "exit_code=1",
    )
    assert sql.count("%s") == 3
    step, for_date_param, db_detail = params
    assert (step, for_date_param) == ("review", D0)
    assert db_detail == long_detail[:500]
    assert len(db_detail) == 500
    # 告警文案截断到 200，level/source 固定
    assert seen == [("ERROR", "pipeline", f"步骤 review 失败：{'e' * 200}")]
    assert len(seen[0][2]) == len("步骤 review 失败：") + 200


def test_mark_failed_default_for_date(db, monkeypatch):
    monkeypatch.setattr(alerts_mod, "record", lambda *a: None)
    pipeline.mark_failed("theme", "短")
    assert one_call(db)[1] == ("theme", None, "短")


def test_mark_failed_swallows_alert_error(db, monkeypatch, caplog):
    calls: list[tuple] = []

    def boom(*a):
        calls.append(a)
        raise RuntimeError("alert 表不可用")

    monkeypatch.setattr(alerts_mod, "record", boom)
    caplog.set_level(logging.DEBUG, logger="emotion_core.pipeline")

    pipeline.mark_failed("fetch", "拉取失败", D0)      # 不得冒泡

    assert calls == [("ERROR", "pipeline", "步骤 fetch 失败：拉取失败")]
    sql, params = one_call(db)                          # FAILED 记录仍落库
    assert sql.count("%s") == 3 and params == ("fetch", D0, "拉取失败")
    assert any(r.levelno == logging.DEBUG
               and r.name == "emotion_core.pipeline"
               and r.getMessage() == "告警入队失败（不拦 FAILED 记录）"
               for r in caplog.records)


# ── overview / failures ─────────────────────────────────────
def frame(dtype=object) -> pd.DataFrame:
    """pipeline_state 帧（dtype=object 让 NULL 保持 Python None，见 NaT 用例）。"""
    return pd.DataFrame([
        {"step": "fetch", "status": "OK", "for_date": D0, "detail": "ok",
         "started_at": datetime(2026, 9, 26, 10, 0, 0),
         "finished_at": datetime(2026, 9, 26, 10, 0, 12, 340000),
         "exit_code": 0},
        {"step": "derive", "status": "FAILED", "for_date": None, "detail": "bad",
         "started_at": datetime(2026, 9, 26, 11, 0, 0),
         "finished_at": None, "exit_code": 1},
        {"step": "unknown-step", "status": "OK", "for_date": None, "detail": None,
         "started_at": None,
         "finished_at": datetime(2026, 9, 26, 12, 0, 0), "exit_code": None},
    ], columns=COLS, dtype=dtype)


def test_overview_shape(db):
    db.frame = frame()
    out = pipeline.overview()

    sql, params = one_call(db)
    assert_sql(
        sql,
        "SELECT step, status, for_date, detail, started_at, finished_at,"
        " exit_code FROM pipeline_state",
        "ORDER BY coalesce(finished_at, started_at) DESC",   # 最近活动在前
    )
    assert params == ()

    assert len(out) == 3
    fetch, derive, unknown = out
    assert set(fetch) == {"step", "status", "for_date", "detail", "duration_s",
                          "exit_code", "rerun"}
    # 两端非 None → round(秒差, 1)
    assert fetch == {"step": "fetch", "status": "OK", "for_date": "2026-09-26",
                     "detail": "ok", "duration_s": 12.3, "exit_code": 0,
                     "rerun": "lkl fetch sync"}
    # 任一端 None → duration None；for_date None → None
    assert derive["duration_s"] is None
    assert derive["for_date"] is None
    assert derive["rerun"] == "lkl derive"
    assert unknown["duration_s"] is None
    assert unknown["for_date"] is None
    assert unknown["rerun"] is None            # 未知 step 无反查命令


def test_overview_rounds_one_decimal(db):
    db.frame = pd.DataFrame([
        {"step": "theme", "status": "OK", "for_date": None, "detail": "",
         "started_at": datetime(2026, 9, 26, 10, 0, 0),
         "finished_at": datetime(2026, 9, 26, 10, 0, 0, 123456),
         "exit_code": 0},
    ])
    assert pipeline.overview()[0]["duration_s"] == 0.1


def test_overview_nat_timestamp_is_not_none(db):
    """真实库路径的事实：列含 NULL → pandas 推断 datetime64，NULL 变 NaT（非 None）。

    与 lkl 同款 DataFrame 构造（`pd.DataFrame(cur.fetchall(), columns=cols)`），
    故 `r.finished_at is not None` 对 NaT 成立、`round(NaT.total_seconds(), 1)` = nan——
    RUNNING 中（finished_at 为 NULL）的步骤 duration_s 是 nan 而非 None。逐字搬运下
    该口径与 lkl 一致；本用例锁住，防搬运后静默改口径。
    """
    db.frame = frame(dtype=None)          # 默认推断：含 None 的时间列为 datetime64[ns]
    out = pipeline.overview()
    assert out[1]["step"] == "derive" and out[1]["status"] == "FAILED"
    assert math.isnan(out[1]["duration_s"])


def test_overview_empty(db):
    assert pipeline.overview() == []


def test_failures_only_failed(db):
    db.frame = frame()
    out = pipeline.failures()
    assert [s["step"] for s in out] == ["derive"]
    assert out[0]["status"] == "FAILED"


def test_failures_empty(db):
    db.frame = pd.DataFrame(columns=COLS)
    assert pipeline.failures() == []
