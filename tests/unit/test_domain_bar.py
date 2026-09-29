"""domain/bar.py 单元测试：Bar / DerivedBar 数据类构造与字段。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.bar import Bar, DerivedBar


D = date(2024, 1, 2)


class TestBar:
    """Bar 原始日线数据类。"""

    def test_construction(self):
        bar = Bar(
            code="600519",
            date=D,
            open_cents=1000,
            high_cents=1050,
            low_cents=990,
            close_cents=1040,
            pre_close_cents=1000,
            volume=50000,
            turnover_rate=1.23,
        )
        assert bar.code == "600519"
        assert bar.date == D
        assert bar.open_cents == 1000
        assert bar.high_cents == 1050
        assert bar.low_cents == 990
        assert bar.close_cents == 1040
        assert bar.pre_close_cents == 1000
        assert bar.volume == 50000
        assert bar.turnover_rate == 1.23

    def test_frozen(self):
        bar = Bar(
            code="600519",
            date=D,
            open_cents=1000,
            high_cents=1050,
            low_cents=990,
            close_cents=1040,
            pre_close_cents=1000,
            volume=50000,
            turnover_rate=1.23,
        )
        # frozen dataclass 不允许修改
        try:
            bar.code = "000001"
        except AttributeError:
            pass
        else:
            raise AssertionError("Bar 应该是 frozen dataclass")

    def test_is_new_listing_placeholder(self):
        bar = Bar(
            code="600519",
            date=D,
            open_cents=1000,
            high_cents=1050,
            low_cents=990,
            close_cents=1040,
            pre_close_cents=1000,
            volume=50000,
            turnover_rate=1.23,
        )
        # 占位实现返回 False
        assert bar.is_new_listing is False

    def test_price_fields_are_int(self):
        bar = Bar(
            code="000001",
            date=D,
            open_cents=500,
            high_cents=550,
            low_cents=495,
            close_cents=540,
            pre_close_cents=500,
            volume=10000,
            turnover_rate=0.85,
        )
        assert isinstance(bar.open_cents, int)
        assert isinstance(bar.high_cents, int)
        assert isinstance(bar.low_cents, int)
        assert isinstance(bar.close_cents, int)
        assert isinstance(bar.pre_close_cents, int)
        assert isinstance(bar.volume, int)
        assert isinstance(bar.turnover_rate, float)


class TestDerivedBar:
    """DerivedBar 判据输出数据类。"""

    def test_construction_defaults(self):
        dbar = DerivedBar(
            code="600519",
            date=D,
            is_limit_up=True,
            is_limit_down=False,
            is_one_word=False,
            is_exchange=True,
            is_bomb=False,
            touched_limit=True,
            cont_days=3,
            amplitude=8.5,
        )
        assert dbar.code == "600519"
        assert dbar.is_limit_up is True
        assert dbar.is_limit_down is False
        assert dbar.is_one_word is False
        assert dbar.is_exchange is True
        assert dbar.is_bomb is False
        assert dbar.touched_limit is True
        assert dbar.cont_days == 3
        assert dbar.amplitude == 8.5
        # 默认值
        assert dbar.quality == "valid"

    def test_quality_unknown(self):
        dbar = DerivedBar(
            code="000001",
            date=D,
            is_limit_up=False,
            is_limit_down=False,
            is_one_word=False,
            is_exchange=False,
            is_bomb=False,
            touched_limit=False,
            cont_days=0,
            amplitude=0.0,
            quality="unknown",
        )
        assert dbar.quality == "unknown"

    def test_frozen(self):
        dbar = DerivedBar(
            code="600519",
            date=D,
            is_limit_up=True,
            is_limit_down=False,
            is_one_word=False,
            is_exchange=True,
            is_bomb=False,
            touched_limit=True,
            cont_days=3,
            amplitude=8.5,
        )
        try:
            dbar.is_limit_up = False
        except AttributeError:
            pass
        else:
            raise AssertionError("DerivedBar 应该是 frozen dataclass")

    def test_exchange_vs_one_word(self):
        """换手板与一字板互斥。"""
        exchange_bar = DerivedBar(
            code="002952",
            date=D,
            is_limit_up=True,
            is_limit_down=False,
            is_one_word=False,
            is_exchange=True,
            is_bomb=False,
            touched_limit=True,
            cont_days=5,
            amplitude=6.0,
        )
        assert exchange_bar.is_exchange is True
        assert exchange_bar.is_one_word is False

        one_word_bar = DerivedBar(
            code="003000",
            date=D,
            is_limit_up=True,
            is_limit_down=False,
            is_one_word=True,
            is_exchange=False,
            is_bomb=False,
            touched_limit=True,
            cont_days=2,
            amplitude=0.0,
        )
        assert one_word_bar.is_one_word is True
        assert one_word_bar.is_exchange is False

    def test_bomb_definition(self):
        """炸板：曾触板且未涨停。"""
        bomb_bar = DerivedBar(
            code="002800",
            date=D,
            is_limit_up=False,
            is_limit_down=False,
            is_one_word=False,
            is_exchange=False,
            is_bomb=True,
            touched_limit=True,
            cont_days=0,
            amplitude=12.0,
        )
        assert bomb_bar.is_bomb is True
        assert bomb_bar.touched_limit is True
        assert bomb_bar.is_limit_up is False
