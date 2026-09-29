"""测试 ref_limit_rule 种子数据与查询（DB mock 版）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services import ref_limit_rule as mod


def _mock_conn(rows=None):
    """创建一个 mock DB 连接，支持 with connect() as conn: with conn.cursor() as cur: 模式。"""
    cursor = MagicMock()
    if rows is not None:
        cursor.fetchone.return_value = rows[0] if rows else None
        cursor.fetchall.return_value = rows

    cursor_ctx = MagicMock()
    cursor_ctx.__enter__ = MagicMock(return_value=cursor)
    cursor_ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor_ctx

    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=conn)
    conn_ctx.__exit__ = MagicMock(return_value=False)

    return conn_ctx, cursor


def test_seed_idempotent():
    """幂等写入：两次调用不报错且条数一致。"""
    conn_ctx, cursor = _mock_conn()
    with patch.object(mod, "connect", return_value=conn_ctx):
        n1 = mod.seed_ref_limit_rule()
    conn_ctx2, cursor2 = _mock_conn()
    with patch.object(mod, "connect", return_value=conn_ctx2):
        n2 = mod.seed_ref_limit_rule()
    assert n1 == n2
    assert n1 > 0
    # 确认 execute 被调用 n1 次
    assert cursor.execute.call_count == n1


def test_main_board_limit():
    """主板 ±10%。"""
    conn_ctx, cursor = _mock_conn(rows=[(10.0,)])
    with patch.object(mod, "connect", return_value=conn_ctx):
        assert mod.get_limit_pct("SSE", "main", date(2024, 1, 1)) == 10.0


def test_star_limit():
    """科创板 ±20%。"""
    conn_ctx, cursor = _mock_conn(rows=[(20.0,)])
    with patch.object(mod, "connect", return_value=conn_ctx):
        assert mod.get_limit_pct("SSE", "star", date(2024, 1, 1)) == 20.0


def test_st_limit():
    """ST 股 ±5%。"""
    conn_ctx, cursor = _mock_conn(rows=[(5.0,)])
    with patch.object(mod, "connect", return_value=conn_ctx):
        assert mod.get_limit_pct("SSE", "st", date(2024, 1, 1)) == 5.0


def test_bse_limit():
    """北交所 ±30%。"""
    conn_ctx, cursor = _mock_conn(rows=[(30.0,)])
    with patch.object(mod, "connect", return_value=conn_ctx):
        assert mod.get_limit_pct("BSE", "bse", date(2024, 1, 1)) == 30.0


def test_unknown_market_returns_none():
    """未知市场返回 None。"""
    conn_ctx, cursor = _mock_conn(rows=[])
    cursor.fetchone.return_value = None
    with patch.object(mod, "connect", return_value=conn_ctx):
        assert mod.get_limit_pct("NYSE", "main", date(2024, 1, 1)) is None


def test_seed_rules_content():
    """种子数据包含关键制度规则。"""
    rules = mod.SEED_RULES
    # 验证有主板规则
    main_rules = [r for r in rules if r[1] == "main"]
    assert len(main_rules) >= 2  # SSE + SZSE
    # 验证有创业板 20% 规则
    chinext_20 = [r for r in rules if r[1] == "chinext" and r[3] == 20.0]
    assert len(chinext_20) >= 1
    # 验证有 ST 5% 规则
    st_rules = [r for r in rules if r[1] == "st"]
    assert len(st_rules) >= 2
