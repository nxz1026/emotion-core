"""stock_basic 填充（§9 第 11 条）：行业/总市值入库，缺字段保留旧值。

`diagnose_service` 查 industry / market_cap，此前两列 DDL 里都没有、服务一调就抛
UndefinedColumn。现在列已补，数据源取 EM clist 的 f100（行业名）/ f20（总市值，元）；
新浪回退源没有这两个字段，故取值必须 NULL → 由 upsert 的 COALESCE 保留库内旧值，
**不能用 None 覆写已有事实**。
"""
from __future__ import annotations

from typing import Self

import pandas as pd

from emotion_core.data import fill_stock_basic as fsb


def test_rows_from_full_em_frame():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": "贵州茅台", "f2": 1500.0,
         "f100": "酿酒行业", "f20": 1.9e12},
        {"f12": "000001", "f14": "平安银行", "f2": 11.0,
         "f100": "银行", "f20": 2.1e11},
    ])
    assert fsb._rows_from_frame(raw) == [
        ("600519", "贵州茅台", "酿酒行业", 1.9e12),
        ("000001", "平安银行", "银行", 2.1e11),
    ]


def test_rows_from_sina_frame_without_industry_columns():
    """新浪回退帧只有 _SINA_TO_EM 收录的列：f100/f20 缺列 → (None, None)，不失行。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "贵州茅台", "f2": 1500.0}])
    assert fsb._rows_from_frame(raw) == [("600519", "贵州茅台", None, None)]


def test_rows_drop_invalid_and_sanitise_nan():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": " 贵州茅台 ", "f100": " ", "f20": float("nan")},
        {"f12": "not-a-code", "f14": "坏行", "f100": None, "f20": None},
        {"f12": "000001", "f14": "", "f100": None, "f20": None},
        {"f12": "000002", "f14": "万科A", "f100": None, "f20": "not-a-number"},
    ])
    assert fsb._rows_from_frame(raw) == [
        ("600519", "贵州茅台", None, None),
        ("000002", "万科A", None, None),
    ]


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
    assert fsb.upsert_stock_basic([("600519", "贵州茅台", "酿酒行业", 1.9e12),
                                   ("000001", "平安银行", None, None)]) == 2

    sql = rec.statements[0].as_string(None)          # 整条语句渲染出来读（铁律）
    assert "INSERT INTO stock_basic (code, name, market, industry, market_cap, in_market)" in sql
    assert "'酿酒行业'" in sql and "1900000000000" in sql
    assert "COALESCE(EXCLUDED.industry, stock_basic.industry)" in sql
    assert "COALESCE(EXCLUDED.market_cap, stock_basic.market_cap)" in sql
    assert "in_market=true" in sql
    assert "updated_at=now()" in sql
    # 缺失字段渲染成 NULL（不是 'None'），且每行都带 in_market=true
    assert sql.count("true)") == 2
    assert "NULL" in sql


def test_market_prefix_rule():
    assert fsb._market("600519") == "sh"
    assert fsb._market("000001") == "sz"
    assert fsb._market("300750") == "sz"
