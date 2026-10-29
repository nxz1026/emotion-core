"""复盘报告入口层单元测试：搬运后从未执行过的四个断点 + 报告 CLI。

背景：`review.publish` 是 review_report 表唯一的写入方，但仓内**零调用方**
（daily 的 13 步里没有 review、无 CLI、无 systemd 单元），模块自 2026-09-26
手写进来后一次都没跑过。首次执行暴露的断点全部是「照 lkl 的 API 写、
emotion-core 的契约已漂移」：

1. `LadderDay` 不带 lkl `LadderRow` 的 name/turnover_rate/bomb_times
   ——ladder_day 表只存梯队判定，这三列不进表。
2. `ladder.y_competition` 在移植时缺失（entry._ctx 里内联算过，没提出来）。
3. `entry.split_checklist` 在 V4 被改成「Checklist 布尔投影 + WarningOnly」
   的结构化分离，报告却还按 lkl 的「两个三元组 list」消费。
4. `entry.checklist()` 只回布尔投影、丢掉已算好的 note，报告 §⑦ 要逐条讲
   「卡在哪一档、为什么」。

本文件锁这四处的语义，纯函数 + IO 桩，不连库。
"""

from __future__ import annotations

import contextlib
from datetime import date

import pytest

from emotion_core.algorithms import entry, ladder
from emotion_core.algorithms import review as review_mod
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Checklist
from emotion_core.orchestration import report as report_cli

D = date(2026, 9, 8)  # 评估日
P = date(2026, 9, 7)  # 前一交易日


# ────────────────────── 断点 2：ladder.y_competition ──────────────────────


class TestYCompetition:
    """R2 淘汰赛况 (g, s) = (昨日最高换手组只数, 今日幸存只数)。"""

    @pytest.fixture
    def stub(self, monkeypatch):
        def _apply(prev_rows, today_exchange, prev_of=lambda d: P):
            by_date = {P: prev_rows, D: [("600001", 4, True)]}
            monkeypatch.setattr(ladder, "load_ladder_candidates", lambda d: by_date.get(d, []))
            monkeypatch.setattr(
                ladder,
                "load_exchange_codes",
                lambda d: set(today_exchange if d == D else [c for c, _, _ in prev_rows]),
            )
            monkeypatch.setattr(ladder, "prev_trading_day", prev_of)
            # R67-2 起 build() 还会查当日停牌候选；本组只测淘汰赛口径，
            # 不桩掉它会穿透到 DB（无 ~/.dbconfig 时整个用例 FileNotFoundError）。
            monkeypatch.setattr(ladder, "load_suspended_candidates", lambda d: [])

        return _apply

    def test_counts_group_and_survivors(self, stub):
        # 昨日 3 板组 2 只（600001/600002），今日只有 600001 仍换手 → (2, 1)
        stub([("600001", 3, True), ("600002", 3, True), ("000003", 2, True)], ["600001"])
        assert ladder.y_competition(D) == (2, 1)

    def test_two_survivors_not_sole(self, stub):
        stub([("600001", 3, True), ("600002", 3, True)], ["600001", "600002"])
        assert ladder.y_competition(D) == (2, 2)

    def test_no_previous_day(self, stub):
        stub([], [], prev_of=lambda d: None)
        assert ladder.y_competition(D) == (0, 0)

    def test_one_word_board_excluded_from_group(self, stub):
        """一字板不参与最高层竞争（§1.4）：昨日唯一 5 板是一字 → 组为空。"""
        stub([("600001", 5, False), ("600002", 3, True)], ["600002"])
        assert ladder.y_competition(D) == (1, 1)


# ────────────────── 断点 1/3：LadderRow 质量列补齐 ──────────────────


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    def execute(self, sql, params):
        self._sink.append(params)
        return _Cur(self._rows)


def _stub_ro(monkeypatch, rows, sink=None):
    """connect_ro 桩：真实实现是上下文管理器，桩必须照同样的协议给。"""

    @contextlib.contextmanager
    def _cm():
        yield _Conn(rows, sink if sink is not None else [])

    monkeypatch.setattr(review_mod, "connect_ro", _cm)


def _base_row(code="600001", cont=4, ex=True):
    return LadderDay(
        date=D,
        code=code,
        cont_days=cont,
        is_exchange=ex,
        is_top=True,
        is_sole_top=True,
        y_top_group_count=2,
        y_top_survivor_count=1,
    )


