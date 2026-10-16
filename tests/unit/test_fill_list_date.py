"""`fill_list_date`：SQL 形状（幂等守卫 + date 字面量）与不写库路径。

守的事：
- 上市日期是不可变事实，二次运行必须一行不写（``IS DISTINCT FROM`` 守卫）——
  没有它每次回填都会 UPDATE 全表、`updated_at` 全被刷新，等于天天假变更；
- ``sql.Literal(date)`` 渲染成 ``'1991-04-03'::date``，不能漏掉类型，否则 VALUES
  列表里是 text，和 date 列比较直接报 operator does not exist；
- dry-run 绝不能开事务。
"""

from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data import fill_list_date as fld


class _Rec:
    """假 connection/cursor：记录 SQL，rowcount 可控。"""

    def __init__(self, rowcount: int = 0) -> None:
        self.statements: list[object] = []
        self.rowcount = rowcount

    def __enter__(self) -> _Rec:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _Rec:
        return self

    def execute(self, sql) -> None:
        self.statements.append(sql)


def test_upsert_empty_rows_is_noop(monkeypatch):
    monkeypatch.setattr(fld, "transaction", lambda: pytest.fail("空行不该开事务"))
    assert fld.upsert_list_date([]) == 0


def test_upsert_sql_has_distinct_guard_and_date_literal(monkeypatch):
    rec = _Rec(rowcount=2)
    monkeypatch.setattr(fld, "transaction", lambda: rec)
    assert fld.upsert_list_date([("000001", date(1991, 4, 3)), ("688001", date(2019, 7, 22))]) == 2

    sql = rec.statements[0].as_string(None)
    assert "UPDATE stock_basic SET list_date = v.d, updated_at = now()" in sql
    assert "FROM (VALUES ('000001', '1991-04-03'::date)" in sql
    assert "'688001', '2019-07-22'::date" in sql
    assert "WHERE stock_basic.code = v.code" in sql
    assert "stock_basic.list_date IS DISTINCT FROM v.d" in sql


def test_upsert_returns_summed_rowcount(monkeypatch):
    """超过 _CHUNK 要分块，返回值是各块 rowcount 之和（不是最后一块）。"""
    monkeypatch.setattr(fld, "_CHUNK", 2)
    rec = _Rec(rowcount=1)
    monkeypatch.setattr(fld, "transaction", lambda: rec)
    rows = [(f"{i:06d}", date(2020, 1, 1 + i)) for i in range(5)]
    assert fld.upsert_list_date(rows) == 3  # 3 块 × rowcount 1
    assert len(rec.statements) == 3


def test_chunks_splits_evenly_and_tail():
    assert list(fld._chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(fld._chunks([], 2)) == []


def test_backfill_dry_run_never_touches_db(monkeypatch):
    monkeypatch.setattr(
        fld.exchange_list, "fetch_list_date_map", lambda: {"000001": date(1991, 4, 3)}
    )
    monkeypatch.setattr(fld, "all_codes", lambda: ["000001"])
    monkeypatch.setattr(fld, "upsert_list_date", lambda rows: pytest.fail("dry-run 不该写库"))
    assert fld.backfill(dry_run=True) == 0


def test_backfill_filters_out_codes_not_in_db(monkeypatch):
    """源里多出来的代码（如已退市/未入库）不能进 SQL，否则 UPDATE 白写。"""
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        fld.exchange_list,
        "fetch_list_date_map",
        lambda: {"000001": date(1991, 4, 3), "999999": date(2020, 1, 1)},
    )
    monkeypatch.setattr(fld, "all_codes", lambda: ["000001"])

    def _fake_upsert(rows):
        seen["rows"] = rows
        return len(rows)

    monkeypatch.setattr(fld, "upsert_list_date", _fake_upsert)
    assert fld.backfill() == 1
    assert seen["rows"] == [("000001", date(1991, 4, 3))]


def test_backfill_only_missing_uses_missing_codes(monkeypatch):
    monkeypatch.setattr(
        fld.exchange_list,
        "fetch_list_date_map",
        lambda: {"000001": date(1991, 4, 3), "600000": date(1999, 11, 10)},
    )
    monkeypatch.setattr(fld, "all_codes", lambda: pytest.fail("only_missing 不该查全量"))
    monkeypatch.setattr(fld, "missing_codes", lambda: ["600000"])
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        fld, "upsert_list_date", lambda rows: (seen.update(rows=rows), len(rows))[1]
    )
    assert fld.backfill(only_missing=True) == 1
    assert seen["rows"] == [("600000", date(1999, 11, 10))]
