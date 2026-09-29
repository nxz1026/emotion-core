"""DSA 策略观察 · 策略目录定位与加载测试。

回归背景（0ba6995 migrate LKL strategy observer natively into emotion-core）：
`runner.STRATEGY_DIR` 用了 `parents[3]`，在旧 LKL 布局下正确；迁移到
`src/emotion_core/services/strategy/runner.py` 后目录深度变了，实际解析到
`src/llm/strategies`（不存在）。`load_strategies()` 对不存在的目录
`glob("*.yaml")` 返回空列表且不抛异常，于是 `run_for_date()` 一路走到
「skills=0 → 跳过」分支静默返回 0：15 个 DSA 策略一行都没跑过，无日志告警。

这里锁三件事：
- 层级语义：STRATEGY_DIR 的锚点必须是包根（parents[2]），不是 src（parents[3]）
- 目录与内容：目录存在、15 个 YAML、全部能加载且 enabled
- 许可证：每个 YAML 保留 MIT 出处署名（DSA 移植合规）
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from datetime import date
from pathlib import Path

import httpx

from emotion_core.services.strategy import runner
from emotion_core.services.strategy.loader import load_strategies
from emotion_core.utils.config import CONFIG as REAL_CONFIG

# ── 独立锚点：从模块文件位置反推包根，不复用实现里的表达式 ──────────────
PKG_ROOT = Path(runner.__file__).resolve().parents[2]
EXPECTED_DIR = PKG_ROOT / "llm" / "strategies"
EXPECTED_YAML_COUNT = 15
MIT_MARKER = "Ported from daily_stock_analysis (MIT, Copyright (c) 2026 ZhuLinsen)"


class TestStrategyDirAnchor:
    """STRATEGY_DIR 的层级锚点必须落在包根，而非仓库 src/。"""

    def test_pkg_root_anchor_is_the_package(self):
        """parents[2] 应正好是 emotion_core 包根；parents[3] 会是 src。"""
        assert PKG_ROOT.name == "emotion_core", (
            f"锚点算错层级：{PKG_ROOT} 不是 emotion_core 包根。"
            "若改回 parents[3] 会指向仓库 src/，策略目录随即失效。"
        )

    def test_strategy_dir_matches_expected(self):
        """STRATEGY_DIR 必须等于 <包根>/llm/strategies。"""
        assert runner.STRATEGY_DIR == EXPECTED_DIR

    def test_strategy_dir_exists(self):
        """核心回归锁：目录必须存在。

        bug 版本这里解析到 src/llm/strategies，exists() 为 False，
        使 load_strategies 静默返回空表 —— 本断言直接拦截该状态。
        """
        assert runner.STRATEGY_DIR.exists(), (
            f"策略目录不存在：{runner.STRATEGY_DIR}。"
            "load_strategies 会静默返回空列表，策略观察空跑。"
        )

    def test_strategy_dir_is_a_directory(self):
        assert runner.STRATEGY_DIR.is_dir()


class TestStrategyYamlContent:
    """策略目录内容：15 个 YAML，且保留 DSA 的 MIT 署名。"""

    def test_yaml_count(self):
        yamls = sorted(runner.STRATEGY_DIR.glob("*.yaml"))
        assert len(yamls) == EXPECTED_YAML_COUNT

    def test_every_yaml_keeps_mit_attribution(self):
        """DSA 移植合规：每个策略文件都要保留出处署名。"""
        missing = [
            p.name
            for p in sorted(runner.STRATEGY_DIR.glob("*.yaml"))
            if MIT_MARKER not in p.read_text(encoding="utf-8")
        ]
        assert not missing, f"缺少 MIT 出处署名的策略文件：{missing}"


class TestLoadStrategies:
    """load_strategies 对真实目录的加载结果。"""

    def test_loads_all_skills(self):
        skills = load_strategies(runner.STRATEGY_DIR)
        assert len(skills) == EXPECTED_YAML_COUNT

    def test_all_enabled(self):
        skills = load_strategies(runner.STRATEGY_DIR)
        disabled = [s.name for s in skills if not s.enabled]
        assert not disabled, f"被禁用的策略：{disabled}"

    def test_names_unique_and_nonempty(self):
        skills = load_strategies(runner.STRATEGY_DIR)
        names = [s.name for s in skills]
        assert all(names), "存在空策略名"
        assert len(set(names)) == len(names), f"策略名重复：{names}"

    def test_every_skill_has_instructions(self):
        """策略需有可注入 LLM 的指令体，否则等于加载了个空壳。"""
        empty = [s.name for s in load_strategies(runner.STRATEGY_DIR) if not s.instructions.strip()]
        assert not empty, f"缺少 instructions 的策略：{empty}"

    def test_missing_directory_yields_empty_without_raising(self):
        """锁定静默失败行为本身：目录不存在时不抛异常、返回空表。

        这正是 bug 长期未被发现的原因，故显式固化，避免有人误以为
        「没报错=在跑」。
        """
        assert load_strategies(Path("/nonexistent/strategies")) == []


# ══════════════════════════════════════════════════════════════════════
# A / B / C 修复的回归测试
#   A 策略饥饿：STRATEGY_MAX_LLM 配额必须跨策略轮转，不得被首个策略独吞
#   B 限流韧性：429 退避重试、失败不占配额、连续失败熔断、失败数上报
#   C 去重同源：prompt_hash 在查询侧与入库侧必须是同一个值
# ══════════════════════════════════════════════════════════════════════

TRADE_DATE = date(2026, 9, 29)


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    """构造真实 httpx.HTTPStatusError，供 `_retryable` 判 429/5xx。"""
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(str(status), request=request, response=response)


class _Reply:
    """LLMReply 替身，只暴露 runner 用到的 text / model。"""

    def __init__(self, model: str = "fake-model") -> None:
        self.text = json.dumps(
            {"action": "WATCH", "score": 60, "confidence": 0.7,
             "reason": "测试用结论", "evidence": {"k": "v"}}, ensure_ascii=False)
        self.model = model


def _who(content: str) -> tuple[str, str]:
    """从 `_prompt` 生成的文本里取回 (策略名, 代码)。"""
    name = content.split("（", 1)[1].split("）", 1)[0]
    code = content.split("股票：", 1)[1].split("\n", 1)[0]
    return name, code


class _FakeClient:
    """按脚本回放的 LLMClient 替身；记录每次尝试的策略与代码。"""

    def __init__(self, before_success: list | None = None,
                 always: Exception | None = None) -> None:
        self.script = list(before_success or [])
        self.always = always
        self.attempts = 0
        self.called: list[tuple[str, str]] = []

    def chat(self, messages, purpose="chat", json_mode=False):
        self.attempts += 1
        self.called.append(_who(messages[1]["content"]))
        if self.always is not None:
            raise self.always
        if self.script:
            nxt = self.script.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return _Reply()


class _FakeSignalStore:
    """用 `_signal_row` 产出的键回填 `_existing`，以验证两侧同源。"""

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
    """拦截 `_snapshot`（避免真写 reports/），同时留存 skipped 供断言。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, trade_date, items, skipped):
        self.calls.append((trade_date, list(items), list(skipped)))
        return Path("/tmp/fake-strategy.json")

    @property
    def skipped(self) -> list[dict]:
        return self.calls[-1][2] if self.calls else []