class TestLadderView:
    def test_attaches_quality_columns(self, monkeypatch):
        seen = []
        # 5 列：R67-1 的 _VIEW_SQL 末尾加了 db.is_one_word（最高板票名后缀要读它）
        _stub_ro(monkeypatch, [("600001", "测试龙", 12.5, 3, True)], seen)
        out = review_mod.ladder_view(D, [_base_row()])
        assert len(out) == 1
        r = out[0]
        assert (r.code, r.name) == ("600001", "测试龙")
        assert r.turnover_rate == 12.5
        assert r.bomb_times == 3
        assert r.is_one_word is True
        # 梯队判定字段原样透传，报告的 ②段 靠它们分组
        assert r.cont_days == 4 and r.is_exchange is True and r.is_top is True
        assert seen[0]["d"] == D and seen[0]["codes"] == ["600001"]

    def test_is_one_word_none_when_derived_bar_missing(self, monkeypatch):
        """derived_bar 无当日行 → is_one_word 为 None（不是 False）。"""
        _stub_ro(monkeypatch, [("600001", "测试龙", 12.5, 3, None)])
        assert review_mod.ladder_view(D, [_base_row()])[0].is_one_word is None

    def test_missing_row_becomes_none_not_zero(self, monkeypatch):
        """F6 不补零：炸板次数缺失即 None——补 0 会被 _ladder_tag 当成「无炸板」。"""
        _stub_ro(monkeypatch, [])
        r = review_mod.ladder_view(D, [_base_row()])[0]
        assert r.bomb_times is None and r.turnover_rate is None
        assert r.is_one_word is None
        assert r.name == ""  # stock_basic 无此票时不编造名称

    def test_empty_rows_skips_query(self, monkeypatch):
        def boom():
            raise AssertionError("空梯队不该打库")

        monkeypatch.setattr(review_mod, "connect_ro", boom)
        assert review_mod.ladder_view(D, []) == []

    def test_json_export_keeps_ladder_fields(self, monkeypatch):
        """JSON 出口走 dataclasses.asdict：若改成包装+透传会只剩包装层字段。"""
        _stub_ro(monkeypatch, [("600001", "测试龙", 12.5, 3, True)])
        import json

        d = {
            "date": D,
            "ladder_rows": review_mod.ladder_view(D, [_base_row()]),
            "stat": None,
            "sole": None,
            "elimination": [],
            "quality": {},
        }
        js = json.loads(review_mod.render_json(d))
        row = js["ladder_rows"][0]
        assert row["code"] == "600001" and row["cont_days"] == 4
        assert row["name"] == "测试龙" and row["bomb_times"] == 3


# ────────────── R67-1：速览最高板数字后缀票名 ──────────────


def _note_row(code, name, cont, *, ex=True, one=False, susp=False):
    """梯队行桩：`_top_boards_note` 只读这几个属性。"""
    return review_mod.LadderRow(
        date=D,
        code=code,
        cont_days=cont,
        is_exchange=ex,
        is_top=True,
        is_sole_top=False,
        y_top_group_count=1,
        y_top_survivor_count=1,
        name=name,
        turnover_rate=None,
        bomb_times=None,
        is_one_word=one,
        is_suspended=susp,
    )


def _stub_review_query(monkeypatch, *, health=P, note=None, sink=None):
    """query_df 路由桩：`_health_of` 走 review_report/daily_bar，兜底票名走 derived_bar。"""

    def q(sql, params=()):
        if sink is not None:
            sink.append((sql, params))
        if "review_report" in sql:
            import pandas as pd

            return pd.DataFrame({"d": [health]})
        if "derived_bar" in sql:
            return note
        if "SELECT DISTINCT date FROM daily_bar" in sql:
            import pandas as pd

            return pd.DataFrame({"date": [P]})
        raise AssertionError(f"意外的查询：{sql}")

    monkeypatch.setattr(review_mod, "query_df", q)


