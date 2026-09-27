"""C2 口径守护：pre_close 必须存列，禁止用 LAG(close) 重算。

裁决（docs/13 §S2 C2）：昨收价是涨停判定的基准，必须来自数据层的 `daily_bar.pre_close`
列。用窗口函数 LAG(close) 现算会在除权日给出错误基准（前收被除权调整过，
不等于上一日收盘），且复权因子不在本库口径内。

三层证据：
1. `data/schema.py` 的 daily_bar DDL 必须含 pre_close；
2. `algorithms/derive.py` 必须读 `b.pre_close` 且全文不得出现 LAG；
3. lkl 导出样本（tests/oracle/fixtures/daily_bar_sample.json）里每根 bar 都带
   pre_close，且 ≥99% 等于前一交易日收盘（少数不等即除权日——正是不能靠 LAG 的原因）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from emotion_core.algorithms import derive
from emotion_core.data import schema

FIXTURE = Path(__file__).resolve().parents[1] / "oracle" / "fixtures" / "daily_bar_sample.json"


def test_daily_bar_ddl_stores_pre_close():
    ddl = schema.DDL["daily_bar"]
    assert "pre_close" in ddl, "daily_bar 必须存 pre_close 列"


def test_derive_reads_pre_close_column_not_lag():
    sql = derive.render_sql()
    assert "b.pre_close" in sql, "涨停价基准必须取 daily_bar.pre_close"
    assert "b.pre_close > 0" in sql, "无昨收（首日/停牌复牌首日）的行必须排除，不能补 0"
    assert "LAG" not in sql.upper(), "禁止 LAG(close) 重算昨收（除权日会错）"
    assert "LAG" not in schema.DDL["daily_bar"].upper()


def test_derive_sql_rejects_nan_pre_close():
    """NaN 门：Postgres 的 numeric NaN **等于 NaN 且大于一切非 NaN**，所以
    `pre_close > 0` 对 NaN 成立、`pre_close = pre_close` 也成立——只有显式比较
    'NaN' 能挡住。实测库内 226 行新股首日 pre_close=NaN，全区间 derive 会直接
    崩 `FeatureNotSupported: cannot convert NaN to bigint`（单日跑恰好没命中）。"""
    sql = derive.render_sql()
    assert "b.pre_close <> 'NaN'::numeric" in sql
    assert derive._finite("b.close") == "(b.close IS NOT NULL AND b.close <> 'NaN'::numeric)"
    for col in ("b.pre_close", "b.close", "b.high", "b.low"):
        assert f"{col} <> 'NaN'::numeric" in sql, f"{col} 缺 NaN 门"


def test_lkl_sample_carries_pre_close_and_shows_ex_rights_days():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert rows, "样本为空"
    assert all(r.get("pre_close") for r in rows), "样本存在缺 pre_close 的行"

    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)
    pairs = mismatched = 0
    for rs in by_code.values():
        rs.sort(key=lambda r: r["date"])
        for prev, cur in pairwise(rs):
            pairs += 1
            if cur["pre_close"] != prev["close"]:
                mismatched += 1
    assert pairs > 1000
    # 几乎全等 → 正常日 pre_close == 前收盘；少数不等 → 除权日，正是 LAG 会给错的地方
    assert mismatched / pairs < 0.01, f"异常比例 {mismatched}/{pairs}"
