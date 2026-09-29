"""presentation/views 视图渲染测试。"""
from __future__ import annotations

from emotion_core.presentation.views import intuitive, logic, algorithm


class TestIntuitiveRender:
    def test_with_recommendation(self):
        result = intuitive.render({"phase": "发酵", "recommendation": {"code": "600000"}})
        assert "发酵" in result
        assert "600000" in result
        assert "今日结论" in result

    def test_without_recommendation(self):
        result = intuitive.render({"phase": "冰点"})
        assert "冰点" in result
        assert "今日无推荐" in result

    def test_default_phase(self):
        result = intuitive.render({})
        assert "未知" in result


class TestLogicRender:
    def test_with_stats(self):
        result = logic.render({"stats": {"metric1": "value1"}})
        assert "metric1" in result
        assert "value1" in result
        assert "市场参数" in result

    def test_empty_stats(self):
        result = logic.render({})
        assert "市场参数" in result


class TestAlgorithmRender:
    def test_with_formulas(self):
        result = algorithm.render({"formulas": {"f1": "x + y"}})
        assert "x + y" in result
        assert "算法口径" in result

    def test_empty_formulas(self):
        result = algorithm.render({})
        assert "算法口径" in result
