"""卖出建议逐组合（不连库）：口径B「T+2 开盘无条件卖 / 跌停封死顺延 / T+3 强制了结」。

裁决来源：`_audit/2026-10-21-修正方案-实测定档.md`（奎爷 2026-10-21 批准）。
旧规则（退潮清仓 / 断板卖出）已废止，理由见 algorithms/exit.py 模块文档：
持有到断板 mean −1.90%/胜率 38.1%，而 T+2 开盘卖 mean +3.54%/胜率 68%。

本文件锁四件事：
1. `held_days` 的交易日计数（建仓当日 = 0，T+2 = 1，T+3 = 2）；
2. `_sell` 的四个分支（建仓当日无建议 / T+2 开盘卖 / 开盘跌停封死顺延 / n≥2 强制了结）；
3. `_open_locked_limit_down` 的开盘跌停+封死判定（缺数按卖得掉处理）；
4. exec_hint 恒 CLOSE_ALL（契约 v2），SELL 不落库。

IO（daily_bar / derived_bar / position / 交易日历）全部桩掉，真实库差分另跑。
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
    _open_locked_limit_down,
    _open_positions,
    _sell,
    held_days,
)
from emotion_core.domain.bar import DerivedBar
from emotion_core.domain.signal import Action, Signal, SignalSource

D = date(2026, 9, 4)          # 建议日（建仓后第 3 个交易日）
ENTRY = date(2026, 9, 1)      # 建仓日（口径B 的 T+1 封板日）
T2 = date(2026, 9, 2)         # 建仓后第 1 个交易日 = 卖出日
T3 = date(2026, 9, 3)         # 建仓后第 2 个交易日 = 强制了结日


def pos(code: str = "000017", entry: date = ENTRY) -> Holding:
    return Holding(code=code, entry_date=entry)


def day(up: bool = True, cont: int = 6, code: str = "000017", d: date = T2) -> DerivedBar:
    return DerivedBar(code=code, date=d, is_limit_up=up, is_limit_down=False,
                      is_one_word=False, is_exchange=up, is_bomb=False,
                      touched_limit=up, cont_days=cont, amplitude=3.2)


class _Cal:
    """交易日历替身：只返回 [start, end] 内的预设日期，并记录调用。"""

    def __init__(self, days: list[date]):
        self.days, self.calls = days, []

    def __call__(self, start: date, end: date) -> list[date]:
        self.calls.append((start, end))
        return [d for d in self.days if start <= d <= end]


def patch_cal(monkeypatch, days: list[date] | None = None) -> _Cal:
    cal = _Cal(days if days is not None else [T2, T3, D])
    monkeypatch.setattr(exit, "trading_days", cal)
    return cal


def patch_io(monkeypatch, *, positions: list[Holding],
             bars: dict[str, DerivedBar | None] | None = None,
             locked: set[str] | None = None) -> dict[str, Any]:
    """桩掉 position / derived / 开盘跌停判定，返回调用记录。"""
    bars = bars or {}
    locked = locked or set()
    calls: dict[str, Any] = {"derived": [], "locked": [], "cal": patch_cal(monkeypatch)}
    monkeypatch.setattr(exit, "_open_positions", lambda: list(positions))
    monkeypatch.setattr(exit, "_derived",
                        lambda d, code: calls["derived"].append((d, code)) or bars.get(code))
    monkeypatch.setattr(exit, "_open_locked_limit_down",
                        lambda d, code: calls["locked"].append((d, code)) or code in locked)
    return calls


# ────────────────────────── held_days：交易日计数 ──────────────────────────

class TestHeldDays:
    def test_zero_on_entry_day(self, monkeypatch):
        patch_cal(monkeypatch)
        assert held_days(ENTRY, ENTRY) == 0
        assert held_days(T2, ENTRY) == 0          # 反向（脏数据）也归 0 而不是负数

    def test_one_on_next_trading_day(self, monkeypatch):
        patch_cal(monkeypatch)
        assert held_days(ENTRY, T2) == 1

    def test_two_on_the_day_after(self, monkeypatch):
        patch_cal(monkeypatch)
        assert held_days(ENTRY, T3) == 2

    def test_counts_only_trading_days(self, monkeypatch):
        """周末不计：9-04(五) 建仓 → 9-07(一) 才是第 1 个交易日。"""
        cal = patch_cal(monkeypatch, [date(2026, 9, 7)])
        assert held_days(date(2026, 9, 4), date(2026, 9, 7)) == 1
        assert cal.calls == [(date(2026, 9, 5), date(2026, 9, 7))]


# ────────────────────────── _sell：四个分支 ──────────────────────────

class TestSellBranches:
    def test_entry_day_no_signal(self, monkeypatch):
        patch_io(monkeypatch, positions=[pos(entry=D)])
        assert _sell(pos(entry=D), D) is None

    def test_sell_at_open_on_t2(self, monkeypatch):
        patch_io(monkeypatch, positions=[pos()])
        sig = _sell(pos(), T2)
        assert sig is not None and sig.action is Action.SELL
        assert sig.date == T2                          # 建议日 = 卖出日
        assert "T+2 开盘卖出" in sig.reason
        assert "口径B" in sig.reason
        assert sig.exec_hint == "CLOSE_ALL"            # 契约 v2
        assert sig.buy_window == ""                    # 卖出不带买入口径标签

    def test_still_limit_up_still_sells(self, monkeypatch):
        """当日仍封板也照卖——口径B 不持有到断板。"""
        patch_io(monkeypatch, positions=[pos()], bars={"000017": day(up=True)})
        sig = _sell(pos(), T2)
        assert sig is not None and "照卖" in sig.reason

    def test_locked_limit_down_defers(self, monkeypatch):
        """开盘跌停且全天封死 → 卖不掉 → 当日无建议（顺延）。"""
        calls = patch_io(monkeypatch, positions=[pos()], locked={"000017"})
        assert _sell(pos(), T2) is None
        assert calls["locked"] == [(T2, "000017")]
        assert calls["derived"] == []                  # 卖不掉就不读判据行

    def test_force_close_on_t3(self, monkeypatch):
        patch_io(monkeypatch, positions=[pos()])
        sig = _sell(pos(), T3)
        assert sig is not None and "强制了结" in sig.reason
        assert "已持有2个交易日" in sig.reason

    def test_force_close_beyond_t3_and_ignores_lock(self, monkeypatch):
        """超过 T+3 仍未卖（含再次跌停）→ 仍出强制了结，不无限顺延。"""
        calls = patch_io(monkeypatch, positions=[pos()], locked={"000017"})
        sig = _sell(pos(), D)                          # 已持有 3 个交易日
        assert sig is not None and "强制了结" in sig.reason
        assert "已持有3个交易日" in sig.reason
        assert calls["locked"] == []                   # 强制分支不读跌停封死

    def test_signal_contract_fields(self, monkeypatch):
        """返回的是 domain.Signal（子类）：消费方按 Signal 契约取字段。"""
        patch_io(monkeypatch, positions=[pos()])
        sig = _sell(pos(), T2)
        assert isinstance(sig, SellSignal) and isinstance(sig, Signal)
        assert sig.code == "000017" and sig.action is Action.SELL
        assert sig.source is SignalSource.LIVE and sig.status == "SUGGESTED"
        assert sig.checklist.passed is True            # 六项 UNKNOWN：无否决条件
        assert sig.checklist.c1_uniqueness is None


# ────────────────────────── 开盘跌停封死判定 ──────────────────────────

class TestOpenLocked:
    def patch_bar(self, monkeypatch, bar: tuple[float, float, float | None, float] | None):
        monkeypatch.setattr(exit, "_open_bar", lambda d, code: bar)

    def test_locked_when_open_at_limit_down_and_low_equals_open(self, monkeypatch):
        # 主板前收 10.00 → 跌停 9.00；开盘 9.00 且收盘 9.00 = 全天封死
        self.patch_bar(monkeypatch, (9.00, 9.00, 10.00, 9.00))
        assert _open_locked_limit_down(T2, "000017") is True

    def test_not_locked_when_opened_intraday(self, monkeypatch):
        """开盘跌停但收盘拉起来（收盘 9.60 > 跌停 9.00）→ 开盘卖得掉。"""
        self.patch_bar(monkeypatch, (9.00, 9.00, 10.00, 9.60))
        assert _open_locked_limit_down(T2, "000017") is False

    def test_not_locked_when_gapped_above(self, monkeypatch):
        self.patch_bar(monkeypatch, (9.80, 9.70, 10.00, 9.75))
        assert _open_locked_limit_down(T2, "000017") is False

    def test_missing_pre_close_not_locked(self, monkeypatch):
        self.patch_bar(monkeypatch, (9.00, 9.00, None, 9.00))
        assert _open_locked_limit_down(T2, "000017") is False

    def test_missing_bar_not_locked(self, monkeypatch):
        """缺数按「卖得掉」处理（保守：宁可给卖出建议，不静默顺延）。"""
        self.patch_bar(monkeypatch, None)
        assert _open_locked_limit_down(T2, "000017") is False

    def test_chinext_uses_20pct_band(self, monkeypatch):
        """创业板 300xxx 跌停是 −20%：前收 10.00 → 8.00。"""
        self.patch_bar(monkeypatch, (8.00, 8.00, 10.00, 8.00))
        assert _open_locked_limit_down(T2, "300750") is True
        self.patch_bar(monkeypatch, (9.00, 9.00, 10.00, 9.00))
        assert _open_locked_limit_down(T2, "300750") is False


# ────────────────────────── suggestions：组合路径（IO 全桩） ──────────────────────────

class TestSuggestions:
    def test_entry_day_position_is_skipped(self, monkeypatch):
        """建仓当日不卖；同一张表里已到期的那只照卖。"""
        patch_io(monkeypatch, positions=[pos("601988", ENTRY), pos("000001", D)])
        sigs = exit.suggestions(D)
        assert [s.code for s in sigs] == ["601988"]
        assert sigs[0].date == D and "强制了结" in sigs[0].reason

    def test_t2_one_sells_one_defers(self, monkeypatch):
        """同为 T+2：一只正常 → 卖；一只跌停封死 → 顺延，当日无建议。"""
        patch_io(monkeypatch, positions=[pos("601988", ENTRY), pos("000001", ENTRY)],
                 bars={"601988": day(up=True, code="601988")}, locked={"000001"})
        sigs = exit.suggestions(T2)
        assert [s.code for s in sigs] == ["601988"]
        assert "T+2 开盘卖出" in sigs[0].reason

    def test_no_positions_empty_list(self, monkeypatch):
        patch_io(monkeypatch, positions=[])
        assert exit.suggestions(D) == []

    def test_derived_read_per_selling_position(self, monkeypatch):
        calls = patch_io(monkeypatch,
                         positions=[pos("601988", ENTRY), pos("000001", ENTRY)],
                         bars={"601988": day(up=False, code="601988"),
                               "000001": day(up=True, code="000001")})
        sigs = exit.suggestions(T2)
        assert [s.code for s in sigs] == ["601988", "000001"]
        assert calls["derived"] == [(T2, "601988"), (T2, "000001")]


# ────────────────────────── 两个读函数（connect_ro 替身） ──────────────────────────

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

    def test_open_bar_projection(self, monkeypatch):
        conn = patch_reads(monkeypatch, _FakeConn(
            row=(Decimal("9.00"), Decimal("9.12"), Decimal("10.00"), Decimal("9.40"))))
        assert exit._open_bar(D, "000017") == (9.0, 9.12, 10.0, 9.4)
        sql, params = conn.calls[0]
        assert "daily_bar" in sql and params == (D, "000017")

    def test_open_positions_projection_and_sql(self, monkeypatch):
        conn = patch_reads(monkeypatch, _FakeConn(
            rows=[("601988", ENTRY), ("000001", D)]))
        assert _open_positions() == [Holding("601988", ENTRY), Holding("000001", D)]
        sql, params = conn.calls[0]
        assert "status = 'OPEN'" in sql and "ORDER BY entry_date" in sql
        assert params is None
