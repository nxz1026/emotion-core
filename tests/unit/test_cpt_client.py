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


# ─────────────────────────────────────────────────────────────────────────────
# R4 / docs/18 §7.1 新函数
# ─────────────────────────────────────────────────────────────────────────────


class TestNormUser:
    @pytest.mark.parametrize("ok", ["default", "test_user", "user-1", "a.b", "A1", "x" * 32])
    def test_valid(self, ok):
        assert cc._norm_user(ok) == ok

    @pytest.mark.parametrize(
        "bad", [None, "", "   ", 123, "a b", "x" * 33, "a@b", "a/b", "a:b", "a*b", "奎爷"],
    )
    def test_invalid_falls_back(self, bad):
        assert cc._norm_user(bad) == "default"


class TestPostJson:
    def test_ok(self, monkeypatch):
        seen: dict = {}

        def fake(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["body"] = req.data
            seen["user"] = req.headers.get("X-cpt-user")
            return _FakeResp(b'{"created": true}')

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
        resp = cc._post_json("/api/x", {"action": "add"}, user="test_user")
        assert resp == {"created": True}
        assert seen["url"] == "http://127.0.0.1:8010/api/x"
        assert seen["method"] == "POST"
        assert seen["body"] == b'{"action": "add"}'
        assert seen["user"] == "test_user"

    def test_http_error_returns_none(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(b""))

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        assert cc._post_json("/api/x", {"action": "add"}, user="u") is None


class TestFetchSnapshotEnsureFactors:
    def test_ensure_factors_true_appends_param(self, monkeypatch):
        seen = _patch_urlopen(monkeypatch, json.dumps({"overlays": {}}).encode())
        cc.fetch_snapshot("601811", ensure_factors=True)
        assert "ensure_factors=1" in seen["url"]

    @pytest.mark.parametrize("val", [None, False])
    def test_ensure_factors_false_or_none_omits_param(self, monkeypatch, val):
        seen = _patch_urlopen(monkeypatch, json.dumps({"overlays": {}}).encode())
        cc.fetch_snapshot("601811", ensure_factors=val)
        assert "ensure_factors" not in seen["url"]


class TestTrackFunctions:
    """track_* 只校验路径、method、user 头；不打实库。"""

    def test_track_list(self, monkeypatch):
        seen = _patch_urlopen(
            monkeypatch,
            json.dumps({"available": True, "items": [{"code": "601811"}], "recycle": []}).encode(),
        )
        resp = cc.track_list(user="test_user")
        assert isinstance(resp, list) and resp[0]["code"] == "601811"
        assert seen["url"] == "http://127.0.0.1:8010/api/dashboard/track?"

    def test_track_list_degraded(self, monkeypatch):
        """CPT available=False → None（不是空列表）。"""
        _patch_urlopen(
            monkeypatch,
            json.dumps({"available": False, "reason": "DBError", "items": []}).encode(),
        )
        assert cc.track_list(user="u") is None

    def test_track_add(self, monkeypatch):
        seen: dict = {}

        def fake(req, timeout=None):
            seen["method"] = req.get_method()
            seen["url"] = req.full_url
            seen["body"] = req.data
            seen["user"] = req.headers.get("X-cpt-user")
            return _FakeResp(b'{"created": true}')

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
        assert cc.track_add("601811", user="test_user", note="watch") is True
        assert seen["method"] == "POST"
        assert seen["url"] == "http://127.0.0.1:8010/api/dashboard/track"
        assert seen["user"] == "test_user"
        assert json.loads(seen["body"]) == {"action": "add", "code": "601811", "note": "watch"}

    def test_track_add_bad_code(self):
        assert cc.track_add("abc", user="u") is False

    def test_track_remove(self, monkeypatch):
        seen: dict = {}

        def fake(req, timeout=None):
            seen["body"] = req.data
            return _FakeResp(b'{"removed": true}')

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
        assert cc.track_remove("601811", user="u") is True
        assert json.loads(seen["body"]) == {"action": "remove", "code": "601811"}

    def test_track_restore(self, monkeypatch):
        seen: dict = {}

        def fake(req, timeout=None):
            seen["body"] = req.data
            return _FakeResp(b'{"restored": true}')

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
        assert cc.track_restore("601811", user="u") is True
        assert json.loads(seen["body"]) == {"action": "restore", "code": "601811"}

    def test_track_advice(self, monkeypatch):
        payload = json.dumps({
            "suggested_points": {"buy": [{"price_level": 15.0, "confirmed": True}]}
        }).encode()
        seen = _patch_urlopen(monkeypatch, payload)
        resp = cc.track_advice("601811", user="test_user")
        assert isinstance(resp, dict)
        assert seen["url"] == "http://127.0.0.1:8010/api/dashboard/track/601811/advice?"

    def test_track_speak(self, monkeypatch):
        seen: dict = {}

        def fake(req, timeout=None):
            seen["method"] = req.get_method()
            seen["url"] = req.full_url
            return _FakeResp('{"text": "说人话：…"}'.encode())

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
        assert cc.track_speak("601811", user="u") is True
        assert seen["method"] == "POST"
        assert seen["url"] == "http://127.0.0.1:8010/api/dashboard/track/601811/speak"


class TestPointsView:
    def test_full(self):
        advice = {
            "suggested_points": {
                "buy": [{"price_level": 15.0, "confirmed": True}, {"price_level": 14.5}],
                "sell": [{"price_level": 27.0, "confirmed": False}],
                "stop_loss_reference": 14.2,
            },
            "raw_close": 21.14,
            "price_ratio": 1.061,
            "as_of_basis": "close",
        }
        v = cc.points_view(advice)
        assert v is not None
        assert v["buy"][0]["price_level"] == 15.0 and v["buy"][0]["confirmed"] is True
        assert v["buy"][1]["confirmed"] is False
        assert v["sell"][0]["price_level"] == 27.0
        assert v["stop_loss"] == 14.2
        assert v["raw_close"] == 21.14 and v["price_ratio"] == 1.061
        assert v["as_of_basis"] == "close"

    def test_missing_fields_are_none(self):
        v = cc.points_view({"suggested_points": {}})
        assert v is not None
        assert v["buy"] is None and v["sell"] is None
        assert v["stop_loss"] is None

    def test_bad_input(self):
        assert cc.points_view(None) is None
        # suggested_points 非 dict → 所有买点/卖点字段为 None，但仍返回结构
        v = cc.points_view({"suggested_points": "nope"})
        assert v is not None
        assert v["buy"] is None and v["sell"] is None


class TestHumanText:
    def test_str(self):
        assert cc.human_text({"human": " 说人话：快跑 "}) == "说人话：快跑"

    def test_dict(self):
        assert cc.human_text({"human": {"text": "快跑"}}) == "快跑"

    def test_missing(self):
        assert cc.human_text({}) is None
        assert cc.human_text({"human": ""}) is None
        assert cc.human_text({"human": 123}) is None


class TestPointsEmptyReason:
    def test_ok(self):
        assert cc.points_empty_reason(
            {"suggested_points": {"reason": "no_structure"}}
        ) == "no_structure"

    def test_missing(self):
        assert cc.points_empty_reason({}) is None
        assert cc.points_empty_reason({"suggested_points": {}}) is None
