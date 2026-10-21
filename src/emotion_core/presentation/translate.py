"""决策语言翻译层（本站唯一的"说人话"入口）。

实现放在 `algorithms/review/wording.py`：报告正文由算法层拼装，而架构约束是
`presentation → algorithms` 单向。这里只做 re-export，公开签名保持不变。
"""
from __future__ import annotations

from emotion_core.algorithms.review.wording import (
    PHRASE_MAP,
    RATING_MAP,
    WINDOW_MAP,
    translate_phase,
    translate_rating,
    translate_window,
)

__all__ = [
    "PHRASE_MAP",
    "RATING_MAP",
    "WINDOW_MAP",
    "translate_phase",
    "translate_rating",
    "translate_window",
]
