"""eval_result 落库（§9 第 8 条）：校准提案不再只落 reports/*.md。

该表此前只有 DDL 与清理策略（保留最新 50 条），没有任何写入方。现在
`calibrate.proposal()` 在写报告的同时落一行（params=窗口与数据起点、
result=markdown + 提案 + 样本统计、config_hash=产出时的配置指纹）。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from emotion_core.services import calibrate
from emotion_core.utils.config import config_hash

D = date(2026, 9, 25)


@contextmanager
def _fake_tx(rec: list):
    class _Conn:
        def execute(self, sql, params=()):
            rec.append((sql, params))
            return SimpleNamespace(rowcount=1)
    yield _Conn()


def test_persist_eval_result_sql_and_params(monkeypatch):
    rec: list = []
    monkeypatch.setattr(calibrate, "transaction", lambda: _fake_tx(rec))

    n = calibrate._persist_eval_result(
        "calib", date(2026, 8, 1), D, {"days": 60}, {"markdown": "# 提案"})

    sql, params = rec[0]
    assert "INSERT INTO eval_result" in sql
    assert "(name, start_date, end_date, params," in sql and "config_hash)" in sql
    assert "%s::jsonb" in sql
    assert n == 1
    assert params[0] == "calib" and params[1] == date(2026, 8, 1) and params[2] == D
    assert json.loads(params[3]) == {"days": 60}
    assert json.loads(params[4]) == {"markdown": "# 提案"}
    assert params[5] == config_hash() and len(params[5]) == 12


def test_proposal_writes_report_and_eval_result(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(calibrate, "_quality_stats",
                        lambda end, days: (pd.DataFrame(), pd.DataFrame(),
                                           date(2026, 8, 1)))
    monkeypatch.setattr(calibrate, "_threshold_sensitivity", lambda: "② 段")
    monkeypatch.setattr(calibrate, "_outcome_summary", lambda: "③ 段")
    monkeypatch.setattr(calibrate, "_proposals", lambda f, r, d: ["维持现行参数"])

    seen: dict = {}

    def fake_persist(name, start, end, params, result):
        seen.update(name=name, start=start, end=end, params=params, result=result)
        return 1

    monkeypatch.setattr(calibrate, "_persist_eval_result", fake_persist)

    path = calibrate.proposal(D, days=60)

    assert Path(path).name == "calib-2026-09-25.md" and Path(path).exists()
    assert "# 校准提案 2026-09-25" in Path(path).read_text(encoding="utf-8")
    assert seen["name"] == "calib"
    assert (seen["start"], seen["end"]) == (date(2026, 8, 1), D)
    assert seen["params"]["days"] == 60
    assert seen["params"]["min_coverage"] == 0.90
    assert seen["result"]["proposals"] == ["维持现行参数"]
    assert seen["result"]["report_path"] == str(Path("reports") / "calib-2026-09-25.md")
