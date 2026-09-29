"""calibrate 周度校准提案测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.services import calibrate


class TestAgg:
    def test_empty(self):
        result = calibrate._agg(pd.DataFrame())
        assert result == {"n": 0, "promote": None, "gap": None}

    def test_with_data(self):
        df = pd.DataFrame({"t1_promote": [0.5, 0.7], "t1_gap": [0.01, 0.02]})
        result = calibrate._agg(df)
        assert result["n"] == 2
        assert result["promote"] == pytest.approx(0.6)
        assert result["gap"] == pytest.approx(0.015)


class TestRollingQuality:
    def test_format(self):
        full = pd.DataFrame({"t1_promote": [0.6], "t1_gap": [0.02]})
        recent = pd.DataFrame({"t1_promote": [0.5], "t1_gap": [0.01]})
        result = calibrate._rolling_quality(full, recent, 60, date(2024, 6, 15), date(2024, 4, 1))
        assert "全期" in result
        assert "近60日" in result
        assert "信号数" in result


class TestThresholdSensitivity:
    def test_with_data(self, monkeypatch):
        df = pd.DataFrame({
            "bomb_rate": [0.3, 0.4],
            "bomb_threshold": [0.35, 0.45],
            "top_amplitude": [5.0, 6.0],
            "limit_up_count": [50, 80],
            "reason": ["正常", "无候选放宽"],
        })
        monkeypatch.setattr(calibrate, "query_df", lambda sql: df)
        result = calibrate._threshold_sensitivity()
        assert "自适应炸板阈值带" in result
        assert "命中" in result


class TestOutcomeSummary:
    def test_empty(self, monkeypatch):
        monkeypatch.setattr(calibrate, "query_df", lambda sql: pd.DataFrame())
        result = calibrate._outcome_summary()
        assert "前向5日齐的信号还没有" in result

    def test_with_data(self, monkeypatch):
        df = pd.DataFrame({
            "action": ["BUY", "BUY", "WATCH"],
            "code": ["c1", "c2", "c3"],
            "t1_gap": [0.01, 0.02, 0.0],
            "t1_promote": [0.5, 0.7, 0.3],
            "t5_close_ret": [0.03, 0.05, 0.01],
            "max_up5": [0.05, 0.08, 0.02],
            "max_dd5": [0.01, 0.02, 0.03],
        })
        monkeypatch.setattr(calibrate, "query_df", lambda sql: df)
        result = calibrate._outcome_summary()
        assert "BUY" in result


class TestProposals:
    def test_stable(self, monkeypatch):
        full = pd.DataFrame({"t1_promote": [0.6] * 20})
        recent = pd.DataFrame({"t1_promote": [0.55] * 10})
        monkeypatch.setattr(calibrate, "query_df", lambda sql: pd.DataFrame({
            "lo": [0.3], "hi": [0.5]
        }))
        result = calibrate._proposals(full, recent, 60)
        assert any("无参数触发异常" in p for p in result)

    def test_decline_detected(self, monkeypatch):
        full = pd.DataFrame({"t1_promote": [0.7] * 20})
        recent = pd.DataFrame({"t1_promote": [0.4] * 10})
        monkeypatch.setattr(calibrate, "query_df", lambda sql: pd.DataFrame({
            "lo": [0.3], "hi": [0.5]
        }))
        result = calibrate._proposals(full, recent, 60)
        assert any("显著低于" in p for p in result)

    def test_threshold_breach(self, monkeypatch):
        full = pd.DataFrame({"t1_promote": [0.6] * 20})
        recent = pd.DataFrame({"t1_promote": [0.55] * 10})
        monkeypatch.setattr(calibrate, "query_df", lambda sql: pd.DataFrame({
            "lo": [0.1], "hi": [0.6]
        }))
        result = calibrate._proposals(full, recent, 60)
        assert any("越出" in p for p in result)
