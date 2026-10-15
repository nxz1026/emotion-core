"""Wind 逐日涨跌停拉取（services.limit_state_wind）：解析、二分、预算、落库守卫。"""
from __future__ import annotations

import re
from datetime import date

import pytest

from emotion_core.services import limit_state_wind as lsw

DAY = date(2025, 6, 3)
BOUNDS_RE = re.compile(r"总市值在([\d.]+)亿元到([\d.]+)亿元之间")
ABOVE_RE = re.compile(r"总市值大于([\d.]+)亿元")
BELOW_RE = re.compile(r"总市值小于([\d.]+)亿元")


def parse_bounds(question: str) -> tuple[float | None, float | None]:
    """从问句里反解出市值上下界（None = 该侧不设限）。"""
    match = BOUNDS_RE.search(question)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = ABOVE_RE.search(question)
    if match:
        return float(match.group(1)), None
    match = BELOW_RE.search(question)
    if match:
        return None, float(match.group(1))
    return None, None


def payload(pairs, cap_name="2025-06-03总市值"):
    """构造 Wind 实测回包形状：data.data[0] = {columns, rows}。"""
    return {"data": {"data": [{
        "columns": [{"name": "Wind代码"}, {"name": "证券简称"}, {"name": cap_name}],
        "rows": [[f"{code}.SH", "某股", cap] for code, cap in pairs],
    }]}}


class FakeCall:
    def __init__(self, data):
        self.data = data


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.questions: list[str] = []

    def call(self, server_type, tool_name, params):
        self.questions.append(params["question"])
        return FakeCall(self.answer(params["question"]))


class TestPure:
    def test_to_local_code(self):
        assert lsw.to_local_code("600519.SH") == "600519"
        assert lsw.to_local_code("920363.BJ") == "920363"
        assert lsw.to_local_code("") == ""

    def test_build_question_variants(self):
        assert lsw.build_question(DAY, "up") == "筛选2025-06-03涨停的A股股票"
        assert lsw.build_question(DAY, "down") == "筛选2025-06-03跌停的A股股票"
        both = lsw.build_question(DAY, "up", 20.0, 100.0)
        assert "20.00亿元到100.00亿元之间" in both
        assert "大于5.00亿元" in lsw.build_question(DAY, "up", 5.0)
        assert "小于5.00亿元" in lsw.build_question(DAY, "up", None, 5.0)

    def test_parse_rows_realistic(self):
        rows = lsw.parse_rows(payload([("600519", 11180.1376), ("000001", 2000.0)]))
        assert rows == [("600519", 11180.1376), ("000001", 2000.0)]

    def test_parse_rows_without_cap_column(self):
        raw = {"data": {"data": [{"columns": [{"name": "Wind代码"}],
                                  "rows": [["600519.SH"]]}]}}
        assert lsw.parse_rows(raw) == [("600519", None)]

    def test_parse_rows_garbage(self):
        assert lsw.parse_rows(None) == []
        assert lsw.parse_rows("not json") == []
        assert lsw.parse_rows({"data": {"data": []}}) == []

    def test_split_point_geometric(self):
        assert lsw.split_point(None, None, [1.0, 100.0]) == pytest.approx(10.0)
        assert lsw.split_point(20.0, 100.0, [50.0]) == pytest.approx((20.0 * 100.0) ** 0.5)
        assert lsw.split_point(None, 40.0, [4.0, 30.0]) == pytest.approx((4.0 * 40.0) ** 0.5)

    def test_split_point_degenerate(self):
        assert lsw.split_point(None, None, [7.0, 7.0]) is None
        assert lsw.split_point(None, None, []) is None
        assert lsw.split_point(10.0, 10.0, [10.0]) is None


