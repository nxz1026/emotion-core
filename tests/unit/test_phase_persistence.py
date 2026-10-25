"""`phase_persistence` 单测：连续天数与「历史同第几天」的延续率（DB-free）。

钉三件事：
1. 连续天数按**相邻行**算（`market_stat` 逐交易日一行，相位相同才 +1）；
2. 延续率的样本是「同一相位 + 同一连续天数」，只有严格同名同序号的才算；
3. 缺数返回空 dict / `n=0`，不填 0%、不硬编「大概会延续」。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import phase_persistence as pp


def _mock(monkeypatch, phases: list[str], start: date = date(2026, 9, 1)):
    """按顺序造 `market_stat` 相位序列（每日一行，跳过周末不必要——只测逻辑）。"""
    dates = [start.fromordinal(start.toordinal() + i) for i in range(len(phases))]
    df = pd.DataFrame({"date": pd.to_datetime(dates), "phase": phases})
    monkeypatch.setattr(pp, "query_df", lambda sql: df.copy())
    return dates


class TestSeries:
    def test_streak_counts_consecutive_same_phase(self, monkeypatch):
        _mock(monkeypatch, ["冰点", "退潮", "退潮", "退潮", "高潮", "高潮"])
        got = pp._series()
        assert got["streak"].tolist() == [1, 1, 2, 3, 1, 2]

    def test_same_next_marked_for_last_row(self, monkeypatch):
        _mock(monkeypatch, ["退潮", "退潮", "高潮"])
        got = pp._series()
        # 最后一行没有次日 → NaN（不计入任何样本）
        assert bool(got["same_next"].iloc[-1] is False or pd.isna(got["same_next"].iloc[-1]))
        assert got["same_next"].iloc[0] is True or bool(got["same_next"].iloc[0])
        assert not bool(got["same_next"].iloc[1])

    def test_empty_table_gives_empty_frame(self, monkeypatch):
        monkeypatch.setattr(pp, "query_df", lambda sql: pd.DataFrame())
        assert pp._series().empty


class TestPersistence:
    def test_rate_uses_same_phase_and_same_streak(self, monkeypatch):
        # 「退潮第 2 天」共出现 3 次（第 1、4、8 天）：只有第 4 天次日仍退潮
        phases = ["退潮", "退潮", "高潮", "退潮", "退潮", "退潮", "冰点", "退潮", "退潮", "高潮"]
        dates = _mock(monkeypatch, phases)
        got = pp.persistence(dates[4])  # 退潮第 2 天
        assert got["phase"] == "退潮"
        assert got["streak"] == 2
        assert got["n"] == 3  # 三个「退潮第 2 天」样本
        assert got["same_next_n"] == 1
        assert got["rate"] == pytest.approx(33, abs=0.6)

    def test_unknown_date_gives_empty(self, monkeypatch):
        _mock(monkeypatch, ["退潮", "退潮"])
        assert pp.persistence(date(2020, 1, 1)) == {}

    def test_no_history_for_streak_gives_zero_n(self, monkeypatch):
        _mock(monkeypatch, ["退潮", "退潮", "退潮", "退潮", "退潮", "退潮", "退潮"])
        got = pp.persistence(date(2026, 9, 7))  # 第 7 天，历史上没有第 7 天的样本
        assert got["streak"] == 7
        assert got["n"] == 0
        assert got["rate"] is None


class TestLine:
    def test_returns_readable_sentence(self):
        text = pp.line({"phase": "退潮", "streak": 3, "n": 20, "rate": 65.0, "same_next_n": 13})
        assert "退潮已连续 3 天" in text
        assert "13/20" in text
        assert "65%" in text

    def test_no_sample_says_so(self):
        text = pp.line({"phase": "发酵", "streak": 9, "n": 0, "rate": None, "same_next_n": 0})
        assert "没有" in text and "延续率" in text

    def test_empty_dict_gives_empty_string(self):
        assert pp.line({}) == ""
