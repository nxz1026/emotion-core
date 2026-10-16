"""新浪行业分类源：GBK + JS 变量解析、分页、跨节点去重。全程不联网。"""

from __future__ import annotations

from emotion_core.data.providers import sina_industry as si


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def _hy_bytes(payload: str) -> bytes:
    """真实响应是 GBK 编码的 JS 变量赋值。"""
    return f"var S_Finance_bankuai_sinaindustry = {payload};".encode("gbk")


def test_fetch_industry_nodes_parses_gbk_and_drops_non_industry(monkeypatch):
    payload = (
        '{"new_blhy":"new_blhy,玻璃行业,19,15.57,-0.33,sz300093",'
        '"new_cbzz":"new_cbzz,船舶制造,8,14.54",'
        '"new_cx":"new_cx,次新股,161,2.0"}'
    )
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: _Resp(_hy_bytes(payload)))

    assert si.fetch_industry_nodes() == [
        ("new_blhy", "玻璃行业", 19),
        ("new_cbzz", "船舶制造", 8),
    ], "次新股 不是行业，必须剔除"


def test_fetch_industry_nodes_rejects_format_change(monkeypatch):
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: _Resp(b"<html>502</html>"))
    try:
        si.fetch_industry_nodes()
    except si.DataError as exc:
        assert "格式变化" in str(exc)
    else:
        raise AssertionError("协议变化必须抛 DataError，不能静默返回空")


def test_fetch_industry_members_pages_until_short_page(monkeypatch):
    calls: list[int] = []

    def fake_list(url, params, **kw):
        calls.append(params["page"])
        if params["page"] == 1:
            return [{"code": "600000"}, {"code": "000001"}]
        return [{"code": "300750"}]

    monkeypatch.setattr(si, "fetch_json_list", fake_list)
    codes = si.fetch_industry_members("new_blhy", page_size=2)
    assert codes == ["600000", "000001", "300750"]
    assert calls == [1, 2], "首页满页才继续翻页"


def test_fetch_industry_members_skips_bad_codes(monkeypatch):
    monkeypatch.setattr(
        si,
        "fetch_json_list",
        lambda *a, **k: [{"code": "600519"}, {"code": "bad"}, {"code": ""}, {}],
    )
    assert si.fetch_industry_members("new_blhy") == ["600519"]


def test_fetch_industry_map_keeps_first_node_on_overlap(monkeypatch):
    monkeypatch.setattr(si, "fetch_industry_nodes", lambda: [("a", "行业A", 2), ("b", "行业B", 1)])
    members = {"a": ["600000", "600001"], "b": ["600001", "000001"]}
    monkeypatch.setattr(si, "fetch_industry_members", lambda node, **k: members[node])

    assert si.fetch_industry_map() == {
        "600000": "行业A",
        "600001": "行业A",  # 跨节点重复 → 先到先得，不被 行业B 覆盖
        "000001": "行业B",
    }
