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

from pathlib import Path

from emotion_core.services.strategy import runner
from emotion_core.services.strategy.loader import load_strategies

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
