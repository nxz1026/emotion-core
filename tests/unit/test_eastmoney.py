"""东财数据源 Provider：行情守卫逻辑、收盘判定、回退路径。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.data.providers import eastmoney
from emotion_core.data.providers.base import DataError


class TestRejectZeroPrices:
    def test_empty_rows(self):
        eastmoney._reject_zero_prices([])

    def test_all_priced(self):
        rows = [{"f2": 10.0}, {"f2": 20.0}, {"f2": 30.0}]
        eastmoney._reject_zero_prices(rows)

    def test_all_zero_raises(self):
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 0}]
        with pytest.raises(DataError, match="价格异常"):
            eastmoney._reject_zero_prices(rows)

    def test_mostly_zero_raises(self):
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 10.0}]
        with pytest.raises(DataError, match="价格异常"):
            eastmoney._reject_zero_prices(rows)

    def test_exactly_half_not_raises(self):
        # 3/5 有价 → 3*2=6 >= 5，不触发
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 10.0}, {"f2": 20.0}, {"f2": 30.0}]
        eastmoney._reject_zero_prices(rows)


class TestSinaSessionClosed:
    def test_none_none_returns_none(self):
        assert eastmoney._sina_session_closed(None, None) is None

    def test_zero_volume_returns_false(self):
        assert eastmoney._sina_session_closed(0, None) is False

    def test_before_close_returns_false(self):
        # 14:59 < 15:00
        assert eastmoney._sina_session_closed(1000, 14 * 60 + 59) is False

    def test_at_close_returns_true(self):
        # 15:00 >= 15:00
        assert eastmoney._sina_session_closed(1000, 15 * 60) is True

    def test_after_close_returns_true(self):
        assert eastmoney._sina_session_closed(1000, 16 * 60) is True

    def test_zero_volume_with_time_returns_false(self):
        # 成交量 0 优先于时间判断
        assert eastmoney._sina_session_closed(0, 15 * 60) is False


class TestSinaQuotePayload:
    def test_normal_payload(self):
        text = 'var hq_str_sh000001="指数,100,101,102,..."'
        result = eastmoney._sina_quote_payload(text)
        assert result == ["指数", "100", "101", "102", "..."]

    def test_empty_payload(self):
        text = 'var hq_str_sh000001=""'
        result = eastmoney._sina_quote_payload(text)
        assert result == [""]

    def test_no_match(self):
        assert eastmoney._sina_quote_payload("no match here") is None


class TestSinaIndexVolume:
    def test_normal(self):
        # 模拟新浪指数回报，成交量在下标 8（第 9 个字段）
        parts = ["指数"] * 8 + ["12345", "extra"]
        text = '="' + ",".join(parts) + '"'
        result = eastmoney._sina_index_volume(text)
        assert result == 12345.0

    def test_too_short(self):
        text = '="a,b,c"'
        assert eastmoney._sina_index_volume(text) is None

    def test_zero_volume(self):
        parts = ["指数"] * 8 + ["0"]
        text = '="' + ",".join(parts) + '"'
        assert eastmoney._sina_index_volume(text) == 0.0


class TestSinaQuoteMinutes:
    def test_normal(self):
        # 时间在下标 31
        parts = ["field"] * 31 + ["14:30:25"]
        text = '="' + ",".join(parts) + '"'
        result = eastmoney._sina_quote_minutes(text)
        assert result == 14 * 60 + 30

    def test_too_short(self):
        text = '="a,b,c"'
        assert eastmoney._sina_quote_minutes(text) is None


class TestDowngradeOpenSession:
    def test_session_closed_downgrades(self, monkeypatch):
        # 模拟新浪返回：成交量 0 → 未收完
        monkeypatch.setattr(eastmoney, "_sina_index_volume", lambda t: 0)
        monkeypatch.setattr(eastmoney, "_sina_quote_minutes", lambda t: None)
        monkeypatch.setattr(eastmoney, "_sina_last_bar_date", lambda: date(2026, 9, 24))

        class MockResp:
            content = b'=""'

            def raise_for_status(self):
                pass

        monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **kw: MockResp())
        result = eastmoney._downgrade_open_session(date(2026, 9, 25))
        assert result == date(2026, 9, 24)

    def test_session_open_keeps_date(self, monkeypatch):
        # 模拟新浪返回：成交量 > 0，时间 >= 15:00 → 已收完
        monkeypatch.setattr(eastmoney, "_sina_index_volume", lambda t: 99999)
        monkeypatch.setattr(eastmoney, "_sina_quote_minutes", lambda t: 15 * 60)

        class MockResp:
            content = b'=""'

            def raise_for_status(self):
                pass

        monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **kw: MockResp())
        result = eastmoney._downgrade_open_session(date(2026, 9, 25))
        assert result == date(2026, 9, 25)


class TestEastmoneyProvider:
    def test_name(self):
        assert eastmoney.EastmoneyProvider.name == "eastmoney"

    def test_fetch_with_non_matching_date(self, monkeypatch):
        provider = eastmoney.EastmoneyProvider()
        monkeypatch.setattr(eastmoney, "em_data_date", lambda: date(2026, 9, 24))
        result = provider.fetch_daily_bars("000001", date(2026, 9, 25), date(2026, 9, 25))
        assert len(result) == 0


class TestSinaClistUnits:
    """新浪回退源的**量纲**换算（`_SINA_UNIT_SCALE`）。

    这类错误不抛异常：数字都在，只是差 100 / 10000 倍。故每条口径都钉一个由
    公开数据独立可验的等式（成交量三方对齐、市值 ÷ 价格反推股本）。
    """

    FIELDS = ["f12", "f2", "f5", "f6", "f20", "f21"]

    def _one(self, monkeypatch, row: dict) -> dict:
        monkeypatch.setattr(eastmoney, "retry_fetch", lambda *a, **k: [row])
        return eastmoney._sina_clist(self.FIELDS).iloc[0].to_dict()

    def test_volume_shares_to_lots_and_cap_wan_to_yuan(self, monkeypatch):
        r = self._one(
            monkeypatch,
            {
                "symbol": "sh600519",
                "code": "600519",
                "name": "贵州茅台",
                "trade": 1288.0,
                "volume": "3098122",
                "amount": "570000000",
                "mktcap": "161010510.2088",
                "nmc": "161010510.2088",
            },
        )
        assert r["f5"] == pytest.approx(30981.22)  # 股 → 手（3098122 = 30981 手）
        assert r["f6"] == "570000000"  # 元，不换算（原值透传）
        assert r["f20"] == pytest.approx(1.610105102088e12)  # 万元 → 元
        assert r["f21"] == pytest.approx(1.610105102088e12)

    def test_cap_divides_back_to_public_share_count(self, monkeypatch):
        """独立校验量纲：f20 ÷ f2 必须落在公开总股本量级（平安银行 194.06 亿股）。"""
        r = self._one(
            monkeypatch,
            {
                "symbol": "sz000001",
                "code": "000001",
                "name": "平安银行",
                "trade": 11.73,
                "volume": "1",
                "amount": "1",
                "mktcap": "22763142.046254",
                "nmc": "1821378.97",
            },
        )
        assert r["f20"] / r["f2"] == pytest.approx(1.9406e10, rel=1e-3)
        assert r["f21"] / r["f2"] == pytest.approx(1.5529e9, rel=1e-3)  # 流通 ≈15.5 亿股

    def test_missing_or_unparseable_values_become_none(self, monkeypatch):
        r = self._one(
            monkeypatch,
            {
                "symbol": "sh600519",
                "code": "600519",
                "name": "贵州茅台",
                "trade": 1288.0,
                "volume": None,
                "amount": None,
                "mktcap": None,
                "nmc": "不是数",
            },
        )
        assert pd.isna(r["f5"]) and pd.isna(r["f21"])
        assert pd.isna(r["f6"]) and pd.isna(r["f20"])

    def test_bj_rows_filtered_out(self, monkeypatch):
        monkeypatch.setattr(
            eastmoney,
            "retry_fetch",
            lambda *a, **k: [
                {"symbol": "bj430047", "code": "430047", "trade": 10.0},
                {"symbol": "sh600519", "code": "600519", "trade": 1288.0},
            ],
        )
        assert eastmoney._sina_clist(self.FIELDS)["f12"].tolist() == ["600519"]

    def test_unrequested_fields_stay_out_of_frame(self, monkeypatch):
        """want 由调用方 fields 过滤 → 不加 f20/f21 的调用方（如 spot 快照）不受影响。"""
        monkeypatch.setattr(
            eastmoney,
            "retry_fetch",
            lambda *a, **k: [
                {
                    "symbol": "sh600519",
                    "code": "600519",
                    "trade": 1288.0,
                    "volume": "100",
                    "mktcap": "1",
                }
            ],
        )
        df = eastmoney._sina_clist(["f12", "f2"])
        assert set(df.columns) == {"f12", "f2"}
        assert df.iloc[0]["f12"] == "600519"


class TestEmClistEmptyFrameFallback:
    """2026-10-13：EM 返回 HTTP 200 但 `data=null` / `diff=[]` 时必须回退新浪。

    这类失败不抛 FetchError：`_em_clist_paged` 只在网络层异常时上抛，空 payload 会
    静默返回空帧。而 `services/ingest.py` 的**默认主源路径**就是 `_em_clist`
    （`_INGEST_FALLBACK_CHAIN` 缺省为空）→ 空帧会让当日日线静默入库 0 行、
    只留一句「入库 0 行」。
    """

    def _patch(self, monkeypatch, payload: dict, sina_calls: list[str]):
        monkeypatch.setattr(eastmoney, "fetch_json", lambda *a, **k: payload)
        monkeypatch.setattr(eastmoney, "_FETCH_RETRY", 1)
        monkeypatch.setattr(eastmoney, "_TIME_SLEEP", 0)

        def fake_sina(fields, page=100, max_pages=80):
            sina_calls.append("sina")
            return pd.DataFrame([{"f12": "600519"}])

        monkeypatch.setattr(eastmoney, "_sina_clist", fake_sina)

    def test_null_data_falls_back_to_sina(self, monkeypatch):
        calls: list[str] = []
        self._patch(monkeypatch, {"rc": 0, "data": None}, calls)
        assert eastmoney._em_clist("m:1+t:2", ["f12"])["f12"].tolist() == ["600519"]
        assert calls == ["sina"]

    def test_empty_diff_falls_back_to_sina(self, monkeypatch):
        calls: list[str] = []
        self._patch(monkeypatch, {"rc": 0, "data": {"total": 0, "diff": []}}, calls)
        assert eastmoney._em_clist("m:1+t:2", ["f12"])["f12"].tolist() == ["600519"]
        assert calls == ["sina"]
