"""utils/config.py 配置与 config_hash 测试。"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from emotion_core.utils.config import CONFIG, Config, config_hash


class TestConfigDefaults:
    def test_data_start(self):
        assert CONFIG.DATA_START == date(2024, 1, 1)

    def test_board_prefixes(self):
        assert "600" in CONFIG.BOARD_PREFIXES
        assert "000" in CONFIG.BOARD_PREFIXES

    def test_limit_pct_default(self):
        assert CONFIG.LIMIT_PCT_DEFAULT == 110

    def test_climax_zt(self):
        assert CONFIG.CLIMAX_ZT == 80

    def test_bse_excluded(self):
        assert "4" in CONFIG.BSE_EXCLUDED_PREFIXES
        assert "8" in CONFIG.BSE_EXCLUDED_PREFIXES

    def test_ice_max_days(self):
        assert CONFIG.ICE_MAX_DAYS == 3

    def test_min_coverage(self):
        assert CONFIG.MIN_COVERAGE == 0.90

    def test_strategy_disabled_by_default(self):
        assert CONFIG.STRATEGY_ENABLED is False


class TestConfigHash:
    def test_returns_string(self):
        h = config_hash()
        assert isinstance(h, str)

    def test_length(self):
        assert len(config_hash()) == 12

    def test_deterministic(self):
        assert config_hash() == config_hash()

    def test_changes_with_config(self):
        original = config_hash()
        # Create a modified config
        import dataclasses
        mod = dataclasses.replace(CONFIG, CLIMAX_ZT=999)
        payload = json.dumps(dataclasses.asdict(mod), default=str, sort_keys=True)
        modified_hash = hashlib.sha256(payload.encode()).hexdigest()[:12]
        assert modified_hash != original
