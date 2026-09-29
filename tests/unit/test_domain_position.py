"""domain/position.py 单元测试：Position 持仓数据类。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.position import Position


D = date(2024, 1, 2)


class TestPosition:
    """Position 持仓行数据类。"""

    def test_construction_minimal(self):
        pos = Position(
            code="600519",
            entry_date=D,
            entry_price=1500.0,
            shares=1000,
        )
        assert pos.code == "600519"
        assert pos.entry_date == D
        assert pos.entry_price == 1500.0
        assert pos.shares == 1000
        assert pos.status == "OPEN"
        assert pos.note == ""
        assert pos.id is None

    def test_construction_full(self):
        pos = Position(
            code="000001",
            entry_date=D,
            entry_price=12.5,
            shares=2000,
            status="CLOSED",
            note="止盈",
            id=42,
        )
        assert pos.status == "CLOSED"
        assert pos.note == "止盈"
        assert pos.id == 42

    def test_frozen(self):
        pos = Position(
            code="600519",
            entry_date=D,
            entry_price=1500.0,
            shares=1000,
        )
        try:
            pos.shares = 2000
        except AttributeError:
            pass
        else:
            raise AssertionError("Position 应该是 frozen dataclass")

    def test_default_status_open(self):
        """新建持仓默认状态为 OPEN。"""
        pos = Position(
            code="600519",
            entry_date=D,
            entry_price=100.0,
            shares=100,
        )
        assert pos.status == "OPEN"

    def test_entry_price_is_float(self):
        """entry_price 用浮点数（元）。"""
        pos = Position(
            code="600519",
            entry_date=D,
            entry_price=1688.88,
            shares=100,
        )
        assert isinstance(pos.entry_price, float)
        assert pos.entry_price == 1688.88
