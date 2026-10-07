"""晋级矩阵单测。

覆盖：多层双口径统计（正常）、分母不足置 None（F6）、边界日/缺数据返回空列表。
纯计算用 `_matrix`；边界守卫用假连接 + monkeypatch（不触库）。
真实数据语义对账见 tests/oracle/fixtures/promotion_day.json（3305 行 / 661 日）。
"""
from __future__ import annotations

import logging
from datetime import date

import pytest

from emotion_core.algorithms import promotion
from emotion_core.algorithms.promotion import PROMOTION_LAYERS, _matrix, promotion_matrix

D = date(2024, 1, 2)


class _FakeCur:
    def __enter__(self) -> _FakeCur:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    """仅用于边界守卫路径（提前 return，不执行 SQL）。"""

    def cursor(self) -> _FakeCur:
        return _FakeCur()

    def close(self) -> None:
        raise AssertionError("外部传入的 conn 不应被关闭")


# (昨日板数, 今日板数, 今日是否换手, 今日涨幅%|None)
_PAIRS = [
    (1, 2, True, 5.0),    # 1->2 晋级（换手）
    (1, 2, False, 3.0),   # 1->2 晋级（一字）
    (1, 0, False, -2.0),  # 1->2 失败
    (1, 1, False, -4.0),  # 1->2 失败
    (1, 1, False, -6.0),  # 1->2 失败
    (2, 3, True, 1.0),    # 2->3 晋级（分母 2 < 3）
    (2, 0, False, -1.0),  # 2->3 失败（样本 1 < 3）
    (3, 4, True, 2.0),    # 3->4 晋级
    (3, 0, False, -1.0),  # 3->4 失败
    (3, 0, False, -3.0),  # 3->4 失败
    (3, 0, False, -5.0),  # 3->4 失败
    (5, 6, True, 4.0),    # 5+->6+ 晋级
    (8, 0, False, -5.0),  # 5+->6+ 失败（需 >=9 才晋级）
]


def _rows() -> dict[str, dict]:
    return {r["layer"]: r for r in _matrix(D, _PAIRS)}


class TestMatrixMultiLayer:
    """正常场景：多层晋级，双口径与失败负反馈。"""

    def test_layer_order_and_date(self):
        assert [r["layer"] for r in _matrix(D, _PAIRS)] == [n for n, _, _ in PROMOTION_LAYERS]
        assert all(r["date"] == D for r in _matrix(D, _PAIRS))

    def test_first_board_layer(self):
        rows = _rows()
        assert rows["1->2"] == {
            "date": D, "layer": "1->2", "promote_from": 5,
            "promote_nominal": 2, "promote_exchange": 1,
            "rate_nominal": 0.4, "rate_exchange": 0.2, "divergence": 0.2,
            "fail_perf": -4.0,  # 失败股 (-2 + -4 + -6) / 3
        }

    def test_third_board_layer(self):
        r = _rows()["3->4"]
        assert (r["promote_from"], r["promote_nominal"], r["promote_exchange"]) == (4, 1, 1)
        assert r["rate_nominal"] == 0.25
        assert r["rate_exchange"] == 0.25
        assert r["divergence"] == 0.0
        assert r["fail_perf"] == -3.0

    def test_upper_layer_merges_five_plus(self):
        # 5 与 8 同层；8 板今日归零 → 判失败
        assert _rows()["5+->6+"] == {
            "date": D, "layer": "5+->6+", "promote_from": 2,
            "promote_nominal": 1, "promote_exchange": 1,
            "rate_nominal": None, "rate_exchange": None, "divergence": None,
            "fail_perf": None,  # 失败样本 1 < 3
        }

    def test_empty_layer(self):
        assert _rows()["4->5"] == {
            "date": D, "layer": "4->5", "promote_from": 0,
            "promote_nominal": 0, "promote_exchange": 0,
            "rate_nominal": None, "rate_exchange": None, "divergence": None,
            "fail_perf": None,
        }


