"""报告用词表：把内部代号翻成用户能读的话。

为什么放在 `algorithms/review/` 而不是 `presentation/`：这些词是**报告正文的一部分**，
由 `algorithms/review` 在拼 Markdown 时就落地（⓪/①/⑫ 段），而架构约束是
`presentation → algorithms` 单向（`tests/architecture/test_dependency_direction.py`）。
把词表放在算法层、由展示层 re-export，既满足依赖方向，也不让两处各写一套说法。

`presentation/translate.py` 保留为薄壳 re-export，`translate_phase/window/rating`
的公开签名不变（`tests/unit/test_translate.py` 仍按原路径调用）。
"""
from __future__ import annotations

#: 相位 → 人话。括号里仍会带上原相位名，方便和内部文档对照。
PHRASE_MAP = {
    "发酵": "上升期",
    "高潮": "狂热期",
    "退潮": "下跌期",
    "冰点": "低迷期",
}

#: 情绪档位 → 人话（W8′：报告里不再出现 `buy_window=` 字样）。
#: A 止血（2026-10-21）：这列不再表示「能不能买」——系统已不出买入建议，
#: 它只是情绪强度档（活跃 / 一般 / 低迷），供观察时判断市场冷热。
WINDOW_MAP = {
    "ENHANCED": "活跃",
    "STANDARD": "一般",
    "NONE": "低迷",
}

#: 生态评级 → 人话。
RATING_MAP = {
    "FAVORABLE": "有利",
    "NEUTRAL": "一般",
    "UNFAVORABLE": "不利",
}


def translate_phase(phase: str) -> str:
    """翻译情绪阶段为通俗语言（未知值原样返回，不猜）。"""
    return PHRASE_MAP.get(phase, phase)


def translate_window(window: str) -> str:
    """翻译情绪档位为通俗语言（`buy_window` 的历史列名保留）。"""
    return WINDOW_MAP.get(window, window)


def translate_rating(rating: str) -> str:
    """翻译生态评级为通俗语言。"""
    return RATING_MAP.get(rating, rating)
