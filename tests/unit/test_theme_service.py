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

from emotion_core.data import schema
from emotion_core.data.theme_source import ENV_PROVIDER, NullProvider
from emotion_core.services import theme_service


class TestDefaultProviderShortCircuit:
    """默认题材源为空 → 不阻断主链，且在开事务之前返回。"""

    def test_run_returns_zero_with_no_tags(self, monkeypatch):
        # 显式清掉激活变量：本测试断言「默认源为空则短路」，若跑测环境恰好导出了
        # EMOTION_THEME_PROVIDER，就会绕过短路去触库——那是另一条测试该管的事。
        monkeypatch.delenv(ENV_PROVIDER, raising=False)
        assert theme_service.run(date(2026, 9, 28)) == 0

    def test_provider_factory_defaults_to_null(self, monkeypatch):
        monkeypatch.delenv(ENV_PROVIDER, raising=False)
        assert isinstance(theme_service.get_provider(), NullProvider)


class TestColumnContracts:
    """写入列必须与 DDL 对得上 —— 表结构漂移在这库是真实踩过的坑。

    `data/schema.py` 的 `create_all()` 在 src 里没有调用方，新表要手动建一次；
    列名一旦两边写岔（比如 theme_service 写 `pct_chg`、DDL 里叫 `chg_pct`），
    单测不会报，只有当天夜里日更才会炸。
    """

    @staticmethod
    def _ddl_columns(table: str) -> set[str]:
        body = schema.DDL[table]
        cols = set()
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("CREATE", "PRIMARY", ")")):
                continue
            cols.add(stripped.split()[0])
        return cols

    def test_theme_tag_columns(self):
        assert set(theme_service._TAG_COLS) <= self._ddl_columns("theme_tag")

    def test_theme_spot_columns(self):
        assert set(theme_service._SPOT_COLS) <= self._ddl_columns("theme_spot")

    def test_theme_spot_table_registered(self):
        assert "theme_spot" in schema.DDL
        assert any("idx_theme_spot_date" in s for s in schema._INDEXES)
