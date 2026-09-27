"""晋级率落库双实现一致性（§9 第 6 条）。

`data/loader.py: upsert_promotion_day` 与 `algorithms/promotion.py: _INSERT_SQL`
写同一张 `promotion_day`。此前 loader 那份漏列 `promote_from`、ON CONFLICT 也不更新它，
两条路径会给出不同结果（而且 loader 那份当时还无人调用）。现两份列集/更新集必须逐列相同，
且缺 `promote_from` 时**响亮报错**（KeyError）而不是静默写 NULL。
"""
from __future__ import annotations

from datetime import date
from typing import Self

import pytest

from emotion_core.algorithms import promotion
from emotion_core.data import loader

D = date(2026, 9, 25)


def _insert_columns(sql: str) -> tuple[list[str], list[str]]:
    """解析 `INSERT INTO promotion_day (…列…) VALUES … DO UPDATE SET a=EXCLUDED.a,…`。"""
    head, _, tail = sql.partition("VALUES")
    cols = [c.strip() for c in head[head.index("(") + 1:head.rindex(")")].split(",")]
    upd = [p.strip().split("=")[0].strip()
           for p in tail.split("DO UPDATE SET", 1)[1].split(",")]
    return cols, upd


class _Rec:
    """loader.transaction 替身：记录 SQL 与行数据。"""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.data: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql, params=()) -> None:
        self.sql.append(sql)

    def executemany(self, sql, rows) -> None:
        self.sql.append(sql)
        self.data.extend(rows)


def _row(**kw) -> dict:
    base = {"date": D, "layer": "1->2", "promote_from": 10,
            "promote_nominal": 3, "promote_exchange": 2, "rate_nominal": 0.3,
            "rate_exchange": 0.2, "divergence": None, "fail_perf": 1.5}
    base.update(kw)
    return base


@pytest.fixture
def rec(monkeypatch) -> _Rec:
    r = _Rec()
    monkeypatch.setattr(loader, "transaction", lambda: r)
    return r


def test_loader_column_set_matches_algorithms(rec):
    assert loader.upsert_promotion_day([_row()]) == 1
    insert_sql = next(s for s in rec.sql if "INSERT INTO promotion_day" in s)
    loader_cols, loader_upd = _insert_columns(insert_sql)
    algo_cols, algo_upd = _insert_columns(promotion._INSERT_SQL)

    assert loader_cols == algo_cols, "两处写入的列集必须逐列相同"
    assert loader_upd == algo_upd, "两处 ON CONFLICT 的更新集必须逐列相同"
    assert "promote_from" in loader_cols
    assert "promote_from=EXCLUDED.promote_from" in insert_sql


def test_row_data_carries_promote_from(rec):
    loader.upsert_promotion_day([_row(promote_from=7)])
    assert rec.data == [(D, "1->2", 7, 3, 2, 0.3, 0.2, None, 1.5)]


def test_missing_promote_from_fails_loud(rec):
    bad = _row()
    del bad["promote_from"]
    with pytest.raises(KeyError):
        loader.upsert_promotion_day([bad])


def test_empty_rows_touch_nothing(rec):
    assert loader.upsert_promotion_day([]) == 0
    assert rec.sql == []
