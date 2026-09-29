"""services/revision.py 数据修订留痕测试。

P1-G：revision.py:50 SQL 注入 — f-string 表名拼接。
修复：加表名白名单校验（_validate_table），拒绝非法/未注册表名。
"""

from __future__ import annotations

from datetime import date

import pytest

from emotion_core.services import revision


class TestValidateTable:
    """_validate_table 白名单校验。"""

    def test_valid_tables_accepted(self):
        for table in ("daily_bar", "derived_bar", "market_stat", "ladder_day"):
            assert revision._validate_table(table) == table

    def test_rejects_sql_injection(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table("daily_bar; DROP TABLE signal--")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table("")

    def test_rejects_none(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table(None)

    def test_rejects_unregistered_table(self):
        with pytest.raises(ValueError, match="未注册的表名"):
            revision._validate_table("nonexistent_table")

    def test_rejects_uppercase(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table("Daily_Bar")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table("daily bar")

    def test_rejects_prefix(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._validate_table("_daily_bar")


class TestDetectAndLog:
    """detect_and_log 入口行为（桩掉 DB）。"""

    def test_skips_when_no_date_in_conflict_cols(self, monkeypatch):
        """冲突键不含 date → 直接返回，不查库。"""
        monkeypatch.setattr(revision, "_existing_dates", lambda *a: (_ for _ in ()).throw(AssertionError("不应调用")))
        revision.detect_and_log("daily_bar", ("code",), [("600519", 1)])

    def test_skips_when_rows_empty(self, monkeypatch):
        """空 rows → 直接返回。"""
        monkeypatch.setattr(revision, "_existing_dates", lambda *a: (_ for _ in ()).throw(AssertionError("不应调用")))
        revision.detect_and_log("daily_bar", ("date",), [])

    def test_invalid_table_does_not_block_upsert(self, monkeypatch):
        """非法表名 → detect_and_log 内部捕获，不抛（不拦写入）。"""
        # 非法表名应在 try/except 内被捕获，不外抛
        revision.detect_and_log("bad;table", ("date",), [(date(2026, 9, 29),)])


class TestExistingDates:
    """_existing_dates 的白名单校验。"""

    def test_rejects_invalid_table(self):
        with pytest.raises(ValueError, match="未注册的表名"):
            revision._existing_dates("bad_table", [date(2026, 9, 29)], conn=None)

    def test_rejects_injection_table(self):
        with pytest.raises(ValueError, match="非法表名"):
            revision._existing_dates("bad;table", [date(2026, 9, 29)], conn=None)
