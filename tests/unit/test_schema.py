"""数据库 schema（data.schema）：DDL 字典基本校验。"""
from __future__ import annotations

from emotion_core.data import schema


class TestSchema:
    def test_expected_tables_present(self):
        expected = {
            "stock_basic", "daily_bar", "derived_bar", "limit_pool_em",
            "market_stat", "ladder_day", "promotion_day",
            "signal", "signal_outcome", "strategy_signal",
            "position", "holding_decision", "trade_event",
            "review_report", "eval_result",
            "theme_tag", "theme_group", "theme_spot",
            "alert", "llm_call_log", "ingest_progress", "hot_rank",
            "pipeline_state", "data_revision", "watchlist",
            "trade_calendar",
            "ref_limit_rule", "ref_security_status", "ref_dividend", "ref_rs",
            "ref_limit_state", "ref_limit_state_run",
            "ref_security_status_event", "ref_security_status_window",
            "ops_raw_manifest", "ops_quota_ledger",
            "trigger_log",
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

    def test_trigger_log_pk_and_columns(self):
        """ADR 0003 §D2：盘中台账 15 列，主键 (date, code)。"""
        ddl = schema.DDL["trigger_log"].upper()
        assert "PRIMARY KEY (DATE, CODE)" in ddl
        for col in ("FIRST_SEAL_TS", "TRIGGER_PRICE", "SEAL_BID1_VOL", "BROKE_TS",
                    "CLOSED_SEALED", "PREV_DAYS", "ENTRY_PX", "EXIT_PX", "ROLLED",
                    "RET_PCT", "SETTLED_AT"):
            assert col in ddl, f"trigger_log 缺列 {col}"

    def test_stock_basic_pk(self):
        assert "CODE         TEXT PRIMARY KEY" in schema.DDL["stock_basic"].upper()