def _skills(names: list[str]) -> list:
    from emotion_core.services.strategy.loader import Skill

    return [Skill(name=n, display_name=n.upper(), instructions=f"{n} 指令")
            for n in names]


def _overrides(max_llm: int, cfg: dict) -> dict:
    """`_wire` 的配置默认值，允许调用方逐项覆盖（cfg 优先）。"""
    values = {"STRATEGY_ENABLED": True, "STRATEGY_MAX_LLM": max_llm,
              "STRATEGY_CALL_INTERVAL": 0.0, "STRATEGY_RETRY_BASE_DELAY": 0.0,
              "STRATEGY_MAX_ATTEMPTS": 3, "STRATEGY_MAX_CONSECUTIVE_FAILURES": 3}
    values.update(cfg)
    return values


def _wire(monkeypatch, *, skills, codes, store, client, snap,
          max_llm=6, **cfg):
    """装配一个不碰 DB / 网络 / 磁盘的 run_for_date 环境。"""
    import emotion_core.services.llm_backend as backend

    conf = dataclasses.replace(REAL_CONFIG, **_overrides(max_llm, cfg))
    monkeypatch.setattr(runner, "CONFIG", conf)
    monkeypatch.setattr(runner, "load_strategies", lambda _dir: skills)
    monkeypatch.setattr(runner, "build_universe", lambda _d: codes)
    monkeypatch.setattr(runner, "build_stock_context", lambda _d, _c: "上下文")
    monkeypatch.setattr(runner, "_stock_name", lambda _c: "测试股")
    monkeypatch.setattr(runner, "_snapshot", snap)
    monkeypatch.setattr(runner, "_existing", store.existing)
    monkeypatch.setattr(runner, "_save_batch", store.save)
    monkeypatch.setattr(backend, "LLMClient", lambda *a, **k: client)


class TestStrategyRotation:
    """A：配额必须跨策略轮转（原 `for skill: for code:` 会饿死后续策略）。"""

    def test_every_skill_gets_a_turn(self, monkeypatch):
        """3 策略 × 3 代码、预算 6：三个策略都得拿到调用。

        旧实现按 skill 外层遍历，s1 独吞 6 个配额，s3 一次都轮不到。
        """
        client, store, snap = _FakeClient(), _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1", "s2", "s3"]),
              codes=["c1", "c2", "c3"], store=store, client=client,
              snap=snap, max_llm=6)
        runner.run_for_date(TRADE_DATE)
        called = {name for name, _ in client.called}
        assert called == {"s1", "s2", "s3"}, (
            f"仅 {sorted(called)} 拿到配额，其余策略被饿死")

    def test_cap_not_exceeded(self, monkeypatch):
        client, store, snap = _FakeClient(), _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1", "s2", "s3"]),
              codes=["c1", "c2", "c3", "c4"], store=store, client=client,
              snap=snap, max_llm=5)
        runner.run_for_date(TRADE_DATE)
        assert client.attempts == 5
        capped = [s for s in snap.skipped if s["reason"] == "达到LLM调用上限"]
        assert capped, "超预算的组合应记为 skipped"


