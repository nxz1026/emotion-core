"""domain/market.py 单元测试：MarketStat / Phase / BuyWindow。"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.market import BuyWindow, MarketStat, Phase


D = date(2024, 1, 2)


class TestPhase:
    """情绪周期 4 相枚举。"""

    def test_phase_values(self):
        assert Phase.ICE.value == "冰点"
        assert Phase.FERMENT.value == "发酵"
        assert Phase.CLIMAX.value == "高潮"
        assert Phase.EBB.value == "退潮"

    def test_phase_is_str_enum(self):
        assert isinstance(Phase.ICE, str)
        assert Phase.ICE == "冰点"

    def test_phase_priority_order(self):
        """优先级：退潮 > 高潮 > 发酵 > 冰点。"""
        # 枚举定义顺序即优先级顺序
        phases = list(Phase)
        assert phases == [Phase.ICE, Phase.FERMENT, Phase.CLIMAX, Phase.EBB]


class TestBuyWindow:
    """买入窗口枚举。"""

    def test_buy_window_values(self):
        assert BuyWindow.STANDARD.value == "STANDARD"
        assert BuyWindow.ENHANCED.value == "ENHANCED"
        assert BuyWindow.NONE.value == "NONE"

    def test_buy_window_is_str_enum(self):
        assert BuyWindow.STANDARD == "STANDARD"


class TestMarketStat:
    """MarketStat 状态机输出数据类。"""

    def test_construction_minimal(self):
        stat = MarketStat(
            date=D,
            phase=Phase.FERMENT,
            buy_window=BuyWindow.STANDARD,
            limit_up_count=55,
            bomb_count=8,
            limit_down_count=3,
            max_height=6,
            force_liquidate=False,
        )
        assert stat.date == D
        assert stat.phase == Phase.FERMENT
        assert stat.buy_window == BuyWindow.STANDARD
        assert stat.limit_up_count == 55
        assert stat.bomb_count == 8
        assert stat.limit_down_count == 3
        assert stat.max_height == 6
        assert stat.force_liquidate is False

    def test_construction_full(self):
        stat = MarketStat(
            date=D,
            phase=Phase.EBB,
            buy_window=BuyWindow.NONE,
            limit_up_count=20,
            bomb_count=15,
            limit_down_count=25,
            max_height=2,
            force_liquidate=True,
            dragon_env="B3",
            dragon_env_reasons="炸板率过高",
            dragon_env_risks="情绪退潮",
            accel_reason="连板加速",
            has_candidate=True,
            bomb_threshold=0.45,
            config_hash="abc123def456",
        )
        assert stat.phase == Phase.EBB
        assert stat.buy_window == BuyWindow.NONE
        assert stat.force_liquidate is True
        assert stat.dragon_env == "B3"
        assert stat.dragon_env_reasons == "炸板率过高"
        assert stat.dragon_env_risks == "情绪退潮"
        assert stat.accel_reason == "连板加速"
        assert stat.has_candidate is True
        assert stat.bomb_threshold == 0.45
        assert stat.config_hash == "abc123def456"

    def test_defaults(self):
        stat = MarketStat(
            date=D,
            phase=Phase.ICE,
            buy_window=BuyWindow.NONE,
            limit_up_count=30,
            bomb_count=5,
            limit_down_count=2,
            max_height=3,
            force_liquidate=False,
        )
        assert stat.dragon_env == ""
        assert stat.dragon_env_reasons == ""
        assert stat.dragon_env_risks == ""
        assert stat.accel_reason == ""
        assert stat.has_candidate is False
        assert stat.bomb_threshold == 0.42
        assert stat.config_hash == ""

    def test_frozen(self):
        stat = MarketStat(
            date=D,
            phase=Phase.CLIMAX,
            buy_window=BuyWindow.ENHANCED,
            limit_up_count=90,
            bomb_count=3,
            limit_down_count=1,
            max_height=10,
            force_liquidate=False,
        )
        try:
            stat.limit_up_count = 100
        except AttributeError:
            pass
        else:
            raise AssertionError("MarketStat 应该是 frozen dataclass")

    def test_ebb_force_liquidate(self):
        """退潮时 force_liquidate 应为 True。"""
        stat = MarketStat(
            date=D,
            phase=Phase.EBB,
            buy_window=BuyWindow.NONE,
            limit_up_count=15,
            bomb_count=20,
            limit_down_count=30,
            max_height=1,
            force_liquidate=True,
        )
        assert stat.force_liquidate is True
        assert stat.buy_window == BuyWindow.NONE

    def test_climax_enhanced_window(self):
        """高潮时 buy_window 应为 ENHANCED。"""
        stat = MarketStat(
            date=D,
            phase=Phase.CLIMAX,
            buy_window=BuyWindow.ENHANCED,
            limit_up_count=85,
            bomb_count=5,
            limit_down_count=2,
            max_height=8,
            force_liquidate=False,
        )
        assert stat.buy_window == BuyWindow.ENHANCED
