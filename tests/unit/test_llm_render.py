"""llm/render.py 单元测试：render_prompt 模板加载与变量填充。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from emotion_core.llm.render import render_prompt, PROMPTS_DIR


class TestRenderPrompt:
    """render_prompt 模板渲染。"""

    def test_render_with_vars(self, tmp_path):
        """正常填充变量。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        (template_dir / "test.md").write_text(
            "Hello {name}, age={age}", encoding="utf-8"
        )
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            result = render_prompt("test", name="Alice", age=30)
        assert result == "Hello Alice, age=30"

    def test_render_no_vars(self, tmp_path):
        """无变量模板。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        (template_dir / "plain.md").write_text(
            "No variables here", encoding="utf-8"
        )
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            result = render_prompt("plain")
        assert result == "No variables here"

    def test_missing_template_raises(self, tmp_path):
        """模板不存在时抛 FileNotFoundError。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            try:
                render_prompt("nonexistent")
            except FileNotFoundError as e:
                assert "nonexistent" in str(e)
            else:
                raise AssertionError("应抛 FileNotFoundError")

    def test_missing_var_raises(self, tmp_path):
        """模板中有占位符但无对应变量时抛 ValueError。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        (template_dir / "incomplete.md").write_text(
            "Hello {name}, missing={missing_var}", encoding="utf-8"
        )
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            try:
                render_prompt("incomplete", name="Alice")
            except ValueError as e:
                assert "missing_var" in str(e) or "缺少变量" in str(e)
            else:
                raise AssertionError("应抛 ValueError")

    def test_render_with_chinese(self, tmp_path):
        """中文变量填充。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        (template_dir / "chinese.md").write_text(
            "情绪阶段：{phase}，涨停：{limit_up_count}", encoding="utf-8"
        )
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            result = render_prompt("chinese", phase="发酵", limit_up_count=55)
        assert result == "情绪阶段：发酵，涨停：55"

    def test_render_multiline(self, tmp_path):
        """多行模板。"""
        template_dir = tmp_path / "prompts"
        template_dir.mkdir()
        (template_dir / "multi.md").write_text(
            "Line 1: {a}\nLine 2: {b}\nLine 3: {c}", encoding="utf-8"
        )
        with patch("emotion_core.llm.render.PROMPTS_DIR", template_dir):
            result = render_prompt("multi", a="1", b="2", c="3")
        assert "Line 1: 1" in result
        assert "Line 2: 2" in result
        assert "Line 3: 3" in result

    def test_prompts_dir_exists(self):
        """PROMPTS_DIR 指向真实目录。"""
        assert PROMPTS_DIR.exists()
        assert PROMPTS_DIR.is_dir()

    def test_real_emotion_template(self):
        """真实 emotion.md 模板可加载。"""
        result = render_prompt(
            "emotion",
            phase="发酵",
            limit_up_count=55,
            bomb_count=8,
            max_height=6,
            ecosystem_rating="G2",
        )
        assert "发酵" in result
        assert "55" in result
        assert "8" in result
        assert "6" in result
        assert "G2" in result

    def test_real_stock_template(self):
        """真实 stock.md 模板可加载。"""
        result = render_prompt(
            "stock",
            code="600519",
            name="贵州茅台",
            trade_date="2024-01-02",
            is_st="否",
            is_new="否",
            industry="白酒",
            market_cap="20000亿",
            stance_text="观望",
            one_liner="情绪回暖",
            reasons="连板高度打开",
            risks="炸板率偏高",
            tags="高标",
            market_env="G2",
            stock_state="活跃",
            stats="连板5天",
        )
        assert "600519" in result
        assert "贵州茅台" in result
