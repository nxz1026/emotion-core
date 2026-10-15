"""`scripts/backfill_limit_state_wind.py` 的待办口径（truncated/suspect 重试上限）。

脚本不在包里，故按路径加载；只替掉它的 `query_df`，不碰网络与库。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from emotion_core.services import limit_state_wind as lsw

_SCRIPT = (Path(__file__).resolve().parents[2] / "scripts"
           / "backfill_limit_state_wind.py")


def _load():
    spec = importlib.util.spec_from_file_location("_bfl_limit_state", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()
D1, D2, D3 = date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)


class _FakeQuery:
    """按 SQL 文本分发：`derived_bar` 那支回交易日，台账那支回已完成日期。"""

    def __init__(self, days, done):
        self.days, self.done = days, done
        self.calls: list[tuple] = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if "FROM derived_bar" in sql:
            return pd.DataFrame({"date": self.days})
        return pd.DataFrame({"date": self.done})


@pytest.fixture
def fake_query(monkeypatch):
    fq = _FakeQuery([D1, D2, D3], [D2])
    monkeypatch.setattr(mod, "query_df", fq)
    return fq


class TestTargetDates:
    def test_filters_done_days(self, fake_query):
        assert mod.target_dates(D1, D3, "up") == [D1, D3]

    def test_retry_cap_and_statuses_reach_sql(self, fake_query):
        mod.target_dates(D1, D3, "up", max_retries=3)
        sql, params = fake_query.calls[-1]
        assert "retries >= %s" in sql
        assert params[1] == lsw.STATUS_OK                     # ok 永不重抓
        assert set(params[2]) == set(mod.RETRY_STATUSES)      # truncated + suspect
        assert params[3] == 3
        assert params[0] == "up"

    def test_redo_skips_ledger_query(self, fake_query):
        assert mod.target_dates(D1, D3, "up", redo=True) == [D1, D2, D3]
        assert len(fake_query.calls) == 1

    def test_default_max_retries_is_two(self, fake_query):
        mod.target_dates(D1, D3, "up")
        assert fake_query.calls[-1][1][3] == mod.DEFAULT_MAX_RETRIES == 2
