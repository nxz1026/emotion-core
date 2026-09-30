"""services/gaps.py 单元测试：验证动态缺口扫描器的每个检查器。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from emotion_core.services import gaps as gaps_mod


class TestGapsScanner:
    """扫描器整体行为。"""

    def test_scan_gaps_returns_list(self):
        result = gaps_mod.scan_gaps()
        assert isinstance(result, list)

    def test_scan_gaps_each_item_has_required_keys(self):
        result = gaps_mod.scan_gaps()
        for item in result:
            assert "item" in item
            assert "detail" in item
            assert "impl" in item
            assert isinstance(item["item"], str)
            assert isinstance(item["detail"], str)
            assert isinstance(item["impl"], str)

    def test_scan_gaps_no_duplicates(self):
        result = gaps_mod.scan_gaps()
        items = [g["item"] for g in result]
        assert len(items) == len(set(items)), f"重复缺口: {[x for x in items if items.count(x) > 1]}"

    def test_scan_gaps_c7_is_known_gap(self):
        """C7 北交所未过滤应该是已知缺口之一（market_service 不过滤 bj）。"""
        result = gaps_mod.scan_gaps()
        items = [g["item"] for g in result]
        # C7 可能以不同措辞出现
        assert any("C7" in item or "北交所" in item or "bj" in item.lower() for item in items), \
            f"未找到 C7 相关缺口，当前缺口: {items}"

    def test_scan_gaps_summary(self):
        result = gaps_mod.scan_gaps_summary()
        assert "total" in result
        assert "gaps" in result
        assert result["total"] == len(result["gaps"])


class TestCoverageGate:
    """覆盖率门槛检查。"""

    def test_coverage_gate_wired(self):
        """orchestration/daily.py 已调用 coverage.gate()，应不报缺口。"""
        result = gaps_mod._check_coverage_gate()
        assert result is None, f"coverage gate 已接线但仍报缺口: {result}"


class TestEcosystemRs:
    """ecosystem.rs 检查。"""

    def test_ecosystem_rs_not_empty(self):
        """ecosystem.rs 应有实际实现（>20 行有效代码）。"""
        result = gaps_mod._check_ecosystem_rs()
        assert result is None, f"ecosystem.rs 有实现但仍报缺口: {result}"


class TestConfigHash:
    """config_hash() 调用方检查。"""

    def test_config_hash_has_callers(self):
        """config_hash() 应有调用方。"""
        result = gaps_mod._check_config_hash_callers()
        assert result is None, f"config_hash 有调用方但仍报缺口: {result}"


class TestSignalSource:
    """signal 表 source 列检查。"""

    def test_signal_has_source_column(self):
        """signal 表 DDL 应包含 source 列。"""
        result = gaps_mod._check_signal_source()
        assert result is None, f"signal 表有 source 列但仍报缺口: {result}"


class TestC7BjFilter:
    """C7 北交所过滤检查。"""

    def test_c7_bj_filter_missing(self):
        """market_service.py 应缺少 bj/8 前缀过滤。"""
        result = gaps_mod._check_c7_bj_filter()
        # 这个缺口应该存在（market_service 不过滤 bj）
        assert result is not None, "C7 bj 过滤缺口应存在但检查器返回 None"
        assert "C7" in result["item"] or "北交所" in result["item"] or "bj" in result["item"].lower()
