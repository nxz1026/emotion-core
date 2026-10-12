"""巨潮行业分类源：单只解析、缺列/空响应分层、批量跳过失败。全程不联网。"""
from __future__ import annotations

import pandas as pd

from emotion_core.data.providers import cninfo_industry as ci


def _profile(industry: object) -> pd.DataFrame:
    return pd.DataFrame({"A股代码": ["600519"], "所属行业": [industry]})


def test_fetch_industry_returns_stripped_value(monkeypatch):
    monkeypatch.setattr(ci.ak, "stock_profile_cninfo",
                        lambda symbol: _profile("  酒、饮料和精制茶制造业 "))
    assert ci.fetch_industry("600519") == "酒、饮料和精制茶制造业"


def test_fetch_industry_blank_and_nan_return_none(monkeypatch):
    monkeypatch.setattr(ci.ak, "stock_profile_cninfo", lambda symbol: _profile("   "))
    assert ci.fetch_industry("600519") is None

    monkeypatch.setattr(ci.ak, "stock_profile_cninfo", lambda symbol: _profile(float("nan")))
    assert ci.fetch_industry("600519") is None


def test_fetch_industry_missing_column_is_data_error(monkeypatch):
    monkeypatch.setattr(ci.ak, "stock_profile_cninfo",
                        lambda symbol: pd.DataFrame({"A股代码": ["600519"]}))
    try:
        ci.fetch_industry("600519")
    except ci.DataError as exc:
        assert "缺列" in str(exc)
    else:
        raise AssertionError("协议变化必须抛 DataError，不能静默返回 None")


def test_fetch_industry_empty_frame_is_fetch_error(monkeypatch):
    monkeypatch.setattr(ci, "_FETCH_RETRY", 1)      # 免掉指数退避的 4 秒
    monkeypatch.setattr(ci.ak, "stock_profile_cninfo", lambda symbol: pd.DataFrame())
    try:
        ci.fetch_industry("600519")
    except ci.FetchError as exc:
        assert "空响应" in str(exc)
    else:
        raise AssertionError("空响应是可重试网络故障，应抛 FetchError")


def test_fetch_industry_map_skips_failures_without_raising(monkeypatch):
    monkeypatch.setattr(ci, "_FETCH_RETRY", 1)

    def fake(symbol: str) -> str | None:
        if symbol == "000002":
            raise RuntimeError("boom")
        return f"行业-{symbol}"

    monkeypatch.setattr(ci, "fetch_industry", fake)

    out = ci.fetch_industry_map(["600519", "000002", "300750"], workers=2, progress_every=0)
    assert out == {"600519": "行业-600519", "300750": "行业-300750"}
    assert "000002" not in out, "单只失败只跳过，不中断整轮"


def test_fetch_industry_map_all_fail_returns_empty(monkeypatch):
    def boom(symbol: str) -> str | None:
        raise RuntimeError("network down")

    monkeypatch.setattr(ci, "fetch_industry", boom)
    assert ci.fetch_industry_map(["600519"], workers=1, progress_every=0) == {}