class TestTopBoardsNote:
    """R67-1：10-12 owner 只收到「名义最高板：9 → 10」不知是哪只票。"""

    def test_uses_ladder_rows_without_touching_db(self, monkeypatch):
        def boom(sql, params=()):
            raise AssertionError("梯队行已够，不该兜底打库")

        monkeypatch.setattr(review_mod, "query_df", boom)
        d = {
            "stat": {"max_limit_days": 10},
            "ladder_rows": [_note_row("600825", "新华传媒", 10, one=True)],
        }
        assert review_mod._top_boards_note(d, "max_limit_days") == "（新华传媒 600825 一字）"

    def test_single_name_has_no_count_suffix(self, monkeypatch):
        """1 只票不加「等 N 只」——速览要短。"""
        monkeypatch.setattr(review_mod, "query_df", lambda *a, **k: None)
        d = {"stat": {"max_limit_days": 5}, "ladder_rows": [_note_row("000530", "冰山冷热", 5)]}
        assert review_mod._top_boards_note(d, "max_limit_days") == "（冰山冷热 000530）"

    def test_tradable_key_requires_exchange(self, monkeypatch):
        """可交易最高板只认换手板：非换手同高行进不了后缀。"""
        seen = []
        _stub_review_query(monkeypatch, note=None, sink=seen)
        d = {
            "date": D,
            "stat": {"tradable_max_days": 5},
            "ladder_rows": [_note_row("600001", "一字龙", 5, one=True, ex=False)],
        }
        # 无换手同高行 → 走兜底（兜底 SQL 必须带 is_exchange 过滤）
        assert review_mod._top_boards_note(d, "tradable_max_days") == ""
        sql = seen[-1][0]
        assert "derived_bar" in sql and "AND d.is_exchange" in sql

    def test_nominal_key_fallback_sql_has_no_exchange_filter(self, monkeypatch):
        seen = []
        _stub_review_query(monkeypatch, note=None, sink=seen)
        d = {"date": D, "stat": {"max_limit_days": 9}, "ladder_rows": []}
        assert review_mod._top_boards_note(d, "max_limit_days") == ""
        assert "AND d.is_exchange" not in seen[-1][0]

    def test_suspended_row_excluded(self, monkeypatch):
        """停牌身位不计入最高板（owner 拍板）——停牌行不得出现在后缀里。"""
        seen = []
        _stub_review_query(monkeypatch, note=None, sink=seen)
        d = {
            "date": D,
            "stat": {"max_limit_days": 10},
            "ladder_rows": [_note_row("600825", "新华传媒", 10, susp=True)],
        }
        assert review_mod._top_boards_note(d, "max_limit_days") == ""
        assert "derived_bar" in seen[-1][0]  # 被排除后只能兜底

    def test_fallback_reads_derived_bar(self, monkeypatch):
        import pandas as pd

        _stub_review_query(
            monkeypatch,
            note=pd.DataFrame({"code": ["600825"], "name": ["新华传媒"], "is_one_word": [True]}),
        )
        d = {"date": D, "stat": {"max_limit_days": 10}, "ladder_rows": []}
        assert review_mod._top_boards_note(d, "max_limit_days") == "（新华传媒 600825 一字）"

    def test_fallback_limit_is_three(self, monkeypatch):
        seen = []
        import pandas as pd

        _stub_review_query(
            monkeypatch,
            note=pd.DataFrame({"code": ["a"], "name": ["A"], "is_one_word": [False]}),
            sink=seen,
        )
        d = {"date": D, "stat": {"max_limit_days": 10}, "ladder_rows": []}
        review_mod._top_boards_note(d, "max_limit_days")
        assert seen[-1][1][-1] == review_mod._MAX_NOTE_NAMES == 3

    def test_more_than_three_gets_count_suffix(self, monkeypatch):
        monkeypatch.setattr(review_mod, "query_df", lambda *a, **k: None)
        rows = [_note_row(f"60000{i}", f"票{i}", 4) for i in range(5)]
        d = {"stat": {"max_limit_days": 4}, "ladder_rows": rows}
        out = review_mod._top_boards_note(d, "max_limit_days")
        assert out == "（票0 600000、票1 600001、票2 600002 等 5 只）"

    @pytest.mark.parametrize("height", [None, "", 0, "abc"])
    def test_no_height_returns_empty(self, monkeypatch, height):
        monkeypatch.setattr(review_mod, "query_df", lambda *a, **k: None)
        d = {"stat": {"max_limit_days": height}, "ladder_rows": []}
        assert review_mod._top_boards_note(d, "max_limit_days") == ""

    def test_empty_df_returns_empty(self, monkeypatch):
        import pandas as pd

        _stub_review_query(monkeypatch, note=pd.DataFrame(columns=["code", "name", "is_one_word"]))
        d = {"date": D, "stat": {"max_limit_days": 10}, "ladder_rows": []}
        assert review_mod._top_boards_note(d, "max_limit_days") == ""

    def test_exception_is_swallowed(self, monkeypatch):
        """速览不能因补票名失败而整篇渲染不出来。"""

        def boom(sql, params=()):
            raise RuntimeError("库炸了")

        monkeypatch.setattr(review_mod, "query_df", boom)
        d = {"date": D, "stat": {"max_limit_days": 10}, "ladder_rows": []}
        assert review_mod._top_boards_note(d, "max_limit_days") == ""

    def test_overview_line_carries_note(self, monkeypatch):
        """集成：`- 名义最高板：5 → 10（新华传媒 600825 一字）`。"""
        _stub_review_query(monkeypatch)
        d = {
            "date": D,
            "stat": {
                "limit_up_count": 61,
                "max_limit_days": 10,
                "tradable_max_days": 4,
                "phase": "主升",
            },
            "prev_stat": {
                "limit_up_count": 36,
                "max_limit_days": 5,
                "tradable_max_days": 4,
                "phase": "主升",
            },
            "ladder_rows": [
                _note_row("600825", "新华传媒", 10, one=True),
                _note_row("000530", "冰山冷热", 4),
            ],
            "quality": {"diff": None, "warns": []},
        }
        out = review_mod._sec_overview(d)
        # suffix 在四分支里都拼（含「持平」）——两端都带票名，与 lkl 口径一致
        assert ("- 名义最高板：5（新华传媒 600825 一字） → 10（新华传媒 600825 一字）") in out
        assert "- 可交易最高板：4（冰山冷热 000530）（持平）" in out
        assert "- 涨停家数：36 → 61" in out
        assert "涨停家数：36（" not in out


