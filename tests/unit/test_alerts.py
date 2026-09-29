"""告警闭环（record / record_dedup / pending / ack / push_pending_webhook）：不连库。

语义来源 lkl/services/alerts.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 alerts.py 模块文档）：写走 utils.db（record 用 INSERT..RETURNING
取实际 id）、webhook 经 `_push` 延迟导入 notify。

DB 全部桩掉（内存 alert 表按 SQL 特征分派，谓词断言锁判定：acked_at IS NULL 过滤、
id DESC 排序、ANY(%s) 批量）；真实库对账另跑（见交付记录：同输入与 lkl.services.alerts
逐字段一致）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

from emotion_core.algorithms import alerts

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
PENDING_SQL = "SELECT id, level, source, detail, created_at FROM alert"


class FakeDB:
    """内存 alert 表替身：query_df / execute 按 SQL 特征分派，未预期 SQL 直接报错。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[int, dict] = {r["id"]: dict(r) for r in (rows or [])}
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple]] = []
        self.limits: list[int] = []
        self.forbid_reads = False
        self.empty_returning = False          # 模拟 RETURNING 无行
        self._seq = max(self.rows, default=0)

    # ── query_df 替身 ──────────────────────────────────
    def query_df(self, sql: str, params=(), conn=None) -> pd.DataFrame:
        self.reads.append((sql, tuple(params)))
        if self.forbid_reads:
            raise AssertionError(f"该路径不应读库: {sql}")
        if "INSERT INTO alert" in sql:
            assert "RETURNING id" in sql, sql
            if self.empty_returning:
                return pd.DataFrame(columns=["id"])
            self._seq = max(self.rows, default=0) + 1
            self.rows[self._seq] = {"id": self._seq, "level": params[0],
                                    "source": params[1], "detail": params[2],
                                    "created_at": NOW, "acked_at": None}
            return pd.DataFrame({"id": [self._seq]})
        if "SELECT 1 FROM alert" in sql:
            assert "acked_at IS NULL" in sql, sql        # 去重只认未确认
            dup = [r for r in self.rows.values()
                   if r["source"] == params[0] and r["detail"] == params[1]
                   and r["acked_at"] is None]
            return pd.DataFrame({"1": [1]}) if dup else pd.DataFrame(columns=["1"])
        if PENDING_SQL in sql:
            assert "acked_at IS NULL" in sql, sql
            assert "ORDER BY id DESC" in sql, sql        # 新→旧
            self.limits.append(params[0])
            unacked = sorted((r for r in self.rows.values()
                              if r["acked_at"] is None),
                             key=lambda r: r["id"], reverse=True)
            return pd.DataFrame([{k: r[k] for k in
                                  ("id", "level", "source", "detail", "created_at")}
                                 for r in unacked[:params[0]]])
        if "SELECT count(*) n FROM alert" in sql:
            assert "acked_at IS NOT NULL" in sql, sql
            ids = params[0]
            n = sum(1 for i in ids
                    if i in self.rows and self.rows[i]["acked_at"] is not None)
            return pd.DataFrame({"n": [n]})
        raise AssertionError(f"未预期的 SQL: {sql}")

    # ── execute 替身 ───────────────────────────────────
    def execute(self, sql: str, params=(), conn=None) -> int:
        self.writes.append((sql, tuple(params)))
        assert "UPDATE alert SET acked_at=now()" in sql, sql
        assert "acked_at IS NULL" in sql, sql            # 不覆盖已确认时间
        ids = params[0]
        n = 0
        for i in ids:
            if i in self.rows and self.rows[i]["acked_at"] is None:
                self.rows[i]["acked_at"] = NOW
                n += 1
        return n


def row(i: int, level: str = "WARN", source: str = "pipeline",
        detail: str = "步骤失败", acked: bool = False) -> dict:
    return {"id": i, "level": level, "source": source, "detail": detail,
            "created_at": NOW, "acked_at": NOW if acked else None}


@pytest.fixture
def fake(monkeypatch) -> FakeDB:
    f = FakeDB()
    monkeypatch.setattr(alerts, "query_df", f.query_df)
    monkeypatch.setattr(alerts, "execute", f.execute)
    return f


@pytest.fixture
def pushed(monkeypatch) -> list[tuple[str, object]]:
    """webhook 接缝记录器：不触网、不依赖 notify 是否已搬运。"""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(alerts, "_push",
                        lambda md, trade_date=None: calls.append((md, trade_date)))
    return calls


