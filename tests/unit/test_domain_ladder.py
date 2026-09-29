"""domain/ladder.py 单元测试：LadderDay / LeaderIdentity。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.ladder import LadderDay, LeaderIdentity


D = date(2024, 1, 2)


class TestLadderDay:
    """梯队行数据类。"""

    def test_construction(self):
        row = LadderDay(
            date=D,
            code="002952",
            cont_days=5,
            is_exchange=True,
            is_top=True,
            is_sole_top=True,
            y_top_group_count=3,
            y_top_survivor_count=1,
        )
        assert row.date == D
        assert row.code == "002952"
        assert row.cont_days == 5
        assert row.is_exchange is True
        assert row.is_top is True
        assert row.is_sole_top is True
        assert row.y_top_group_count == 3
        assert row.y_top_survivor_count == 1

    def test_frozen(self):
        row = LadderDay(
            date=D,
            code="002952",
            cont_days=5,
            is_exchange=True,
            is_top=True,
            is_sole_top=True,
            y_top_group_count=3,
            y_top_survivor_count=1,
        )
        try:
            row.cont_days = 6
        except AttributeError:
            pass
        else:
            raise AssertionError("LadderDay 应该是 frozen dataclass")

    def test_non_exchange_not_top(self):
        """非换手板不应进入最高层。"""
        row = LadderDay(
            date=D,
            code="003000",
            cont_days=2,
            is_exchange=False,
            is_top=False,
            is_sole_top=False,
            y_top_group_count=1,
            y_top_survivor_count=0,
        )
        assert row.is_exchange is False
        assert row.is_top is False
        assert row.is_sole_top is False

    def test_r2_survivor_ratio(self):
        """R2 淘汰赛：y_top_survivor_count / y_top_group_count。"""
        row = LadderDay(
            date=D,
            code="600519",
            cont_days=4,
            is_exchange=True,
            is_top=True,
            is_sole_top=False,
            y_top_group_count=4,
            y_top_survivor_count=2,
        )
        ratio = row.y_top_survivor_count / row.y_top_group_count
        assert ratio == 0.5


class TestLeaderIdentity:
    """龙头身份数据类。"""

    def test_construction(self):
        leader = LeaderIdentity(
            date=D,
            code="002952",
            cont_days=5,
            is_exchange=True,
            is_sole_top=True,
        )
        assert leader.date == D
        assert leader.code == "002952"
        assert leader.cont_days == 5
        assert leader.is_exchange is True
        assert leader.is_sole_top is True

    def test_frozen(self):
        leader = LeaderIdentity(
            date=D,
            code="002952",
            cont_days=5,
            is_exchange=True,
            is_sole_top=True,
        )
        try:
            leader.cont_days = 6
        except AttributeError:
            pass
        else:
            raise AssertionError("LeaderIdentity 应该是 frozen dataclass")

    def test_leader_requires_exchange_and_sole_top(self):
        """龙头 = 唯一最高板 + 充分换手。"""
        leader = LeaderIdentity(
            date=D,
            code="002952",
            cont_days=5,
            is_exchange=True,
            is_sole_top=True,
        )
        assert leader.is_exchange is True
        assert leader.is_sole_top is True

    def test_non_leader(self):
        """非龙头：非唯一最高板。"""
        non_leader = LeaderIdentity(
            date=D,
            code="603530",
            cont_days=4,
            is_exchange=True,
            is_sole_top=False,
        )
        assert non_leader.is_sole_top is False
