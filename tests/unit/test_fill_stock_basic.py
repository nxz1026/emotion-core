"""stock_basic 填充（§9 第 11 条）：行业/总市值/流通股本入库，缺字段保留旧值。

`diagnose_service` 查 industry / market_cap，此前两列 DDL 里都没有、服务一调就抛
UndefinedColumn。现在列已补，数据源取 EM clist 的 f100（行业名）/ f20（总市值，元）；
`float_shares` 由 f21（流通市值，元）÷ f2（收盘价）本地反推，零额外请求。

新浪回退源：f100 确实没有（新浪只给证监会门类级行业，粒度太粗），故 industry
缺列时必须 NULL → 由 upsert 的 COALESCE 保留库内旧值，**不能用 None 覆写已有事实**；
f20/f21 自 2026-10-12 起已收录（provider 侧 `_SINA_UNIT_SCALE` 做万元→元），
所以本机 EM 持续 502 时回退源下市值/流通股本照样有值。
"""
from __future__ import annotations

from typing import Self

import pandas as pd
import pytest

from emotion_core.data import fill_stock_basic as fsb


def test_rows_from_full_em_frame():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": "贵州茅台", "f2": 1288.0,
         "f100": "酿酒行业", "f20": 1.61e12, "f21": 1.61e12},
        {"f12": "000001", "f14": "平安银行", "f2": 11.73,
         "f100": "银行", "f20": 2.276e11, "f21": 1.8214e11},
    ])
    rows = fsb._rows_from_frame(raw)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("600519", "贵州茅台", "酿酒行业", 1.61e12),
        ("000001", "平安银行", "银行", 2.276e11),
    ]
    assert rows[0][4] == pytest.approx(1.61e12 / 1288.0)      # ≈12.50 亿股
    assert rows[1][4] == pytest.approx(1.8214e11 / 11.73)     # ≈155.3 亿股


def test_rows_from_sina_frame_without_industry_column():
    """新浪回退帧：f100 缺列 → industry=None（COALESCE 保旧值）；f20/f21 有值。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "贵州茅台", "f2": 1288.0,
                         "f20": 1.6101051e12, "f21": 1.6101051e12}])
    rows = fsb._rows_from_frame(raw)
    assert len(rows) == 1
    code, name, industry, cap, shares = rows[0]
    assert (code, name, industry) == ("600519", "贵州茅台", None)
    assert cap == pytest.approx(1.6101051e12)
    assert shares == pytest.approx(1.6101051e12 / 1288.0)     # ≈12.50 亿股


def test_float_shares_absent_when_column_missing():
    """整列 f21 不存在（未request 该字段/更老的回退帧）→ None，不失行。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "甲", "f2": 100.0}])
    assert [r[4] for r in fsb._rows_from_frame(raw)] == [None]


def test_float_shares_needs_positive_price():
    """f21 空 / f2 为 0、负、NaN、不可解析 → 一律 None，不抛也不除零。"""
    raw = pd.DataFrame([
        {"f12": "600519", "f14": "甲", "f2": 100.0, "f21": None},
        {"f12": "600519", "f14": "甲", "f2": 0.0, "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": -1.0, "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": float("nan"), "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": "不是价", "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": 100.0, "f21": "不是数"},
    ])
    assert [r[4] for r in fsb._rows_from_frame(raw)] == [None] * 6


def test_float_shares_uses_pre_close_when_suspended():
    """停牌股 f2=0 → 退到 f18 昨收；f2 有值时**必须**优先用 f2，不能误用 f18。"""
    raw = pd.DataFrame([
        {"f12": "000016", "f14": "*ST康佳A", "f2": 0.0, "f18": 2.42,
         "f21": 5.923546e9},
        {"f12": "000001", "f14": "平安银行", "f2": 11.73, "f18": 11.5,
         "f21": 1.8214e11},
    ])
    rows = fsb._rows_from_frame(raw)
    assert rows[0][4] == pytest.approx(5.923546e9 / 2.42)     # 用昨收
    assert rows[1][4] == pytest.approx(1.8214e11 / 11.73)     # 用收盘价，不是 f18


def test_rows_drop_invalid_and_sanitise_nan():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": " 贵州茅台 ", "f100": " ", "f20": float("nan")},
        {"f12": "not-a-code", "f14": "坏行", "f100": None, "f20": None},
        {"f12": "000001", "f14": "", "f100": None, "f20": None},
        {"f12": "000002", "f14": "万科A", "f100": None, "f20": "not-a-number"},
    ])
    rows = fsb._rows_from_frame(raw)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("600519", "贵州茅台", None, None),
        ("000002", "万科A", None, None),
    ]
    assert [r[4] for r in rows] == [None, None]           # 无 f21 列


class _Rec:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _Rec:
        return self

    def execute(self, sql) -> None:
        self.statements.append(sql)


def test_upsert_sql_keeps_old_values_on_missing_fields(monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(fsb, "transaction", lambda: rec)
    assert fsb.upsert_stock_basic([("600519", "贵州茅台", "酿酒行业", 1.9e12, 1.25e9),
                                   ("000001", "平安银行", None, None, None)]) == 2

    sql = rec.statements[0].as_string(None)          # 整条语句渲染出来读（铁律）
    assert ("INSERT INTO stock_basic (code, name, market, industry, market_cap,"
            " float_shares, in_market)") in sql
    assert "'酿酒行业'" in sql and "1900000000000" in sql and "1250000000" in sql
    assert "COALESCE(EXCLUDED.industry, stock_basic.industry)" in sql
    assert "COALESCE(EXCLUDED.market_cap, stock_basic.market_cap)" in sql
    assert "COALESCE(EXCLUDED.float_shares, stock_basic.float_shares)" in sql
    assert "in_market=true" in sql
    assert "updated_at=now()" in sql
    # 缺失字段渲染成 NULL（不是 'None'），且每行都带 in_market=true
    assert sql.count("true)") == 2
    assert "NULL" in sql


def test_upsert_empty_rows_is_noop(monkeypatch):
    monkeypatch.setattr(fsb, "transaction", lambda: pytest.fail("空行不该开事务"))
    assert fsb.upsert_stock_basic([]) == 0


def test_em_fields_requests_cap_and_float_market_cap():
    """f20/f21 必须在请求列表里 —— 漏了就是 COALESCE 保护下静默永远 NULL。"""
    assert "f20" in fsb.EM_FIELDS
    assert "f21" in fsb.EM_FIELDS
    assert "f2" in fsb.EM_FIELDS          # 反推流通股本要用
    assert "f18" in fsb.EM_FIELDS         # 停牌股的价格兜底


def test_market_prefix_rule():
    assert fsb._market("600519") == "sh"
    assert fsb._market("000001") == "sz"
    assert fsb._market("300750") == "sz"