class TestRecord:
    def test_inserts_and_returns_actual_id(self, fake):
        assert alerts.record("ERROR", "pipeline", "步骤回填失败") == 1
        assert fake.rows[1]["level"] == "ERROR"
        assert fake.rows[1]["source"] == "pipeline"
        assert fake.rows[1]["detail"] == "步骤回填失败"
        assert fake.rows[1]["acked_at"] is None          # 入队即待确认
        assert "RETURNING id" in fake.reads[0][0]        # id 来自 RETURNING，非 max(id) 二查

    def test_ids_are_sequential(self, fake):
        assert alerts.record("INFO", "a", "x") == 1
        assert alerts.record("WARN", "a", "y") == 2
        assert sorted(fake.rows) == [1, 2]

    def test_invalid_level_raises_without_touching_db(self, fake):
        for bad in ("warn", "FATAL", "", "WARNING"):
            with pytest.raises(ValueError, match="level 须为 INFO/WARN/ERROR"):
                alerts.record(bad, "pipeline", "x")
        assert fake.reads == [] and fake.writes == []

    def test_empty_returning_frame_yields_none(self, fake):
        fake.empty_returning = True
        assert alerts.record("WARN", "pipeline", "x") is None


class TestRecordDedup:
    def test_same_source_same_detail_pending_skips(self, fake):
        fake.rows = {7: row(7)}
        assert alerts.record_dedup("WARN", "pipeline", "步骤失败") is None
        assert list(fake.rows) == [7]                    # 未重复入队
        assert not any("INSERT" in sql for sql, _ in fake.reads)

    def test_same_source_different_detail_not_swallowed(self, fake):
        fake.rows = {7: row(7, detail="东财池缺失")}
        assert alerts.record_dedup("WARN", "review", "东财池缺失") == 8
        assert alerts.record_dedup("WARN", "review", "涨停池缺失") == 9
        assert fake.rows[9]["detail"] == "涨停池缺失"     # 不同问题必须入队

    def test_different_source_same_detail_not_swallowed(self, fake):
        fake.rows = {7: row(7, source="pipeline")}
        assert alerts.record_dedup("ERROR", "compare", "步骤失败") == 8

    def test_acked_same_detail_reenqueues(self, fake):
        fake.rows = {7: row(7, acked=True)}              # 已确认 → 允许再次告警
        assert alerts.record_dedup("WARN", "pipeline", "步骤失败") == 8
        assert fake.rows[8]["acked_at"] is None

    def test_invalid_level_raises_before_dedup_check(self, fake):
        fake.rows = {7: row(7)}                          # 即便已存在同问题也必须报错
        with pytest.raises(ValueError, match="level 须为"):
            alerts.record_dedup("fatal", "pipeline", "步骤失败")
        assert fake.reads == [] and fake.writes == []

    def test_returns_inserted_id(self, fake):
        assert alerts.record_dedup("INFO", "health", "断档 3 天") == 1


class TestPending:
    def test_newest_first_with_panel_fields(self, fake):
        fake.rows = {i: row(i, level=lv) for i, lv in
                     ((1, "INFO"), (2, "WARN"), (3, "ERROR"))}
        got = alerts.pending()
        assert [a["id"] for a in got] == [3, 2, 1]
        assert set(got[0]) == {"id", "level", "source", "detail", "created_at"}
        assert got[0]["created_at"] == NOW.isoformat()
        assert got[0] == {"id": 3, "level": "ERROR", "source": "pipeline",
                          "detail": "步骤失败", "created_at": NOW.isoformat()}

    def test_acked_rows_excluded(self, fake):
        fake.rows = {1: row(1), 2: row(2, acked=True), 3: row(3)}
        assert [a["id"] for a in alerts.pending()] == [3, 1]

    def test_default_limit_is_100(self, fake):
        alerts.pending()
        assert fake.limits == [100]

    def test_limit_forwarded_and_applied(self, fake):
        fake.rows = {i: row(i) for i in range(1, 6)}
        assert [a["id"] for a in alerts.pending(2)] == [5, 4]
        assert fake.limits == [2]

    def test_empty_queue_is_empty_list(self, fake):
        assert alerts.pending() == []


