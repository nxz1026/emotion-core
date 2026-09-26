"""Rust 算法核心：加载 core_lib/emotion_core_rust.so（阶段3 接线）。

用法：
    from emotion_core.core import emotion_core_rust as rust
    rust.classify_series(...)
    # 或直接取符号：
    from emotion_core.core import classify_series
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_path = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), _os.pardir, _os.pardir, "core_lib", "emotion_core_rust.so")
)
_spec = _ilu.spec_from_file_location("emotion_core_rust", _path)
emotion_core_rust = _ilu.module_from_spec(_spec)
_sys.modules.setdefault("emotion_core_rust", emotion_core_rust)
_spec.loader.exec_module(emotion_core_rust)

# 重新导出全部公开符号（from emotion_core.core import classify_series）
for _name in dir(emotion_core_rust):
    if not _name.startswith("_"):
        globals()[_name] = getattr(emotion_core_rust, _name)

del _ilu, _os, _sys, _path, _spec, _name
