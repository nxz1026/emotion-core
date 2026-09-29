"""网络请求工具函数（utils.fetch）：fetch_json / fetch_json_list / is_positive / retry_fetch。"""
from __future__ import annotations

from emotion_core.utils import errors, fetch


class TestIsPositive:
    def test_positive_number(self):
        assert fetch.is_positive(10.5) is True

    def test_zero(self):
        assert fetch.is_positive(0) is False

    def test_negative(self):
        assert fetch.is_positive(-5) is False

    def test_string_number(self):
        assert fetch.is_positive("1685.000") is True

    def test_none(self):
        assert fetch.is_positive(None) is False

    def test_non_numeric_string(self):
        assert fetch.is_positive("abc") is False


class TestFetchJson:
    def test_returns_dict(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"key": "value"}

        monkeypatch.setattr(fetch, "_do_request",
                            lambda url, **kw: FakeResp())
        result = fetch.fetch_json("http://example.com", {"a": 1})
        assert result == {"key": "value"}

    def test_http_error_raises_fetch_error(self, monkeypatch):
        import requests

        def bad_request(url, **kw):
            raise requests.RequestException("timeout")

        monkeypatch.setattr(fetch, "_do_request", bad_request)
        try:
            fetch.fetch_json("http://example.com", {})
        except errors.FetchError as e:
            assert "timeout" in str(e)
        else:
            raise AssertionError("应抛 FetchError")

    def test_non_json_raises_data_error(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                raise ValueError("not json")

        monkeypatch.setattr(fetch, "_do_request",
                            lambda url, **kw: FakeResp())
        try:
            fetch.fetch_json("http://example.com", {})
        except errors.DataError as e:
            assert "非 JSON" in str(e)
        else:
            raise AssertionError("应抛 DataError")

    def test_non_dict_raises_data_error(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return [1, 2, 3]

        monkeypatch.setattr(fetch, "_do_request",
                            lambda url, **kw: FakeResp())
        try:
            fetch.fetch_json("http://example.com", {})
        except errors.DataError as e:
            assert "非 dict" in str(e)
        else:
            raise AssertionError("应抛 DataError")


class TestRetryFetch:
    def test_first_success_no_retry(self, monkeypatch):
        monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert fetch.retry_fetch(fn, fetch_retry=3) == "ok"
        assert len(calls) == 1

    def test_retries_on_failure(self, monkeypatch):
        monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)
        state = {"n": 0}

        def fn():
            state["n"] += 1
            if state["n"] < 3:
                raise Exception("fail")
            return "ok"

        assert fetch.retry_fetch(fn, fetch_retry=5) == "ok"
        assert state["n"] == 3

    def test_data_error_immediate_raise(self, monkeypatch):
        monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)

        def fn():
            raise errors.DataError("协议变化")

        try:
            fetch.retry_fetch(fn, fetch_retry=3)
        except errors.DataError:
            pass
        else:
            raise AssertionError("DataError 应立即上抛")

    def test_exhausts_retries(self, monkeypatch):
        monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)

        def fn():
            raise Exception("always fail")

        try:
            fetch.retry_fetch(fn, fetch_retry=3)
        except Exception as e:
            assert "always fail" in str(e)
        else:
            raise AssertionError("应抛末次异常")

    def test_hard_limit_error_immediate_raise(self, monkeypatch):
        monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)

        def fn():
            raise Exception("只能获取最近 100 条")

        try:
            fetch.retry_fetch(fn, fetch_retry=3)
        except Exception as e:
            assert "只能获取最近" in str(e)
        else:
            raise AssertionError("硬限错误应立即上抛")
