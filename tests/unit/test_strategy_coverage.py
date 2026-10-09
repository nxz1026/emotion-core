"""C/D 覆盖测试：候选池口径、失败补跑、显式 codes、汇总可观测性。

2026-10-09 日志巡检 C/D：
- C 候选池 = 手动自选（watchlist，优先）+ 热门池（hot_rank）；汇总行带
  跳过原因分布。
- D ``run_for_date`` 支持显式 codes（补跑历史缺口）；主循环后自动补跑
  一轮 LLM/契约失败组合，退避基数 RETRY_ROUND_DELAY（env
  EC_STRATEGY_RETRY_DELAY）。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from emotion_core.orchestration import strategy as strategy_cli
from emotion_core.services.strategy import runner
from emotion_core.utils.config import CONFIG as REAL_CONFIG

TRADE_DATE = date(2026, 10, 9)


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(str(status), request=request, response=response)


class _Reply:
    def __init__(self) -> None:
        self.text = json.dumps(
            {
                "action": "WATCH",
                "score": 60,
                "confidence": 0.7,
                "reason": "补跑成功",
                "evidence": {},
            },
            ensure_ascii=False,
        )
        self.model = "fake-model"


class _FakeClient:
    """按脚本回放的 LLMClient 替身。"""

    def __init__(self, script: list | None = None, always: Exception | None = None) -> None:
        self.script = list(script or [])
        self.always = always
        self.attempts = 0
        self.called: list[tuple] = []

    def chat(self, messages, purpose="chat", json_mode=False):
        self.attempts += 1
        self.called.append(
            (
                messages[1]["content"].split("（", 1)[1].split("）", 1)[0],
                messages[1]["content"].split("股票：", 1)[1].split("\n", 1)[0],
            )
        )
        if self.always is not None:
            raise self.always
        nxt = self.script.pop(0) if self.script else _Reply()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _Store:
    def __init__(self) -> None:
        self.keys: set[tuple] = set()
        self.items: list[dict] = []

    def save(self, trade_date, items) -> int:
        self.items.extend(items)
        for it in items:
            row = runner._signal_row(trade_date, it)
            self.keys.add((row[0], row[1], row[2], row[3]))
        return len(items)

    def existing(self, trade_date, code, strategy, prompt_hash) -> bool:
        return (trade_date, code, strategy, prompt_hash) in self.keys


class _Snapshots:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.merges: list[bool] = []

    def __call__(self, trade_date, items, skipped, *, merge: bool = False):
        self.calls.append((trade_date, list(items), list(skipped)))
        self.merges.append(merge)
        return Path("/tmp/fake-strategy.json")

    @property
    def skipped(self) -> list[dict]:
        return self.calls[-1][2] if self.calls else []


def _skills(names: list[str]) -> list:
    from emotion_core.services.strategy.loader import Skill

    return [Skill(name=n, display_name=n.upper(), instructions=f"{n} 指令") for n in names]


def _wire(
    monkeypatch,
    *,
    skills,
    codes,
    store,
    client,
    snap,
    max_llm=6,
    call_interval=0.0,
    max_attempts=3,
    retry_base_delay=0.0,
    retry_round_delay=0.0,
    max_consecutive_failures=8,
    universe=None,
    **cfg,
):
    import emotion_core.services.llm_backend as backend

    conf = dataclasses.replace(REAL_CONFIG, STRATEGY_ENABLED=True, STRATEGY_MAX_LLM=max_llm, **cfg)
    monkeypatch.setattr(runner, "CONFIG", conf)
    monkeypatch.setattr(runner, "CALL_INTERVAL", call_interval)
    monkeypatch.setattr(runner, "MAX_ATTEMPTS", max_attempts)
    monkeypatch.setattr(runner, "RETRY_BASE_DELAY", retry_base_delay)
    monkeypatch.setattr(runner, "RETRY_ROUND_DELAY", retry_round_delay)
    monkeypatch.setattr(runner, "MAX_CONSECUTIVE_FAILURES", max_consecutive_failures)
    monkeypatch.setattr(runner, "load_strategies", lambda _dir: skills)
    if universe is None:
        monkeypatch.setattr(runner, "build_universe", lambda _d: codes)
    else:
        monkeypatch.setattr(runner, "build_universe", universe)
    monkeypatch.setattr(runner, "build_stock_context", lambda _d, _c: "上下文")
    monkeypatch.setattr(runner, "_stock_name", lambda _c: "测试股")
    monkeypatch.setattr(runner, "_snapshot", snap)
    monkeypatch.setattr(runner, "_existing", store.existing)
    monkeypatch.setattr(runner, "_save_batch", store.save)
    monkeypatch.setattr(backend, "LLMClient", lambda *a, **k: client)


class TestExplicitCodes:
    """D1：显式 codes 跳过 build_universe，仍按前缀过滤/去重。"""

    def test_explicit_codes_bypass_universe(self, monkeypatch):
        def boom(_d):
            raise AssertionError("显式 codes 时不应调用 build_universe")

        client, store, snap = _FakeClient(), _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=[],
            store=store,
            client=client,
            snap=snap,
            max_llm=5,
            universe=boom,
        )
        count = runner.run_for_date(TRADE_DATE, codes=["600519", "000001", "830001", "600519"])
        assert count == 2
        assert sorted(code for _name, code in client.called) == ["000001", "600519"]

    def test_none_codes_still_uses_universe(self, monkeypatch):
        client, store, snap = _FakeClient(), _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519"],
            store=store,
            client=client,
            snap=snap,
            max_llm=5,
        )
        assert runner.run_for_date(TRADE_DATE) == 1


class TestRetryRound:
    """D3：主循环后自动补跑 LLM/契约失败组合。"""

    def test_failed_pair_is_retried_and_saved(self, monkeypatch, caplog):
        client = _FakeClient(script=[ValueError("boom"), _Reply()])
        store, snap = _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519"],
            store=store,
            client=client,
            snap=snap,
            max_llm=2,
        )
        with caplog.at_level(logging.INFO):
            count = runner.run_for_date(TRADE_DATE)
        assert count == 1
        assert len(store.items) == 1
        assert snap.skipped == [], "补跑成功后不应再有 skipped 记录"
        assert client.attempts == 2, "首轮失败 + 补跑成功"
        assert "失败补跑" in caplog.text

    def test_contract_failure_is_retried(self, monkeypatch):
        bad = _Reply()
        bad.text = "不是 JSON"
        client = _FakeClient(script=[bad, _Reply()])
        store, snap = _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519"],
            store=store,
            client=client,
            snap=snap,
            max_llm=2,
        )
        assert runner.run_for_date(TRADE_DATE) == 1
        assert len(store.items) == 1
        assert snap.skipped == []

    def test_retry_keeps_skipped_when_still_failing(self, monkeypatch):
        client = _FakeClient(always=ValueError("boom"))
        store, snap = _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519"],
            store=store,
            client=client,
            snap=snap,
            max_llm=2,
        )
        assert runner.run_for_date(TRADE_DATE) == 0
        assert [s["reason"].split(":", 1)[0] for s in snap.skipped] == ["LLM调用失败"]

    def test_retry_uses_longer_backoff_base(self, monkeypatch):
        client = _FakeClient(always=_http_status_error(429))
        store, snap = _Store(), _Snapshots()
        sleeps: list[float] = []
        monkeypatch.setattr(runner.time, "sleep", lambda seconds: sleeps.append(seconds))
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519"],
            store=store,
            client=client,
            snap=snap,
            max_llm=5,
            max_attempts=2,
            retry_base_delay=1.0,
            retry_round_delay=7.0,
        )
        runner.run_for_date(TRADE_DATE)
        assert sleeps[0] == 1.0, "首轮退避基数应为 RETRY_BASE_DELAY"
        assert 7.0 in sleeps[1:], "补跑退避基数应为 RETRY_ROUND_DELAY"

    def test_retry_skipped_when_quota_already_full(self, monkeypatch, caplog):
        client = _FakeClient(script=[ValueError("boom"), _Reply()])
        store, snap = _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519", "000001"],
            store=store,
            client=client,
            snap=snap,
            max_llm=1,
        )
        with caplog.at_level(logging.INFO):
            count = runner.run_for_date(TRADE_DATE)
        assert count == 1, "配额已被 000001 用满，补跑不再发起"
        assert client.attempts == 2
        assert "失败补跑" not in caplog.text

    def test_retry_respects_circuit_breaker(self, monkeypatch):
        client = _FakeClient(always=_http_status_error(429))
        store, snap = _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=[f"6005{i:02d}" for i in range(9)],
            store=store,
            client=client,
            snap=snap,
            max_llm=50,
            max_attempts=1,
            max_consecutive_failures=2,
        )
        runner.run_for_date(TRADE_DATE)
        assert client.attempts == 2, "熔断开启时补跑轮不得再发起调用"


class TestSummaryObservability:
    """C3：汇总行带跳过原因分布（按次数降序）。"""

    def test_reason_distribution_aggregates_and_sorts(self):
        skipped = [
            {"strategy": "s", "code": "c", "reason": "LLM调用失败:boom"},
            {"strategy": "s", "code": "c", "reason": "LLM调用失败:bang"},
            {"strategy": "s", "code": "c", "reason": "返回不符合输出契约"},
            {"strategy": "s", "code": "c", "reason": "达到LLM调用上限"},
        ]
        assert runner._reason_distribution(skipped) == (
            "LLM调用失败 2, 返回不符合输出契约 1, 达到LLM调用上限 1"
        )

    def test_empty_distribution(self):
        assert runner._reason_distribution([]) == "无"

    def test_summary_line_carries_distribution(self, monkeypatch, caplog):
        client, store, snap = _FakeClient(), _Store(), _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["s1"]),
            codes=["600519", "000001"],
            store=store,
            client=client,
            snap=snap,
            max_llm=1,
        )
        with caplog.at_level(logging.INFO):
            runner.run_for_date(TRADE_DATE)
        summary = [r.getMessage() for r in caplog.records if "策略观察" in r.getMessage()][-1]
        assert "跳过 1（达到LLM调用上限 1）" in summary, summary


class TestCliCodes:
    """D2：CLI --codes 解析与传递。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("600825,000420", ["600825", "000420"]),
            ("420", ["000420"]),
            (" 600825 , 420 ", ["600825", "000420"]),
            ("600825,600825", ["600825", "600825"]),
        ],
    )
    def test_parse_valid(self, raw, expected):
        assert strategy_cli.parse_codes(raw) == expected

    @pytest.mark.parametrize("raw", ["", "sh600825", "1234567", "600825,,000420", "6008a5"])
    def test_parse_invalid(self, raw):
        with pytest.raises(ValueError):
            strategy_cli.parse_codes(raw)

    def test_main_passes_codes(self):
        with (
            patch("sys.argv", ["strategy", "--date", "2026-10-09", "--codes", "600825,420"]),
            patch("emotion_core.orchestration.strategy.run_for_date") as mock_run,
        ):
            mock_run.return_value = 2
            assert strategy_cli.main() == 0
            mock_run.assert_called_once_with(TRADE_DATE, ["600825", "000420"])

    def test_main_without_codes_passes_none(self):
        with (
            patch("sys.argv", ["strategy", "--date", "2026-10-09"]),
            patch("emotion_core.orchestration.strategy.run_for_date") as mock_run,
        ):
            mock_run.return_value = 0
            assert strategy_cli.main() == 0
            mock_run.assert_called_once_with(TRADE_DATE, None)

    def test_main_rejects_illegal_codes(self):
        with (
            patch("sys.argv", ["strategy", "--codes", "bad-code"]),
            patch("emotion_core.orchestration.strategy.run_for_date") as mock_run,
            pytest.raises(SystemExit) as excinfo,
        ):
            strategy_cli.main()
        assert excinfo.value.code == 2
        mock_run.assert_not_called()


