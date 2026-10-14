"""tests for `algorithms/sizing.py`（R3 持仓决策数量口径，docs/17 §6.3；决策 D1/D2/D9）。

纯函数穷举边界：不连库、不发请求。
"""
from __future__ import annotations

import pytest

from emotion_core.algorithms import sizing as sz


class TestThreeConstraintsTakeMin:
    def test_risk_wins(self):
        # risk =1_000_000×1%/(20−18)=5_000 ; maxpos=1_000_000×30%/20=15_000 ; liq 很大
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=18.0, amount=3e8)
        assert s.suggested_shares == 5_000
        assert s.method == "risk+liq+maxpos"
        assert s.position_pct == pytest.approx(0.10)
        assert s.suggested_amount == pytest.approx(100_000.0)

    def test_liquidity_wins(self):
        # liq =1_000_000×1%/20=500 ; risk 5_000 ; maxpos 15_000
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=18.0, amount=1_000_000)
        assert s.suggested_shares == 500

    def test_maxpos_wins(self):
        # 止损极近 → risk=1_000_000×1%/0.1=100_000 ; maxpos=15_000 兜住
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=19.9, amount=1e9)
        assert s.suggested_shares == 15_000

    def test_rounds_down_to_lot(self):
        # risk =1_000_000×1%/3=3_333.33 → 3_300
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=17.0, amount=1e12)
        assert s.suggested_shares == 3_300
        assert "每手 100 股" in s.detail


class TestStarBoardLot:
    def test_lot_is_200(self):
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=18.5,
                           amount=1e12, board="star")
        # risk =1_000_000×1%/1.5=6_666.67 → //200×200 = 6_600
        assert s.suggested_shares == 6_600
        assert "每手 200 股" in s.detail

    @pytest.mark.parametrize(
        ("board", "lot"), [("main", 100), ("star", 200), ("kcb", 200), ("688", 200),
                           ("", 100), ("sh_main", 100)]
    )
    def test_lot_size(self, board, lot):
        assert sz.lot_size(board) == lot


class TestMissingCapitalD2:
    def test_pct_only_risk_derived(self):
        # 0.01 ÷ ((20−18)/20) = 0.10
        s = sz.plan_shares(capital=None, entry=20.0, stop=18.0, amount=1e9)
        assert s.suggested_shares is None
        assert s.suggested_amount is None
        assert s.method == "risk_only_pct"
        assert s.position_pct == pytest.approx(0.10)

    def test_pct_capped_by_max_position(self):
        # 0.01 ÷ 0.005 = 2.0 → 被单票上限 0.30 压住
        s = sz.plan_shares(capital=None, entry=20.0, stop=19.9, amount=1e9)
        assert s.position_pct == pytest.approx(0.30)

    def test_no_capital_and_no_stop_is_liquidity_only(self):
        s = sz.plan_shares(capital=None, entry=20.0, stop=None, amount=1e9)
        assert s.suggested_shares is None
        assert s.position_pct is None
        assert s.method == "liquidity_only"
        assert "未给总资金" in s.detail


class TestDroppedConstraints:
    def test_stop_equals_entry_disables_risk(self):
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=20.0, amount=1e9)
        assert s.method == "liq+maxpos"
        assert s.suggested_shares == 15_000

    def test_stop_above_entry_disables_risk(self):
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=25.0, amount=1e9)
        assert "risk" not in s.method

    def test_stop_missing_disables_risk(self):
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=None, amount=1e9)
        assert s.method == "liq+maxpos"
        assert s.suggested_shares == 15_000

    @pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), "abc", True])
    def test_bad_amount_drops_liquidity(self, bad):
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=18.0, amount=bad)
        assert "liq" not in s.method
        assert s.suggested_shares == 5_000          # risk 5_000 < maxpos 15_000

    def test_only_maxpos_available_gives_no_shares(self):
        """只剩单票上限 → 它是「上限」不是「建议」，不给股数（上限只写进 detail）。"""
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=None, amount=None)
        assert s.suggested_shares is None
        assert s.position_pct is None
        assert s.method == "maxpos_only"
        assert "15000" in s.detail

    def test_no_inputs_at_all(self):
        s = sz.plan_shares(capital=None, entry=20.0, stop=None, amount=None)
        assert s.suggested_shares is None
        assert s.method == "liquidity_only"
        assert "未给总资金" in s.detail


