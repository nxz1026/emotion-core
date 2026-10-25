"""`sina_quote` provider 单测（不联网）。

真实联网路径靠 `tests/oracle/test_intraday_quote_vs_cpt.py` 与人工实跑；
这里把解析口径钉死，**不依赖网络**。
"""

from __future__ import annotations

import pytest

from emotion_core.data.providers import sina_quote as sq
from emotion_core.utils.errors import ProviderError


def _fields(
    *,
    name: str = "测试股",
    last: str = "10.89",
    bid_vol: str = "19635490",
    bid_price: str = "10.89",
) -> list[str]:
    f = [name, "10.00", "9.90", last, "10.90", "9.95", "10.10", "10.20", "1234567", "13000000.00"]
    for _ in range(5):
        f.extend([bid_vol, bid_price])
    for _ in range(5):
        f.extend(["225700", "10.90"])
    f.extend(["2026-10-21", "15:00:00", "00", "00"])
    assert len(f) == 34
    return f


def _resp(symbol: str, fields: list[str]) -> str:
    return f'var hq_str_{symbol}="{",".join(fields)}";\n'


class TestNormalizeCode:
    @pytest.mark.parametrize(
        ("code", "want"),
        [
            ("600519", "sh600519"),
            ("000002", "sz000002"),
            ("300750", "sz300750"),
            ("688981", "sh688981"),
            ("920296", "bj920296"),
            ("830799", "bj830799"),
            ("sh600519", "sh600519"),
            ("600519.SH", "sh600519"),
        ],
    )
    def test_ok(self, code, want):
        assert sq.normalize_code(code) == want

    def test_bare_code_and_prefix_agree(self):
        assert sq.normalize_code("600519") == sq.normalize_code("sh600519")

    @pytest.mark.parametrize("code", ["abcdef", "60051", "600519.XX", "1234567"])
    def test_bad(self, code):
        with pytest.raises(ProviderError):
            sq.normalize_code(code)


class TestParse:
    def test_fields(self):
        out = sq.parse_sina_quotes(_resp("sz000002", _fields()))
        assert set(out) == {"000002"}
        q = out["000002"]
        assert q["name"] == "测试股"
        assert q["last"] == 10.89
        assert q["prev_close"] == 9.9
        assert q["open"] == 10.0
        assert q["high"] == 10.9
        assert q["low"] == 9.95
        assert q["volume"] == 1234567.0
        assert q["amount"] == 13000000.0
        assert q["date"] == "2026-10-21"
        assert q["time"] == "15:00:00"
        assert q["bid1_price"] == 10.89
        assert q["bid1_vol"] == 19635490.0
        assert q["ask1_price"] == 10.9
        assert q["bids"][0] == {"price": 10.89, "volume": 19635490.0}
        assert len(q["bids"]) == 5 and len(q["asks"]) == 5

    def test_suspended_price_none_volume_zero(self):
        """停牌：价量全 0 ⇒ 价 `None`、量 `0.0`、整档 `None`。"""
        f = _fields(last="0", bid_vol="0", bid_price="0")
        f[1] = f[4] = f[5] = "0"
        f[8] = f[9] = "0"
        # 卖五档也清零（真实停牌响应整行 0）
        for i in range(10, 30):
            f[i] = "0"
        q = sq.parse_sina_quotes(_resp("sz000016", f))["000016"]
        assert q["last"] == 0.0
        assert q["bid1_price"] is None
        assert q["bid1_vol"] == 0.0
        assert q["bids"][0] is None
        assert q["asks"][0] is None

    def test_empty_body_skipped(self):
        text = 'var hq_str_sz000000="";\n' + _resp("sz000002", _fields())
        assert set(sq.parse_sina_quotes(text)) == {"000002"}

    def test_too_few_fields_raises(self):
        with pytest.raises(ProviderError):
            sq.parse_sina_quotes('var hq_str_sz000002="1,2,3";\n')

    def test_expected_not_matched_raises(self):
        with pytest.raises(ProviderError):
            sq.parse_sina_quotes(_resp("sz000002", _fields()), expected=["sz999999"])

    def test_multiple_rows(self):
        text = _resp("sz000002", _fields()) + _resp("sh600519", _fields(name="茅台"))
        out = sq.parse_sina_quotes(text)
        assert set(out) == {"000002", "600519"}
        assert out["600519"]["name"] == "茅台"


class TestFetchQuotes:
    def test_dedup_and_chunk(self, monkeypatch):
        seen: list[list[str]] = []

        def fake(self, symbols):
            seen.append(list(symbols))
            return {s[2:]: {"code": s[2:]} for s in symbols}

        monkeypatch.setattr(sq.SinaQuoteProvider, "_fetch_chunk", fake)
        codes = [f"{600000 + i:06d}" for i in range(120)]
        codes.append("600000")  # 重复
        out = sq.SinaQuoteProvider().fetch_quotes(codes)
        assert len(out) == 120
        assert [len(c) for c in seen] == [50, 50, 20]

    def test_empty_codes(self):
        assert sq.SinaQuoteProvider().fetch_quotes([]) == {}

    def test_http_error_raises(self, monkeypatch):

        def boom(self, symbols):
            raise ProviderError("boom")

        monkeypatch.setattr(sq.SinaQuoteProvider, "_fetch_chunk", boom)
        with pytest.raises(ProviderError):
            sq.SinaQuoteProvider().fetch_quotes(["600519"])

    def test_request_has_referer(self, monkeypatch):
        """新浪没 Referer 一律 403 —— 必须用 Request 带 headers。"""
        captured: dict = {}

        class _Resp:
            def read(self):
                return _resp("sh600519", _fields()).encode("gbk")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.headers)
            return _Resp()

        monkeypatch.setattr(sq.urllib.request, "urlopen", fake_urlopen)
        out = sq.SinaQuoteProvider().fetch_quotes(["600519"])
        assert set(out) == {"600519"}
        assert "finance.sina.com.cn" in captured["headers"].get("Referer", "")
