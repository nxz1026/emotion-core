"""tests for `services/cpt_client.py`（R3 §4.5：绝不抛、只回环、不传 ensure_factors）。

不发真实请求：`urlopen` 全部打桩。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from emotion_core.services import cpt_client as cc


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _patch_urlopen(monkeypatch, payload: bytes):
    """打桩 urlopen，返回 (seen, ) 供断言；seen["url"] / seen["timeout"]。"""
    seen: dict = {}

    def fake(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResp(payload)

    monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
    return seen


class TestSwitches:
    @pytest.mark.parametrize("val", ["", "1", "yes", "true", "on", "TRUE"])
    def test_cpt_enabled_on(self, monkeypatch, val):
        monkeypatch.setenv("CPT_ENABLED", val)
        assert cc.cpt_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", " FALSE "])
    def test_cpt_enabled_off(self, monkeypatch, val):
        monkeypatch.setenv("CPT_ENABLED", val)
        assert cc.cpt_enabled() is False

    def test_cpt_enabled_unset(self, monkeypatch):
        monkeypatch.delenv("CPT_ENABLED", raising=False)
        assert cc.cpt_enabled() is True

    @pytest.mark.parametrize("val", ["0", "no", "off", "false"])
    def test_recommendation_enabled_off(self, monkeypatch, val):
        monkeypatch.setenv("CPT_RECOMMENDATION_ENABLED", val)
        assert cc.recommendation_enabled() is False

    def test_recommendation_enabled_default_on(self, monkeypatch):
        monkeypatch.delenv("CPT_RECOMMENDATION_ENABLED", raising=False)
        assert cc.recommendation_enabled() is True


class TestNormCode:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [("601811", "601811"), ("1811", "001811"), ("1", "000001"),
         (601811, "601811"), (" 601811 ", "601811")],
    )
    def test_ok(self, raw, want):
        assert cc._norm_code(raw) == want

    @pytest.mark.parametrize("bad", ["abc", "", "  ", None, True, "6018111", "60.5", []])
    def test_bad(self, bad):
        assert cc._norm_code(bad) is None


class TestGetJson:
    def test_ok_dict(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, json.dumps({"a": 1}).encode())
        assert cc._get_json("/x", {"code": "601811"}, 3.0) == {"a": 1}
        assert seen["url"] == "http://127.0.0.1:8010/x?code=601811"
        assert seen["timeout"] == 3.0

    def test_http_error_returns_none(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, io.BytesIO(b""))

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        assert cc._get_json("/x", {"code": "1"}, 3.0) is None

    @pytest.mark.parametrize(
        "exc", [urllib.error.URLError("down"), TimeoutError(), OSError("econnrefused")]
    )
    def test_transport_errors_return_none(self, monkeypatch, exc):
        def boom(req, timeout=None):
            raise exc

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        assert cc._get_json("/x", {"code": "1"}, 3.0) is None

    def test_bad_json_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, b"<html>not json")
        assert cc._get_json("/x", {"code": "1"}, 3.0) is None

    def test_json_array_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, b"[1, 2]")
        assert cc._get_json("/x", {"code": "1"}, 3.0) is None

    def test_never_raises_even_on_unexpected(self, monkeypatch):
        """契约是「绝不抛」：连兜底分支没列到的异常类型也要变成 None。"""
        def boom(req, timeout=None):
            raise RuntimeError("surprise")

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        assert cc._get_json("/x", {"code": "1"}, 3.0) is None


class TestFetchEndpoints:
    def test_snapshot_url_is_loopback_and_has_no_ensure_factors(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, b'{"ok": 1}')
        cc.fetch_snapshot("601811")
        assert seen["url"].startswith("http://127.0.0.1:8010/api/dashboard/a-share/snapshot?")
        assert "code=601811" in seen["url"]
        assert "ensure_factors" not in seen["url"]

    def test_snapshot_normalizes_code(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, b'{"ok": 1}')
        cc.fetch_snapshot("1811")
        assert "code=001811" in seen["url"]

    def test_recommendation_url(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, b'{"ok": 1}')
        cc.fetch_recommendation("601811")
        assert seen["url"].startswith(
            "http://127.0.0.1:8010/api/dashboard/a-share/recommendation?"
        )

    def test_bad_code_makes_no_request(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("不该发请求")

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        assert cc.fetch_snapshot("abc") is None
        assert cc.fetch_recommendation(None) is None

    def test_timeout_is_forwarded(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, b"{}")
        cc.fetch_snapshot("601811", timeout=1.5)
        assert seen["timeout"] == 1.5


class TestOverlayUsable:
    def test_ok_when_any_overlay_non_empty(self):
        snap = {"data_quality": {"degraded": False}, "overlays": {"bis": [{"direction": -1}]}}
        assert cc.overlay_usable(snap) is True

    def test_degraded_is_not_usable(self):
        snap = {"data_quality": {"degraded": True}, "overlays": {"bis": [1]}}
        assert cc.overlay_usable(snap) is False

    def test_all_empty_is_not_usable(self):
        snap = {"data_quality": {"degraded": False},
                "overlays": {"fractals": [], "bis": [], "zhongshus": [], "trend_types": []}}
        assert cc.overlay_usable(snap) is False

    @pytest.mark.parametrize("bad", [None, [], "x", {}, {"overlays": None},
                                     {"overlays": {"bis": "x"}}])
    def test_malformed_is_not_usable(self, bad):
        assert cc.overlay_usable(bad) is False

    def test_snapshot_overlays_returns_dict_or_none(self):
        snap = {"overlays": {"bis": [1]}}
        assert cc.snapshot_overlays(snap) == {"bis": [1]}
        assert cc.snapshot_overlays({"overlays": {"bis": []}}) is None


class TestPriceRatioOf:
    def test_valid(self):
        assert cc.price_ratio_of({"price_ratio": 7.0605}) == pytest.approx(7.0605)
        assert cc.price_ratio_of({"price_ratio": 2}) == pytest.approx(2.0)

    @pytest.mark.parametrize("bad", [None, 0, 0.0, -1.0, "7.06", True, [], {}])
    def test_invalid(self, bad):
        assert cc.price_ratio_of({"price_ratio": bad}) is None

    def test_no_recommendation(self):
        assert cc.price_ratio_of(None) is None


class TestDataQualityNote:
    def test_ok_returns_none(self):
        snap = {"data_quality": {"severity": "ok", "gap": False, "gap_count": 0,
                                 "stale": False, "closed_bar_count": 119}}
        assert cc.data_quality_note(snap) is None

    @pytest.mark.parametrize("bad", [None, {}, {"data_quality": None}, {"data_quality": {}}])
    def test_missing_returns_none(self, bad):
        assert cc.data_quality_note(bad) is None

    def test_degraded_shape(self):
        """实测 001381：只有 degraded + reason。"""
        note = cc.data_quality_note({"data_quality": {"degraded": True, "reason": "no_data"}})
        assert note is not None and "no_data" in note

    def test_gap_shape_from_real_payload(self):
        """实测 000016：没有 degraded 键，靠 severity/gap_count 才看得出缺数据。"""
        note = cc.data_quality_note({"data_quality": {
            "severity": "gap", "gap": True, "gap_count": 5, "stale": False,
            "closed_bar_count": 92, "expected_trade_days": 97}})
        assert note is not None
        assert "severity=gap" in note and "gap_count=5" in note

    def test_stale_is_reported(self):
        note = cc.data_quality_note({"data_quality": {"severity": "ok", "stale": True}})
        assert note is not None and "stale=True" in note

    def test_degraded_takes_precedence(self):
        note = cc.data_quality_note({"data_quality": {
            "degraded": True, "reason": "no_data", "severity": "gap", "gap_count": 3}})
        assert note is not None and "no_data" in note and "gap_count" not in note


class TestRecommendationView:
    def test_projection(self):
        rec = {"action": "watch", "action_label": "关注", "headline": "h", "reason": "r",
               "price": 21.3, "status": "structure_ready", "signal_type": "first_buy",
               "divergence_status": "not_checked", "available": True, "disclaimer": "d",
               "price_ratio": 7.06, "raw_close": 21.3}
        view = cc.recommendation_view(rec)
        assert view is not None
        assert view["action"] == "watch" and view["status"] == "structure_ready"
        assert "price_ratio" not in view          # 换算系数不进展示投影

    @pytest.mark.parametrize("bad", [None, {}, {"unknown": 1}])
    def test_empty_is_none(self, bad):
        assert cc.recommendation_view(bad) is None
