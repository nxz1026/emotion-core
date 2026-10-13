"""交易所官方 A 股列表源（list_date）的解析与容错。

守的事：
- 深交所给字符串 ``1991-04-03``、上交所给 ``datetime.date``，两种形态都要归一；
- ``pd.NaT`` 也是 ``datetime`` 的实例，不能被误当成有效日期（先判 isna 再判类型）；
- 单个市场抖挂（深交所偶发 ConnectionResetError 104）只跳过它，不拖垮沪市。
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from emotion_core.data.providers import exchange_list as el
from emotion_core.utils.errors import FetchError


class TestToDate:
    @pytest.mark.parametrize(("raw", "want"), [
        ("1991-04-03", date(1991, 4, 3)),            # 深交所口径
        (date(1999, 11, 10), date(1999, 11, 10)),    # 上交所口径
        (datetime(2019, 7, 22, 0, 0), date(2019, 7, 22)),
        ("1991/04/03", date(1991, 4, 3)),
        ("19910403", date(1991, 4, 3)),
        ("  2019-07-22  ", date(2019, 7, 22)),
    ])
    def test_parses(self, raw, want):
        assert el.to_date(raw) == want

    @pytest.mark.parametrize("raw", [None, "", "   ", "不是日期", float("nan"), pd.NaT])
    def test_returns_none(self, raw):
        assert el.to_date(raw) is None

    def test_nat_is_not_mistaken_for_a_date(self):
        """pd.NaT 是 datetime 实例：先判 isna 才不会把它当成立日期。"""
        assert isinstance(pd.NaT, datetime)
        assert el.to_date(pd.NaT) is None


def _sz_frame() -> pd.DataFrame:
    return pd.DataFrame({"A股代码": ["000001", "300750"],
                         "A股上市日期": ["1991-04-03", "2018-06-11"]})


def _sh_frame() -> pd.DataFrame:
    return pd.DataFrame({"证券代码": ["600000"], "上市日期": [date(1999, 11, 10)]})


def _kc_frame() -> pd.DataFrame:
    return pd.DataFrame({"证券代码": ["688001"], "上市日期": [date(2019, 7, 22)]})


def test_fetch_list_date_map_merges_three_markets(monkeypatch):
    monkeypatch.setattr(el.ak, "stock_info_sz_name_code", lambda **kw: _sz_frame())
    monkeypatch.setattr(el.ak, "stock_info_sh_name_code",
                        lambda **kw: _kc_frame() if kw["symbol"] == "科创板" else _sh_frame())
    assert el.fetch_list_date_map() == {
        "000001": date(1991, 4, 3), "300750": date(2018, 6, 11),
        "600000": date(1999, 11, 10), "688001": date(2019, 7, 22),
    }


def test_fetch_list_date_map_skips_failed_market(monkeypatch):
    """深市抖挂不能拖垮沪市：只 warning + 跳过。"""
    monkeypatch.setattr(el, "_FETCH_RETRY", 1)
    monkeypatch.setattr(el, "_TIME_SLEEP", 0)

    def boom(**_kw):
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(el.ak, "stock_info_sz_name_code", boom)
    monkeypatch.setattr(el.ak, "stock_info_sh_name_code",
                        lambda **kw: _kc_frame() if kw["symbol"] == "科创板" else _sh_frame())
    assert el.fetch_list_date_map() == {"600000": date(1999, 11, 10),
                                       "688001": date(2019, 7, 22)}


def test_fetch_one_raises_on_empty_frame(monkeypatch):
    """空表是异常（接口改字段/被拦），不能当成「这批没有新股」静默成功。"""
    monkeypatch.setattr(el, "_FETCH_RETRY", 1)
    monkeypatch.setattr(el, "_TIME_SLEEP", 0)
    monkeypatch.setattr(el.ak, "stock_info_sz_name_code", lambda **kw: pd.DataFrame())
    with pytest.raises(FetchError, match="返回空"):
        el._fetch_one("深交所A股列表", el.ak.stock_info_sz_name_code, {"symbol": "A股列表"})


def test_fetch_one_wraps_network_error_as_fetch_error(monkeypatch):
    monkeypatch.setattr(el, "_FETCH_RETRY", 1)
    monkeypatch.setattr(el, "_TIME_SLEEP", 0)

    def boom(**_kw):
        raise ConnectionError("down")

    monkeypatch.setattr(el.ak, "stock_info_sz_name_code", boom)
    with pytest.raises(FetchError, match="请求失败"):
        el._fetch_one("深交所A股列表", el.ak.stock_info_sz_name_code, {"symbol": "A股列表"})


def test_all_markets_failing_yields_empty_map(monkeypatch):
    monkeypatch.setattr(el, "_FETCH_RETRY", 1)
    monkeypatch.setattr(el, "_TIME_SLEEP", 0)

    def boom(**_kw):
        raise ConnectionError("down")

    monkeypatch.setattr(el.ak, "stock_info_sz_name_code", boom)
    monkeypatch.setattr(el.ak, "stock_info_sh_name_code", boom)
    assert el.fetch_list_date_map() == {}


def test_one_market_column_drift_does_not_lose_other_markets(monkeypatch):
    """2026-10-13：解析循环原先在 try 之外，任一市场改列名会让**已拿到的其它市场一起丢**。

    实测（monkeypatch 出一个列名漂移市场 + 一个正常市场）：修正前直接
    `KeyError: '上市日期'` 穿透，正常市场的 000001 也没进 map；修正后只丢漂移市场。
    """
    def fake_sources():
        return (
            ("漂移市场", lambda **_kw: None, {}, "证券代码", "上市日期"),
            ("正常市场", lambda **_kw: None, {}, "A股代码", "A股上市日期"),
        )

    def fake_fetch_one(label, fn, kw):
        if label == "漂移市场":
            return pd.DataFrame({"证券代码": ["600000"], "换了名字的日期列": ["1999-11-10"]})
        return pd.DataFrame({"A股代码": ["000001"], "A股上市日期": ["1991-04-03"]})

    monkeypatch.setattr(el, "_sources", fake_sources)
    monkeypatch.setattr(el, "_fetch_one", fake_fetch_one)
    assert el.fetch_list_date_map() == {"000001": date(1991, 4, 3)}


def test_non_six_digit_codes_are_ignored(monkeypatch):
    """akshare 的空串/短码不该进 map（源侧偶发空 code 列）。"""
    def fake_sources():
        return (("正常市场", lambda **_kw: None, {}, "A股代码", "A股上市日期"),)

    def fake_fetch_one(label, fn, kw):
        return pd.DataFrame({"A股代码": ["000001", "", "  600000  ", "60000"],
                             "A股上市日期": ["1991-04-03"] * 4})

    monkeypatch.setattr(el, "_sources", fake_sources)
    monkeypatch.setattr(el, "_fetch_one", fake_fetch_one)
    assert el.fetch_list_date_map() == {"000001": date(1991, 4, 3),
                                        "600000": date(1991, 4, 3)}
