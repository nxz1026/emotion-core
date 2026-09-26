"""卖出建议逐组合（不连库）：涨停 / 断板 / 一字 / 退潮 / 缺行。

语义来源 lkl/tests/test_exit.py（4 组合纯函数用例逐条保留）+ suggestions 组合路径：
- _sell 的 date 语义逐字同 lkl：退潮分支填开仓日，suggestions 再统一改写为建议日；
- exec_hint 恒 CLOSE_ALL（契约 v2）；buy_window 只是买入口径标签（退潮期 NONE）；
- 缺 derived_bar 行 = 缺数，不当断板（F6）；stat=None 不退潮（逐仓断板检查）。

IO（market_stat / derived_bar / position）全部桩掉，真实库差分另跑
（见交付记录：同 trade_date 与 lkl.services.exit.suggestions 逐字段一致）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Self

from emotion_core.algorithms import exit
from emotion_core.algorithms.exit import (
    Holding,
    SellSignal,
    _derived,
    _open_positions,
    _sell,
    _stat,
)
from emotion_core.domain.bar import DerivedBar
from emotion_core.domain.signal import Action, Signal, SignalSource

D = date(2026, 9, 4)          # 建议日（lkl 用例里的 2026-08-28 位置）
BAR_DATE = date(2026, 8, 28)  # 判据行日期
POS_DATE = date(2026, 8, 20)  # 开仓日


def pos(code: str = "000017", entry: date = POS_DATE) -> Holding:
    return Holding(code=code, entry_date=entry)


def day(up: bool = True, one_word: bool = False, cont: int = 6,
        code: str = "000017", d: date = BAR_DATE) -> DerivedBar:
    return DerivedBar(code=code, date=d, is_limit_up=up, is_limit_down=False,
                      is_one_word=one_word, is_exchange=up and not one_word,
                      is_bomb=False, touched_limit=up, cont_days=cont, amplitude=3.2)


STAT_EBB = {"phase": "退潮", "buy_window": "NONE", "force_liquidate": True}
STAT_CLIMAX = {"phase": "高潮", "buy_window": "ENHANCED", "force_liquidate": False}


# ────────────────────────── _sell：4 组合（lkl oracle 逐条对应） ──────────────────────────

class TestSellCombos:
    def test_holding_limit_up_no_signal(self):
        assert _sell(pos(), None, day(up=True)) is None

    def test_broken_board_sell(self):
        sig = _sell(pos(), None, day(up=False, cont=0))
        assert sig and sig.action == "SELL" and "断板" in sig.reason
        assert sig.exec_hint == "CLOSE_ALL"          # 契约 v2：SELL 执行语义
        assert sig.buy_window == ""                  # 断板分支不带窗口
        assert sig.date == BAR_DATE                  # lkl：confirm_date = day.date

    def test_one_word_still_holding(self):
        assert _sell(pos(), None, day(up=True, one_word=True)) is None

    def test_liquidate_overrides_everything(self):
        sig = _sell(pos(), STAT_EBB, None)
        assert sig and "清仓" in sig.reason
        assert sig.exec_hint == "CLOSE_ALL"          # 退潮清仓同样 CLOSE_ALL
        assert sig.buy_window == "NONE"              # window 只是市场标签
        assert sig.date == POS_DATE                  # 退潮分支先填开仓日（suggestions 再改写）
        assert _sell(pos(), {"force_liquidate": False}, None) is None

    def test_missing_bar_no_signal(self):
        assert _sell(pos(), None, None) is None

    def test_liquidate_ignores_limit_up_day(self):
        """退潮优先级最高：即使当日仍封板（传 day）也走清仓分支。"""
        sig = _sell(pos(), STAT_EBB, day(up=True))
        assert sig is not None and "清仓" in sig.reason and sig.date == POS_DATE

    def test_cont_days_zero_reason_stays_verbatim(self):
        """lkl 文案逐字：f"断板建议（原{cont_days or ''}连板今日未封）"，0 板不留数字。"""
        sig = _sell(pos(), None, day(up=False, cont=0))
        assert sig is not None and sig.reason == "断板建议（原连板今日未封）"
        sig6 = _sell(pos(), None, day(up=False, cont=6))
        assert sig6 is not None and sig6.reason == "断板建议（原6连板今日未封）"

    def test_signal_contract_fields(self):
        """返回的是 domain.Signal（子类）：消费方按 Signal 契约取字段。"""
        sig = _sell(pos(), None, day(up=False, cont=3))
        assert isinstance(sig, SellSignal) and isinstance(sig, Signal)
        assert sig.code == "000017" and sig.action is Action.SELL
        assert sig.source is SignalSource.LIVE and sig.status == "SUGGESTED"
        assert sig.checklist.passed is True          # 六项 UNKNOWN：无否决条件
        assert sig.checklist.c1_uniqueness is None


# ────────────────────────── suggestions：组合路径（IO 全桩） ──────────────────────────

def patch_io(monkeypatch, *, stat: dict | None, positions: list[Holding],
             bars: dict[str, DerivedBar | None]) -> dict[str, Any]:
    calls: dict[str, Any] = {"derived": []}

    def fake_derived(d: date, code: str) -> DerivedBar | None:
        calls["derived"].append((d, code))
        return bars.get(code)

    monkeypatch.setattr(exit, "_stat", lambda d: stat)
    monkeypatch.setattr(exit, "_open_positions", lambda: list(positions))
    monkeypatch.setattr(exit, "_derived", fake_derived)
    return calls


class TestSuggestions:
    def test_liquidate_day_closes_every_position(self, monkeypatch):
        """退潮：全部持仓出清仓建议，且日期统一改写为建议日（逐仓不看判据行）。"""
        patch_io(monkeypatch, stat=STAT_EBB,
                 positions=[pos("601988"), pos("000001")],
                 bars={"601988": day(up=True, code="601988"),
                       "000001": day(up=True, code="000001")})
        sigs = exit.suggestions(D)
        assert [s.code for s in sigs] == ["601988", "000001"]
        for s in sigs:
            assert s.action is Action.SELL and s.date == D
            assert "清仓" in s.reason and s.buy_window == "NONE"
            assert s.exec_hint == "CLOSE_ALL"

    def test_non_liquidate_day_only_broken_board(self, monkeypatch):
        patch_io(monkeypatch, stat=STAT_CLIMAX,
                 positions=[pos("601988"), pos("000001")],
                 bars={"601988": day(up=True, code="601988"),
                       "000001": day(up=False, cont=0, code="000001")})
        sigs = exit.suggestions(D)
        assert [s.code for s in sigs] == ["000001"]
        assert "断板" in sigs[0].reason and sigs[0].date == BAR_DATE

    def test_missing_stat_falls_back_to_broken_board_check(self, monkeypatch):
        """market_stat 无当日行 → 不走退潮分支（lkl 同：stat and …）。"""
        patch_io(monkeypatch, stat=None, positions=[pos("000001")],
                 bars={"000001": day(up=False, cont=2, code="000001")})
        sigs = exit.suggestions(D)
        assert len(sigs) == 1 and "断板" in sigs[0].reason

    def test_missing_bar_no_signal(self, monkeypatch):
        """缺判据行 = 缺数，不当作断板（F6）。"""
        calls = patch_io(monkeypatch, stat=STAT_CLIMAX, positions=[pos("000001")],
                         bars={"000001": None})
        assert exit.suggestions(D) == []
        assert calls["derived"] == [(D, "000001")]

    def test_no_positions_empty_list(self, monkeypatch):
        patch_io(monkeypatch, stat=STAT_EBB, positions=[], bars={})
        assert exit.suggestions(D) == []

    def test_derived_read_per_position(self, monkeypatch):
        calls = patch_io(monkeypatch, stat=STAT_CLIMAX,
                         positions=[pos("601988"), pos("000001")],
                         bars={"601988": day(up=False, code="601988"),
                               "000001": day(up=False, code="000001")})
        sigs = exit.suggestions(D)
        assert [s.code for s in sigs] == ["601988", "000001"]
        assert calls["derived"] == [(D, "601988"), (D, "000001")]

    def test_liquidate_branch_skips_derived_reads(self, monkeypatch):
        calls = patch_io(monkeypatch, stat=STAT_EBB, positions=[pos("601988")], bars={})
        assert exit.suggestions(D)[0].date == D
        assert calls["derived"] == []               # 退潮不读判据行（逐字同 lkl）


# ────────────────────────── 三个读函数（connect_ro 替身） ──────────────────────────

class _FakeConn:
    """connect_ro 的最小替身：记录 (sql, params)，返回预置行。"""

    def __init__(self, row: tuple | None = None, rows: list[tuple] | None = None):
        self._row, self._rows, self.calls = row, rows or [], []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> Self:
        self.calls.append((sql, params))
        return self

    def fetchone(self) -> tuple | None:
        return self._row

    def fetchall(self) -> list[tuple]:
        return self._rows


def patch_reads(monkeypatch, conn: _FakeConn) -> _FakeConn:
    monkeypatch.setattr(exit, "connect_ro", lambda: conn)
    return conn


class TestReads:
    def test_stat_none_without_row(self, monkeypatch):
        patch_reads(monkeypatch, _FakeConn(row=None))
        assert _stat(D) is None

    def test_stat_normalizes_null_force_liquidate(self, monkeypatch):
        conn = patch_reads(monkeypatch, _FakeConn(row=("发酵", "STANDARD", None)))
        assert _stat(D) == {"phase": "发酵", "buy_window": "STANDARD",
                            "force_liquidate": False}
        sql, params = conn.calls[0]
        assert "market_stat" in sql and "force_liquidate" in sql and params == (D,)

    def test_derived_none_without_row(self, monkeypatch):
        conn = patch_reads(monkeypatch, _FakeConn(row=None))
        assert _derived(D, "601988") is None
        sql, params = conn.calls[0]
        assert "derived_bar" in sql and params == (D, "601988")

    def test_derived_builds_full_domain_bar(self, monkeypatch):
        patch_reads(monkeypatch, _FakeConn(
            row=("601988", D, False, False, False, False, False, False, 0,
                 Decimal("1.8237"))))
        bar = _derived(D, "601988")
        assert bar is not None
        assert (bar.code, bar.date, bar.is_limit_up, bar.cont_days) == \
            ("601988", D, False, 0)
        assert bar.amplitude == 1.8237          # Decimal → float
        assert bar.quality == "valid"

    def test_derived_null_amplitude_kept_none(self, monkeypatch):
        """688xxx 停牌行 amplitude IS NULL（存量 15 行）：F6 不补 0，也不 float(None) 崩。"""
        patch_reads(monkeypatch, _FakeConn(
            row=("688432", D, False, False, False, False, False, False, 0, None)))
        bar = _derived(D, "688432")
        assert bar is not None and bar.amplitude is None

    def test_open_positions_projection_and_sql(self, monkeypatch):
        conn = patch_reads(monkeypatch, _FakeConn(
            rows=[("601988", POS_DATE), ("000001", D)]))
        assert _open_positions() == [Holding("601988", POS_DATE), Holding("000001", D)]
        sql, params = conn.calls[0]
        assert "status = 'OPEN'" in sql and "ORDER BY entry_date" in sql
        assert params is None

    def test_sell_is_pure(self):
        """_sell 不碰 IO（纯判定）——喂 dict/对象即可断言，无需桩。"""
        assert _sell(pos(), {"force_liquidate": True}, None) is not None
