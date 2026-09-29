"""theme_service 冒烟测试（无 DB）。

锁一个真实发生过的回归：`run()` 里曾写 `CONFIG.get("THEME_PROVIDER")`——那是旧 dict
配置时代的写法，而 `utils/config.py` 的 `CONFIG` 是 frozen dataclass，既没有 `.get()`
也没有 `THEME_PROVIDER` 字段，于是日更每次走到 theme 步骤都 AttributeError 崩
（2026-09-28 那次日更在 ladder 修复后才暴露出来）。

本文件不需要数据库：默认 provider（NullProvider）返回空标签，`run()` 在触库之前
就返回 0。
"""
from __future__ import annotations

from datetime import date

from emotion_core.data.theme_source import NullProvider
from emotion_core.services import theme_service


class TestDefaultProviderShortCircuit:
    """默认题材源为空 → 不阻断主链，且在开事务之前返回。"""

    def test_run_returns_zero_with_no_tags(self):
        assert theme_service.run(date(2026, 9, 28)) == 0

    def test_provider_factory_defaults_to_null(self):
        assert isinstance(theme_service.get_provider(), NullProvider)
