"""domain/signal.py 单元测试：Checklist / Signal / Action / SignalSource。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.signal import (
    Action,
    Checklist,
    Signal,
    SignalSource,
)


D = date(2024, 1, 2)


class TestAction:
    """信号动作枚举。"""

    def test_action_values(self):
        assert Action.BUY.value == "BUY"
        assert Action.SECONDARY.value == "SECONDARY"
        assert Action.SELL.value == "SELL"

    def test_action_is_str_enum(self):
        assert Action.BUY == "BUY"


class TestSignalSource:
    """信号来源枚举。"""

    def test_source_values(self):
        assert SignalSource.LIVE.value == "live"
        assert SignalSource.REPLAY.value == "replay"


class TestChecklist:
    """5 条件 + 1 警告 checklist。"""

    def test_all_passed(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        assert cl.passed is True

    def test_one_fails(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=False,  # 失败
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        assert cl.passed is False

    def test_none_skipped(self):
        """None 表示 UNKNOWN，不计入判定。"""
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=None,  # 未知，跳过
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=None,
        )
        assert cl.passed is True

    def test_all_none(self):
        """全 None → passed 为 True（没有 False）。"""
        cl = Checklist(
            c1_uniqueness=None,
            c2_exchange=None,
            c3_elimination=None,
            c4_min_days=None,
            c5_strength_diverge=None,
            w1_crowding=None,
        )
        assert cl.passed is True

    def test_warning_never_vetoes(self):
        """警告 w1_crowding 永不否决。"""
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=True,  # 警告触发
        )
        assert cl.passed is True

    def test_frozen(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        try:
            cl.c1_uniqueness = False
        except AttributeError:
            pass
        else:
            raise AssertionError("Checklist 应该是 frozen dataclass")


class TestSignal:
    """信号数据类。"""

    def test_construction(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        sig = Signal(
            code="002952",
            date=D,
            action=Action.BUY,
            checklist=cl,
            source=SignalSource.LIVE,
        )
        assert sig.code == "002952"
        assert sig.date == D
        assert sig.action == Action.BUY
        assert sig.checklist.passed is True
        assert sig.source == SignalSource.LIVE
        assert sig.status == "SUGGESTED"

    def test_status_adopted(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        sig = Signal(
            code="002952",
            date=D,
            action=Action.BUY,
            checklist=cl,
            source=SignalSource.LIVE,
            status="ADOPTED",
        )
        assert sig.status == "ADOPTED"

    def test_replay_source(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        sig = Signal(
            code="600519",
            date=D,
            action=Action.SECONDARY,
            checklist=cl,
            source=SignalSource.REPLAY,
        )
        assert sig.source == SignalSource.REPLAY
        assert sig.action == Action.SECONDARY

    def test_frozen(self):
        cl = Checklist(
            c1_uniqueness=True,
            c2_exchange=True,
            c3_elimination=True,
            c4_min_days=True,
            c5_strength_diverge=True,
            w1_crowding=False,
        )
        sig = Signal(
            code="002952",
            date=D,
            action=Action.BUY,
            checklist=cl,
            source=SignalSource.LIVE,
        )
        try:
            sig.status = "EXPORTED"
        except AttributeError:
            pass
        else:
            raise AssertionError("Signal 应该是 frozen dataclass")
