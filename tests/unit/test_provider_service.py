"""数据源提供商服务 facade（services.provider_service）：get_provider / fetch_daily_bars，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.services import provider_service


class TestGetProvider:
    def test_eastmoney(self, monkeypatch):
        fake = object()
        monkeypatch.setitem(provider_service.__dict__, "_PROVIDERS", {})
        import sys
        fake_mod = type(sys)("fake_eastmoney")
        fake_mod.PROVIDER = fake
        monkeypatch.setitem(sys.modules, "emotion_core.data.providers.eastmoney", fake_mod)
        assert provider_service.get_provider("eastmoney") is fake

    def test_pytdx(self, monkeypatch):
        import sys
        fake = object()
        fake_mod = type(sys)("fake_pytdx")
        fake_mod.PROVIDER = fake
        monkeypatch.setitem(sys.modules, "emotion_core.data.providers.pytdx_provider", fake_mod)
        assert provider_service.get_provider("pytdx") is fake

    def test_sina(self, monkeypatch):
        import sys
        fake = object()
        fake_mod = type(sys)("fake_sina")
        fake_mod.PROVIDER = fake
        monkeypatch.setitem(sys.modules, "emotion_core.data.providers.sina", fake_mod)
        assert provider_service.get_provider("sina") is fake

    def test_unknown_raises(self):
        try:
            provider_service.get_provider("unknown")
        except ValueError as e:
            assert "未知 provider" in str(e)
        else:
            raise AssertionError("应抛 ValueError")


class TestFetchDailyBars:
    def test_delegates_to_provider(self, monkeypatch):
        captured: dict = {}

        class FakeProvider:
            def fetch_daily_bars(self, code, start, end):
                captured["code"] = code
                captured["start"] = start
                captured["end"] = end
                return pd.DataFrame({"close": [10.0]})

        monkeypatch.setattr(provider_service, "get_provider",
                            lambda name: FakeProvider())
        df = provider_service.fetch_daily_bars(
            "eastmoney", "000001", date(2026, 1, 1), date(2026, 9, 25))
        assert captured["code"] == "000001"
        assert captured["start"] == date(2026, 1, 1)
        assert captured["end"] == date(2026, 9, 25)
        assert not df.empty


class TestGetProviderError:
    def test_returns_error_class(self, monkeypatch):
        class FakeError(Exception):
            pass

        import sys
        fake_mod = type(sys)("fake_base")
        fake_mod.ProviderError = FakeError
        monkeypatch.setitem(sys.modules, "emotion_core.data.providers.base", fake_mod)
        assert provider_service.get_provider_error() is FakeError
