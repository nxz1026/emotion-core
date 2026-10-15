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


class TestCleanTagRow:
    """写库前剔 STYLE_ONLY（含属性桶扩展）：2026-10-14 拍板。

    背景（生产实测）：当日 24 个题材组里 7 个是属性桶（参股金融 / 含B股 / 本月解禁 /
    业绩预降…），逐票 `primary_theme` 也会被桶占位（成分股最多的概念往往是桶）。
    """

    def test_attribute_bucket_dropped_from_primary(self):
        row = {"code": "600000", "primary_theme": "参股金融",
               "secondary_themes": ["机器人概念", "含B股"], "source": "sina_concept"}
        got = theme_service._clean_tag_row(row)
        assert got["primary_theme"] == "机器人概念"      # 桶被剔，真题材升位
        assert got["secondary_themes"] == []
        assert got["code"] == "600000"
        assert got["source"] == "sina_concept"            # 其余字段原样带过

    def test_all_noise_keeps_row_with_null_primary(self):
        row = {"code": "600519", "primary_theme": "基金重仓",
               "secondary_themes": ["业绩预升"], "source": "sina_concept"}
        got = theme_service._clean_tag_row(row)
        assert got["primary_theme"] is None               # 无有效题材：行保留，记覆盖
        assert got["secondary_themes"] == []

    def test_region_theme_kept(self):
        """地域族是真题材（炒地图），刻意不剔——本判据有意留的边界。"""
        row = {"code": "600758", "primary_theme": "成渝特区",
               "secondary_themes": ["振兴沈阳", "参股金融"], "source": "sina_concept"}
        got = theme_service._clean_tag_row(row)
        assert got["primary_theme"] == "成渝特区"
        assert got["secondary_themes"] == ["振兴沈阳"]

    def test_order_preserved(self):
        row = {"code": "000001", "primary_theme": "机器人概念",
               "secondary_themes": ["固态电池", "含H股", "白酒概念"],
               "source": "sina_concept"}
        got = theme_service._clean_tag_row(row)
        assert got["primary_theme"] == "机器人概念"
        assert got["secondary_themes"] == ["固态电池", "白酒概念"]

    def test_original_row_not_mutated(self):
        row = {"code": "000002", "primary_theme": "参股金融",
               "secondary_themes": ["含B股"], "source": "sina_concept"}
        theme_service._clean_tag_row(row)
        assert row["primary_theme"] == "参股金融"
