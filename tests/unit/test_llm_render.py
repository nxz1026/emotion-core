"""llm/render.py prompt 模板渲染测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.llm import render


class TestRenderPrompt:
    def test_render_with_vars(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "Hello {name}, you are {age} years old"
        mock_path.__truediv__ = lambda self, other: mock_path

        with patch("emotion_core.llm.render.PROMPTS_DIR", mock_path):
            result = render.render_prompt("test_template", name="Alice", age=30)
            assert result == "Hello Alice, you are 30 years old"

    def test_missing_var_raises_value_error(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "Hello {name}, missing {missing}"
        mock_path.__truediv__ = lambda self, other: mock_path

        with patch("emotion_core.llm.render.PROMPTS_DIR", mock_path):
            with pytest.raises(ValueError, match="缺少变量"):
                render.render_prompt("test_template", name="Alice")

    def test_no_vars_needed(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "Static text"
        mock_path.__truediv__ = lambda self, other: mock_path

        with patch("emotion_core.llm.render.PROMPTS_DIR", mock_path):
            result = render.render_prompt("test_template")
            assert result == "Static text"

    def test_template_not_found(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_path.__truediv__ = lambda self, other: mock_path

        with patch("emotion_core.llm.render.PROMPTS_DIR", mock_path):
            with pytest.raises(FileNotFoundError):
                render.render_prompt("nonexistent")
