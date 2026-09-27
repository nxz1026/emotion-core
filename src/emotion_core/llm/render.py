"""prompt 模板加载 + 变量填充。"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("emotion_core.llm.render")

PROMPTS_DIR = Path(__file__).parent / "prompts"


def render_prompt(template_name: str, **vars) -> str:
    """加载 prompt 模板并填充变量。

    Args:
        template_name: 模板文件名（不含 .md）。
        **vars: 模板变量。

    Returns:
        填充后的 prompt 文本。

    Raises:
        FileNotFoundError: 模板文件不存在。
        ValueError: 模板中的占位符没有对应变量。
    """
    path = PROMPTS_DIR / f"{template_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt 模板不存在: {path}")
    template = path.read_text(encoding="utf-8")
    try:
        return template.format(**vars)
    except KeyError as e:
        raise ValueError(f"prompt 模板 {template_name} 缺少变量: {e}") from e
