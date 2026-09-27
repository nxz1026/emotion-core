"""ThemeProvider 协议：题材数据获取的抽象层。

设计：
- ThemeProvider 协议定义题材标签获取接口
- NullProvider 默认实现（降级不阻断，返回空列表）
- WindProvider 占位（subprocess 调 node CLI，暂不实现）
"""
from __future__ import annotations

from datetime import date
from typing import Protocol


class ThemeProvider(Protocol):
    """题材标签获取协议。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        """获取当日题材标签行。

        Returns:
            标签行列表，每行格式：
            {"code": str, "primary_theme": str, "secondary_themes": list[str]}
        """


class NullProvider:
    """空实现：题材数据不可用时返回空列表，不阻断主链。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        return []


class WindProvider:
    """Wind MCP 题材获取（占位，暂不实现）。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        raise NotImplementedError("WindProvider 暂不实现，请使用 NullProvider")


def get_provider(name: str | None = None) -> ThemeProvider:
    """获取题材 Provider 实例。

    Args:
        name: provider 名称（"null" / "wind"），None 返回 NullProvider。

    Returns:
        ThemeProvider 实例。
    """
    if name == "wind":
        return WindProvider()
    return NullProvider()
