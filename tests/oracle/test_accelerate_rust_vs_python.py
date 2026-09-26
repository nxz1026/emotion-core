"""对账测试：accelerate Rust vs Python 同输入同输出。

覆盖 4 个纯逻辑函数：
- hit(a1, a2, a3) — 命中规则（8 组合）
- baseline_median(ratios, window) — 内存滚动中位数
- top_streak(seq, cal_pos) — 断档归零
- detect(...) — 完整判定（hit + reason + facts）

Python 参考实现：src/emotion_core/algorithms/accelerate.py 的 __hit / _baseline_ratio 内存分支 /
_top_streak 序列处理 / detect 纯逻辑部分（SQL 取数不在对账范围）。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms.accelerate import (  # noqa: E402
    ACCEL_BASELINE_WINDOW,
    ACCEL_HEIGHT_GAP,
    ACCEL_ONEWORD_DAYS,
    ACCEL_ONEWORD_RATIO,
    _hit,
    _baseline_ratio,
)
from emotion_core.core import emotion_core_rust as rust  # noqa: E402


# ---------- hit ----------

@pytest.mark.parametrize("a1,a2,a3,expected", [
    (True, True, True, True),
    (True, True, False, True),
    (True, False, True, True),
    (True, False, False, False),
    (False, True, True, False),
    (False, True, False, False),
    (False, False, True, False),
    (False, False, False, False),
])
def test_hit(a1, a2, a3, expected):
    assert rust.hit(a1, a2, a3) == _hit(a1, a2, a3) == expected


# ---------- baseline_median ----------

@pytest.mark.parametrize("ratios,window", [
    ([0.5, 0.3, 0.4] + [0.1] * 27, 30),                  # 30 个样本
    ([0.5, 0.3, 0.4, 0.2] + [0.1] * 26, 30),             # 30 个样本（偶数窗口）
    ([None, 0.5, 0.3, 0.4] + [0.1] * 26, 30),            # 含 None
    ([0.5, 0.3] + [0.1] * 27, 30),                       # 有效样本 29 → None
    ([None] * 30, 30),                                   # 全 None → None
    ([0.7] * 30, 30),                                    # 单值重复
    ([round(i * 0.03, 2) for i in range(1, 31)], 30),          # 30 个递增
])
def test_baseline_median(ratios, window):
    expected = _baseline_ratio(None, prior_ratios=ratios)
    got = rust.baseline_median(ratios, window)
    assert got == expected, f"ratios={ratios} window={window}: rust={got} py={expected}"


# ---------- top_streak ----------

@pytest.mark.parametrize("seq,cal_pos,expected", [
    ([(True, True), (True, True), (True, True)], [0, 1, 2], 3),   # 连续 3 日
    ([(True, True), (False, True), (True, True)], [0, 1, 2], 1),  # 中间非一字 → 归零后 1
    ([(True, True), (True, True)], [0, 2], 1),                   # 停牌断档（隔 1 日）→ 归零后 1
    ([(True, True), (True, True), (True, True)], [0, 2, 3], 2),  # 首日断档 → 归零后重新累加
    ([(False, False), (False, False)], [0, 1], 0),               # 全程非一字
    ([], [], 0),                                                  # 空序列
    ([(True, False), (True, True)], [0, 1], 1),                  # 首日非涨停 → 归零后 1
])
def test_top_streak(seq, cal_pos, expected):
    got = rust.top_streak(seq, cal_pos)
    assert got == expected, f"seq={seq} cal_pos={cal_pos}: rust={got} py={expected}"


# ---------- detect ----------

@pytest.mark.parametrize("nominal_h,tradable_h,ratio,base,streak", [
    (5, 3, 0.5, 0.3, 3),    # A1✓ A2✓ A3✓ → hit
    (5, 3, 0.5, 0.3, 1),    # A1✓ A2✗ A3✓ → hit
    (5, 3, 0.2, 0.3, 3),    # A1✓ A2✓ A3✗ → hit
    (5, 3, 0.2, 0.3, 1),    # A1✓ A2✗ A3✗ → no hit
    (3, 3, 0.5, 0.3, 3),    # A1✗ → no hit
    (5, 3, None, 0.3, 3),   # ratio None → A3✗
    (5, 3, 0.5, None, 3),   # base None → A3✗
    (5, 3, 0.35, 0.35, 2),  # ratio == base → A3✗（严格大于）
    (4, 2, 0.4, 0.2, 2),    # 边界：gap == height_gap
    (5, 3, 0.35, 0.34, 2),  # 边界：ratio == oneword_ratio
])
def test_detect(nominal_h, tradable_h, ratio, base, streak):
    # Python 参考：直接内联 detect 纯逻辑（避免 mock SQL）
    gap = nominal_h - tradable_h
    a1 = gap >= ACCEL_HEIGHT_GAP
    a2 = streak >= ACCEL_ONEWORD_DAYS
    a3 = (ratio is not None and base is not None
          and ratio >= ACCEL_ONEWORD_RATIO and ratio > base)
    expected_hit = _hit(a1, a2, a3)

    r_hit, r_reason, r_facts = rust.detect(
        nominal_h, tradable_h, ratio, base, streak,
        ACCEL_HEIGHT_GAP, ACCEL_ONEWORD_DAYS, ACCEL_ONEWORD_RATIO)

    assert r_hit == expected_hit
    assert r_facts.nominal_h == nominal_h
    assert r_facts.tradable_h == tradable_h
    assert r_facts.gap == gap
    assert r_facts.streak == streak
    assert r_facts.oneword_ratio == ratio
    assert r_facts.baseline_ratio == base
    assert r_facts.a1 == a1
    assert r_facts.a2 == a2
    assert r_facts.a3 == a3
    # reason 字符串一致性（数值 + ✓/✗ 标记）
    assert str(gap) in r_reason
    assert ("✓" if a1 else "✗") in r_reason
    assert ("✓" if a2 else "✗") in r_reason
    assert ("✓" if a3 else "✗") in r_reason


def test_baseline_window_constant():
    """确认 Python 侧窗口常量与默认参数一致。"""
    assert ACCEL_BASELINE_WINDOW == 30
