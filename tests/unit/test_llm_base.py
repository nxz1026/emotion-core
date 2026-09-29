"""llm/base.py 单元测试：LLMClient 协议、NullClient、get_client。"""
from __future__ import annotations

from emotion_core.llm.base import (
    LLMClient,
    LLMNotEnabledError,
    NullClient,
    get_client,
)


class TestLLMNotEnabledError:
    """LLM 未启用异常。"""

    def test_is_runtime_error(self):
        assert issubclass(LLMNotEnabledError, RuntimeError)

    def test_raise_message(self):
        try:
            raise LLMNotEnabledError("test message")
        except LLMNotEnabledError as e:
            assert "test message" in str(e)


class TestNullClient:
    """空实现客户端。"""

    def test_complete_raises(self):
        client = NullClient()
        try:
            client.complete("hello")
        except LLMNotEnabledError:
            pass
        else:
            raise AssertionError("NullClient.complete 应抛 LLMNotEnabledError")

    def test_complete_raises_with_kwargs(self):
        client = NullClient()
        try:
            client.complete("hello", system="sys", temperature=0.5)
        except LLMNotEnabledError:
            pass
        else:
            raise AssertionError("NullClient.complete 应抛 LLMNotEnabledError")

    def test_is_llmclient(self):
        """NullClient 实现 LLMClient 协议。"""
        client = NullClient()
        assert isinstance(client, LLMClient)


class TestGetClient:
    """get_client 工厂函数。"""

    def test_none_returns_null(self):
        client = get_client(None)
        assert isinstance(client, NullClient)

    def test_unknown_profile_returns_null(self):
        """未知 profile 返回 NullClient。"""
        client = get_client("nonexistent")
        assert isinstance(client, NullClient)


class TestLLMClientProtocol:
    """LLMClient 协议检查。"""

    def test_is_protocol(self):
        """LLMClient 是 Protocol 子类。"""
        import typing
        assert issubclass(LLMClient, typing.Protocol)

    def test_is_runtime_protocol(self):
        """LLMClient 标记了 runtime_checkable。"""
        assert LLMClient._is_runtime_protocol is True

    def test_has_complete_method(self):
        """协议要求 complete 方法。"""
        assert hasattr(LLMClient, "complete")
        assert callable(getattr(LLMClient, "complete"))

    def test_null_client_satisfies_protocol(self):
        client = NullClient()
        assert isinstance(client, LLMClient)


class TestLLMClientProtocolAttrs:
    """LLMClient 协议属性检查。"""

    def test_protocol_attrs_present(self):
        """Python 3.14 协议有 __protocol_attrs__ 属性。"""
        assert hasattr(LLMClient, "__protocol_attrs__")

    def test_is_protocol_flag(self):
        """_is_protocol 标记存在。"""
        assert hasattr(LLMClient, "_is_protocol")
        assert LLMClient._is_protocol is True
