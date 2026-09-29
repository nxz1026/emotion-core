"""全局异常定义（utils.errors）：FetchError / DataError / ProviderError。"""
from __future__ import annotations

from emotion_core.utils import errors


class TestFetchError:
    def test_stores_cause(self):
        cause = Exception("timeout")
        err = errors.FetchError("HTTP 失败", cause)
        assert err.cause is cause
        assert "HTTP 失败" in str(err)
        assert "timeout" in str(err)

    def test_is_exception(self):
        assert issubclass(errors.FetchError, Exception)


class TestDataError:
    def test_is_exception(self):
        assert issubclass(errors.DataError, Exception)

    def test_with_message(self):
        err = errors.DataError("非 JSON 响应")
        assert "非 JSON 响应" in str(err)


class TestProviderError:
    def test_is_exception(self):
        assert issubclass(errors.ProviderError, Exception)

    def test_with_message(self):
        err = errors.ProviderError("Provider 不可用")
        assert "Provider 不可用" in str(err)
