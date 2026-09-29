"""LLM 抽象层：接口与空实现。

设计：填入 prompt，输出信息。不关心 LLM 是什么、怎么调、怎么计费。
默认关闭：主链不依赖 LLM；LLM 层是可选的。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端接口。填入 prompt，输出信息。"""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """填入 prompt，输出信息。

        Args:
            prompt: 用户 prompt（可含模板变量）
            system: 系统提示（可选）
            temperature: 温度（0=确定性，1=随机）
            max_tokens: 最大输出 token 数
        Returns:
            LLM 输出的文本
        """
        ...


class LLMNotEnabledError(RuntimeError):
    """LLM 未启用时抛出。主链应捕获此错误并跳过 LLM 增强。"""


class NullClient:
    """空实现：LLM 未启用时使用。抛出 LLMNotEnabledError。"""

    def complete(self, prompt: str, **kwargs) -> str:
        raise LLMNotEnabledError("LLM 未启用；设置 LLM_PROFILE=agnes 启用")


def get_client(profile: str | None = None) -> LLMClient:
    """获取 LLM 客户端。

    Args:
        profile: "agnes" 启用 Agnes，None 返回 NullClient。

    Returns:
        LLMClient 实例。
    """
    if profile == "agnes":
        from emotion_core.llm.agnes import AgnesClient

        return AgnesClient()
    return NullClient()