# ────────────── R67-2：②段停牌身位单独展示 ──────────────


class TestSecLadderSuspended:
    """`_sec_ladder`：停牌行不并进板数分组，只在末尾单独一行「N板（停牌）」。"""

    @staticmethod
    def _row(code, name, cont, *, ex=True, susp=False, bomb=None):
        return review_mod.LadderRow(
            date=D,
            code=code,
            cont_days=cont,
            is_exchange=ex,
            is_top=False,
            is_sole_top=False,
            y_top_group_count=1,
            y_top_survivor_count=1,
            name=name,
            turnover_rate=None,
            bomb_times=bomb,
            is_one_word=not ex,
            is_suspended=susp,
        )

    def test_suspended_line_is_rendered_last(self):
        d = {
            "ladder_rows": [
                self._row("002952", "捷荣技术", 4),
                self._row("600825", "新华传媒", 10, susp=True),
            ]
        }
        out = review_mod._sec_ladder(d)
        lines = [ln for ln in out.splitlines() if ln.startswith("- **")]
        assert lines == [
            "- **4板** [1只] 捷荣技术(002952)",
            "- **10板（停牌）** [1只] 新华传媒(600825)",
        ]

    def test_suspended_not_merged_into_same_height_group(self):
        """同板数时停牌行不得混进正常分组（否则数字被污染）。"""
        d = {
            "ladder_rows": [
                self._row("002952", "捷荣技术", 4),
                self._row("600825", "新华传媒", 4, susp=True),
            ]
        }
        out = review_mod._sec_ladder(d)
        assert "- **4板** [1只] 捷荣技术(002952)" in out
        assert "- **4板（停牌）** [1只] 新华传媒(600825)" in out

    def test_suspended_tag_has_no_one_word_or_bomb_suffix(self):
        """停牌行只写名(码)：一字/开板附注对停牌日无意义。

        注意尾部固有一行「开板计数为东财 zbc 字段…」说明，故只断言停牌行本身。
        """
        d = {"ladder_rows": [self._row("600825", "新华传媒", 10, ex=False, susp=True, bomb=7)]}
        out = review_mod._sec_ladder(d)
        line = next(ln for ln in out.splitlines() if ln.startswith("- **"))
        assert line == "- **10板（停牌）** [1只] 新华传媒(600825)"
        assert next(ln for ln in out.splitlines() if ln.startswith("> ")).startswith("> 开板计数")

    def test_only_suspended_rows_has_no_heading_hole(self):
        d = {"ladder_rows": [self._row("600825", "新华传媒", 10, susp=True)]}
        out = review_mod._sec_ladder(d)
        assert out.startswith("## ② 梯队全景\n")
        assert "- **10板（停牌）** [1只] 新华传媒(600825)" in out

    def test_all_empty_keeps_original_placeholder(self):
        assert review_mod._sec_ladder({"ladder_rows": []}) == (
            "## ② 梯队全景\n\n无连板梯队（当日无 ≥2 板）\n"
        )

    def test_suspended_rows_excluded_from_seal_notes(self):
        """停牌行已被摘出，`_seal_notes` 不该为它出开板回封附注。"""
        d = {
            "ladder_rows": [self._row("600825", "新华传媒", 10, susp=True, bomb=7)],
            "seal_map": {"600825": ("09:31", "14:20")},
        }
        out = review_mod._sec_ladder(d)
        assert "首封" not in out

    def test_normal_rows_keep_seal_notes(self):
        d = {
            "ladder_rows": [self._row("002952", "捷荣技术", 4, bomb=2)],
            "seal_map": {"002952": ("09:31", "14:20")},
        }
        out = review_mod._sec_ladder(d)
        assert "> 捷荣技术：首封09:31 末次回封14:20（东财 zbc=2）" in out

    def test_heights_sorted_descending_with_suspended_tail(self):
        d = {
            "ladder_rows": [
                self._row("600001", "甲", 3),
                self._row("600002", "乙", 6),
                self._row("600825", "新华传媒", 10, susp=True),
                self._row("600003", "丙", 2, susp=True),
            ]
        }
        out = review_mod._sec_ladder(d)
        lines = [ln for ln in out.splitlines() if ln.startswith("- **")]
        assert lines == [
            "- **6板** [1只] 乙(600002)",
            "- **3板** [1只] 甲(600001)",
            "- **10板（停牌）** [1只] 新华传媒(600825)",
            "- **2板（停牌）** [1只] 丙(600003)",
        ]


