"""Lab 变体搜索的判定逻辑（P2，DB-free）。

搜索的价值全在门禁：没有门禁的搜索一定会挖出一个"选参区间 10 倍收益"的噪声变体。
这里逐条钉住七道门禁与它们的边界，另钉 `_t()` 的数学与 digest 文案。
"""
from __future__ import annotations

from datetime import date, timedelta

from emotion_core.lab import notify, search
from emotion_core.lab.rules import BASELINE, LabRules


def _win(name: str, rets: list[float]) -> search.Window:
    start = date(2024, 1, 2)
    return search.Window(name, start, start + timedelta(days=len(rets)), rets)


def _ev(*, sel: list[float], hold: list[float], rules: LabRules | None = None) -> dict:
    r = rules or BASELINE
    return {
        "variant_id": search.variant_id(r),
        "rules": r,
        "signals": len(sel) + len(hold),
        "sel": _win("sel", sel),
        "hold": _win("hold", hold),
        "hold_t": search._t(hold),
    }


def _base() -> dict:
    """基线：hold-out 明确为负（-1% × 40 条）。"""
    return _ev(sel=[-0.01] * 300, hold=[-0.01] * 40)


def _good_rule() -> LabRules:
    return BASELINE.replace(min_leader_days=3)


def _passing_ev() -> dict:
    """一个"本该过门禁"的变体：hold 全正、n=40、t 极大。"""
    return _ev(sel=[0.03] * 300, hold=[0.02] * 20 + [0.01] * 20, rules=_good_rule())


# ------------------------------------------------------------------ _t

def test_t_value_math():
    assert search._t([]) == 0.0
    assert search._t([0.01]) == 0.0
    assert search._t([0.02] * 5) == 0.0          # σ=0 → 不造无限 t
    assert abs(search._t([0.02, 0.0]) - 1.0) < 1e-9


def test_t_value_grows_with_consistency():
    noisy = search._t([0.1, -0.05, 0.08, -0.03, 0.06, -0.02])
    tight = search._t([0.02, 0.021, 0.019, 0.02, 0.02, 0.02])
    assert tight > noisy


# ------------------------------------------------------------------ variant_id

def test_variant_id_is_stable_and_sensitive():
    assert search.variant_id(BASELINE) == search.variant_id(LabRules())
    assert search.variant_id(BASELINE) != search.variant_id(
        BASELINE.replace(min_leader_days=3))
    assert search.variant_id(BASELINE) == "baseline_mainline"   # 基线复用 P1 的 id


def test_neighbors_are_plus_minus_one_step():
    ns = search._neighbors(BASELINE.replace(min_leader_days=4, climax_zt=80))
    assert len(ns) == 2 * len(search._NEIGHBOR_STEP)
    values = {n.min_leader_days for n in ns}
    assert {3, 5} <= values
    assert {n for n in values if n != 4} == {3, 5}


# ------------------------------------------------------------------ 七道门禁

def test_gate_all_pass():
    ok, reasons = search._gates(_passing_ev(), _base(), [])
    assert ok, reasons


def test_gate_insufficient_samples():
    ev = _ev(sel=[0.03] * 10, hold=[0.02] * 10)
    ok, reasons = search._gates(ev, _base(), [])
    assert not ok
    assert any("选参区间样本不足" in r for r in reasons)
    assert any("验证区间样本不足" in r for r in reasons)


def test_gate_negative_lcb():
    ev = _ev(sel=[0.03] * 300, hold=[0.010, -0.005] * 15)   # 均值正但噪声大 → LCB<0
    ok, reasons = search._gates(ev, _base(), [])
    assert not ok
    assert any("LCB" in r for r in reasons)


def test_gate_not_better_than_baseline():
    ev = _ev(sel=[0.03] * 300, hold=[-0.01] * 40)            # 与基线同值
    ok, reasons = search._gates(ev, _base(), [])
    assert not ok
    assert any("未优于基线" in r for r in reasons)


def test_gate_t_threshold():
    ev = _passing_ev()
    ev["hold_t"] = 2.9
    ok, reasons = search._gates(ev, _base(), [])
    assert not ok
    assert any("多重比较" in r for r in reasons)


def test_gate_holdout_halves_must_both_be_positive():
    ev = _ev(sel=[0.03] * 300, hold=[0.05] * 20 + [-0.03] * 20)
    ok, reasons = search._gates(ev, _base(), [])
    assert not ok
    assert any("第 2 段" in r for r in reasons)


def test_gate_neighborhood_instability():
    weak = {"variant_id": "n1", "hold": _win("hold", [-0.001] * 40)}
    ok, reasons = search._gates(_passing_ev(), _base(), [weak])
    assert not ok
    assert any("邻域不稳定" in r for r in reasons)


def test_gate_ignores_tiny_neighbor_samples():
    """邻域样本不足的邻居不参与稳定性判定（n<30 不给分）。"""
    tiny = {"variant_id": "n1", "hold": _win("hold", [-0.05] * 5)}
    ok, reasons = search._gates(_passing_ev(), _base(), [tiny])
    assert ok, reasons


# ------------------------------------------------------------------ digest

def test_digest_without_proposals_says_so():
    out = {"vars": 17, "proposals": [],
           "baseline": {"hold": {"n": 58, "mean": -1.178, "lcb": -2.699}},
           "results": [{"variant_id": "v_x", "sel": {"mean": -0.871},
                        "hold": {"mean": -1.293}, "reasons": ["hold-out LCB≤0"]}]}
    text = notify.digest(out)
    assert "过门禁 0 个" in text
    assert "没有变体过七道门禁" in text
    assert "v_x" in text


def test_digest_with_proposals_points_to_page():
    out = {"vars": 17, "proposals": [{"variant_id": "v_y", "hold": {"mean": 0.42},
                                      "hold_t": 3.4}],
           "baseline": {"hold": {"n": 58, "mean": -1.178, "lcb": -2.699}},
           "results": []}
    text = notify.digest(out)
    assert "提案 v_y" in text
    assert "8091" in text
