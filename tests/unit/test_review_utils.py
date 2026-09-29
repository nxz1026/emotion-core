"""algorithms/review/utils.py 格式化工具函数测试。"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.algorithms.review import utils as rv


class TestPct:
    def test_normal(self):
        assert rv._pct(0.5) == "50.0%"
        assert rv._pct(0.123) == "12.3%"

    def test_none(self):
        assert rv._pct(None) == "—"

    def test_nan(self):
        assert rv._pct(float("nan")) == "—"

    def test_zero(self):
        assert rv._pct(0) == "0.0%"


class TestNum:
    def test_default_prec(self):
        assert rv._num(3.14159) == "3.14"

    def test_custom_prec(self):
        assert rv._num(3.14159, 3) == "3.142"

    def test_none(self):
        assert rv._num(None) == "—"

    def test_nan(self):
        assert rv._num(float("nan")) == "—"

    def test_int(self):
        assert rv._num(42) == "42.00"


class TestPctv:
    def test_normal(self):
        assert rv._pctv(5.5) == "5.50%"

    def test_none(self):
        assert rv._pctv(None) == "—"

    def test_nan(self):
        assert rv._pctv(float("nan")) == "—"


class TestDeltaOf:
    def test_no_previous(self):
        result = rv._delta_of("10.5", None, "涨停家数")
        assert "昨日无数据" in result

    def test_same_value(self):
        result = rv._delta_of("10.5", "10.5", "涨停家数")
        assert "持平" in result

    def test_changed(self):
        result = rv._delta_of("10.5", "8.0", "涨停家数")
        assert "8.0 → 10.5" in result

    def test_cur_none(self):
        assert rv._delta_of(None, "8.0", "涨停家数") == ""

    def test_with_suffix(self):
        result = rv._delta_of("5.2", "4.8", "炸板率", "%")
        assert "%" in result


class TestFmtStat:
    def test_basic(self):
        cur = {
            "limit_up_count": 50,
            "bomb_rate": 0.2,
            "zt_performance_mean": 2.5,
            "zt_performance_median": 1.8,
            "max_limit_days": 8,
            "tradable_max_days": 6,
            "oneword_ratio": 0.1,
            "limit_down_count": 5,
        }
        prev = {
            "limit_up_count": 45,
            "bomb_rate": 0.18,
            "zt_performance_mean": 2.0,
            "zt_performance_median": 1.5,
            "max_limit_days": 7,
            "tradable_max_days": 6,
            "oneword_ratio": 0.12,
            "limit_down_count": 3,
        }
        result = rv._fmt_stat(cur, prev)
        assert "涨停家数" in result
        assert "炸板率" in result
        assert "50" in result
        assert "45" in result

    def test_none_cur(self):
        result = rv._fmt_stat(None, None)
        assert "指标" in result  # 表头仍在


class TestEnvCaveat:
    def test_not_favorable(self):
        de = {"rating": "NEUTRAL", "goods": []}
        assert rv._env_caveat(de) == ""

    def test_favorable_no_unknown(self):
        de = {"rating": "FAVORABLE", "goods": [{"ok": True, "cond": "G2"}]}
        assert rv._env_caveat(de) == ""

    def test_favorable_with_unknown(self):
        de = {"rating": "FAVORABLE", "goods": [
            {"ok": True, "cond": "G1"},
            {"ok": None, "cond": "G2 题材关联"},
        ]}
        result = rv._env_caveat(de)
        assert "G2" in result
        assert "尚无数据" in result


class TestEnvConflict:
    def test_favorable_and_ebb(self):
        d = {
            "dragon_env": {"rating": "FAVORABLE", "bads": []},
            "stat": {"phase": "退潮"},
            "window": "STANDARD",
        }
        result = rv._env_conflict(d)
        assert len(result) == 2
        assert "口径冲突" in result[0]

    def test_unfavorable_and_enhanced(self):
        d = {
            "dragon_env": {"rating": "UNFAVORABLE", "goods": [],
                           "bads": [{"ok": True, "cond": "G3 龙头断板"}]},
            "stat": {"phase": "发酵"},
            "window": "ENHANCED",
        }
        result = rv._env_conflict(d)
        assert len(result) == 2
        assert "坏土壤" in result[0]

    def test_no_conflict(self):
        d = {
            "dragon_env": {"rating": "NEUTRAL", "bads": []},
            "stat": {"phase": "发酵"},
            "window": "STANDARD",
        }
        assert rv._env_conflict(d) == ()

    def test_no_dragon(self):
        d = {"dragon_env": None, "stat": {}, "window": "NONE"}
        assert rv._env_conflict(d) == ()


class TestLadderTag:
    @dataclasses.dataclass
    class Row:
        name: str
        code: str
        is_exchange: bool
        bomb_times: int
        cont_days: int = 1

    def test_normal(self):
        r = self.Row("贵州茅台", "600000", True, 0)
        result = rv._ladder_tag(r)
        assert "贵州茅台(600000)" in result

    def test_bomb(self):
        r = self.Row("华海药业", "600001", True, 3)
        result = rv._ladder_tag(r)
        assert "开板3(东财)" in result

    def test_oneword(self):
        r = self.Row("一字板", "600002", False, 0)
        result = rv._ladder_tag(r)
        assert "一字" in result


class TestSealNotes:
    @dataclasses.dataclass
    class Row:
        name: str
        code: str
        bomb_times: int
        cont_days: int

    def test_basic(self):
        rows = [self.Row("华海药业", "600001", 2, 3)]
        seal_map = {"600001": ("09:35", "14:20")}
        result = rv._seal_notes(rows, seal_map)
        assert len(result) == 1
        assert "首封09:35" in result[0]

    def test_low_cont_days_filtered(self):
        rows = [self.Row("低板", "600002", 1, 1)]
        seal_map = {"600002": ("09:30", "10:00")}
        result = rv._seal_notes(rows, seal_map)
        assert len(result) == 0

    def test_no_bomb_filtered(self):
        rows = [self.Row("无炸板", "600003", 0, 3)]
        seal_map = {}
        result = rv._seal_notes(rows, seal_map)
        assert len(result) == 0


class TestFmtCatalyst:
    def test_empty(self):
        tags = pd.DataFrame(columns=["catalyst_source", "cont_days", "code",
                                     "name", "primary_theme", "catalyst",
                                     "evidence_date", "confidence"])
        assert rv._fmt_catalyst(tags) == ""

    def test_announcement(self):
        tags = pd.DataFrame({
            "catalyst_source": ["announcement"],
            "cont_days": [3],
            "code": ["600000"],
            "name": ["贵州茅台"],
            "primary_theme": ["白酒"],
            "catalyst": ["业绩预增"],
            "evidence_date": ["2024-06-15"],
            "confidence": ["HIGH"],
        })
        result = rv._fmt_catalyst(tags)
        assert "催化剂证据" in result
        assert "贵州茅台" in result

    def test_high_cont(self):
        tags = pd.DataFrame({
            "catalyst_source": ["concept"],
            "cont_days": [4],
            "code": ["600001"],
            "name": ["华海药业"],
            "primary_theme": ["医药"],
            "catalyst": [None],
            "evidence_date": [None],
            "confidence": [None],
        })
        result = rv._fmt_catalyst(tags)
        assert "无事件催化" in result


class TestFmtCaliber:
    def test_basic(self):
        c = {
            "universe": "全A",
            "include_bj": False,
            "include_20cm": True,
            "snapshot_time": "15:00",
            "limit_method": "东财池",
            "counts_self": {"zt": 50, "dt": 3},
            "counts_em_pool": {"zt": 52, "dt": 2},
            "diff_expectation": "差异在预期内",
        }
        result = rv._fmt_caliber(c)
        assert "全A" in result
        assert "50" in result
        assert "ST" in result


class TestUsability:
    def test_ok(self):
        d = {"quality": {"diff": pd.DataFrame(), "warns": []}}
        result = rv._usability(d)
        assert result["state"] == "OK"
        assert "质检通过" in result["note"]

    def test_unknown(self):
        d = {"quality": {"diff": None, "warns": []}}
        result = rv._usability(d)
        assert result["state"] == "UNKNOWN"
        assert "东财池缺失" in result["note"]

    def test_partial_diff(self):
        d = {"quality": {"diff": pd.DataFrame({"a": [1]}), "warns": []}}
        result = rv._usability(d)
        assert result["state"] == "PARTIAL"

    def test_partial_warns(self):
        d = {"quality": {"diff": pd.DataFrame(), "warns": ["缺数"]}}
        result = rv._usability(d)
        assert result["state"] == "PARTIAL"
        assert "缺数" in result["note"]


class TestCounterItems:
    def test_empty(self):
        d = {"stat": {"top_broke": True}, "dragon_env": {}, "quality": {"warns": [], "diff": pd.DataFrame()}}
        assert rv._counter_items(d) == []

    def test_mean_median_conflict(self):
        d = {"stat": {"zt_performance_mean": 2.0, "zt_performance_median": -1.0, "top_broke": True,
                      "max_limit_days": None, "tradable_max_days": None},
             "dragon_env": {}, "quality": {"warns": [], "diff": pd.DataFrame()}}
        result = rv._counter_items(d)
        assert len(result) == 1
        assert "方向相反" in result[0]

    def test_max_vs_tradable(self):
        d = {"stat": {"max_limit_days": 8, "tradable_max_days": 5},
             "dragon_env": {}, "quality": {"warns": [], "diff": pd.DataFrame()}}
        result = rv._counter_items(d)
        assert any("名义" in item for item in result)

    def test_top_broke_none(self):
        d = {"stat": {"top_broke": None}, "dragon_env": {},
             "quality": {"warns": [], "diff": pd.DataFrame()}}
        result = rv._counter_items(d)
        assert any("断板" in item for item in result)


class TestStripPositionBlock:
    def test_keeps_safe_sections(self):
        md = "## 速览\n市场情绪\n## 情绪\n发酵\n## 持仓\n不应出现"
        result = rv._strip_position_block(md)
        assert "速览" in result
        assert "发酵" in result
        assert "持仓" not in result

    def test_removes_non_safe(self):
        md = "## 明日参考\n不应出现\n## LLM自评\n也不应出现"
        result = rv._strip_position_block(md)
        assert result == ""

    def test_empty_string(self):
        assert rv._strip_position_block("") == ""


class TestJsonable:
    def test_dataframe(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = rv._jsonable(df)
        assert result == [{"a": 1}, {"a": 2}]

    def test_date(self):
        result = rv._jsonable(date(2024, 6, 15))
        assert result == "2024-06-15"

    def test_decimal(self):
        result = rv._jsonable(Decimal("3.14"))
        assert result == 3.14

    def test_numpy(self):
        import numpy as np
        result = rv._jsonable(np.int64(42))
        assert result == 42

    def test_type_error(self):
        with pytest.raises(TypeError, match="不可序列化"):
            rv._jsonable(object())


class TestAtomicWrite:
    def test_writes_and_renames(self, tmp_path):
        out = tmp_path / "test.md"
        rv._atomic_write(out, "hello")
        assert out.read_text() == "hello"