class TestDenomGuard:
    """分母不足：比率与背离置 None，不补零（F6）。"""

    def test_rate_none_when_denom_below_threshold(self):
        rows = {r["layer"]: r for r in _matrix(D, _PAIRS)}
        assert rows["2->3"]["promote_from"] == 2  # < PROMOTION_MIN_DENOM(3)
        assert rows["2->3"]["rate_nominal"] is None
        assert rows["2->3"]["rate_exchange"] is None
        assert rows["2->3"]["divergence"] is None

    def test_denominator_threshold_boundary(self):
        # 分母恰好为 3 → 出率；2 → None
        at3 = _matrix(D, [(1, 2, True, 1.0), (1, 0, False, 1.0), (1, 0, False, 1.0)])
        assert at3[0]["promote_from"] == 3
        assert at3[0]["rate_nominal"] == pytest.approx(0.3333)
        at2 = _matrix(D, [(1, 2, True, 1.0), (1, 0, False, 1.0)])
        assert at2[0]["rate_nominal"] is None

    def test_fail_perf_none_when_no_valid_sample(self):
        # 全晋级（无失败样本）→ None；涨幅缺失一并剔除
        rows = _matrix(D, [(1, 2, True, None), (1, 2, True, None), (1, 2, True, None)])
        assert rows[0]["fail_perf"] is None

    def test_missing_today_row_counts_as_fail(self):
        # 今日无行由 SQL 归零（t_cont=0），判失败并计入 fail_perf
        rows = _matrix(D, [(1, 0, False, -2.0)] * 3)
        assert rows[0]["promote_nominal"] == 0
        assert rows[0]["fail_perf"] == -2.0


class TestBoundary:
    """边界日 / 缺数据：返回空列表，不产出假数。"""

    def test_no_prev_trading_day(self, monkeypatch):
        """★2026-10-07：日历失效是「算不出来」，**不是**「今天没晋级」。

        原实现与「真平静」一样返回 `[]`，`persist` 由此把当日已有行 DELETE 掉。
        现在抛 `PromotionSkipped`，`persist` 据此跳过删除。
        """
        monkeypatch.setattr(promotion, "_prev_trading_day", lambda cur, d: None)
        with pytest.raises(promotion.PromotionSkipped, match="无前一交易日"):
            promotion_matrix(D, conn=_FakeConn())

    def test_today_bars_missing(self, monkeypatch):
        """上游缺数同理：不得被当成「真平静」而清空当日行。"""
        monkeypatch.setattr(promotion, "_prev_trading_day", lambda cur, d: date(2023, 12, 29))
        monkeypatch.setattr(promotion, "_today_bar_count", lambda cur, d: 0)
        with pytest.raises(promotion.PromotionSkipped, match="derived_bar 当日整体缺失"):
            promotion_matrix(D, conn=_FakeConn())

    def test_no_yesterday_limit_up(self, monkeypatch):
        """真平静：确实没有晋级对，仍返回 []（这是唯一允许 persist 清空的语义）。"""
        monkeypatch.setattr(promotion, "_prev_trading_day", lambda cur, d: date(2023, 12, 29))
        monkeypatch.setattr(promotion, "_today_bar_count", lambda cur, d: 100)
        monkeypatch.setattr(promotion, "_fetch_pairs", lambda cur, d, p: [])
        assert promotion_matrix(D, conn=_FakeConn()) == []


class _RecordingConn:
    """记录 execute() 的连接替身（`persist` 用 `with connect() as conn`）。"""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql, params=()) -> None:
        self.sql.append(sql)


class TestPersistDoesNotDestroyDataOnGuardHit:
    """★2026-10-07 回归：`PromotionSkipped` 命中时**一条 SQL 都不许发**。

    原实现的失效模式：`promotion_matrix` 对「日历失效」和「上游缺数」也返回 `[]`，
    `persist` 只看 `if not rows` ⇒ 无条件 `DELETE FROM promotion_day WHERE date=%s`
    并提交，日志写「真平静，清空」；而 `daily.py` 不消费 persist 的返回值，
    步骤照样标 `DONE`。**数据被删 + 步骤是绿的 + 零告警**。
    """

    def _stub(self, monkeypatch, rows_or_exc):
        conn = _RecordingConn()
        monkeypatch.setattr(promotion, "connect", lambda: conn)
        monkeypatch.setattr(promotion, "promotion_matrix",
                            lambda *a, **k: (_ for _ in ()).throw(rows_or_exc)
                            if isinstance(rows_or_exc, Exception) else rows_or_exc)
        return conn

    def test_guard_hit_sends_no_sql(self, monkeypatch):
        conn = self._stub(monkeypatch,
                          promotion.PromotionSkipped(D, "derived_bar 当日整体缺失"))
        assert promotion.persist(D) == 0
        assert conn.sql == [], f"守卫命中仍发了 SQL，数据会被清空：{conn.sql}"

    def test_guard_hit_logs_error_not_warning(self, monkeypatch, caplog):
        self._stub(monkeypatch, promotion.PromotionSkipped(D, "无前一交易日"))
        with caplog.at_level(logging.ERROR, logger="emotion_core.algorithms.promotion"):
            promotion.persist(D)
        assert "保持原样（未清空）" in caplog.text

    def test_genuine_quiet_day_still_deletes(self, monkeypatch):
        """「真平静」仍要清空——否则源修正后的幽灵层会一直残留。"""
        conn = self._stub(monkeypatch, [])
        assert promotion.persist(D) == 0
        assert any("DELETE FROM promotion_day" in s for s in conn.sql)
