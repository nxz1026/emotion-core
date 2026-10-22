"""`cpt_intraday` provider 单测：读 CPT 的封板结论表（ADR 0003 §H）。

表是 `public.intraday_seal_snapshot`（5 列：at / code / sealed / bid1_lots /
limit_price），不是 §G 里的 13 列 `intraday_quote`。读表三条铁律见
`Chan_Pattern_Trader/docs/intraday-seal-contract.md` §3：

1. 没行 = 判不了，不是「没封板」
2. `sealed=False` 是采到了且这轮没封，要和「没采到」分开
3. 按 `at` 取最新一轮，不按 code 单点查最新
"""

from __future__ import annotations

import pytest

from emotion_core.data.providers import cpt_intraday as ci


class _Col:
    """psycopg `cur.description` 的一列替身（只要 `.name`）。"""

    def __init__(self, name):
        self.name = name


class _FakeCur:
    def __init__(self, rows, names):
        self._rows = rows
        self.description = [_Col(n) for n in names]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, names):
        self._rows = rows
        self._names = names

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCur(self._rows, self._names)


def _fake_conn(rows, names):
    def factory(*a, **k):
        return _FakeConn(rows, names)

    return factory


class TestTableMissing:
    def test_returns_none_when_table_absent(self, monkeypatch):
        """表还没建是**正常状态**，不是故障。"""
        import psycopg

        def boom(*a, **k):
            raise psycopg.errors.UndefinedTable(
                'relation "intraday_seal_snapshot" does not exist'
            )

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt(["600519"]) is None
        assert ci.fetch_round_from_cpt(["600519"]) is None

    def test_other_error_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt() is None


class TestRows:
    def test_empty_table_returns_empty_dict(self, monkeypatch):
        """表在、但一轮都没有 → `{}`，不是 None（区分「没建表」与「还没采」）。"""
        monkeypatch.setattr(ci, "connect_ro", _fake_conn([], list(ci._COLUMNS)))
        assert ci.fetch_quotes_from_cpt() == {}

    def test_records_and_round_at(self, monkeypatch):
        at = "2026-10-22 09:20:36+00:00"
        rows = [
            (at, "600293", True, 847522.54, 10.19),
            (at, "002724", False, None, 10.19),
        ]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, list(ci._COLUMNS)))
        got = ci.fetch_round_from_cpt()
        assert got is not None
        round_at, out = got
        assert round_at == at
        assert set(out) == {"600293", "002724"}
        assert out["600293"]["sealed"] is True
        # 单位是**手**，不是股 —— 用的时候别直接拿去比 SEAL_BID1_VOL_MIN（股）
        assert out["600293"]["bid1_lots"] == 847522.54
        assert out["002724"]["bid1_lots"] is None

    def test_filter_by_codes(self, monkeypatch):
        at = "2026-10-22 09:20:36+00:00"
        rows = [
            (at, "600293", True, 100.0, 10.19),
            (at, "000002", False, 5.0, 3.51),
        ]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, list(ci._COLUMNS)))
        out = ci.fetch_quotes_from_cpt(["000002"])
        assert out is not None
        assert set(out) == {"000002"}

    def test_round_at_survives_code_filter(self, monkeypatch):
        """过滤不该抹掉「这一轮存在」—— `round_at` 仍要给出。"""
        at = "2026-10-22 09:20:36+00:00"
        rows = [(at, "600293", True, 100.0, 10.19)]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, list(ci._COLUMNS)))
        got = ci.fetch_round_from_cpt(["999999"])
        assert got == (at, {})


class TestSealedState:
    """契约 §3 第 1、2 条 + §7 最后一行的三态判定。"""

    def test_no_row_is_unknown_not_false(self):
        """没行 = 判不了，**不是**没封板。"""
        assert ci.sealed_state(None) is None

    def test_sealed_true(self):
        assert ci.sealed_state({"sealed": True, "bid1_lots": 100.0}) is True

    def test_sealed_false_with_bid_is_real_open(self):
        """采到了、这轮没封，且档位在 → 是真开板证据。"""
        assert ci.sealed_state({"sealed": False, "bid1_lots": 5.0}) is False

    def test_sealed_false_without_bid_is_unknown(self):
        """停牌 / 缺档位：`sealed=False` 且 `bid1_lots` 为 NULL → 判不了。"""
        assert ci.sealed_state({"sealed": False, "bid1_lots": None}) is None

    def test_missing_sealed_is_unknown(self):
        assert ci.sealed_state({"bid1_lots": 5.0}) is None


class TestBid1VolumeUnit:
    def test_lots_to_shares(self):
        assert ci.bid1_vol_shares({"bid1_lots": 847522.54}) == pytest.approx(84752254.0)

    def test_missing_is_none_not_zero(self):
        assert ci.bid1_vol_shares({"bid1_lots": None}) is None
        assert ci.bid1_vol_shares(None) is None

    def test_shares_per_lot_constant(self):
        assert ci.SHARES_PER_LOT == 100


def test_select_sql_only_takes_latest_round():
    """SQL 必须按 `at = max(at)` 取整轮 —— 不能按 code 单点查最新（§H.3 第 3 条）。"""
    sql = " ".join(ci._SELECT_SQL.split()).lower()
    assert "at = (select max(at)" in sql
    assert "order by code" in sql


def test_columns_match_h2_ddl():
    """列清单必须与 ADR §H.2 / 契约 §1 的表结构逐字对齐（只有 5 列）。"""
    assert ci._COLUMNS == ("at", "code", "sealed", "bid1_lots", "limit_price")
    assert ci.CPT_INTRADAY_TABLE == "intraday_seal_snapshot"


def test_docstring_states_three_reading_rules():
    """三条读表铁律必须写在模块 docstring 里（改代码时别把它删了）。"""
    doc = ci.__doc__ or ""
    assert "没行 = 判不了" in doc
    assert "sealed=False` 是采到了且这一轮没封" in doc
    assert "按 `at` 分组取最新一轮" in doc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
