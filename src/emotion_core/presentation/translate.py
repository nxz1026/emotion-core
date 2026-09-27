"""决策语言翻译层：通俗语言，不用术语。"""
from __future__ import annotations

PHRASE_MAP = {
    "发酵": "上升期",
    "高潮": "狂热期",
    "退潮": "下跌期",
    "冰点": "低迷期",
}

WINDOW_MAP = {
    "ENHANCED": "可买入（强度高）",
    "STANDARD": "可买入",
    "NONE": "禁买",
}


def translate_phase(phase: str) -> str:
    """翻译情绪阶段为通俗语言。"""
    return PHRASE_MAP.get(phase, phase)


def translate_window(window: str) -> str:
    """翻译窗口为通俗语言。"""
    return WINDOW_MAP.get(window, window)


def translate_rating(rating: str) -> str:
    """翻译生态评级为通俗语言。"""
    if rating == "FAVORABLE":
        return "有利"
    elif rating == "NEUTRAL":
        return "一般"
    elif rating == "UNFAVORABLE":
        return "不利"
    return rating
