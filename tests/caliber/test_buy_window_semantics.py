"""口径守护：买入窗口语义（退潮→清仓、优先级 退潮>高潮>发酵>冰点、无命中继承昨日）。

裁决（docs/13 §S2）：`buy_window` 与 `force_liquidate` 是策略执行的最后一道闸，
映射写错会直接造成「该禁买时买入」。本测试锁三件事：

1. `state.window_of` 的相位→(窗口, 强制清仓) 映射逐项固定；
2. `state._RULES` 的优先级顺序 = 退潮 > 高潮 > 发酵 > 冰点（同时命中时高优先级胜）；
3. `classify_series` 的推进语义：无规则命中 → 继承昨日；首日无命中 → 冰点基线。
"""
from __future__ import annotations

from datetime import date, timedelta

from emotion_core.algorithms import state
from emotion_core.utils.config import CONFIG

D0 = date(2026, 1, 5)


def dm(day: date, **kw) -> state.DayMetrics:
    """构造单日指标（缺省为「无规则命中」形状）。"""
    base = {"date": day, "limit_up_count": 50, "bomb_rate": None,
            "zt_performance": None, "max_limit_days": 5, "limit_down_count": 1,
            "top_amplitude": None, "top_broke": None,
            "bomb_threshold": CONFIG.BOMB_RATE_FALLBACK, "has_candidate": True,
            "tradable_max_days": 5, "oneword_ratio": None}
    base.update(kw)
    return state.DayMetrics(**base)


def test_window_mapping_is_fixed():
    assert state.window_of("退潮") == ("NONE", True)
    assert state.window_of("高潮") == ("ENHANCED", False)
    assert state.window_of("发酵") == ("STANDARD", False)
    assert state.window_of("冰点") == ("NONE", False)
    # 只允许退潮清仓：其余相位一律不清仓
    for phase, (_win, liq) in [(p, state.window_of(p))
                               for p in ("冰点", "发酵", "高潮", "退潮")]:
        assert liq is (phase == "退潮")


def test_rule_priority_order():
    assert [name for _fn, name in state._RULES] == ["退潮", "高潮", "发酵", "冰点"]


def test_ebb_beats_climax_on_the_same_day():
    """涨停 90 家（高潮）+ 昨涨停表现 -5%（退潮）同时命中 → 退潮（禁买+清仓）。"""
    t = dm(D0, limit_up_count=90, zt_performance=-5.0)
    assert state._rule_ebb(t, None, None) and state._rule_climax(t, None, None)
    out = state.classify_series([t])[0]
    assert out.phase == "退潮"
    assert out.buy_window == "NONE" and out.force_liquidate is True


def test_no_hit_inherits_yesterday_and_seed_is_ice():
    """无命中继承昨日；首日无命中 → 冰点基线；两连炸板才判退潮。"""
    d1 = dm(D0, limit_up_count=90)                     # 高潮
    d2 = dm(D0 + timedelta(days=1))                    # 无命中 → 继承高潮
    d3 = dm(D0 + timedelta(days=2), top_broke=True)    # 首日炸板：two_day 需昨日也炸 → 继承
    d4 = dm(D0 + timedelta(days=3), top_broke=True)    # 连续两日炸板 → 退潮
    out = state.classify_series([d1, d2, d3, d4])
    assert [s.phase for s in out] == ["高潮", "高潮", "高潮", "退潮"]
    assert out[0].buy_window == "ENHANCED"
    assert out[1].buy_window == "ENHANCED" and out[1].force_liquidate is False
    assert out[2].buy_window == "ENHANCED" and out[2].force_liquidate is False
    assert out[3].buy_window == "NONE" and out[3].force_liquidate is True

    seed = state.classify_series([dm(D0)])[0]
    assert seed.phase == "冰点"
    assert seed.buy_window == "NONE" and seed.force_liquidate is False
