"""tests for `services/watchlist_service.py`（R4 关注列表，真源 = CPT）。

所有 CPT 调用都打桩 `cpt_client` 函数；不发真实 HTTP。
"""
from __future__ import annotations

from emotion_core.services import watchlist_service as ws


def _patch_track(monkeypatch, *, list_result=None, add_result=True, remove_result=True,
                 restore_result=True):
    """统一打桩 watchlist_service 内的 track_* 引用（直接绑在 ws 命名空间）。"""
    monkeypatch.setattr(ws, "track_list", lambda user: list_result)
    monkeypatch.setattr(ws, "track_add", lambda code, user, note="": add_result)
    monkeypatch.setattr(ws, "track_remove", lambda code, user: remove_result)
    monkeypatch.setattr(ws, "track_restore", lambda code, user: restore_result)


class TestListCodes:
    def test_ok(self, monkeypatch):
        items = [{"code": "601811", "note": ""}]
        _patch_track(monkeypatch, list_result=items)
        got, ok = ws.list_codes(user="u")
        assert ok is True and got == items

    def test_empty(self, monkeypatch):
        _patch_track(monkeypatch, list_result=[])
        got, ok = ws.list_codes(user="u")
        assert ok is True and got == []

    def test_cpt_unreachable(self, monkeypatch):
        _patch_track(monkeypatch, list_result=None)
        got, ok = ws.list_codes(user="u")
        assert ok is False and got == []


class TestAdd:
    def test_ok(self, monkeypatch):
        _patch_track(monkeypatch, list_result=[], add_result=True)
        ok, msg = ws.add("601811", user="u", note="watch")
        assert ok is True and "已加入" in msg

    def test_bad_code(self, monkeypatch):
        _patch_track(monkeypatch)
        ok, msg = ws.add("abc", user="u")
        assert ok is False and "非法 code" in msg

    def test_cpt_unreachable(self, monkeypatch):
        _patch_track(monkeypatch, add_result=False)
        ok, msg = ws.add("601811", user="u")
        assert ok is False and "CPT 不可达" in msg

    def test_note_truncated(self, monkeypatch):
        seen = {}

        def fake_add(code, user, note=""):
            seen["note"] = note
            return True

        monkeypatch.setattr(ws, "track_add", fake_add)
        monkeypatch.setattr(ws, "track_list", lambda user: [])
        ok, _ = ws.add("601811", user="u", note="x" * 500)
        assert ok is True and len(seen["note"]) == ws.TRACK_NOTE_MAX


class TestRemove:
    def test_ok(self, monkeypatch):
        _patch_track(monkeypatch, remove_result=True)
        ok, msg = ws.remove("601811", user="u")
        assert ok is True and "已移出" in msg

    def test_bad_code(self, monkeypatch):
        _patch_track(monkeypatch)
        ok, msg = ws.remove("xyz", user="u")
        assert ok is False and "非法 code" in msg

    def test_cpt_unreachable(self, monkeypatch):
        _patch_track(monkeypatch, remove_result=False)
        ok, msg = ws.remove("601811", user="u")
        assert ok is False and "CPT 不可达" in msg


class TestRestore:
    def test_ok(self, monkeypatch):
        _patch_track(monkeypatch, restore_result=True)
        ok, msg = ws.restore("601811", user="u")
        assert ok is True and "已恢复" in msg

    def test_cpt_unreachable(self, monkeypatch):
        _patch_track(monkeypatch, restore_result=False)
        ok, msg = ws.restore("601811", user="u")
        assert ok is False and "CPT 不可达" in msg


class TestIsTracked:
    def test_tracked(self, monkeypatch):
        _patch_track(monkeypatch, list_result=[{"code": "601811"}, {"code": "000002"}])
        assert ws.is_tracked("601811", user="u") is True

    def test_not_tracked(self, monkeypatch):
        _patch_track(monkeypatch, list_result=[{"code": "000002"}])
        assert ws.is_tracked("601811", user="u") is False

    def test_empty_list(self, monkeypatch):
        _patch_track(monkeypatch, list_result=[])
        assert ws.is_tracked("601811", user="u") is False

    def test_cpt_unreachable(self, monkeypatch):
        _patch_track(monkeypatch, list_result=None)
        assert ws.is_tracked("601811", user="u") is None

    def test_bad_code(self, monkeypatch):
        _patch_track(monkeypatch)
        assert ws.is_tracked("abc", user="u") is False
