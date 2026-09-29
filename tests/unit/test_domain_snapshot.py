"""domain/snapshot.py 单元测试：展示层视图模型。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.snapshot import (
    LadderSnapshot,
    MarketSnapshot,
    SignalSnapshot,
    StockSnapshot,
)


D = date(2024, 1, 2)


class TestMarketSnapshot:
    """直观层首页数据。"""

    def test_construction(self):
        snap = MarketSnapshot(
            date=D,
            phase_text="上升期",
            buy_window="STANDARD",
            limit_up_count=55,
            bomb_count=8,
            max_height=6,
            ecosystem_rating="G2",
            one_liner="情绪回暖，连板高度打开",
        )
        assert snap.date == D
        assert snap.phase_text == "上升期"
        assert snap.buy_window == "STANDARD"
        assert snap.limit_up_count == 55
        assert snap.bomb_count == 8
        assert snap.max_height == 6
        assert snap.ecosystem_rating == "G2"
        assert snap.one_liner == "情绪回暖，连板高度打开"
        assert snap.sample_n == 0

    def test_frozen(self):
        snap = MarketSnapshot(
            date=D,
            phase_text="上升期",
            buy_window="STANDARD",
            limit_up_count=55,
            bomb_count=8,
            max_height=6,
            ecosystem_rating="G2",
            one_liner="test",
        )
        try:
            snap.phase_text = "退潮期"
        except AttributeError:
            pass
        else:
            raise AssertionError("MarketSnapshot 应该是 frozen dataclass")


class TestLadderSnapshot:
    """逻辑层梯队数据。"""

    def test_construction_empty(self):
        snap = LadderSnapshot(date=D)
        assert snap.date == D
        assert snap.rows == ()

    def test_construction_with_rows(self):
        from emotion_core.domain.ladder import LadderDay
        rows = (
            LadderDay(
                date=D, code="002952", cont_days=5,
                is_exchange=True, is_top=True, is_sole_top=True,
                y_top_group_count=3, y_top_survivor_count=1,
            ),
        )
        snap = LadderSnapshot(date=D, rows=rows)
        assert len(snap.rows) == 1
        assert snap.rows[0].code == "002952"

    def test_frozen(self):
        snap = LadderSnapshot(date=D)
        try:
            snap.date = date(2024, 1, 3)
        except AttributeError:
            pass
        else:
            raise AssertionError("LadderSnapshot 应该是 frozen dataclass")


class TestSignalSnapshot:
    """直观层推荐数据。"""

    def test_construction(self):
        snap = SignalSnapshot(
            code="002952",
            date=D,
            cont_days=5,
            is_exchange=True,
            checklist={"c1": True, "c2": True},
        )
        assert snap.code == "002952"
        assert snap.date == D
        assert snap.cont_days == 5
        assert snap.is_exchange is True
        assert snap.checklist == {"c1": True, "c2": True}
        assert snap.odds == {}
        assert snap.sample_n == 0

    def test_with_odds(self):
        snap = SignalSnapshot(
            code="002952",
            date=D,
            cont_days=5,
            is_exchange=True,
            checklist={"c1": True},
            odds={"rule": 0.65, "ml": 0.72},
            sample_n=100,
        )
        assert snap.odds == {"rule": 0.65, "ml": 0.72}
        assert snap.sample_n == 100

    def test_none_code(self):
        """无推荐时 code 为 None。"""
        snap = SignalSnapshot(
            code=None,
            date=D,
            cont_days=0,
            is_exchange=False,
            checklist={},
        )
        assert snap.code is None


class TestStockSnapshot:
    """个股诊断视图模型。"""

    def test_construction_minimal(self):
        snap = StockSnapshot(
            code="600519",
            name="贵州茅台",
            date=D,
            cont_days=0,
            is_exchange=False,
            is_sole_top=False,
        )
        assert snap.code == "600519"
        assert snap.name == "贵州茅台"
        assert snap.date == D
        assert snap.cont_days == 0
        assert snap.is_exchange is False
        assert snap.is_sole_top is False
        assert snap.leader_history == ()
        assert snap.signal_history == ()
        assert snap.promotion_rate is None
        assert snap.divergence is None
        assert snap.sample_n == 0

    def test_construction_full(self):
        snap = StockSnapshot(
            code="002952",
            name="XXX",
            date=D,
            cont_days=5,
            is_exchange=True,
            is_sole_top=True,
            leader_history=(date(2024, 1, 1), date(2023, 12, 28)),
            signal_history=(date(2024, 1, 2),),
            promotion_rate=0.65,
            divergence=0.12,
            sample_n=50,
        )
        assert snap.promotion_rate == 0.65
        assert snap.divergence == 0.12
        assert snap.sample_n == 50
        assert len(snap.leader_history) == 2

    def test_frozen(self):
        snap = StockSnapshot(
            code="600519",
            name="贵州茅台",
            date=D,
            cont_days=0,
            is_exchange=False,
            is_sole_top=False,
        )
        try:
            snap.name = "YYY"
        except AttributeError:
            pass
        else:
            raise AssertionError("StockSnapshot 应该是 frozen dataclass")
