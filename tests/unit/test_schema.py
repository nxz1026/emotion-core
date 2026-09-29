"""数据库 schema（data.schema）：DDL 字典基本校验。"""
from __future__ import annotations

from emotion_core.data import schema


class TestSchema:
    def test_expected_tables_present(self):
        expected = {
            "stock_basic", "daily_bar", "derived_bar", "limit_pool_em",
            "market_stat", "ladder_day", "promotion_day",
            "signal", "signal_outcome", "strategy_signal",
            "position", "trade_event",
            "review_report", "eval_result",
            "theme_tag", "theme_group",
            "alert", "llm_call_log", "ingest_progress", "hot_rank",
            "pipeline_state", "data_revision", "watchlist",
            "trade_calendar",
            "ref_limit_rule", "ref_security_status", "ref_dividend", "ref_rs",
            "ops_raw_manifest", "ops_quota_ledger",
        }
        assert set(schema.DDL.keys()) == expected

    def test_all_ddl_are_strings(self):
        for table, ddl in schema.DDL.items():
            assert isinstance(ddl, str), f"{table}: DDL 必须是字符串"

    def test_all_ddl_create_table(self):
        for table, ddl in schema.DDL.items():
            assert "CREATE TABLE IF NOT EXISTS" in ddl, f"{table}: 缺少 CREATE TABLE"

    def test_primary_keys_defined(self):
        """所有表必须有主键或唯一约束（实体完整性）。"""
        for table, ddl in schema.DDL.items():
            upper = ddl.upper()
            has_pk = ("PRIMARY KEY" in upper or "UNIQUE" in upper)
            assert has_pk, f"{table}: 缺少 PRIMARY KEY 或 UNIQUE 约束"

    def test_daily_bar_pk(self):
        assert "PRIMARY KEY (CODE, DATE)" in schema.DDL["daily_bar"].upper()

    def test_stock_basic_pk(self):
        assert "CODE         TEXT PRIMARY KEY" in schema.DDL["stock_basic"].upper()
