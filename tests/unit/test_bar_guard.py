"""`data/bar_guard.py` 写库前自检测试。

判据的由来见模块 docstring：2026-09-29 的 5218 行被写成 2026-10-13 的盘中快照
（行的 date 是 09-29，价格是 10-13 的），三层判据都要能抓住它。
"""

from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data import bar_guard

D1 = date(2026, 9, 29)


def _row(
    code: str = "600825",
    d: date = D1,
    o: float = 9.41,
    h: float = 9.50,
    low: float = 9.30,
    close: float = 9.41,
    pre: float = 8.55,
    vol: float = 1000.0,
) -> tuple:
    return (code, d, o, h, low, close, pre, vol, 10000.0, 1.0)


class TestRowProblems:
    def test_clean_row_has_no_problem(self):
        assert bar_guard.row_problems([_row()]) == []

    def test_ohlc_inconsistent(self):
        bad = _row(o=10.0, h=10.2, low=9.0, close=11.0)  # high < close
        assert bar_guard.row_problems([bad])

    def test_non_positive_close(self):
        bad = _row(o=10.0, h=10.0, low=10.0, close=0.0)
        assert bar_guard.row_problems([bad])

    def test_negative_volume(self):
        assert bar_guard.row_problems([_row(vol=-1.0)])

    def test_padding_row_may_have_zero_prices(self):
        # 顺延行：OHLC 全 0、零成交，close == pre_close（600825 停牌日的形状）
        pad = _row(o=0.0, h=0.0, low=0.0, close=13.78, pre=13.78, vol=0.0)
        assert bar_guard.is_padding_row(pad)
        assert bar_guard.row_problems([pad]) == []

    def test_no_price_padding_row_allowed(self):
        # 「无价占位行」：close=0 但 pre_close 有值 —— 上游既有的合法形状
        # （全市场每天 6~15 行；2026-10-16 真实批里 000016@2.46 等 6 行）
        pad = _row(o=0.0, h=0.0, low=0.0, close=0.0, pre=2.46, vol=0.0)
        assert bar_guard.is_padding_row(pad)
        assert bar_guard.row_problems([pad]) == []

    def test_padding_row_with_new_price_is_rejected(self):
        # 09-29 那起污染的顺延形状：close 取了「回补时刻的最新价」13.78，
        # 而上一根真实收盘是 8.55 ⇒ close 必须 == pre_close 才能自洽
        bad = _row(o=0.0, h=0.0, low=0.0, close=13.78, pre=8.55, vol=0.0)
        problems = bar_guard.row_problems([bad])
        assert problems and "顺延行" in problems[0]

    def test_row_width(self):
        assert bar_guard.row_problems([("600825", D1, 1.0)])


class TestCheckBars:
    def test_empty_passes(self):
        bar_guard.check_bars([])

    def test_shape_problem_raises(self):
        with pytest.raises(bar_guard.BarGuardError, match="形状/顺延"):
            bar_guard.check_bars([_row(o=10.0, h=10.0, low=10.0, close=12.0)], lookup=False)

    def test_clean_batch_without_db(self):
        bar_guard.check_bars([_row()], lookup=False)

    def test_batch_median_gap_rejects_wrong_day(self):
        """整批 30 行 pre_close 都比库内前收高 3% ⇒ 判为写错日期。"""
        rows = [_row(code=f"6005{i:02d}", pre=10.30) for i in range(30)]
        prev = {(r[0], r[1]): 10.0 for r in rows}
        with pytest.raises(bar_guard.BarGuardError, match="整批自洽"):
            bar_guard.check_bars(rows, prev_close=prev)
        # allow_gap 显式放行（历史修复场景）
        bar_guard.check_bars(rows, prev_close=prev, allow_gap=True)

    def test_consistent_batch_passes(self):
        rows = [_row(code=f"6005{i:02d}", pre=10.0) for i in range(30)]
        prev = {(r[0], r[1]): 10.0 for r in rows}
        bar_guard.check_bars(rows, prev_close=prev)

    def test_small_batch_not_statistically_judged(self):
        rows = [_row(code=f"6005{i:02d}", pre=10.30) for i in range(5)]
        prev = {(r[0], r[1]): 10.0 for r in rows}  # 偏离 3%，但只有 5 行
        bar_guard.check_bars(rows, prev_close=prev)

    def test_exrights_row_only_warns(self):
        """除权除息日：单行偏离 > 21% 只记告警，中位仍 ~0 ⇒ 放行。"""
        rows = [_row(code=f"6005{i:02d}", pre=10.0) for i in range(29)]
        rows.append(_row(code="600519", pre=5.0))  # 腰斩（10 送 10 复权）
        prev = {(r[0], r[1]): 10.0 for r in rows}
        bar_guard.check_bars(rows, prev_close=prev)

    def test_unknown_prev_close_skips_chain(self):
        bar_guard.check_bars([_row()], prev_close={})

    def test_db_failure_is_not_fatal(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("库不可用")

        monkeypatch.setattr(bar_guard, "prev_closes", _boom)
        bar_guard.check_bars([_row()])  # lookup=True，但库挂了不该拦住写入


class TestPrevCloses:
    def test_queries_db_once_per_chunk(self, monkeypatch):
        calls: list[object] = []

        class _Cur:
            def execute(self, query, params):
                calls.append(params)

            def fetchall(self):
                return [("600825", D1, 8.55)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Conn:
            def cursor(self):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(bar_guard, "transaction", lambda: _Conn())
        got = bar_guard.prev_closes([_row(), _row(code="000001")])
        assert got[("600825", D1)] == 8.55
        assert len(calls) == 1
