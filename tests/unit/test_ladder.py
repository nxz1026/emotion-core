"""梯队算法单元测试。

无 DB：SQL 行选择（主板/次新/ST 过滤）由 tests/oracle/test_ladder_vs_lkl.py 用
lkl 真实产物对账；本文件锁 fold/persist 的纯逻辑与编排分支。
"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.algorithms import ladder
from emotion_core.domain.ladder import LadderDay
from emotion_core.utils.config import CONFIG

D = date(2024, 1, 2)
P = date(2023, 12, 29)


@pytest.fixture
def stub(monkeypatch):
    """替换 IO：候选行按日期给定，昨日固定为 P，换手代码集与 derived_bar 行数可控。"""
    def _apply(rows=(), prev_rows=(), exchange=(), derived_count=1):
        by_date = {D: [(c, n, x) for c, n, x in rows],
                   P: [(c, n, x) for c, n, x in prev_rows]}
        monkeypatch.setattr(ladder, "load_ladder_candidates", lambda d: by_date.get(d, []))
        monkeypatch.setattr(ladder, "load_exchange_codes", lambda d: set(exchange))
        monkeypatch.setattr(ladder, "prev_trading_day", lambda d: P)
        monkeypatch.setattr(ladder, "count_derived_rows", lambda d: derived_count)
    return _apply


def _by_code(rows: list[LadderDay]) -> dict[str, LadderDay]:
    return {r.code: r for r in rows}


class TestBuild:
    """build 的换手口径与 R2 计数。"""

    def test_normal_sole_top(self, stub):
        stub(rows=[("002952", 9, True), ("603530", 4, True), ("002976", 3, False)],
             prev_rows=[("600519", 5, True)],
             exchange={"600519", "002952"})
        rows = ladder.build(D)

        assert [r.code for r in rows] == ["002952", "603530", "002976"]
        got = _by_code(rows)
        # 换手板最高身位唯一 = 龙头
        assert got["002952"].is_top is True and got["002952"].is_sole_top is True
        assert got["603530"].is_top is False and got["603530"].is_sole_top is False
        # 一字板（is_exchange=False）不进最高层
        assert got["002976"].is_top is False and got["002976"].is_exchange is False
        # R2：昨日最高组 1 只，其中今日仍换手 1 只（600519）
        assert {(r.y_top_group_count, r.y_top_survivor_count) for r in rows} == {(1, 1)}

    def test_top_ignores_one_word_higher_board(self, stub):
        """最高板是一字板时不占最高身位（换手口径）。"""
        stub(rows=[("000001", 7, False), ("600519", 4, True)])
        rows = _by_code(ladder.build(D))

        assert rows["600519"].is_top is True
        assert rows["600519"].is_sole_top is True
        assert rows["000001"].is_top is False

    def test_two_way_top_is_not_sole(self, stub):
        stub(rows=[("600519", 5, True), ("000001", 5, True)])
        rows = ladder.build(D)

        assert {r.code for r in rows if r.is_top} == {"600519", "000001"}
        assert all(r.is_sole_top is False for r in rows)
        assert ladder.sole_top(rows) is None

    def test_sole_top_below_min_leader_days(self, stub):
        stub(rows=[("600519", CONFIG.MIN_LEADER_DAYS - 1, True)])
        rows = ladder.build(D)

        assert rows[0].is_top is True
        assert rows[0].is_sole_top is False
        assert ladder.sole_top(rows) is None

    def test_prev_top_group_survivors(self, stub):
        """昨日最高组 2 只，今日 1 只仍换手。"""
        stub(rows=[("600519", 3, True)],
             prev_rows=[("600519", 5, True), ("000001", 5, True), ("002594", 4, True)],
             exchange={"600519", "002594", "603530"})
        rows = ladder.build(D)

        assert {(r.y_top_group_count, r.y_top_survivor_count) for r in rows} == {(2, 1)}

    def test_no_candidates_is_empty(self, stub):
        stub(rows=[], prev_rows=[("600519", 5, True)])
        assert ladder.build(D) == []

    def test_no_previous_day_counts_zero(self, stub, monkeypatch):
        stub(rows=[("600519", 5, True)])
        monkeypatch.setattr(ladder, "prev_trading_day", lambda d: None)
        rows = ladder.build(D)

        assert (rows[0].y_top_group_count, rows[0].y_top_survivor_count) == (0, 0)


class TestTopGroupAndSoleTop:
    """纯函数口径（不依赖 DB）。"""

    def _row(self, code: str, days: int, exchange: bool = True) -> LadderDay:
        return LadderDay(date=D, code=code, cont_days=days, is_exchange=exchange,
                         is_top=False, is_sole_top=False,
                         y_top_group_count=0, y_top_survivor_count=0)

    def test_top_group_exchange_only(self):
        rows = [self._row("000001", 7, exchange=False), self._row("600519", 5),
                self._row("000002", 5), self._row("603530", 3)]
        assert {r.code for r in ladder.top_group(rows)} == {"600519", "000002"}

    def test_top_group_no_exchange(self):
        assert ladder.top_group([self._row("000001", 7, exchange=False)]) == []

    def test_sole_top_min_days_override(self):
        rows = [self._row("600519", 3)]
        assert ladder.sole_top(rows) is None
        assert ladder.sole_top(rows, min_days=3) is not None


class TestYSurvivors:
    """y_survivors：昨日最高换手组 ∩ 今日换手。"""

    def test_survivors_intersection(self, stub):
        stub(rows=[("600519", 3, True)],
             prev_rows=[("600519", 5, True), ("000001", 5, True)],
             exchange={"000001", "603530"})
        assert ladder.y_survivors(D) == {"000001"}

    def test_no_previous_day(self, stub, monkeypatch):
        stub(rows=[("600519", 3, True)])
        monkeypatch.setattr(ladder, "prev_trading_day", lambda d: None)
        assert ladder.y_survivors(D) == set()


class TestPersist:
    """persist 三个分支：写入 / 真平静清空 / 上游缺数拒绝。"""

    def test_writes_rows(self, stub, monkeypatch):
        stub(rows=[("002952", 9, True)])
        calls = []
        monkeypatch.setattr(ladder, "replace_ladder_day",
                            lambda d, rows: calls.append((d, rows)) or len(rows))

        n = ladder.persist(D)

        assert n == 1
        assert calls[0][0] == D
        assert [r.code for r in calls[0][1]] == ["002952"]

    def test_clears_when_no_ladder_but_derived_rows_exist(self, stub, monkeypatch):
        """derived_bar 当日有行、候选全被过滤（全 ST / 全无连板）= 真平静，清空当日。"""
        stub(rows=[], derived_count=4123)
        calls = []
        monkeypatch.setattr(ladder, "replace_ladder_day",
                            lambda d, rows: calls.append((d, rows)) or len(rows))

        assert ladder.persist(D) == 0
        assert calls == [(D, [])]

    def test_refuses_when_derived_bar_empty(self, stub, monkeypatch):
        stub(rows=[], derived_count=0)
        calls = []
        monkeypatch.setattr(ladder, "replace_ladder_day",
                            lambda d, rows: calls.append((d, rows)) or len(rows))

        with pytest.raises(RuntimeError, match="上游缺数，拒绝清空"):
            ladder.persist(D)
        assert calls == []