# ────────────── 断点 3：entry.split_checklist API 漂移后的行拆分 ──────────────


class TestSplitRows:
    ROWS = [
        ("c1 唯一换手高标", True, "唯一换手高标: 600001 4板"),
        ("c3 淘汰赛身份", False, "昨日最高板组2只→今日幸存1只"),
        ("W1 同身位扎堆", True, "⚠ 绝对最高4板2只同身位"),
    ]

    def test_split_by_label_prefix(self):
        conds, warns = review_mod._split_rows(self.ROWS)
        assert [r[0] for r in conds] == ["c1 唯一换手高标", "c3 淘汰赛身份"]
        assert [r[0] for r in warns] == ["W1 同身位扎堆"]

    def test_passed_ignores_warning_rows(self):
        """W- 警告无否决权：警告行 ok=False 也不能带塌整体判定。"""
        rows = [("c1 x", True, ""), ("c2 x", True, ""), ("W1 x", False, "⚠")]
        assert review_mod._passed_of_rows(rows) is True

    def test_passed_filters_unknown(self):
        """UNKNOWN(None) 不否决，与 domain.signal.Checklist.passed 逐条等价。"""
        rows = [("c1 x", True, ""), ("c2 x", None, ""), ("c3 x", True, "")]
        assert review_mod._passed_of_rows(rows) is True
        rows[1] = ("c2 x", False, "")
        assert review_mod._passed_of_rows(rows) is False

    @pytest.mark.parametrize(
        "ok_vals,expect",
        [
            ((True, True, True, True, True), True),
            ((True, True, False, True, True), False),
            ((True, None, True, True, True), True),
            ((True, None, False, True, None), False),
        ],
    )
    def test_parity_with_checklist_passed(self, ok_vals, expect):
        """与契约层 Checklist.passed 对拍——报告不得另立通过规则。"""
        rows = [(f"c{i + 1} x", v, "") for i, v in enumerate(ok_vals)]
        cl = Checklist(
            c1_uniqueness=ok_vals[0],
            c2_exchange=ok_vals[1],
            c3_elimination=ok_vals[2],
            c4_min_days=ok_vals[3],
            c5_strength_diverge=ok_vals[4],
            w1_crowding=True,
        )
        assert cl.passed is expect
        assert review_mod._passed_of_rows(rows) is expect


# ────────────── 断点 4：entry.rows() 暴露带说明的行 ──────────────


