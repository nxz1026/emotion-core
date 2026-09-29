"""presentation/translate.py 决策语言翻译测试。"""
from __future__ import annotations

from emotion_core.presentation.translate import (
    translate_phase,
    translate_window,
    translate_rating,
)


class TestTranslatePhase:
    def test_known_phrases(self):
        assert translate_phase("发酵") == "上升期"
        assert translate_phase("高潮") == "狂热期"
        assert translate_phase("退潮") == "下跌期"
        assert translate_phase("冰点") == "低迷期"

    def test_unknown_returns_original(self):
        assert translate_phase("unknown") == "unknown"


class TestTranslateWindow:
    def test_known_windows(self):
        assert translate_window("ENHANCED") == "可买入（强度高）"
        assert translate_window("STANDARD") == "可买入"
        assert translate_window("NONE") == "禁买"

    def test_unknown_returns_original(self):
        assert translate_window("unknown") == "unknown"


class TestTranslateRating:
    def test_favorable(self):
        assert translate_rating("FAVORABLE") == "有利"

    def test_neutral(self):
        assert translate_rating("NEUTRAL") == "一般"

    def test_unfavorable(self):
        assert translate_rating("UNFAVORABLE") == "不利"

    def test_unknown_returns_original(self):
        assert translate_rating("unknown") == "unknown"
