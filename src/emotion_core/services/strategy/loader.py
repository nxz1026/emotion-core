"""DSA 策略 YAML 子集加载器；不依赖第三方 YAML 库。

Native emotion-core implementation (ported pattern from lkl/strategy/loader.py).
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    name: str
    display_name: str
    instructions: str
    aliases: list[str] = field(default_factory=list)
    default_priority: int = 100
    enabled: bool = True


def _split_comment(text: str) -> str:
    quoted = False
    quote = ""
    for i, char in enumerate(text):
        if char in "'\"":
            if not quoted:
                quoted, quote = True, char
            elif quote == char and (i == 0 or text[i - 1] != "\\"):
                quoted = False
        elif char == "#" and not quoted and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
    return text.rstrip()


def _scalar(text: str):
    value = _split_comment(text).strip()
    if not value:
        return ""
    if value.startswith(("'", '"')) and value[-1:] == value[0]:
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _split_list(value: str) -> list:
    body, parts, start, quoted, quote = value[1:-1], [], 0, False, ""
    for i, char in enumerate(body):
        if char in "'\"":
            if not quoted:
                quoted, quote = True, char
            elif quote == char and (i == 0 or body[i - 1] != "\\"):
                quoted = False
        elif char == "," and not quoted:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [_scalar(part) for part in parts if part.strip()]


def _parse_value(text: str):
    text = _split_comment(text).strip()
    if text.startswith("[") and text.endswith("]"):
        return _split_list(text)
    return _scalar(text)


def load_strategies(directory: Path) -> list[Skill]:
    """从 directory/*.yaml 加载策略 Skill 列表。"""
    skills = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            skills.append(_load_one(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("跳过策略文件 %s: %s", path.name, exc)
    return skills


def _load_one(path: Path) -> Skill:
    """解析单个 YAML 子集文件（key: value 行 + # 注释）。"""
    name = path.stem
    display_name = name
    instructions = ""
    aliases: list[str] = []
    enabled = True
    priority = 100
    instr_lines: list[str] = []
    in_instructions = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "name":
            display_name = value.strip("'\"")
        elif key == "instructions":
            in_instructions = True
            if value:
                instr_lines.append(value)
        elif key == "aliases":
            aliases = _parse_value(value) if isinstance(value, str) else []
        elif key == "enabled":
            enabled = value.lower() in ("true", "yes", "1")
        elif key == "priority":
            priority = int(value)
        elif in_instructions and stripped.startswith(" "):
            instr_lines.append(stripped.strip())

    instructions = " ".join(instr_lines).strip()
    return Skill(name=name, display_name=display_name,
                 instructions=instructions, aliases=aliases,
                 default_priority=priority, enabled=enabled)