class TestAck:
    def test_empty_ids_short_circuits(self, fake):
        assert alerts.ack([]) == 0
        assert fake.reads == [] and fake.writes == []

    def test_marks_acked_and_counts_existing_only(self, fake):
        fake.rows = {1: row(1), 2: row(2)}
        assert alerts.ack([1, 999]) == 1                 # 不存在的 id 静默跳过
        assert fake.rows[1]["acked_at"] == NOW
        assert fake.rows[2]["acked_at"] is None
        assert fake.writes[0][1] == ([1, 999],)          # 批量一次，不逐个 UPDATE

    def test_batch_counts_all_acked_ids(self, fake):
        fake.rows = {i: row(i) for i in range(1, 4)}
        assert alerts.ack([1, 2, 3]) == 3
        assert len(fake.writes) == 1
        assert alerts.pending() == []

    def test_already_acked_counts_toward_return(self, fake):
        """lkl 语义：返回值 = ids 中「已确认」的条数（不是本次变更数）。"""
        fake.rows = {1: row(1, acked=True)}
        assert alerts.ack([1]) == 1
        assert fake.writes[0][1] == ([1],)

    def test_all_missing_ids_return_zero(self, fake):
        assert alerts.ack([404, 405]) == 0


class TestPushPendingWebhook:
    def test_only_warn_error_pushed(self, fake, pushed):
        fake.rows = {1: row(1, level="INFO", detail="观察"),
                     2: row(2, level="WARN", detail="断档"),
                     3: row(3, level="ERROR", detail="回填失败")}
        alerts.push_pending_webhook()
        assert len(pushed) == 1
        md, trade_date = pushed[0]
        assert trade_date is None
        assert "[WARN] pipeline: 断档" in md and "[ERROR] pipeline: 回填失败" in md
        assert "INFO" not in md
        assert "**未确认告警 2 条**" in md                  # 计数只算 WARN/ERROR

    def test_pending_window_is_20(self, fake, pushed):
        fake.rows = {i: row(i) for i in range(1, 4)}
        alerts.push_pending_webhook()
        assert fake.limits == [20]

    def test_body_capped_at_8_but_count_reports_all(self, fake, pushed):
        fake.rows = {i: row(i, level="ERROR", detail=f"失败{i}")
                     for i in range(1, 11)}
        alerts.push_pending_webhook()
        md = pushed[0][0]
        assert "**未确认告警 10 条**" in md
        assert md.count("[ERROR]") == 8                   # 正文截断 8 条

    def test_markdown_wrapped_for_notify_summary(self, fake, pushed):
        fake.rows = {1: row(1, level="ERROR")}
        alerts.push_pending_webhook()
        md = pushed[0][0]
        assert md.startswith("## ⓪ 速览\n\n")             # notify._summary 只认该段
        assert md.endswith("\n")

    def test_nothing_pending_skips_push(self, fake, pushed):
        fake.rows = {1: row(1, level="ERROR", acked=True),
                     2: row(2, level="INFO")}
        alerts.push_pending_webhook()
        assert pushed == []

    def test_push_seam_returns_md_and_none_date(self, fake, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(alerts, "_push",
                            lambda md, trade_date=None: seen.append(md) or True)
        fake.rows = {1: row(1, level="WARN")}
        assert alerts.push_pending_webhook() is None       # 推送失败/成功都不外泄
        assert len(seen) == 1

    def test_notify_missing_is_silent(self, monkeypatch, caplog):
        """services/ 与 algorithms/ 两条路都缺 notify 时 _push 返回 False，不抛。

        ★2026-09-29：原实现只 `from emotion_core.algorithms import notify`，而 notify
        实际在 services/ —— 该 ImportError 恒成立，2026-09-27~09-28 四次 daily 失败
        因此一条 webhook 都没发出去。现按 services 优先、algorithms 兜底两路试。
        """
        for mod in ("emotion_core.services.notify", "emotion_core.algorithms.notify"):
            monkeypatch.setitem(sys.modules, mod, None)
        with caplog.at_level("WARNING", logger="emotion_core.alerts"):
            assert alerts._push("## ⓪ 速览\n\nx\n", None) is False
        assert "均未找到" in caplog.text

    def test_notify_resolved_from_services_first(self, monkeypatch):
        """真实模块路径：notify 落在 services/，_push 必须能解析到它。"""
        import types
        seen: list[str] = []
        stub = types.ModuleType("emotion_core.services.notify")
        stub.push = lambda md, trade_date=None: seen.append(md) or True
        monkeypatch.setitem(sys.modules, "emotion_core.services.notify", stub)
        monkeypatch.setitem(sys.modules, "emotion_core.algorithms.notify", None)
        assert alerts._push("## ⓪ 速览\n\nx\n", None) is True
        assert seen == ["## ⓪ 速览\n\nx\n"]
