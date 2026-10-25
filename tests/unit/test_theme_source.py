"""data/theme_source.py 题材 Provider 测试。"""

from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data.providers.sina_concept import ConceptNode
from emotion_core.data.theme_source import (
    ENV_PROVIDER,
    NullProvider,
    SinaConceptProvider,
    ThemeProvider,
    WindProvider,
    get_provider,
)


class TestNullProvider:
    def test_returns_empty_list(self):
        provider = NullProvider()
        result = provider.fetch_tags(date(2024, 6, 15))
        assert result == []

    def test_any_date(self):
        provider = NullProvider()
        assert provider.fetch_tags(date(2020, 1, 1)) == []
        assert provider.fetch_tags(date(2025, 12, 31)) == []

    def test_spots_empty(self):
        assert NullProvider().fetch_spots(date(2024, 6, 15)) == []


class TestWindProvider:
    def test_raises_not_implemented(self):
        provider = WindProvider()
        with pytest.raises(NotImplementedError, match="暂不实现"):
            provider.fetch_tags(date(2024, 6, 15))


def _node(code: str, name: str, count: int) -> ConceptNode:
    return ConceptNode(
        node=code,
        name=name,
        member_count=count,
        pct_chg=1.5,
        amount=1e9,
        leading_code="600000",
        leading_name="浦发银行",
    )


class TestSinaConceptProvider:
    """新浪概念源：用假 `fetch_concept_map` 锁行形状与排序确定性（不触网）。"""

    def _patch(self, monkeypatch, cmap):
        monkeypatch.setattr(
            "emotion_core.data.providers.sina_concept.fetch_concept_map",
            lambda nodes=None: ([], cmap),
        )

    def test_row_shape(self, monkeypatch):
        a, b = _node("gn_a", "华为汽车", 97), _node("gn_b", "固态电池", 40)
        self._patch(monkeypatch, {"002886": [a, b]})

        rows = SinaConceptProvider().fetch_tags(date(2026, 10, 14))

        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "002886"
        assert row["tag_date"] == date(2026, 10, 14)
        assert row["primary_theme"] == "华为汽车"  # 成员数最多者当主标签
        assert row["secondary_themes"] == ["固态电池"]
        assert row["source"] == "sina_concept"

    def test_primary_tie_breaks_by_node_code(self, monkeypatch):
        """成员数并列时按节点码排序 —— 同日重跑必须产出同一行（tag 表有唯一键）。"""
        a, b = _node("gn_z", "同类B", 50), _node("gn_a", "同类A", 50)
        self._patch(monkeypatch, {"600000": [a, b]})

        row = SinaConceptProvider().fetch_tags(date(2026, 10, 14))[0]
        assert row["primary_theme"] == "同类A"
        assert row["secondary_themes"] == ["同类B"]

    def test_multi_stock_multi_theme(self, monkeypatch):
        a, b = _node("gn_a", "题材A", 10), _node("gn_b", "题材B", 5)
        self._patch(monkeypatch, {"600000": [a], "300001": [a, b]})

        rows = {r["code"]: r for r in SinaConceptProvider().fetch_tags(date(2026, 10, 14))}
        assert set(rows) == {"600000", "300001"}
        assert rows["300001"]["primary_theme"] == "题材A"
        assert rows["300001"]["secondary_themes"] == ["题材B"]

    def test_nodes_without_name_are_dropped(self, monkeypatch):
        self._patch(monkeypatch, {"600000": [_node("gn_x", "", 3)]})
        assert SinaConceptProvider().fetch_tags(date(2026, 10, 14)) == []

    def test_empty_map(self, monkeypatch):
        self._patch(monkeypatch, {})
        assert SinaConceptProvider().fetch_tags(date(2026, 10, 14)) == []


class TestSinaConceptSpots:
    def test_spot_row_shape(self, monkeypatch):
        node = ConceptNode(
            node="gn_hwqc",
            name="华为汽车",
            member_count=97,
            pct_chg=-2.559,
            amount=25497037032.0,
            leading_code="002886",
            leading_name="沃特股份",
        )
        monkeypatch.setattr(
            "emotion_core.data.providers.sina_concept.fetch_concept_nodes",
            lambda: [node],
        )

        rows = SinaConceptProvider().fetch_spots(date(2026, 10, 14))

        assert rows[0]["node"] == "gn_hwqc"
        assert rows[0]["theme"] == "华为汽车"
        assert rows[0]["pct_chg"] == -2.559
        assert rows[0]["leading_code"] == "002886"
        assert rows[0]["source"] == "sina_concept"


class TestGetProvider:
    def test_default_null(self, monkeypatch):
        monkeypatch.delenv(ENV_PROVIDER, raising=False)
        provider = get_provider()
        assert isinstance(provider, NullProvider)

    def test_none_null(self):
        provider = get_provider(None)
        assert isinstance(provider, NullProvider)

    def test_wind(self):
        provider = get_provider("wind")
        assert isinstance(provider, WindProvider)

    def test_unknown_null(self):
        provider = get_provider("unknown")
        assert isinstance(provider, NullProvider)

    def test_sina_names(self):
        assert isinstance(get_provider("sina_concept"), SinaConceptProvider)
        assert isinstance(get_provider("sina"), SinaConceptProvider)
        assert isinstance(get_provider("  Sina_Concept  "), SinaConceptProvider)

    def test_env_activates(self, monkeypatch):
        monkeypatch.setenv(ENV_PROVIDER, "sina_concept")
        assert isinstance(get_provider(), SinaConceptProvider)

    def test_empty_env_stays_null(self, monkeypatch):
        monkeypatch.setenv(ENV_PROVIDER, "")
        assert isinstance(get_provider(), NullProvider)


class TestProtocol:
    def test_protocol_exists(self):
        assert hasattr(ThemeProvider, "fetch_tags")