class TestEntryRows:
    @pytest.fixture
    def stub(self, monkeypatch):
        """桩掉 _ctx（含 _candidate_view 的 IO），只测 rows 的编排与行序。"""

        def _apply(window="STANDARD", **extra):
            base = {
                "sole": _base_row(),
                "window": window,
                "min_days": entry.MIN_LEADER_DAYS,
                "diverge_min": entry.DIVERGE_MIN_TURNOVER,
                "y_comp": (2, 1),
                "y_surv": ({"600001"}, {"600001"}),
                "rows": [_base_row()],
                "c5_verifiable": True,
                "name": "测试龙",
                "turnover_rate": 12.5,
                "bomb_times": 0,
            }
            # y_peer 由真实 _ctx 在 include_peer=True 时补入；桩掉 _ctx 后要自带，
            # 否则次级 c3（_c3_secondary）读 ctx["y_peer"] 会 KeyError。
            base.setdefault("y_peer", 2)
            base.update(extra)
            monkeypatch.setattr(entry, "_ctx", lambda *a, **k: base)

        return _apply

    def test_rows_carry_notes_for_all_conditions(self, stub):
        """报告 §⑦ 靠 note 逐条讲「卡在哪一档」——布尔投影写不出这句话。"""
        stub()
        rows = entry.rows(_base_row(), "STANDARD")
        assert len(rows) == 6, rows  # 5 条件 + 1 警告
        assert all(note for _, _, note in rows), rows
        assert [r[0] for r in rows[:5]] == [
            "c1 唯一换手高标",
            "c2 换手板非一字",
            "c3 身份档（不否决）",
            "c4 最低板数门槛",
            "c5 强度与分歧补偿",
        ]
        conds, warns = review_mod._split_rows(rows)
        assert len(conds) == 5 and len(warns) == 1

    def test_rows_bool_matches_checklist(self, stub):
        """行版的布尔位与 checklist() 投影必须一致（同一次评估）。"""
        stub()
        rows = entry.rows(_base_row(), "STANDARD")
        cl = entry.checklist(_base_row(), "STANDARD")
        conds, _ = review_mod._split_rows(rows)
        for (_, ok, _), field in zip(
            conds,
            (
                "c1_uniqueness",
                "c2_exchange",
                "c3_elimination",
                "c4_min_days",
                "c5_strength_diverge",
            ),
            strict=False,
        ):
            assert ok == getattr(cl, field), field

    def test_secondary_uses_relaxed_threshold(self, stub, monkeypatch):
        """secondary=True 走次级阈值（c4 门槛 3 板 + 次级 c3）。"""
        seen = {}
        real = entry._evaluate

        def spy(cand_, win, **kw):
            seen.update(kw)
            return real(cand_, win, **kw)

        stub()
        monkeypatch.setattr(entry, "_evaluate", spy)
        low = _base_row(cont=3)
        entry.rows(low, "STANDARD", secondary=True)
        assert seen["min_days"] == entry.SECONDARY_MIN_LEADER_DAYS
        assert seen["conditions"] is entry._SECONDARY_CONDITIONS
        assert seen["include_peer"] is True


# ────────────────────────── 报告 CLI ──────────────────────────


def _stub_query_df(monkeypatch, sqls, dates):
    """query_df 桩：记录 SQL，并返回可 itertuples 的最小 DataFrame 替身。"""
    from types import SimpleNamespace

    class _DF:
        def __init__(self, rows):
            self._rows = rows

        def itertuples(self):
            return iter([SimpleNamespace(date=d) for d in self._rows])

    def _q(sql, *a, **k):
        sqls.append(sql)
        return _DF(dates)

    monkeypatch.setattr(report_cli, "query_df", _q)


class TestReportCLI:
    def test_bad_date_returns_2(self):
        assert report_cli.main(["--date", "2026/09/08"]) == 2

    def test_requires_a_selector(self):
        with pytest.raises(SystemExit) as e:
            report_cli.main([])
        assert e.value.code == 2

    def test_list_uses_derived_bar(self, monkeypatch, capsys):
        """报告的根表是 derived_bar：它缺行 = 那天没跑 derive，出报告满屏缺数。"""
        seen = []
        _stub_query_df(monkeypatch, seen, [D, P])
        assert report_cli.main(["--list", "2"]) == 0
        assert "derived_bar" in seen[0]
        assert capsys.readouterr().out.split() == [D.isoformat(), P.isoformat()]

    def test_latest_without_data_returns_1(self, monkeypatch):
        _stub_query_df(monkeypatch, [], [])
        assert report_cli.main(["--latest"]) == 1
        assert report_cli.main(["--list", "5"]) == 1
