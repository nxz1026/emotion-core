"""回测—实盘一致性对比（_live_codes/_replay_codes/_drift_alert/compare/render）：不连库。

语义来源 lkl/services/compare.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 compare.py 模块文档）：db 入口换 emotion_core.utils.db、
回放侧延迟导入换 emotion_core.algorithms.evaluate、logger 名改前缀；
SQL 文本、参数顺序、sorted 口径、dict 键序、MD 文案与结尾换行逐字保留。

IO 全部桩掉（signal 表读 query_df、回放 evaluate.replay、alert 写 execute），
真实库对账另跑（编排者阶段）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import compare
from emotion_core.algorithms import evaluate as eval_mod

D0 = date(2026, 9, 25)

LIVE_SQL = ("SELECT code, status FROM signal WHERE confirm_date=%s"
            " AND action='BUY' ORDER BY code")
ALERT_SQL = ("INSERT INTO alert (level, source, detail) VALUES"
             " ('WARN','compare',%s)")

EMPTY_LIVE = pd.DataFrame({"code": [], "status": []})
EMPTY_REPLAY = pd.DataFrame({"code": [], "passed": []})


class IO:
    """IO 桩：记录 query_df（实盘侧）/ evaluate.replay（回放侧）/ execute（alert 写）。"""

    def __init__(self, monkeypatch, live_frame=None, replay_frame=None) -> None:
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple]] = []
        self.replays: list[dict] = []
        self._live = EMPTY_LIVE if live_frame is None else live_frame
        self._replay = EMPTY_REPLAY if replay_frame is None else replay_frame

        def query_df(sql, params=()):
            self.reads.append((sql, params))
            return self._live

        def execute(sql, params=()):
            self.writes.append((sql, params))
            return 1

        def replay(start, end, min_days=None, as_of=None):
            self.replays.append({"start": start, "end": end,
                                 "min_days": min_days, "as_of": as_of})
            return self._replay

        monkeypatch.setattr(compare, "query_df", query_df)
        monkeypatch.setattr(compare, "execute", execute)
        monkeypatch.setattr(eval_mod, "replay", replay)


@pytest.fixture
def io(monkeypatch):
    def make(live_frame=None, replay_frame=None) -> IO:
        return IO(monkeypatch, live_frame, replay_frame)

    return make


def live_frame(*rows) -> pd.DataFrame:
    return pd.DataFrame({"code": [c for c, _ in rows],
                         "status": [s for _, s in rows]})


def replay_frame(*rows) -> pd.DataFrame:
    return pd.DataFrame({"code": [c for c, _ in rows],
                         "passed": [p for _, p in rows]})


# ────────────────────────── _live_codes ──────────────────────────

class TestLiveCodes:
    def test_sql_text_param_and_row_shape(self, io):
        """SQL 逐字 + 参数为单元素元组 + 返回 (code, status) 二元组列表。"""
        stub = io(live_frame(("000001", "BUY"), ("600000", "SELL")))
        assert compare._live_codes(D0) == [("000001", "BUY"),
                                           ("600000", "SELL")]
        assert stub.reads == [(LIVE_SQL, (D0,))]
        sql, params = stub.reads[0]
        assert "confirm_date=%s" in sql
        assert "action='BUY'" in sql
        assert sql.endswith("ORDER BY code")
        assert params == (D0,)
        assert isinstance(params, tuple) and len(params) == 1

    def test_result_is_list_of_pairs(self, io):
        io(live_frame(("000001", "BUY")))
        out = compare._live_codes(D0)
        assert isinstance(out, list)
        assert all(isinstance(r, tuple) and len(r) == 2 for r in out)
        assert all(isinstance(c, str) and isinstance(s, str)
                   for c, s in out)

    def test_status_passed_through_verbatim(self, io):
        """status 不折算成 bool：SELL/SECONDARY 原样带出。"""
        io(live_frame(("000001", "SELL"), ("000002", "SECONDARY")))
        assert compare._live_codes(D0) == [("000001", "SELL"),
                                           ("000002", "SECONDARY")]

    def test_empty_table(self, io):
        stub = io()
        assert compare._live_codes(D0) == []
        assert stub.reads == [(LIVE_SQL, (D0,))]


# ────────────────────────── _replay_codes ──────────────────────────

class TestReplayCodes:
    def test_call_args_are_start_end_as_of(self, io):
        """调用参数逐字：replay(d, d, as_of=d)，min_days 不传（保持 None）。"""
        stub = io(replay_frame=replay_frame(("000001", True)))
        compare._replay_codes(D0)
        assert stub.replays == [{"start": D0, "end": D0,
                                 "min_days": None, "as_of": D0}]

    def test_bool_coercion_none_and_true(self, io):
        """passed=None → False，passed=True → True（bool() 强制，非恒真）。"""
        io(replay_frame=replay_frame(("000001", None), ("000002", True)))
        assert compare._replay_codes(D0) == [("000001", False),
                                             ("000002", True)]

    def test_bool_coercion_ints(self, io):
        """1 → True、0 → False（按真值强制，不要求 is True）。"""
        io(replay_frame=replay_frame(("000001", 1), ("000002", 0)))
        assert compare._replay_codes(D0) == [("000001", True),
                                             ("000002", False)]

    def test_returns_bool_objects(self, io):
        io(replay_frame=replay_frame(("000001", None)))
        (code, passed), = compare._replay_codes(D0)
        assert code == "000001"
        assert passed is False

    def test_empty_replay(self, io):
        io()
        assert compare._replay_codes(D0) == []


# ────────────────────────── _drift_alert ──────────────────────────

class TestDriftAlert:
    def test_insert_sql_and_param_verbatim(self, io):
        """逐字直插：SQL 含 ('WARN','compare',%s)，参数 = '<date> 规则漂移：<detail>'。"""
        stub = io()
        compare._drift_alert(D0, "实盘=['000001'] 回放=[]")
        assert stub.writes == [
            (ALERT_SQL, ("2026-09-25 规则漂移：实盘=['000001'] 回放=[]",))]

    def test_sql_literal_pieces(self, io):
        stub = io()
        compare._drift_alert(D0, "x")
        sql, params = stub.writes[0]
        assert sql.startswith("INSERT INTO alert (level, source, detail)")
        assert "'WARN'" in sql and "'compare'" in sql and sql.endswith("%s)")
        assert len(params) == 1

    def test_date_uses_isoformat_prefix(self, io):
        stub = io()
        compare._drift_alert(date(2026, 1, 5), "d")
        assert stub.writes[0][1][0].startswith("2026-01-05 规则漂移：")


# ────────────────────────── compare ──────────────────────────

class TestCompare:
    def test_consistent_no_alert(self, io):
        """两侧代码集一致 → consistent True、drift []、**不写 alert**。"""
        stub = io(live_frame(("000001", "BUY")), replay_frame(("000001", True)))
        rep = compare.compare(D0)
        assert rep == {"date": "2026-09-25",
                       "live": [{"code": "000001", "status": "BUY"}],
                       "replay": [{"code": "000001", "passed": True}],
                       "consistent": True,
                       "drift": []}
        assert stub.writes == []
        assert stub.reads == [(LIVE_SQL, (D0,))]
        assert len(stub.replays) == 1

    def test_both_sides_empty_is_consistent(self, io):
        stub = io()
        rep = compare.compare(D0)
        assert rep["consistent"] is True
        assert rep["drift"] == []
        assert rep["live"] == [] and rep["replay"] == []
        assert stub.writes == []

    def test_common_code_different_status_is_not_drift(self, io):
        """漂移只比代码集，不比 status/passed（lkl 原口径）。"""
        stub = io(live_frame(("600000", "BUY")), replay_frame(("600000", False)))
        rep = compare.compare(D0)
        assert rep["drift"] == []
        assert rep["consistent"] is True
        assert stub.writes == []

    def test_drift_sorted_and_alert_verbatim(self, io):
        """漂移 = 对称差 sorted；alert 一条、SQL/参数逐字。"""
        stub = io(live_frame(("600000", "BUY"), ("000001", "BUY"),
                             ("300100", "SELL")),
                  replay_frame(("000002", True), ("600000", False)))
        rep = compare.compare(D0)
        assert rep["drift"] == ["000001", "000002", "300100"]
        assert rep["drift"] == sorted(rep["drift"])
        assert rep["consistent"] is False
        assert set(rep) == {"date", "live", "replay", "consistent", "drift"}
        assert rep["live"] == [{"code": "600000", "status": "BUY"},
                               {"code": "000001", "status": "BUY"},
                               {"code": "300100", "status": "SELL"}]
        assert rep["replay"] == [{"code": "000002", "passed": True},
                                 {"code": "600000", "passed": False}]
        assert stub.writes == [(ALERT_SQL, (
            "2026-09-25 规则漂移：实盘=['000001', '300100', '600000'] "
            "回放=['000002', '600000']；差异=['000001', '000002', '300100']；"
            "回放passed={'000001': None, '000002': True, '300100': None}",))]

        """回放多出的代码同样计入漂移（对称差，不是左差）。"""
        stub = io(live_frame(), replay_frame(("000002", False)))
        rep = compare.compare(D0)
        assert rep["drift"] == ["000002"]
        assert rep["consistent"] is False
        assert stub.writes[0][1] == (
            "2026-09-25 规则漂移：实盘=[] 回放=['000002']；差异=['000002']；"
            "回放passed={'000002': False}",)

    def test_dict_key_order_matches_source(self, io):
        io(live_frame(("000001", "BUY")), replay_frame(("000001", True)))
        assert list(compare.compare(D0)) == ["date", "live", "replay",
                                             "consistent", "drift"]

    def test_date_field_is_isoformat(self, io):
        io()
        assert compare.compare(date(2026, 1, 5))["date"] == "2026-01-05"


# ────────────────────────── render ──────────────────────────

HEAD = "## 回测—实盘一致性 2026-09-25\n\n"
LIVE_HEAD = "**实盘当日记录（signal 表）**\n"
REPLAY_HEAD = "**今日代码+数据重放（replay as_of=当日）**\n"
OK_LINE = "✅ 两侧一致：无规则漂移\n"


def rep(live=(), replay=(), consistent=True, drift=(), d="2026-09-25") -> dict:
    return {"date": d,
            "live": [{"code": c, "status": s} for c, s in live],
            "replay": [{"code": c, "passed": p} for c, p in replay],
            "consistent": consistent,
            "drift": list(drift)}


class TestRender:
    def test_full_consistent(self):
        """实盘有 + 回放有 + 一致：两栏逐行 + ✅ 行 + 结尾换行。"""
        got = compare.render(rep(live=[("000001", "BUY")],
                                 replay=[("000001", True)]))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- 000001（BUY）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- 000001（passed=True）\n"
                       + "\n"
                       + OK_LINE)
        assert got.endswith("\n") and not got.endswith("\n\n")

    def test_empty_live_placeholder(self):
        got = compare.render(rep(live=[], replay=[("000001", False)]))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- （无信号）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- 000001（passed=False）\n"
                       + "\n"
                       + OK_LINE)

    def test_empty_replay_placeholder(self):
        got = compare.render(rep(live=[("000001", "BUY")], replay=[]))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- 000001（BUY）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- （无候选/窗口 NONE）\n"
                       + "\n"
                       + OK_LINE)

    def test_both_empty(self):
        got = compare.render(rep())
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- （无信号）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- （无候选/窗口 NONE）\n"
                       + "\n"
                       + OK_LINE)

    def test_drift_branch_text(self):
        got = compare.render(rep(live=[("000001", "BUY")],
                                 replay=[("000002", True)],
                                 consistent=False,
                                 drift=["000001", "000002"]))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- 000001（BUY）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- 000002（passed=True）\n"
                       + "\n"
                       + "⚠ **规则漂移报警**：['000001', '000002']"
                       "——已插入 alert（lkl alerts 查看 / alert-ack 确认）\n")
        assert "✅" not in got

    def test_multiple_rows_keep_order(self):
        got = compare.render(rep(live=[("600000", "BUY"), ("000001", "SELL")],
                                 replay=[("600000", True), ("000001", False)]))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- 600000（BUY）\n"
                       + "- 000001（SELL）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- 600000（passed=True）\n"
                       + "- 000001（passed=False）\n"
                       + "\n"
                       + OK_LINE)

    def test_branch_taken_from_consistent_flag(self):
        """分支读 rep['consistent']（不依 drift 重算）——lkl 原语义。"""
        got = compare.render(rep(drift=["000001"], consistent=True))
        assert got.endswith(OK_LINE)

    def test_drift_renders_compare_output(self, io):
        """compare() 的漂移报告直接可渲染（键/文本贯通）。"""
        io(live_frame(("000001", "BUY")), replay_frame(("000002", True)))
        got = compare.render(compare.compare(D0))
        assert got == (HEAD
                       + LIVE_HEAD
                       + "- 000001（BUY）\n"
                       + "\n"
                       + REPLAY_HEAD
                       + "- 000002（passed=True）\n"
                       + "\n"
                       + "⚠ **规则漂移报警**：['000001', '000002']"
                       "——已插入 alert（lkl alerts 查看 / alert-ack 确认）\n")

    def test_head_uses_date_field_verbatim(self):
        got = compare.render(rep(d="2026-01-05"))
        assert got.startswith("## 回测—实盘一致性 2026-01-05\n")
