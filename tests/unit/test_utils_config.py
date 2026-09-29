"""utils/config.py 单元测试：Config 字段默认值与 config_hash。"""
from __future__ import annotations

from datetime import date

from emotion_core.utils.config import Config, CONFIG, config_hash


class TestConfigDefaults:
    """Config 默认值。"""

    def test_data_start(self):
        assert CONFIG.DATA_START == date(2024, 1, 1)

    def test_board_prefixes(self):
        assert "600" in CONFIG.BOARD_PREFIXES
        assert "000" in CONFIG.BOARD_PREFIXES
        assert "002" in CONFIG.BOARD_PREFIXES

    def test_limit_ratio(self):
        assert CONFIG.LIMIT_RATIO == 0.10

    def test_climax_thresholds(self):
        assert CONFIG.CLIMAX_ZT == 80
        assert CONFIG.CLIMAX_AMPLITUDE == 15.0

    def test_ferment_threshold(self):
        assert CONFIG.FERMENT_ZT_PERF == 1.5

    def test_ice_thresholds(self):
        assert CONFIG.ICE_ZT_MAX == 40

    def test_leader_thresholds(self):
        assert CONFIG.MIN_LEADER_DAYS == 4
        assert CONFIG.SECONDARY_MIN_DAYS == 3

    def test_exchange_turnover_min(self):
        assert CONFIG.EXCHANGE_TURNOVER_MIN == 5.0

    def test_promotion_min_denom(self):
        assert CONFIG.PROMOTION_MIN_DENOM == 3

    def test_min_coverage(self):
        assert CONFIG.MIN_COVERAGE == 0.90

    def test_bomb_rate_fallback(self):
        assert CONFIG.BOMB_RATE_FALLBACK == 0.42

    def test_bomb_rate_window(self):
        assert CONFIG.BOMB_RATE_WINDOW == 30

    def test_new_issuer_min_days(self):
        assert CONFIG.NEW_ISSUER_MIN_DAYS == 90

    def test_new_issuer_floor(self):
        assert CONFIG.NEW_ISSUER_FLOOR == date(2024, 1, 2)

    def test_bse_excluded_prefixes(self):
        assert "4" in CONFIG.BSE_EXCLUDED_PREFIXES
        assert "8" in CONFIG.BSE_EXCLUDED_PREFIXES
        assert "920" in CONFIG.BSE_EXCLUDED_PREFIXES
        assert "bj" in CONFIG.BSE_EXCLUDED_PREFIXES

    def test_ebb_thresholds(self):
        assert CONFIG.EBB_ZT_PERF == -2.0
        assert CONFIG.EBB_LD_MIN == 15
        assert CONFIG.EBB_LD_MULT == 2.0

    def test_ice_max_days(self):
        assert CONFIG.ICE_MAX_DAYS == 3

    def test_diverge_neg_min(self):
        assert CONFIG.DIVERGE_NEG_MIN == 3

    def test_switches_off_by_default(self):
        assert CONFIG.SECONDARY_EXPORT_ENABLED is False
        assert CONFIG.TRADE_EXPORT_ENABLED is False
        assert CONFIG.LLM_PROFILE is None

    def test_frozen(self):
        """Config 是 frozen dataclass。"""
        try:
            CONFIG.DATA_START = date(2025, 1, 1)
        except AttributeError:
            pass
        else:
            raise AssertionError("Config 应该是 frozen dataclass")

    def test_limit_pct_by_prefix(self):
        assert CONFIG.LIMIT_PCT_BY_PREFIX["68"] == 120
        assert CONFIG.LIMIT_PCT_BY_PREFIX["30"] == 120
        assert CONFIG.LIMIT_PCT_DEFAULT == 110


class TestConfigHash:
    """config_hash 函数。"""

    def test_returns_string(self):
        h = config_hash()
        assert isinstance(h, str)

    def test_returns_12_chars(self):
        h = config_hash()
        assert len(h) == 12

    def test_deterministic(self):
        """同一 Config 产生相同 hash。"""
        h1 = config_hash()
        h2 = config_hash()
        assert h1 == h2

    def test_uses_sha256(self):
        """hash 是 sha256 前 12 字符。"""
        import hashlib
        import json
        from dataclasses import asdict
        payload = json.dumps(asdict(CONFIG), default=str, sort_keys=True)
        expected = hashlib.sha256(payload.encode()).hexdigest()[:12]
        assert config_hash() == expected


class TestNewConfig:
    """新建 Config 实例。"""

    def test_default_instance(self):
        cfg = Config()
        assert cfg.DATA_START == date(2024, 1, 1)
        assert cfg.CLIMAX_ZT == 80

    def test_custom_instance(self):
        cfg = Config(CLIMAX_ZT=100, MIN_LEADER_DAYS=5)
        assert cfg.CLIMAX_ZT == 100
        assert cfg.MIN_LEADER_DAYS == 5
        # 其他字段保持默认
        assert cfg.DATA_START == date(2024, 1, 1)
