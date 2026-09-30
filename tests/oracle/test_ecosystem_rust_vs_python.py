"""对账测试：ecosystem Rust vs Python 同输入同输出。

覆盖 dragon_env.py 的纯判定逻辑：
- g1_height_expanding(h_series) — 高度扩张
- g4_headroom(h_series) — 上方空间
- g2_theme_ladder(theme_rows) — 主线梯队
- g3_break_feedback(fail_perfs) — 断板反馈
- b1_acceleration(accel_hit) — 加速事件
- b2_oneword_made(a3_hit, max_divergence) — 一字制造
- b3_next_day_dump(performances, count, mode) — 核按钮
- b4_no_sector(rows) — 无板块支持
- b5_cross_theme(distinct, total, confident) — 跨题材
- verdict(goods, bads) — 裁决
- ladder_health(rows) — 梯队健康度
- promotion_strength(layers, prior) — 晋级强度

Python 参考实现：src/emotion_core/algorithms/dragon_env.py。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import dragon_env as py  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402


# ── g1_height_expanding ─────────────────────────────────────────────────────

@pytest.mark.parametrize("h_series,expected_ok", [
    ([3, 4, 5], True),           # 单调递增
    ([3, 3, 4], True),           # 不降且至少一日上升
    ([5, 4, 3], False),          # 单调递减
    ([3, 3, 3], False),          # 恒定不变（无上升）
    ([3, 5, 4], False),          # 先升后降
])
def test_g1(h_series, expected_ok):
    # Python 版：all(b >= a for a, b in pairwise(hs)) and hs[-1] > hs[0]
    py_ok = (all(b >= a for a, b in zip(h_series, h_series[1:]))
             and h_series[-1] > h_series[0])
    rust_ok, rust_note = rust.g1_height_expanding(h_series, 3)
    assert rust_ok == py_ok == expected_ok, f"h_series={h_series}: rust={rust_ok} py={py_ok}"


# ── g4_headroom ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h_series,ref_window,expected_ok", [
    ([3, 4, 5, 4, 3], 10, True),     # H=3, max=5, 3 <= 5-1, len=5 >= 10//2=5
    ([3, 4, 5, 6, 7], 10, False),    # H=7, max=7, 7 > 7-1
    ([5, 5, 5, 5, 5], 10, False),    # H=5, max=5, 5 > 5-1
    ([3, 3, 3, 3, 4], 10, False),    # H=4, max=4, 4 <= 4-1=3? No → False
])
def test_g4(h_series, ref_window, expected_ok):
    ref = max(h_series)
    h = h_series[-1]
    py_ok = h <= ref - 1
    rust_ok, rust_note = rust.g4_headroom(h_series, 1, ref_window)
    assert rust_ok == py_ok == expected_ok, f"h_series={h_series}: rust={rust_ok} py={py_ok}"


# ── g2_theme_ladder ────────────────────────────────────────────────────────

def test_g2_empty():
    rust_ok, rust_note = rust.g2_theme_ladder([], 3)
    assert rust_ok is None


def test_g2_no_qualified():
    rows = [("A", 1, 0, 5), ("B", 2, 0, 4)]
    rust_ok, rust_note = rust.g2_theme_ladder(rows, 3)
    assert rust_ok is False


def test_g2_qualified():
    rows = [("A", 1, 0, 5), ("B", 3, 0, 4)]
    rust_ok, rust_note = rust.g2_theme_ladder(rows, 3)
    assert rust_ok is None  # 首板维度未实现 → UNKNOWN


# ── g3_break_feedback ──────────────────────────────────────────────────────

@pytest.mark.parametrize("perfs,expected_ok", [
    ([-1.0, -2.0], True),         # avg=-1.5 > -3.0
    ([-5.0, -6.0], False),        # avg=-5.5 < -3.0
    ([-3.0, -3.0], False),        # avg=-3.0 == -3.0 → False
    ([1.0], None),                # 样本不足
])
def test_g3(perfs, expected_ok):
    rust_ok, rust_note = rust.g3_break_feedback(perfs, -3.0)
    assert rust_ok == expected_ok, f"perfs={perfs}: rust={rust_ok}"


# ── b1_acceleration ────────────────────────────────────────────────────────

def test_b1_hit():
    ok, note = rust.b1_acceleration(True)
    assert ok is True


def test_b1_no_hit():
    ok, note = rust.b1_acceleration(False)
    assert ok is False


# ── b2_oneword_made ────────────────────────────────────────────────────────

def test_b2_a3_not_hit():
    ok, note = rust.b2_oneword_made(False, 0.5)
    assert ok is False


def test_b2_a3_hit_divergence_high():
    ok, note = rust.b2_oneword_made(True, 0.5)
    assert ok is True


def test_b2_a3_hit_divergence_low():
    ok, note = rust.b2_oneword_made(True, 0.2)
    assert ok is False


def test_b2_a3_hit_no_divergence():
    ok, note = rust.b2_oneword_made(True, None)
    assert ok is None


# ── b3_next_day_dump ───────────────────────────────────────────────────────

@pytest.mark.parametrize("avg,count,expected_ok", [
    (-8.5, 2, True),      # avg=-8.5 < -7.0
    (-5.5, 2, False),     # avg=-5.5 > -7.0
    (1.0, 1, None),       # 样本不足
    (0.0, 0, None),       # 空
])
def test_b3(avg, count, expected_ok):
    rust_ok, rust_note = rust.b3_next_day_dump(avg, count, -7.0, "live")
    assert rust_ok == expected_ok, f"avg={avg}: rust={rust_ok}"


# ── b4_no_sector ───────────────────────────────────────────────────────────

def test_b4_empty():
    ok, note = rust.b4_no_sector([])
    assert ok is None


def test_b4_supported():
    rows = [("000001", "金融", 3), ("000002", "地产", 2)]
    ok, note = rust.b4_no_sector(rows)
    assert ok is False


def test_b4_all_isolated():
    rows = [("000001", "金融", 1), ("000002", "地产", 1)]
    ok, note = rust.b4_no_sector(rows)
    assert ok is True


def test_b4_no_theme():
    rows = [("000001", None, 0), ("000002", None, 0)]
    ok, note = rust.b4_no_sector(rows)
    assert ok is None


# ── b5_cross_theme ────────────────────────────────────────────────────────

@pytest.mark.parametrize("distinct,total,confident,expected_ok", [
    (2, 5, 3, True),              # 跨 2 题材
    (1, 5, 3, False),             # 仅 1 题材
    (0, 0, 0, None),              # 无同身位
    (1, 5, 0, None),              # 无可信标注
])
def test_b5(distinct, total, confident, expected_ok):
    rust_ok, rust_note = rust.b5_cross_theme(distinct, total, confident)
    assert rust_ok == expected_ok, f"distinct={distinct}: rust={rust_ok}"


# ── verdict ────────────────────────────────────────────────────────────────

def test_verdict_unfavorable():
    goods = [("G1", True, "ok"), ("G4", True, "ok")]
    bads = [("B1", True, "bad")]
    assert rust.verdict(
        [(g[0], g[1], g[2]) for g in goods],
        [(b[0], b[1], b[2]) for b in bads],
    ) == "UNFAVORABLE"


def test_verdict_favorable():
    goods = [("G1 可交易高度扩张", True, "ok"), ("G2 主线梯队完整", None, "ok"),
             ("G3 断板负反馈温和", None, "ok"), ("G4 胜者上方有空间", True, "ok")]
    bads = [("B1 加速事件", False, "ok"), ("B2 高度靠一字制造", False, "ok"),
            ("B3 胜出次日核按钮", None, "ok"), ("B4 无板块支持", False, "ok"),
            ("B5 高标跨题材", False, "ok")]
    assert rust.verdict(
        [(g[0], g[1], g[2]) for g in goods],
        [(b[0], b[1], b[2]) for b in bads],
    ) == "FAVORABLE"


def test_verdict_neutral_core_false():
    goods = [("G1 可交易高度扩张", False, "bad"), ("G4 胜者上方有空间", True, "ok")]
    bads = [("B1 加速事件", False, "ok")]
    assert rust.verdict(
        [(g[0], g[1], g[2]) for g in goods],
        [(b[0], b[1], b[2]) for b in bads],
    ) == "NEUTRAL"


def test_verdict_neutral_all_none():
    goods = [("G1 可交易高度扩张", None, "ok"), ("G4 胜者上方有空间", None, "ok")]
    bads = [("B1 加速事件", None, "ok")]
    assert rust.verdict(
        [(g[0], g[1], g[2]) for g in goods],
        [(b[0], b[1], b[2]) for b in bads],
    ) == "NEUTRAL"


# ── ladder_health ──────────────────────────────────────────────────────────

def test_ladder_health_empty():
    health = rust.ladder_health([])
    assert health.rows == 0
    assert health.height_nominal is None


def test_ladder_health_basic():
    rows = [
        ("000001", 5, True, True),
        ("000002", 5, True, False),
        ("000003", 4, True, False),
        ("000004", 3, False, False),
    ]
    health = rust.ladder_health(rows)
    assert health.rows == 4
    assert health.height_nominal == 5
    assert health.height_exchange == 5
    assert health.sole_top == "000001"
    assert len(health.top_group) == 2


# ── promotion_strength ─────────────────────────────────────────────────────

def test_promotion_strength_empty():
    ps = rust.promotion_strength([], [])
    assert ps.layers == []
    assert ps.deep_layer is None


def test_promotion_strength_basic():
    layers = [
        ("1→2", 10, 8, 0.8, 0.7, 0.1, None),
        ("2→3", 8, 6, 0.75, 0.6, 0.2, None),
        ("3→4", 6, 4, None, None, None, -2.0),
    ]
    prior = [("1→2", 0.75), ("2→3", 0.55)]
    ps = rust.promotion_strength(layers, prior)
    assert len(ps.layers) == 3
    assert ps.deep_layer == "2→3"
    assert ps.deep_rate_exchange == 0.6
    # delta = 0.6 - 0.55 = 0.05
    assert ps.deep_delta_exchange == 0.05


# ── rate (完整评级) ─────────────────────────────────────────────────────────

def test_rate_favorable():
    """全真场景 → FAVORABLE"""
    # g4_h_series needs at least ref_window/2 = 10 elements for g4_headroom
    g4_series = [3, 4, 5, 4, 3, 3, 4, 5, 4, 3]
    rating = rust.rate(
        g1_h_series=[3, 4, 5],
        g4_h_series=g4_series,
        theme_rows=[("主线", 5, 0, 10)],
        fail_perfs=[-1.0, -2.0],
        accel_hit=False,
        a3_hit=False,
        max_divergence=None,
        b3_avg=0.0,
        b3_count=0,
        b4_rows=[("000001", None, 0)],
        b5_distinct=0,
        b5_total=0,
        b5_confident=0,
        mode="live",
    )
    # G1=True, G4=True (H=3, max=5, 3 <= 5-1), G2=None(qualified), G3=True
    # B1=False, B3=None(count<2), B4=None(empty theme), B5=None(total=0)
    # All core goods True, no bad True → FAVORABLE
    assert rating.rating == "FAVORABLE"


def test_rate_unfavorable():
    """B1 命中 → UNFAVORABLE"""
    rating = rust.rate(
        g1_h_series=[3, 4, 5],
        g4_h_series=[3, 4, 5, 4, 3],
        theme_rows=[],
        fail_perfs=[],
        accel_hit=True,
        a3_hit=False,
        max_divergence=None,
        b3_avg=0.0,
        b3_count=0,
        b4_rows=[],
        b5_distinct=0,
        b5_total=0,
        b5_confident=0,
        mode="live",
    )
    assert rating.rating == "UNFAVORABLE"


def test_rate_neutral():
    """G1 被证伪 → NEUTRAL"""
    rating = rust.rate(
        g1_h_series=[5, 4, 3],
        g4_h_series=[3, 4, 5, 4, 3],
        theme_rows=[],
        fail_perfs=[],
        accel_hit=False,
        a3_hit=False,
        max_divergence=None,
        b3_avg=0.0,
        b3_count=0,
        b4_rows=[],
        b5_distinct=0,
        b5_total=0,
        b5_confident=0,
        mode="live",
    )
    # G1=False, G4=True → core not all True → NEUTRAL
    assert rating.rating == "NEUTRAL"


# ── EcosystemItem / EcosystemRating 类 ────────────────────────────────────

def test_ecosystem_item():
    item = rust.EcosystemItem("G1", True, "高度扩张")
    assert item.cond == "G1"
    assert item.ok is True
    assert item.note == "高度扩张"


def test_ecosystem_rating():
    goods = [rust.EcosystemItem("G1", True, "ok")]
    bads = [rust.EcosystemItem("B1", False, "ok")]
    rating = rust.EcosystemRating("FAVORABLE", goods, bads)
    assert rating.rating == "FAVORABLE"
    assert len(rating.goods) == 1
    assert len(rating.bads) == 1
