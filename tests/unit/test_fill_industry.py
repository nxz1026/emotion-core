"""`stock_basic.industry` 回填：SQL 渲染、分块、分批增量写、dry-run。不联网。"""

from __future__ import annotations

from typing import Self

from emotion_core.data import fill_industry as fi


class _Rec:
    def __init__(self, rowcount: int = 0) -> None:
        self.statements: list[object] = []
        self.rowcount = rowcount

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _Rec:
        return self

    def execute(self, sql) -> None:
        self.statements.append(sql)


# ── upsert_industry ──────────────────────────────────────────


def test_upsert_industry_sql_updates_only_existing_codes(monkeypatch):
    rec = _Rec(rowcount=2)
    monkeypatch.setattr(fi, "transaction", lambda: rec)

    assert fi.upsert_industry([("600519", "酿酒行业"), ("000001", "银行")]) == 2

    sql = rec.statements[0].as_string(None)  # 整条语句渲染出来读（铁律）
    assert "UPDATE stock_basic SET industry = v.industry" in sql
    assert "updated_at = now()" in sql
    assert "FROM (VALUES" in sql
    assert "WHERE stock_basic.code = v.code" in sql
    assert "'600519'" in sql and "'酿酒行业'" in sql
    assert "'000001'" in sql and "'银行'" in sql


def test_upsert_industry_empty_rows_is_noop(monkeypatch):
    def _boom():
        raise AssertionError("空列表不该开事务")

    monkeypatch.setattr(fi, "transaction", _boom)
    assert fi.upsert_industry([]) == 0


def test_upsert_industry_chunks_large_input(monkeypatch):
    """全量 5221 只写成一条 SQL 会到 200KB 量级，必须分块提交。"""
    recs: list[_Rec] = []

    def _tx() -> _Rec:
        rec = _Rec(rowcount=1)
        recs.append(rec)
        return rec

    monkeypatch.setattr(fi, "transaction", _tx)
    n = fi.upsert_industry([(f"{i:06d}", "制造业") for i in range(fi._CHUNK + 5)])
    assert n == 2, "1005 行应分 2 块、每块贡献一次 rowcount"
    assert len(recs) == 2


# ── backfill ─────────────────────────────────────────────────


def test_fill_industry_uses_sorted_rows(monkeypatch):
    monkeypatch.setattr(
        fi.sina_industry,
        "fetch_industry_map",
        lambda: {"300750": "电池", "000001": "银行", "600519": "酿酒行业"},
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(fi, "upsert_industry", lambda rows: seen.update(rows=rows) or 3)

    assert fi.backfill("sina") == 3
    assert seen["rows"] == [("000001", "银行"), ("300750", "电池"), ("600519", "酿酒行业")]


def test_backfill_cninfo_writes_each_batch(monkeypatch):
    """巨潮逐股要跑 20 分钟以上：必须抓一批写一批，不能攒完再写。"""
    monkeypatch.setattr(
        fi, "cninfo_targets", lambda **kw: ["000001", "000002", "000003", "000004", "000005"]
    )
    batches: list[list[str]] = []

    def fake_map(chunk, **kw):
        batches.append(list(chunk))
        return {c: "制造业" for c in chunk}

    monkeypatch.setattr(fi.cninfo_industry, "fetch_industry_map", fake_map)
    wrote: list[list[tuple[str, str]]] = []
    monkeypatch.setattr(fi, "upsert_industry", lambda rows: wrote.append(list(rows)) or len(rows))

    assert fi.backfill("cninfo", batch=2) == 5
    assert batches == [["000001", "000002"], ["000003", "000004"], ["000005"]]
    assert [len(b) for b in wrote] == [2, 2, 1], "每批独立落库"


def test_backfill_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(fi, "cninfo_targets", lambda **kw: ["000001"])
    monkeypatch.setattr(
        fi.cninfo_industry, "fetch_industry_map", lambda chunk, **kw: {c: "制造业" for c in chunk}
    )

    def _boom(rows):
        raise AssertionError("dry-run 不许写库")

    monkeypatch.setattr(fi, "upsert_industry", _boom)
    assert fi.backfill("cninfo", dry_run=True) == 0


def test_backfill_skips_stocks_without_industry(monkeypatch):
    """取不到行业的股票不该进 UPDATE 语句。"""
    monkeypatch.setattr(fi, "cninfo_targets", lambda **kw: ["000001", "000002"])
    monkeypatch.setattr(
        fi.cninfo_industry, "fetch_industry_map", lambda chunk, **kw: {"000001": "货币金融服务"}
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(fi, "upsert_industry", lambda rows: seen.update(rows=rows) or len(rows))

    fi.backfill("cninfo", batch=10)
    assert seen["rows"] == [("000001", "货币金融服务")]


# ── cninfo_targets ───────────────────────────────────────────


def test_cninfo_targets_only_missing_and_limit(monkeypatch):
    monkeypatch.setattr(fi, "missing_codes", lambda: ["600519", "000001", "300750"])
    monkeypatch.setattr(
        fi, "all_codes", lambda: (_ for _ in ()).throw(AssertionError("不应查全量"))
    )

    assert fi.cninfo_targets(only_missing=True) == ["600519", "000001", "300750"]
    assert fi.cninfo_targets(only_missing=True, limit=2) == ["600519", "000001"]


def test_cninfo_targets_full_market_ignores_missing(monkeypatch):
    monkeypatch.setattr(fi, "all_codes", lambda: ["000001", "000002"])
    monkeypatch.setattr(
        fi, "missing_codes", lambda: (_ for _ in ()).throw(AssertionError("不应查缺口"))
    )
    assert fi.cninfo_targets(only_missing=False) == ["000001", "000002"]
