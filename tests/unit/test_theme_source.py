"""data/theme_source.py 题材 Provider 测试。"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data.theme_source import (
    ThemeProvider,
    NullProvider,
    WindProvider,
    get_provider,
)


class TestNullProvider:
    def test_returns_empty_list(self):
        provider = NullProvider()
        result = provider.fetch_tags(date(2024, 6, 15))
        assert result == []

    def test_any_date(self):
        provider = NullProvider()
        assert provider.fetch_tags(date(2020, 1, 1)) == []
        assert provider.fetch_tags(date(2025, 12, 31)) == []


class TestWindProvider:
    def test_raises_not_implemented(self):
        provider = WindProvider()
        with pytest.raises(NotImplementedError, match="暂不实现"):
            provider.fetch_tags(date(2024, 6, 15))


class TestGetProvider:
    def test_default_null(self):
        provider = get_provider()
        assert isinstance(provider, NullProvider)

    def test_none_null(self):
        provider = get_provider(None)
        assert isinstance(provider, NullProvider)

    def test_wind(self):
        provider = get_provider("wind")
        assert isinstance(provider, WindProvider)

    def test_unknown_null(self):
        provider = get_provider("unknown")
        assert isinstance(provider, NullProvider)


class TestProtocol:
    def test_protocol_exists(self):
        assert hasattr(ThemeProvider, "fetch_tags")