class TestBackfillSnapshotMerge:
    """``--codes`` 补跑必须与旧快照合并，不能把当日其余组合冲掉。

    2026-10-09 日志巡检 D：补跑是局部运行，直接覆盖
    ``reports/strategy_<date>.json`` 会顺手删掉当日完整快照（其余 code 的
    items 与 skipped 分布）—— 那正是排查覆盖率的证据。
    """

    def _wire_reports_dir(self, monkeypatch, tmp_path):
        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_REPORTS_DIR=str(tmp_path))
        monkeypatch.setattr(runner, "CONFIG", conf)
        return tmp_path / f"strategy_{TRADE_DATE.isoformat()}.json"

    def _write_old(self, output):
        output.write_text(
            json.dumps(
                {
                    "date": TRADE_DATE.isoformat(),
                    "items": [
                        {"code": "000001", "strategy": "a", "score": 5},
                        {"code": "600825", "strategy": "b", "score": 9},
                    ],
                    "skipped": [
                        {"code": "000001", "strategy": "b", "reason": "达到LLM调用上限"},
                        {"code": "600825", "strategy": "b", "reason": "返回不符合输出契约"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_backfilled_pair_replaces_old_entry_and_keeps_the_rest(self, monkeypatch, tmp_path):
        output = self._wire_reports_dir(monkeypatch, tmp_path)
        self._write_old(output)

        runner._snapshot(
            TRADE_DATE, [{"code": "600825", "strategy": "b", "score": 3}], [], merge=True
        )

        payload = json.loads(output.read_text(encoding="utf-8"))
        # 补跑到的组合以本次为准，没跑到的原样保留，仍按 score 排序
        assert [(it["code"], it["strategy"], it["score"]) for it in payload["items"]] == [
            ("000001", "a", 5),
            ("600825", "b", 3),
        ]
        # 补跑成功 ⇒ 该组合不再留在 skipped；别的组合的跳过原因保留
        assert [(e["code"], e["strategy"]) for e in payload["skipped"]] == [("000001", "b")]

    def test_daily_run_still_overwrites_the_day(self, monkeypatch, tmp_path):
        output = self._wire_reports_dir(monkeypatch, tmp_path)
        self._write_old(output)

        runner._snapshot(TRADE_DATE, [{"code": "600825", "strategy": "b", "score": 1}], [])

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert [(it["code"], it["strategy"]) for it in payload["items"]] == [("600825", "b")]
        assert payload["skipped"] == []

    def test_unreadable_old_snapshot_is_not_fatal(self, monkeypatch, tmp_path):
        output = self._wire_reports_dir(monkeypatch, tmp_path)
        output.write_text("{not json", encoding="utf-8")

        runner._snapshot(
            TRADE_DATE, [{"code": "600825", "strategy": "b", "score": 1}], [], merge=True
        )

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert [(it["code"], it["strategy"]) for it in payload["items"]] == [("600825", "b")]

    def test_explicit_codes_run_asks_for_merge(self, monkeypatch):
        """``run_for_date(codes=[...])`` 必须把 merge=True 传下去；日更传 False。"""
        snap = _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["a"]),
            codes=["600825"],
            store=_Store(),
            client=_FakeClient(),
            snap=snap,
        )
        runner.run_for_date(TRADE_DATE, ["600825"])
        assert snap.merges == [True]

        snap2 = _Snapshots()
        _wire(
            monkeypatch,
            skills=_skills(["a"]),
            codes=["600825"],
            store=_Store(),
            client=_FakeClient(),
            snap=snap2,
        )
        runner.run_for_date(TRADE_DATE)
        assert snap2.merges == [False]
