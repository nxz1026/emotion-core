"""测试根 conftest —— **本仓此前一个 conftest 都没有**（2026-10-07 新增）。

存在理由：Rust 产物 `src/core_lib/emotion_core_rust.so` 是**平台相关**产物
（本机 arm64 / pyo3 0.20）。2026-10-19 起它随仓库分发，于是异构机器（CI 是
x86_64）拿到的是一份**加载不了**的产物。若「就位」只按文件存在判断，这些机器上
任何 import Rust 的测试都会在**收集阶段**炸掉（`AttributeError: 'NoneType' object
has no attribute 'loader'`），而 CI 的命令正是 `pytest tests`。

这里的行为：**产物不可用（缺失或加载失败）时，把确实依赖 Rust 的测试模块整份
跳过并说明原因**，而不是让收集失败。判据是 `emotion_core.core.available()`
——存在 **且** 能在当前解释器里加载。判据用 **AST 解析 import 语句**而不是文本搜索——后者会把
`tests/architecture/test_dependency_direction.py` 里那两处**注释**中提到的
`dragon_env` 也算进去，把一条无关的门禁误伤掉。

登记新依赖时要做的只有一件事：把测试文件里对应的 import 写出来即可，
不需要在这里登记（AST 自动发现）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from emotion_core.core import available, load_error, so_path

# 传递依赖 Rust 的模块：core 是产物本身；dragon_env 在模块级持有 `_rust`；
# ecosystem_service 又 import dragon_env；daily 的 `_run_step("ecosystem")`
# 会 import ecosystem_service —— 它是**函数内**的延迟 import，AST 扫 import 语句
# 看不见，只能在这里显式登记（这正是本文件唯一需要手工维护的清单）。
_RUST_DEPENDENT = (
    "emotion_core.core",
    "emotion_core.algorithms.dragon_env",
    "emotion_core.services.ecosystem_service",
    "emotion_core.orchestration.daily",
)

_announced = False


def _imports_rust(path: Path) -> bool:
    """该测试文件是否 import 了任一 Rust 依赖模块。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in _RUST_DEPENDENT for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            # from emotion_core.core import emotion_core_rust → emotion_core.core
            if base in _RUST_DEPENDENT:
                return True
            # from emotion_core.services import ecosystem_service → 拼成全名再比
            if any(f"{base}.{a.name}" in _RUST_DEPENDENT for a in node.names):
                return True
    return False


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    global _announced
    if available() or collection_path.suffix != ".py":
        return False
    if not _imports_rust(collection_path):
        return False
    if not _announced:
        _announced = True
        print(
            f"\n[conftest] Rust 产物不可用 —— 依赖 Rust 的测试本次整份跳过。\n"
            f"          产物：{so_path()}\n"
            f"          原因：{load_error()}\n"
            "          这不是「测过了」，是**根本没测**：产物是平台相关的"
            "（本机 arm64 / pyo3 0.20），\n"
            "          异构机器与 CI（x86_64）上加载不了。\n"
            "          Rust↔Python 对账目前只在生产机（docs/06 §3.3）跑。\n"
            "          详见 scripts/ 与 docs/06-上线后工作手册.md §3.3。\n",
            file=sys.stderr,
        )
    return True
