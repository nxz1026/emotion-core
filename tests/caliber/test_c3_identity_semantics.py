"""口径守护：c3 从「一票否决」改为「身份档定仓位」（W2′）。

裁决背景（实测定档 `_audit/2026-10-21-修正方案-实测定档.md`，奎爷 2026-10-21 批准）：

- 旧 c3 要求「昨日最高板组≥2 只 → 今日幸存恰 1 只 → 幸存者就是候选」。全史 427 个
  唯一换手高标日里，这个前提只出现过 27 天；可买日漏斗里 160/181 天被它单独否掉。
- 更关键的是它**没有筛选信息量**：旧口径（T 日收盘买→T+1 开盘卖）下
  passed=True（n=20）与 passed=False（n=161）的次日涨停率是 40.0% vs 42.2%，
  「次日开盘买→T+3 收盘卖」两组都恰好是 −0.83%，谁也没比谁强。
- 而身份本身是有信息量的（口径B T+1 封板价买→T+2 开盘卖）：
  身位延续 +3.27%/72%（n=212）、新插队 +2.15%/61%（n=215）、
  严格胜出 +3.38%/84%（n=21）——差别在**幅度和胜率**，不在「做不做」。

所以 c3 不再是 `Checklist` 的否决项，而是 `position_tier` 的三档输入。
本测试锁四件事：

1. `_CONDITIONS` 里 c3 槽位挂的是 `c3_identity`（恒 None = 不否决）；
2. `c3_tier` 的三档判定（严格胜出 / 身位延续 / 新插队）逐条固定；
3. 三档都不否决：`passed_of` 在 c3=None 时只看 c1/c2/c4/c5；
4. `position_tier` 的仓位矩阵与定档表一致（含分歧降级降档、冰点观望）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from emotion_core.algorithms import entry
from emotion_core.domain.signal import Checklist


def cand() -> SimpleNamespace:
    return SimpleNamespace(
        code="600001",
        date="2026-10-20",
        cont_days=5,
        is_exchange=True,
        is_top=True,
        is_sole_top=True,
    )


def ctx(*, g: int, s: int, y_codes: set[str], survivors: set[str]) -> dict:
    return {
        "sole": cand(),
        "y_comp": (g, s),
        "y_surv": (y_codes, survivors),
        "min_days": 4,
        "rows": [],
        "window": "ENHANCED",
        "diverge_min": 5.0,
        "c5_verifiable": True,
        "name": "",
        "turnover_rate": 6.0,
        "bomb_times": 0,
        "close": 10.0,
    }


# ── 1. 生产链路挂的是 c3_identity ──────────────────────────────────
def test_production_c3_slot_is_identity():
    labels = [(fn.__name__, label) for fn, _, label in entry._CONDITIONS]
    assert labels[2] == ("c3_identity", "c3 身份档（不否决）")
    # 旧的否决版函数仍然保留（SECONDARY/RECOMMEND 与 Rust 对账要用）
    assert callable(entry.c3_elimination) and callable(entry.c3_loose)


def test_every_production_label_has_a_checklist_field():
    for _, _, label in entry._CONDITIONS:
        assert label in entry._FIELD_OF, f"{label} 没有落库字段映射"


# ── 2. 三档判定 ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("g", "s", "y_codes", "survivors", "expect"),
    [
        # 昨日最高组 2 只竞争、今日恰剩候选 1 只 → 严格胜出（真正打赢了淘汰赛）
        (2, 1, {"600001", "600002"}, {"600001"}, entry.TIER_STRICT),
        # 候选本来就在昨日最高组，但昨日无人竞争 → 身位延续（不是打赢，是没输）
        (1, 1, {"600001"}, {"600001"}, entry.TIER_INHERIT),
        # 候选不在昨日最高组 → 新插队（从下面插上来抢位）
        (2, 1, {"600002", "600003"}, {"600001"}, entry.TIER_NEWCOMER),
    ],
)
def test_c3_tier(g, s, y_codes, survivors, expect):
    assert entry.c3_tier(cand(), ctx(g=g, s=s, y_codes=y_codes, survivors=survivors)) == expect


@pytest.mark.parametrize(
    ("g", "s", "y_codes", "survivors"),
    [
        (2, 1, {"600001", "600002"}, {"600001"}),
        (1, 1, {"600001"}, {"600001"}),
        (2, 1, {"600002"}, {"600001"}),
    ],
)
def test_c3_identity_never_vetoes(g, s, y_codes, survivors):
    ok, note = entry.c3_identity(cand(), ctx(g=g, s=s, y_codes=y_codes, survivors=survivors))
    assert ok is None, "身份档只描述身份，不做否决"
    assert "身份 " in note


# ── 3. 不否决 ⇒ 聚合器只看剩下四条 ──────────────────────────────────
def test_identity_none_does_not_block_passed():
    cl = Checklist(
        c1_uniqueness=True,
        c2_exchange=True,
        c3_elimination=None,
        c4_min_days=True,
        c5_strength_diverge=True,
        w1_crowding=True,
    )
    assert cl.passed is True
    # 但真条件否决时仍然失败（证明不是把 c3 槽位整体失效）
    bad = Checklist(
        c1_uniqueness=True,
        c2_exchange=False,
        c3_elimination=None,
        c4_min_days=True,
        c5_strength_diverge=True,
        w1_crowding=True,
    )
    assert bad.passed is False


# ── 4. 观察档矩阵（与定档表逐格一致；A 止血后标签是优先级不是仓位） ─────
@pytest.mark.parametrize(
    ("phase", "diverge", "tier", "weight", "label"),
    [
        # 退潮解禁后整体最肥：身位延续/新插队优先，严格胜出样本少给次选
        ("退潮", False, entry.TIER_INHERIT, 1.0, "优先"),
        ("退潮", False, entry.TIER_NEWCOMER, 1.0, "优先"),
        ("退潮", False, entry.TIER_STRICT, 0.5, "次选"),
        # 高潮：打赢淘汰赛/身位延续优先，新插队次选
        ("高潮", False, entry.TIER_STRICT, 1.0, "优先"),
        ("高潮", False, entry.TIER_INHERIT, 1.0, "优先"),
        ("高潮", False, entry.TIER_NEWCOMER, 0.5, "次选"),
        # 高潮 + 高位分歧降级：整体降档，新插队期望 +0.15% ≈ 0 → 不参与
        ("高潮", True, entry.TIER_STRICT, 0.5, "次选"),
        ("高潮", True, entry.TIER_INHERIT, 0.5, "次选"),
        ("高潮", True, entry.TIER_NEWCOMER, 0.0, "不参与"),
        # 发酵样本小，统一次选
        ("发酵", False, entry.TIER_INHERIT, 0.5, "次选"),
        ("发酵", False, entry.TIER_NEWCOMER, 0.5, "次选"),
        # 冰点不产观察标的
        ("冰点", False, entry.TIER_STRICT, 0.0, "不参与"),
        ("冰点", False, entry.TIER_INHERIT, 0.0, "不参与"),
        ("冰点", False, entry.TIER_NEWCOMER, 0.0, "不参与"),
    ],
)
def test_position_matrix(phase, diverge, tier, weight, label):
    pt = entry.position_tier(phase, diverge, tier)
    assert pt.weight == weight
    assert pt.label == label


def test_position_tier_reason_carries_expectancy():
    pt = entry.position_tier("退潮", False, entry.TIER_INHERIT)
    assert "+2.45%" in pt.expectancy
    assert "退潮" in pt.reason and "优先" in pt.reason


# ── 5. A 止血：买点/操作卡已从 entry 删除（口径B 成交假设证伪） ────────
def test_buy_point_and_plan_text_removed():
    """守死止血边界：这两个函数一旦被加回来，就等于重新开始给买点建议。"""
    assert not hasattr(entry, "buy_point")
    assert not hasattr(entry, "plan_text")
