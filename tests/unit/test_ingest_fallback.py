"""services/ingest.py 东财降级路径测试。

守的是 P0-B：东财 clist 对本机 IP 高频 502（实测失败率 80%），
10-01 起连续 3 天 daily pipeline 同步失败。本文件用 monkeypatch 把 requests.get
钉死成 502，验证：

1. `_em_clist` 在 EM FetchError 后回退新浪
2. `snapshot_daily` 的日期守卫拒绝陈旧数据（防把实时行情盖上历史）
3. `_fallback_rows` 在主源缺口时按配置链补齐，单源失败不阻断
4. `_retry` 在 FetchError 耗尽后上抛（不静默吞错）
5. 三池全败抛 RuntimeError（A7 护栏），不静默出假报告
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
import requests

from emotion_core.data.providers.base import (
    BAR_COLS, DataError, FetchError, ProviderError, valid_frame,
)
from emotion_core.services import ingest
from emotion_core.utils.fetch import fetch_json, retry_fetch


# ── helpers ───────────────────────────────────────────────────────────

class _FakeResp:
    """伪造 requests.Response。"""

    def __init__(self, *, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.ok = 200 <= status < 400

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def _make_502_get(url, *args, **kwargs):
    """模拟东财 502：raise requests.HTTPError（RequestException 子类）。"""
    raise requests.HTTPError(f"502 mock for {url}")


def _make_data_error_get(url, *args, **kwargs):
    """模拟协议变化：raise DataError（非 RequestException）。"""
    raise DataError("协议变化 mock")


def _stock_basic_df():
    return pd.DataFrame({"code": ["600519", "000001"]})


# ── 1. _em_clist: EM 502 → 回退新浪 ──────────────────────────────────

class TestEmClistFallback:
    """_em_clist 在 EM FetchError 后回退新浪全市场源。"""

    def test_em_502_falls_back_to_sina(self, monkeypatch):
        """EM clist 返回 FetchError 时，必须回退新浪而不是上抛。"""
        sina_calls = []

        def mock_get(url, *args, **kwargs):
            if "eastmoney" in url:
                raise requests.HTTPError(f"502 mock for {url}")
            if "sina" in url:
                sina_calls.append(url)
                return _FakeResp(json_data=[
                    {"symbol": "sh600519", "name": "贵州茅台",
                     "open": 1800, "high": 1850, "low": 1790, "trade": 1840,
                     "settlement": 1830, "volume": "3098122",
                     "amount": "570000000", "turnoverratio": "0.4"},
                    {"symbol": "sz000001", "name": "平安银行",
                     "open": 12, "high": 13, "low": 11.5, "trade": 12.5,
                     "settlement": 12.2, "volume": "1000000",
                     "amount": "12500000", "turnoverratio": "1.2"},
                ])
            return _FakeResp(status=404)

        monkeypatch.setattr(ingest.requests, "get", mock_get)
        monkeypatch.setattr(ingest, "_FETCH_RETRY", 1)
        monkeypatch.setattr(ingest, "_TIME_SLEEP", 0)

        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        df = ingest._em_clist(fs, list(ingest._SPOT_MAP))

        assert len(sina_calls) > 0, "必须回退到新浪"
        assert not df.empty, "回退后应有数据"

    def test_em_data_error_does_not_fall_back(self, monkeypatch):
        """DataError（协议变化）不应回退，直接上抛。"""
        monkeypatch.setattr(ingest.requests, "get", _make_data_error_get)
        monkeypatch.setattr(ingest, "_FETCH_RETRY", 1)
        monkeypatch.setattr(ingest, "_TIME_SLEEP", 0)

        with pytest.raises(DataError):
            ingest._em_clist("m:0+t:6", ["f12"])


# ── 2. snapshot_daily 日期守卫 ────────────────────────────────────────

class TestSnapshotDailyGuard:
    """snapshot_daily 的 EM 日期守卫拒绝陈旧数据。"""

    def test_rejects_stale_date(self, monkeypatch):
        """EM 最新数据日期 ≠ 传入日期 → 拒绝，不写库。"""
        monkeypatch.setattr(ingest, "query_df", _stock_basic_df)
        monkeypatch.setattr(
            "emotion_core.data.providers.eastmoney.em_data_date",
            lambda: date(2026, 9, 28),  # 不是 09-29
        )
        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", [])

        with pytest.raises(ValueError, match="拒绝把陈旧行情"):
            ingest.snapshot_daily(date(2026, 9, 29))

    def test_em_date_match_proceeds(self, monkeypatch):
        """EM 日期匹配 → 不抛日期守卫错误。"""
        monkeypatch.setattr(ingest, "query_df", _stock_basic_df)
        monkeypatch.setattr(
            "emotion_core.data.providers.eastmoney.em_data_date",
            lambda: date(2026, 9, 29),
        )
        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", [])
        monkeypatch.setattr(ingest, "_em_clist_paged",
                            lambda *a, **kw: pd.DataFrame())

        with pytest.raises(Exception) as exc_info:
            ingest.snapshot_daily(date(2026, 9, 29))
        assert "拒绝把陈旧行情" not in str(exc_info.value)


# ── 3. _fallback_rows: 多源补齐 ──────────────────────────────────────

class TestFallbackRows:
    """_fallback_rows 按配置链补齐主源缺口。"""

    def test_skips_failed_provider(self, monkeypatch):
        """单源失败（ProviderError）应跳过该源继续，不阻断整链。"""

        class _FailingProvider:
            name = "failing"

            def fetch_daily_bars(self, *a, **kw):
                raise ProviderError("mock 502")

        class _GoodProvider:
            name = "good"

            def fetch_daily_bars(self, code, start, end):
                return pd.DataFrame({
                    "code": [code], "date": [start],
                    "open": [100], "high": [110], "low": [90], "close": [105],
                    "pre_close": [100], "volume": [1000], "amount": [100000],
                    "turnover_rate": [1.0],
                })

        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", ["failing", "good"])
        monkeypatch.setattr(ingest, "_provider_registry", lambda: {
            "failing": _FailingProvider(),
            "good": _GoodProvider(),
        })

        rows = ingest._fallback_rows(["600519"], date(2026, 9, 29), date(2026, 9, 29), set())
        assert len(rows) == 1, "good 源应补上 failing 源的缺口"

    def test_skips_invalid_frame(self, monkeypatch):
        """口径不合格的帧（valid_frame=False）应跳过。"""

        class _BadFrameProvider:
            name = "bad"

            def fetch_daily_bars(self, *a, **kw):
                return pd.DataFrame()  # 空帧 → valid_frame 失败

        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", ["bad"])
        monkeypatch.setattr(ingest, "_provider_registry", lambda: {
            "bad": _BadFrameProvider(),
        })

        rows = ingest._fallback_rows(["600519"], date(2026, 9, 29), date(2026, 9, 29), set())
        assert rows == [], "空帧应被跳过"

    def test_empty_primary_codes_fetches_all(self, monkeypatch):
        """primary 为空（主源全失败）时，所有 code 都走备源。"""

        class _GoodProvider:
            name = "good"

            def fetch_daily_bars(self, code, start, end):
                return pd.DataFrame({
                    "code": [code], "date": [start],
                    "open": [100], "high": [110], "low": [90], "close": [105],
                    "pre_close": [100], "volume": [1000], "amount": [100000],
                    "turnover_rate": [1.0],
                })

        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", ["good"])
        monkeypatch.setattr(ingest, "_provider_registry", lambda: {
            "good": _GoodProvider(),
        })

        rows = ingest._fallback_rows(
            ["600519", "000001"], date(2026, 9, 29), date(2026, 9, 29), set(),
        )
        assert len(rows) == 2, "两个 code 都应由备源补齐"

    def test_already_covered_codes_skipped(self, monkeypatch):
        """主源已覆盖的 code 不应重复拉。"""

        calls = []

        class _GoodProvider:
            name = "good"

            def fetch_daily_bars(self, code, start, end):
                calls.append(code)
                return pd.DataFrame({
                    "code": [code], "date": [start],
                    "open": [100], "high": [110], "low": [90], "close": [105],
                    "pre_close": [100], "volume": [1000], "amount": [100000],
                    "turnover_rate": [1.0],
                })

        monkeypatch.setattr(ingest, "_INGEST_FALLBACK_CHAIN", ["good"])
        monkeypatch.setattr(ingest, "_provider_registry", lambda: {
            "good": _GoodProvider(),
        })

        rows = ingest._fallback_rows(
            ["600519", "000001"], date(2026, 9, 29), date(2026, 9, 29),
            primary={"600519"},
        )
        assert "600519" not in calls, "已覆盖的 code 不应重复拉"
        assert "000001" in calls
        assert len(rows) == 1


# ── 4. _retry 语义 ────────────────────────────────────────────────────

class TestRetry:
    """retry_fetch 的重试与异常传播语义。"""

    def test_fetch_error_retries_then_raises(self):
        """FetchError 应重试 fetch_retry 次后上抛。"""
        call_count = 0

        def failing_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise FetchError("HTTP 请求失败", Exception("mock 502"))

        with pytest.raises(FetchError):
            retry_fetch(failing_fn, fetch_retry=3, time_sleep=0)

        assert call_count == 3, f"应重试 3 次，实际 {call_count}"

    def test_data_error_no_retry(self):
        """DataError 应立即上抛，不重试。"""
        call_count = 0

        def data_err_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise DataError("协议变化 mock")

        with pytest.raises(DataError):
            retry_fetch(data_err_fn, fetch_retry=3, time_sleep=0)

        assert call_count == 1, "DataError 不应重试"

    def test_successful_call_returns(self):
        """正常返回时应立即返回，不重试。"""
        def good_fn(*a, **kw):
            return {"ok": True}

        result = retry_fetch(good_fn, fetch_retry=3, time_sleep=0)
        assert result == {"ok": True}


# ── 5. fetch_limit_pool A7 护栏 ───────────────────────────────────────

class TestFetchLimitPoolGuard:
    """fetch_limit_pool 三池全败抛 RuntimeError（A7 护栏）。"""

    def test_all_three_pools_fail_raises(self, monkeypatch):
        """三池全败应抛 RuntimeError，不静默返回 0。"""
        monkeypatch.setattr(ingest, "_POOL_RECENT_DAYS", 30)

        def raise_runtime(*a, **kw):
            raise RuntimeError("东财接口 mock 502")

        monkeypatch.setattr("emotion_core.services.ingest.retry_fetch", raise_runtime)

        with pytest.raises(RuntimeError):
            ingest.fetch_limit_pool(date(2026, 9, 29))

    def test_empty_pool_counts_as_fail(self, monkeypatch):
        """三池都返回 0 行（不抛异常）也应计为失败。"""
        monkeypatch.setattr(ingest, "_POOL_RECENT_DAYS", 30)

        def return_empty(*a, **kw):
            return pd.DataFrame()

        monkeypatch.setattr("emotion_core.services.ingest.retry_fetch", return_empty)

        with pytest.raises(RuntimeError):
            ingest.fetch_limit_pool(date(2026, 9, 29))


# ── 6. _get_json 异常分层 ─────────────────────────────────────────────

class TestGetJsonErrorSplit:
    """fetch_json 的 FetchError / DataError 分层语义。"""

    def test_http_error_raises_fetch_error(self, monkeypatch):
        """HTTP 5xx 应包装为 FetchError。"""
        monkeypatch.setattr("emotion_core.utils.fetch._do_request", _make_502_get)

        with pytest.raises(FetchError):
            fetch_json("https://example.com", {})

    def test_invalid_json_raises_data_error(self, monkeypatch):
        """非 JSON 响应应包装为 DataError。"""

        def bad_json_get(url, *args, **kwargs):
            return _FakeResp(text="not json")

        monkeypatch.setattr("emotion_core.utils.fetch._do_request", bad_json_get)

        with pytest.raises(DataError, match="非 JSON"):
            fetch_json("https://example.com", {})

    def test_non_dict_response_raises_data_error(self, monkeypatch):
        """dict 以外的 JSON（如 list）应包装为 DataError。"""

        def list_json_get(url, *args, **kwargs):
            return _FakeResp(json_data=[1, 2, 3])

        monkeypatch.setattr("emotion_core.utils.fetch._do_request", list_json_get)

        with pytest.raises(DataError, match="响应非 dict"):
            fetch_json("https://example.com", {})

    def test_successful_response(self, monkeypatch):
        """正常 dict 响应应返回。"""

        def good_get(url, *args, **kwargs):
            return _FakeResp(json_data={"data": {"total": 100}})

        monkeypatch.setattr("emotion_core.utils.fetch._do_request", good_get)

        result = fetch_json("https://example.com", {})
        assert result == {"data": {"total": 100}}