class TestBadEntry:
    @pytest.mark.parametrize("bad", [None, 0, -5, float("nan"), "abc", True])
    def test_bad_entry_gives_none(self, bad):
        s = sz.plan_shares(capital=1_000_000, entry=bad, stop=18.0, amount=1e9)
        assert s.suggested_shares is None
        assert s.position_pct is None
        assert s.method == "none"


class TestZeroIsNotNone:
    def test_tiny_amount_yields_zero_not_none(self):
        # liq =1_000×1%/20=0.5 → 0 股；risk 5_000 / maxpos 15_000 → 取小 0
        s = sz.plan_shares(capital=1_000_000, entry=20.0, stop=18.0, amount=1_000)
        assert s.suggested_shares == 0
        assert s.position_pct == 0.0


class TestMethodVocabulary:
    """锁死 `method` 的取值集合：`entry` 有效时**绝不**出现 `none`。

    曾经有一个 `if not cand:` 的「三条约束全无有效输入」分支，但它不可达 ——
    走到那里 `capital` 必有效 ⇒ `cand` 里必有 `maxpos`。该分支已删，本测试防它回来。
    """

    def test_valid_entry_never_yields_none(self):
        import itertools

        seen = set()
        for c, s, a in itertools.product(
            [None, 0, -1, 1_000_000.0],
            [None, 0, 25.0, 20.0, 15.0],
            [None, 0, -1, 5e8],
        ):
            seen.add(sz.plan_shares(capital=c, entry=20.0, stop=s, amount=a).method)
        assert seen == {
            "liquidity_only", "risk_only_pct", "maxpos_only",
            "liq+maxpos", "risk+maxpos", "risk+liq+maxpos",
        }


class TestConstantsStayOutOfConfig:
    def test_values(self):
        assert (sz.RISK_PCT, sz.LIQ_PCT, sz.MAX_POSITION_PCT) == (0.01, 0.01, 0.30)

    def test_not_in_config(self):
        """docs/17 §1.3：加进 CONFIG 会让 config_hash() 换代。

        注意 `CONFIG` 是 `@dataclass(frozen=True)`，没有 `.get()`。
        """
        from emotion_core.utils import config

        for name in ("RISK_PCT", "LIQ_PCT", "MAX_POSITION_PCT",
                     "FALLBACK_BUY_PCT", "FALLBACK_STOP_PCT"):
            assert not hasattr(config.CONFIG, name), f"{name} 不该进 CONFIG"


class TestSizingBounds:
    """R4 参数化合法区间（docs/18 §7.3）。"""

    def test_bounds_values(self):
        assert sz.SIZING_BOUNDS == {
            "risk_pct": (0.001, 0.05),
            "liq_pct": (0.001, 0.05),
            "max_position_pct": (0.05, 1.0),
        }

    def test_plan_shares_accepts_custom_pct(self):
        # risk_pct=2% → risk=1_000_000×2%/(20−18)=10_000 ; maxpos=15_000 ; liq 很大
        s = sz.plan_shares(
            capital=1_000_000, entry=20.0, stop=18.0, amount=3e8, risk_pct=0.02,
        )
        assert s.suggested_shares == 10_000

    def test_plan_shares_board_arg_unchanged(self):
        # 已有调用方零改动：board 仍按位置参数工作
        s = sz.plan_shares(
            capital=1_000_000, entry=20.0, stop=18.0, amount=1_000_000, board="star",
        )
        assert s.suggested_shares == 400  # liq 获胜，lot=200 → floor(500, 200) = 400
