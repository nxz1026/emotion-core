"""Rust 算法核心：**惰性**加载 `src/core_lib/emotion_core_rust.so`。

用法：
    from emotion_core.core import emotion_core_rust as rust
    rust.classify_series(...)
    # 或直接取符号：
    from emotion_core.core import classify_series

⚠️ 2026-10-07：从「导入期无条件 exec_module」改为惰性加载（PEP 562）。

为什么
----
`core_lib/emotion_core_rust.so` 是 **`.gitignore` 排除的构建产物**（`*.so`），
而仓里**没有 CI 编译它的步骤**，本地/CI 环境也没有 cargo。于是任何
`import emotion_core.core` 的代码，在干净 checkout 上都会在**模块导入期**
直接炸掉：

    spec_from_file_location(...) → None   # 文件不存在
    module_from_spec(None)        → AttributeError: 'NoneType' object has no attribute 'loader'

实测后果：`tests/oracle/test_*_rust_vs_python.py`（6 份）、`tests/unit/test_dragon_env.py`、
`tests/unit/test_ecosystem_service.py` 在无产物的环境里**收集阶段即报错**，
而 CI 的命令正是 `pytest tests` ⇒ 那条 CI 从未真正跑通过。

惰性化之后，「产物没编」变成一条**在第一次真正用到算法时**才抛出的、
带明确指引的错误，而不是一串看不懂的 AttributeError。
`tests/conftest.py` 会在产物缺失时把依赖 Rust 的测试整份 skip 并说明原因。

⚠️ 生产上 `.so` 是存在的（`src/core_lib/emotion_core_rust.so`），行为与从前一致；
且 `cargo build` 后必须按 `docs/06` §3.3 跑一遍 `tests/oracle` 对账。
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

__all__ = ["available", "so_path"]

# src/emotion_core/core/__init__.py → 上溯两级到 src/ → core_lib/emotion_core_rust.so
SO_PATH = _os.path.normpath(_os.path.join(
    _os.path.dirname(__file__), _os.pardir, _os.pardir, "core_lib", "emotion_core_rust.so"))

_loaded = False


def so_path() -> str:
    """产物绝对路径（无论是否存在）。"""
    return SO_PATH


def available() -> bool:
    """产物是否就位——**不加载**，可直接用于门禁/降级分支。"""
    return _os.path.exists(SO_PATH)


class RustCoreUnavailable(RuntimeError):
    """`.so` 缺失或加载失败。继承 RuntimeError 以便调用方按既有 except 兜住。"""


def _load():
    """加载并缓存 .so 模块（幂等）。"""
    global _loaded
    if _loaded:
        return _sys.modules["emotion_core_rust"]
    if not _os.path.exists(SO_PATH):
        raise RustCoreUnavailable(
            f"Rust 核心产物不存在：{SO_PATH}\n"
            f"  它是 `*.so` 构建产物、被 .gitignore 排除，不随仓库分发。\n"
            f"  编译：cd src/emotion_core/core && cargo build --release\n"
            f"  然后把 target/release/libemotion_core.so 拷到：{SO_PATH}\n"
            f"  编完必须按 docs/06 §3.3 跑 `pytest tests/oracle` 做 Rust↔Python 对账。"
        )
    spec = _ilu.spec_from_file_location("emotion_core_rust", SO_PATH)
    if spec is None or spec.loader is None:
        raise RustCoreUnavailable(f"无法为 {SO_PATH} 创建 import spec")
    module = _ilu.module_from_spec(spec)
    _sys.modules.setdefault("emotion_core_rust", module)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del _sys.modules["emotion_core_rust"]
        raise RustCoreUnavailable(
            f"加载 {SO_PATH} 失败：{type(exc).__name__}: {exc}\n"
            f"  多半是 pyo3 / Python ABI 不匹配（.so 与当前解释器版本对不上）。"
        ) from exc
    _loaded = True
    return module


def __getattr__(name: str):
    """PEP 562：属性访问时才加载，并把 .so 的公开符号逐个转发出去。

    前向兼容地保留原「重新导出全部公开符号」的行为（`from emotion_core.core
    import classify_series` 照旧可用），只是把加载时机从导入期挪到首次访问。
    """
    if name.startswith("__"):
        raise AttributeError(name)
    if name == "emotion_core_rust":
        module = _load()
        globals()[name] = module
        return module
    module = _load()
    try:
        value = getattr(module, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value                      # 缓存，后续走正常查找
    return value


def __dir__() -> list[str]:
    base = list(globals())
    if available():
        try:
            base += [n for n in dir(_load()) if not n.startswith("_")]
        except RustCoreUnavailable:              # 产物在但加载失败：dir 仍可用
            pass
    return sorted(set(base))