class TestPromptHashUnification:
    """C：`_existing` 与 `_signal_row` 必须用同一个 prompt_hash。"""

    def test_signal_row_uses_item_prompt_hash(self):
        item = {"code": "c1", "strategy": "s1", "prompt_hash": "HASH-X",
                "action": "PASS", "score": 10, "confidence": 0.5}
        assert runner._signal_row(TRADE_DATE, item)[3] == "HASH-X"

    def test_hash_is_prompt_digest_not_composite(self):
        """入库摘要必须是 prompt 的摘要，而不是 strategy:code:date。

        旧实现入库用复合摘要、查询用 prompt 摘要，两者永不相等，
        `_existing` 恒为 False，每次重跑都全量重打 LLM。
        """
        skill = _skills(["s1"])[0]
        _messages, prompt = runner._prompt(skill, "c1", "上下文")
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        composite = hashlib.sha256(
            f"s1:c1:{TRADE_DATE.isoformat()}".encode()).hexdigest()
        assert digest != composite, "两种算法意外相同，测试前提失效"
        item = {"code": "c1", "strategy": "s1", "prompt_hash": digest,
                "action": "PASS", "score": 10, "confidence": 0.5}
        assert runner._signal_row(TRADE_DATE, item)[3] == digest

    def test_rerun_does_not_recall_llm(self, monkeypatch):
        """去重生效的判据：同日重跑零 LLM 调用。"""
        client, store, snap = _FakeClient(), _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1", "s2"]), codes=["c1", "c2"],
              store=store, client=client, snap=snap, max_llm=4)
        first = runner.run_for_date(TRADE_DATE)
        attempts_after_first = client.attempts
        second = runner.run_for_date(TRADE_DATE)
        assert first == 4
        assert second == 0
        assert client.attempts == attempts_after_first, (
            "重跑又发起 LLM 调用：两侧 prompt_hash 不同源，_existing 恒 False")


class TestRateLimitResilience:
    """B：429 退避重试、失败不占配额、熔断、失败数上报。"""

    def test_429_is_retried_then_succeeds(self, monkeypatch, caplog):
        client = _FakeClient(before_success=[_http_status_error(429),
                                            _http_status_error(429)])
        store, snap = _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1"]), codes=["c1"],
              store=store, client=client, snap=snap, max_llm=1)
        with caplog.at_level(logging.WARNING):
            runner.run_for_date(TRADE_DATE)
        assert client.attempts == 3, "两次 429 后应重试至成功"
        assert len(store.items) == 1
        assert "限流" in caplog.text

    def test_non_retryable_error_is_not_retried(self, monkeypatch):
        client = _FakeClient(always=ValueError("boom"))
        store, snap = _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1"]), codes=["c1", "c2", "c3"],
              store=store, client=client, snap=snap, max_llm=5)
        runner.run_for_date(TRADE_DATE)
        assert client.attempts == 3, "非瞬时错误不该重试，只触发熔断"

    def test_failures_do_not_consume_budget(self, monkeypatch):
        """1 次失败 + 后续成功：预算只被成功调用消耗。"""
        client = _FakeClient(before_success=[ValueError("boom")])
        store, snap = _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1"]), codes=["c1", "c2", "c3"],
              store=store, client=client, snap=snap, max_llm=2)
        runner.run_for_date(TRADE_DATE)
        assert len(store.items) == 2, "失败调用吃掉了配额"
        assert any(s["reason"].startswith("LLM调用失败") for s in snap.skipped)

    def test_circuit_breaker_opens(self, monkeypatch):
        """持续 429 时提前止损，不把 1020 个组合逐个耗尽。"""
        client = _FakeClient(always=_http_status_error(429))
        store, snap = _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1"]),
              codes=[f"c{i}" for i in range(9)], store=store, client=client,
              snap=snap, max_llm=50, STRATEGY_MAX_CONSECUTIVE_FAILURES=2)
        runner.run_for_date(TRADE_DATE)
        distinct = {code for _name, code in client.called}
        assert distinct == {"c0", "c1"}, "熔断前只应尝试前 2 个组合"
        assert client.attempts == 6, "每个组合 3 次尝试（1 次 + 2 次退避重试）"
        assert any("熔断" in s["reason"] for s in snap.skipped)

    def test_summary_line_reports_failures(self, monkeypatch, caplog):
        client = _FakeClient(always=ValueError("boom"))
        store, snap = _FakeSignalStore(), _Snapshots()
        _wire(monkeypatch, skills=_skills(["s1"]), codes=["c1"],
              store=store, client=client, snap=snap, max_llm=1)
        with caplog.at_level(logging.INFO):
            runner.run_for_date(TRADE_DATE)
        summary = [r.getMessage() for r in caplog.records
                   if "策略观察" in r.getMessage()]
        assert summary and "失败 1" in summary[-1], summary
