"""Rust 算法核心：**惰性**加载 `src/core_lib/emotion_core_rust.so`。

用法：
    from emotion_core.core import emotion_core_rust as rust
    rust.classify_series(...)
    # 或直接取符号：
    from emotion_core.core import classify_series

⚠️ 2026-10-07：从「导入期无条件 exec_module」改为惰性加载（PEP 562）。
⚠️ 2026-10-19：`.so` 改为**随仓库分发**（`.gitignore` 精确放行
`src/core_lib/emotion_core_rust.so`），并把 `available()` 从「文件存在」升级为
「**存在且能在当前解释器里加载**」。

为什么
----
旧形态：`core_lib/*.so` 被 `*.so` 整类排除、仓里又没有编译它的 CI 步骤，于是干净
checkout 上任何 `import emotion_core.core` 都会在**模块导入期**炸掉：

    spec_from_file_location(...) → None   # 文件不存在
    module_from_spec(None)        → AttributeError: 'NoneType' object has no attribute 'loader'

惰性化 + `tests/conftest.py` 的整份 skip 解决了「产物不在」这一半；但还有同类坑没堵：
`.so` 是**平台相关**产物（本机 arm64 / pyo3 0.20），而 CI 跑在 x86_64。只要「就位」
仅按 `os.path.exists()` 判断，x86 上 check out 出来的 arm64 产物就会被当成可用，
本该 skip 的测试改为在 `exec_module` 处报错。现在 `available()` 会试加载一次，
失败原因见 `load_error()`。`tests/conftest.py` 据此在产物**不可用**时整份 skip。

⚠️ 生产上 `.so` 与解释器匹配，行为与从前一致；
且 `cargo build` 后必须按 `docs/06` §3.3 跑一遍 `tests/oracle` 对账。
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

__all__ = ["available", "load_error", "so_path"]

# src/emotion_core/core/__init__.py → 上溯两级到 src/ → core_lib/emotion_core_rust.so
SO_PATH = _os.path.normpath(_os.path.join(
    _os.path.dirname(__file__), _os.pardir, _os.pardir, "core_lib", "emotion_core_rust.so"))

_loaded = False
_last_error: str | None = None


def so_path() -> str:
    """产物绝对路径（无论是否存在）。"""
    return SO_PATH


def load_error() -> str | None:
    """产物不可用的原因（`available()` 为 True 或尚未探测时为 None）。"""
    return _last_error


def available() -> bool:
    """产物是否**可用**——存在**且**能在当前解释器里加载。

    ⚠️ 2026-10-19：此前只查 `os.path.exists()`。「文件在、但架构/ABI 不符」
    （CI 是 x86_64，产物是 arm64）会被误判成就位，让本该 skip 的 Rust 依赖
    测试改为在 `exec_module` 处报错。现在试加载一次（成功即缓存），
    失败原因写入 `load_error()`。
    """
    global _last_error
    if not _os.path.exists(SO_PATH):
        _last_error = f"Rust 产物不存在：{SO_PATH}"
        return False
    try:
        _load()
    except RustCoreUnavailable as exc:
        _last_error = str(exc)
        return False
    return True


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
            f"  它随仓库分发（`.gitignore` 精确放行 `src/core_lib/emotion_core_rust.so`），\n"
            f"  缺失说明当前 checkout 没有它，或本机副本被删。\n"
            f"  编译：cd src/emotion_core/core && cargo build --release\n"
            f"  然后把 target/release/libemotion_core.so 拷到：{SO_PATH}\n"
            f"  编完必须按 docs/06 §3.3 跑 `pytest tests/oracle` 做 Rust↔Python 对账。"
        )
    spec = _ilu.spec_from_file_location("emotion_core_rust", SO_PATH)
    if spec is None or spec.loader is None:
        raise RustCoreUnavailable(f"无法为 {SO_PATH} 创建 import spec")
    # ⚠️ 2026-10-19：`module_from_spec` 里就会 dlopen —— 架构/ABI 不符时它**在
    # 这一步**抛 ImportError（如「file too short」「invalid ELF header」），
    # 不能只把 exec_module 包在 try 里，否则 available() 兜不住、CI 上仍会炸收集。
    try:
        module = _ilu.module_from_spec(spec)
        _sys.modules.setdefault("emotion_core_rust", module)
        spec.loader.exec_module(module)
    except Exception as exc:
        _sys.modules.pop("emotion_core_rust", None)
        raise RustCoreUnavailable(
            f"加载 {SO_PATH} 失败：{type(exc).__name__}: {exc}\n"
            f"  多半是平台/ABI 不匹配：产物是针对某个架构与 Python 版本编的\n"
            f"  （当前解释器 {_sys.version.split()[0]} / {_sys.platform}），\n"
            f"  或 pyo3 与 Python ABI 对不上。本机重编即可。"
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