class TestFetchDay:
    def test_single_call_when_under_cap(self):
        client = FakeClient(lambda q: payload([("600001", 30.0), ("600002", 40.0)]))
        result = lsw.fetch_day(client, DAY)
        assert result.calls == 1 and result.leaves == 1
        assert result.status == lsw.STATUS_OK
        assert result.codes == ["600001", "600002"]
        assert client.questions == ["筛选2025-06-03涨停的A股股票"]

    def test_recurses_by_market_cap_when_capped(self):
        universe = [(f"{600000 + i}", float(i + 1)) for i in range(150)]

        def answer(question):
            lo, hi = parse_bounds(question)
            hit = [(c, v) for c, v in universe
                   if (lo is None or v >= lo) and (hi is None or v <= hi)]
            return payload(hit[:lsw.ROW_CAP])

        client = FakeClient(answer)
        result = lsw.fetch_day(client, DAY)
        assert result.status == lsw.STATUS_OK
        assert result.codes == sorted(c for c, _ in universe)
        assert result.calls == result.leaves >= 4
        assert any("总市值" in q for q in client.questions)

    def test_budget_exhausted(self):
        universe = [(f"{600000 + i}", float(i + 1)) for i in range(150)]
        client = FakeClient(lambda q: payload(universe))
        result = lsw.fetch_day(client, DAY, max_calls=2)
        assert result.exhausted is True
        assert result.status == lsw.STATUS_EXHAUSTED
        assert result.calls == 2
        assert result.complete is False
        assert len(result.codes) == 150  # 触顶片先收下，不丢已知

    def test_truncated_when_no_market_cap_spread(self):
        flat = [(f"{600000 + i}", 50.0) for i in range(lsw.ROW_CAP)]
        client = FakeClient(lambda q: payload(flat))
        result = lsw.fetch_day(client, DAY)
        assert result.status == lsw.STATUS_TRUNCATED
        assert result.truncated is True and result.exhausted is False
        assert result.calls == 1
        assert len(result.codes) == lsw.ROW_CAP

    def test_client_error_is_captured(self):
        def boom(question):
            raise RuntimeError("Wind 额度/限流（backend_error）：账户积分余额不足")

        result = lsw.fetch_day(FakeClient(boom), DAY)
        assert result.status == lsw.STATUS_ERROR
        assert "积分余额不足" in result.error
        assert result.calls == 1

    def test_unknown_direction(self):
        with pytest.raises(ValueError):
            lsw.fetch_day(FakeClient(lambda q: payload([])), DAY, direction="sideways")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, rows):
        self.conn.sql.append((sql, rows))


class FakeConn:
    def __init__(self):
        self.sql: list[tuple] = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture
def fake_db(monkeypatch):
    conn = FakeConn()

    class _Ctx:
        def __enter__(self):
            return conn

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(lsw, "transaction", lambda: _Ctx())
    monkeypatch.setattr(lsw, "self_limit_codes", lambda day, direction: {"600001"})
    return conn


class TestPersistDay:
    def test_writes_codes_and_ledger(self, fake_db):
        result = lsw.LimitFetchResult(day=DAY, direction="up",
                                      codes=["600001", "600002"], calls=2)
        stats = lsw.persist_day(result)
        assert stats == {"n_wind": 2, "n_self": 1, "n_only_wind": 1,
                         "n_only_self": 0, "status": lsw.STATUS_OK, "calls": 2}
        joined = " ".join(str(s) for s, _ in fake_db.sql)
        assert "DELETE FROM ref_limit_state" in joined
        assert "INSERT INTO ref_limit_state_run" in joined

    def test_error_does_not_wipe_existing_rows(self, fake_db):
        result = lsw.LimitFetchResult(day=DAY, direction="up", calls=1,
                                      error="Wind 额度/限流")
        stats = lsw.persist_day(result)
        assert stats["status"] == lsw.STATUS_ERROR
        joined = " ".join(str(s) for s, _ in fake_db.sql)
        assert "DELETE FROM ref_limit_state" not in joined
        assert "INSERT INTO ref_limit_state (" not in joined
        assert "INSERT INTO ref_limit_state_run" in joined
