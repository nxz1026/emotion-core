"""口径守护：`StateRules` 默认值 = `CONFIG` 现值（主线零行为变化）。

P2（docs/20 §7）把状态机参数化，唯一允许的改动是「新增可替换的 rules 对象」：
主线 `classify_series(series)` 不传参就必须与改版前逐字等价，否则整库相位
重新洗牌、`config_hash` 之外的语义也漂移。

本测试钉三件事：
1. 每个 `StateRules` 字段与 `CONFIG` 同名键取值相同（默认值不许手写常量）；
2. `classify_series(series)` 与 `classify_series(series, DEFAULT_RULES)` 逐条相等；
3. `replace()` 换参真的生效（否则 Lab 搜索搜索的是空气）。
"""

from __future__ import annotations

from dataclasses import fields, replace as dc_replace
from datetime import date, timedelta

from emotion_core.algorithms import state
from emotion_core.algorithms.state import DEFAULT_RULES, DayMetrics, StateRules
from emotion_core.utils.config import CONFIG

D0 = date(2026, 1, 5)


def _day(i: int, **kw) -> DayMetrics:
    base = dict(
        date=D0 + timedelta(days=i),
        limit_up_count=50,
        bomb_rate=0.30,
        zt_performance=1.0,
        max_limit_days=3,
        limit_down_count=3,
        top_amplitude=8.0,
        top_broke=False,
        bomb_threshold=0.42,
        has_candidate=True,
        tradable_max_days=4,
        oneword_ratio=0.10,
    )
    base.update(kw)
    return DayMetrics(**base)


# 覆盖四种相位 + 继承日的一段序列（够触发全部规则分支）
SERIES = [
    _day(0),
    _day(1, limit_up_count=90),  # 高潮（涨停数越阈）
    _day(2, zt_performance=-3.0),  # 退潮
    _day(3, limit_up_count=60, zt_performance=2.0),
    _day(4, limit_up_count=20, max_limit_days=2, has_candidate=False),  # 冰点
    _day(5, top_broke=True),
    _day(6, top_broke=True),  # 两天最高板都断 → 退潮
    _day(7),
]


#: 字段 → CONFIG 键（`ferment_min_days` 用的是 MIN_LEADER_DAYS，不是同名键，
#: 所以不能 `.upper()` 一把梭——映射必须显式且穷举）
FIELD_TO_CONFIG = {
    "ebb_zt_perf": "EBB_ZT_PERF",
    "ebb_ld_min": "EBB_LD_MIN",
    "ebb_ld_mult": "EBB_LD_MULT",
    "climax_zt": "CLIMAX_ZT",
    "climax_amplitude": "CLIMAX_AMPLITUDE",
    "ferment_zt_perf": "FERMENT_ZT_PERF",
    "ferment_min_days": "MIN_LEADER_DAYS",
    "ice_max_days": "ICE_MAX_DAYS",
    "ice_zt_max": "ICE_ZT_MAX",
}


def test_default_rules_equal_config():
    """默认值必须逐项来自 CONFIG —— 手写常量是口径漂移的头号来源。"""
    names = {f.name for f in fields(StateRules)}
    assert names == set(FIELD_TO_CONFIG), "StateRules 增删字段必须同步改映射"
    for fname, ckey in FIELD_TO_CONFIG.items():
        assert getattr(DEFAULT_RULES, fname) == getattr(CONFIG, ckey), f"{fname} vs {ckey}"
    assert DEFAULT_RULES.as_dict() == {f: getattr(CONFIG, c) for f, c in FIELD_TO_CONFIG.items()}


def test_no_rules_arg_is_bitwise_equal():
    """不传 rules 与传 DEFAULT_RULES 必须完全一致（含 reason 文本）。"""
    a = state.classify_series(list(SERIES))
    b = state.classify_series(list(SERIES), DEFAULT_RULES)
    assert [(s.date, s.phase, s.buy_window, s.force_liquidate, s.reason) for s in a] == [
        (s.date, s.phase, s.buy_window, s.force_liquidate, s.reason) for s in b
    ]


def test_replace_actually_changes_phase_sequence():
    """把高潮门槛压到 10 家 → 同一序列里更多天判为高潮。"""
    loose = DEFAULT_RULES.replace(climax_zt=10)
    base = state.classify_series(list(SERIES))
    alt = state.classify_series(list(SERIES), loose)
    assert sum(s.phase == "高潮" for s in alt) > sum(s.phase == "高潮" for s in base)


def test_replace_is_frozen_copy():
    alt = DEFAULT_RULES.replace(climax_zt=77)
    assert alt is not DEFAULT_RULES
    assert alt.climax_zt == 77 and DEFAULT_RULES.climax_zt == CONFIG.CLIMAX_ZT
    assert dc_replace(DEFAULT_RULES, ice_zt_max=99) == DEFAULT_RULES.replace(ice_zt_max=99)
