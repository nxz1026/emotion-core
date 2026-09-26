"""情绪状态机（指标层 + 时序推进 + 落库）单元测试。

无 DB：SQL 行选择（主板前缀 / 次新剔除 / 分母0 / 缺数据）与真实库差分对账由一次性
脚本完成；本文件锁纯逻辑与时序语义——优先级、继承、W1 seed 不重判、V3 缺数据只继承、
R2 延续标记、高潮分歧降级、加速影子开关、自适应阈值滚动、F6 缺失不补 0、
落库行序/行宽、增量与全量重放等价。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from emotion_core.algorithms import emotion
from emotion_core.utils.config import CONFIG


# ── 假查询层：按 SQL 特征分派到 per-date 事实 ──────────────
def day_facts(*, n_rows: int = 5000, zt: int = 0, zb: int = 0, touched: int = 0,
              dt: int = 0, h: int = 0, amp: float = 0.0, mean: float | None = None,
              med: float | None = None, broke: bool | None = None,
              has: bool = False, oneword: float | None = None,
              tradable_h: int | None = None, accelerate: bool = False) -> dict:
    return {"n_rows": n_rows, "zt": zt, "zb": zb, "touched": touched, "dt": dt,
            "h": h, "amp": amp, "mean": mean, "med": med, "broke": broke,
            "has": has, "oneword": oneword, "tradable_h": tradable_h,
            "accelerate": accelerate}


class FakeDB:
    """query_df 替身：按 SQL 特征分派；可断言调用次数与顺序。"""

    def __init__(self, facts: dict[date, dict], calendar: list[date],
                 prior_rows: tuple = (), seed_row: dict | None = None):
        self.facts = facts
        self.calendar = calendar
        self.prior_rows = prior_rows          # (date, bomb_rate, oneword_ratio)
        self.seed_row = seed_row              # {date, phase, bomb_rate, oneword_ratio}
        self.calls: list[str] = []

    def __call__(self, sql: str, params=()) -> pd.DataFrame:
        self.calls.append(sql)
        if "count(*) n FROM derived_bar" in sql:
            return pd.DataFrame({"n": [self.facts[params[0]]["n_rows"]]})
        if "count(*) FILTER (WHERE d.is_bomb) zb" in sql:
            f = self.facts[params[len(CONFIG.BOARD_PREFIXES)]]
            return pd.DataFrame([{"zt": f["zt"], "zb": f["zb"],
                                  "touched": f["touched"], "dt": f["dt"],
                                  "maxh": f["h"]}])
        if "max(cont_days) h, max(amplitude) a" in sql:
            f = self.facts[params[0]]
            if not f["h"]:                    # 零涨停：无最高板组，振幅不参与判据
                return pd.DataFrame({"h": [None], "a": [None]})
            return pd.DataFrame({"h": [f["h"]], "a": [f["amp"]]})
        if "percentile_cont(0.5) WITHIN GROUP" in sql:
            f = self.facts[params[0]]
            return pd.DataFrame({"mean": [f["mean"]], "median": [f["med"]]})
        if "bool_or(d2.is_limit_up)" in sql:
            prev_facts = self.facts.get(params[1], {})
            return pd.DataFrame({"broke": [prev_facts.get("broke")]})
        if "WITH t AS (" in sql:
            return pd.DataFrame({"has": [self.facts[params[0]]["has"]]})
        if "SELECT max(date) AS d FROM daily_bar" in sql:
            prev = [d for d in self.calendar if d < params[0]]
            return pd.DataFrame({"d": [max(prev) if prev else None]})
        if "SELECT DISTINCT date FROM daily_bar" in sql:
            prev = [d for d in self.calendar if d < params[0]]
            return pd.DataFrame({"date": sorted(prev, reverse=True)[:params[1]]})
        if "SELECT date, phase, bomb_rate, oneword_ratio FROM market_stat" in sql:
            seed = self.seed_row
            if seed is None or seed["date"] != params[0]:
                return pd.DataFrame()
            return pd.DataFrame([seed])
        if "SELECT date, bomb_rate, oneword_ratio FROM market_stat" in sql:
            rows = [r for r in self.prior_rows if params[1] <= r[0] < params[0]]
            return pd.DataFrame(sorted(rows, reverse=True),
                                columns=["date", "bomb_rate", "oneword_ratio"])
        if "SELECT bomb_rate FROM market_stat" in sql:
            rows = [r for r in self.prior_rows
                    if r[0] < params[0] and r[1] is not None]
            rows = sorted(rows, reverse=True)[:params[1]]
            return pd.DataFrame({"bomb_rate": [r[1] for r in rows]})
        raise AssertionError(f"未覆盖的 SQL: {sql}")


class FakeDetect:
    """accelerate.detect 替身：加速判定与一字占比/换手高度由 facts 提供。"""

    def __init__(self, facts: dict[date, dict]):
        self.facts = facts
        self.calls: list[date] = []

    def __call__(self, trade_date: date, prior_ratios=None):
        self.calls.append(trade_date)
        f = self.facts[trade_date]
        facts = {"tradable_h": f["tradable_h"], "oneword_ratio": f["oneword"]}
        return f["accelerate"], f"加速实测(假) ratio={f['oneword']}", facts


@pytest.fixture
def patch_io(monkeypatch):
    """装配：query_df / trading_days / accelerate.detect 全替身；捕获落库行。"""
    def _install(fake: FakeDB, calendar: list[date]):
        written: list[tuple] = []
        monkeypatch.setattr(emotion, "query_df", fake)
        monkeypatch.setattr(emotion, "trading_days",
                            lambda start, end, **kw: [d for d in calendar
                                                      if start <= d <= end])
        monkeypatch.setattr(emotion.accelerate, "detect", FakeDetect(fake.facts))
        monkeypatch.setattr(emotion, "_upsert_rows",
                            lambda rows: (written.extend(rows), len(rows))[1])
        return written
    return _install


_D = date(2026, 9, 1)


def _day(**kw) -> emotion.EmotionDay:
    return emotion.EmotionDay(date=kw.pop("date", _D), **kw)


# ── 时序推进（classify）────────────────────────────────
def test_first_day_seed_ice():
    """首日无历史、无规则命中 → 冰点种子 + ·首日基线。"""
    (out,) = emotion.classify([_day(limit_up_count=10, max_limit_days=9)])
    assert out.phase == "冰点" and out.buy_window == "NONE"
    assert out.force_liquidate is False
    assert out.reason_tag == "·首日基线"
    assert out.phase_inherited is False


def test_inherit_yesterday_without_rule_hit():
    """无规则命中且非首日 → 继承昨日 phase + R2 ·延续 标记。"""
    series = [_day(date=_D, limit_up_count=81, max_limit_days=5,
                   bomb_rate=0.1, bomb_threshold=0.3, zt_performance=2.0),
              _day(date=_D + timedelta(days=1), limit_up_count=50,
                   max_limit_days=4, bomb_rate=0.1, bomb_threshold=0.3,
                   zt_performance=2.0)]
    out = emotion.classify(series)
    assert out[0].phase == "高潮" and out[0].buy_window == "ENHANCED"
    assert out[1].phase == "高潮" and out[1].phase_inherited is True
    assert out[1].reason_tag == "·延续"
    assert out[1].reason.startswith("高潮（延续：当日无规则触发")


def test_priority_ebb_over_climax():
    """退潮优先级 > 高潮；退潮 → 窗口 NONE + 清仓。"""
    series = [_day(date=_D, limit_up_count=85, max_limit_days=6, top_broke=True,
                   bomb_rate=0.1, bomb_threshold=0.3),
              _day(date=_D + timedelta(days=1), limit_up_count=85,
                   max_limit_days=6, top_broke=True, bomb_rate=0.1,
                   bomb_threshold=0.3)]
    out = emotion.classify(series)
    assert out[0].phase == "高潮"           # 首日 y=None：two_day 分支不成立
    assert out[1].phase == "退潮"           # 断板连两日优先于高潮
    assert out[1].buy_window == "NONE" and out[1].force_liquidate is True


def test_ferment_uses_two_day_history():
    """发酵需 y/b 两日：高度≥4 + 昨涨停表现连两日>1.5 + 涨停数连增两日。"""
    days = [_day(date=_D, limit_up_count=30, max_limit_days=4,
                 zt_performance=2.0, bomb_rate=0.1, bomb_threshold=0.3),
            _day(date=_D + timedelta(days=1), limit_up_count=40, max_limit_days=4,
                 zt_performance=2.0, bomb_rate=0.1, bomb_threshold=0.3,
                 has_candidate=True),
            _day(date=_D + timedelta(days=2), limit_up_count=50, max_limit_days=5,
                 zt_performance=2.0, bomb_rate=0.1, bomb_threshold=0.3,
                 has_candidate=True)]
    out = emotion.classify(days)
    assert [d.phase for d in out] == ["冰点", "冰点", "发酵"]
    assert out[2].buy_window == "STANDARD"


def test_ice_tags_reversal_and_no_candidate():
    """冰点两条放行腿各带自己的标记（·反转 / ·无候选放宽）。"""
    rev = emotion.classify([
        _day(date=_D, zt_performance=-1.0, limit_up_count=20, max_limit_days=2,
             has_candidate=True),
        _day(date=_D + timedelta(days=1), zt_performance=1.0, limit_up_count=20,
             max_limit_days=2, has_candidate=True)])
    assert rev[1].phase == "冰点" and rev[1].reason_tag == "·反转"
    relaxed = emotion.classify([_day(date=_D, zt_performance=1.0, has_candidate=False,
                                     limit_up_count=20, max_limit_days=2)])
    assert relaxed[0].phase == "冰点" and relaxed[0].reason_tag == "·无候选放宽"


def test_climax_diverge_downgrade():
    """高潮 + 负反馈 nb≥3 → 窗口降级 NONE（禁买不清仓），并记 diverge。"""
    series = [_day(date=_D, limit_up_count=90, max_limit_days=6, bomb_rate=0.2,
                   bomb_threshold=0.3, zt_performance=1.0, limit_down_count=2),
              _day(date=_D + timedelta(days=1), limit_up_count=90,
                   max_limit_days=5, bomb_rate=0.4, bomb_threshold=0.3,
                   zt_performance=0.5, limit_down_count=5, top_broke=True)]
    _, t = emotion.classify(series)
    assert t.phase == "高潮" and t.neg_feedback == 5     # 断板/高度降/炸升/表现降/跌停升
    assert t.diverge is True and t.buy_window == "NONE"
    assert t.force_liquidate is False                    # 分歧降级≠退潮清仓
    assert "高位分歧降级(nb=5)" in t.reason


def test_accel_is_event_not_state(monkeypatch):
    """加速命中只打标记；仅 ACCEL_ENFORCE=True 才压窗口。"""
    day = _day(limit_up_count=81, max_limit_days=5, accelerate=True,
               bomb_rate=0.1, bomb_threshold=0.3, zt_performance=2.0)
    out = emotion.classify([day])[0]
    assert out.phase == "高潮" and out.buy_window == "ENHANCED"    # 影子：不动窗口
    assert out.reason_tag.endswith("·加速(影子)")
    monkeypatch.setattr(emotion, "_ACCEL_ENFORCE", True)
    out = emotion.classify([_day(limit_up_count=81, max_limit_days=5,
                                 accelerate=True, bomb_rate=0.1,
                                 bomb_threshold=0.3,
                                 zt_performance=2.0)])[0]
    assert out.buy_window == "NONE" and out.reason_tag.endswith("·加速降级")


def test_data_missing_inherits_only():
    """V3：derived_bar 整体缺失日只继承昨日，不改判冰点。"""
    series = [_day(date=_D, limit_up_count=81, max_limit_days=5, bomb_rate=0.1,
                   bomb_threshold=0.3, zt_performance=2.0),
              _day(date=_D + timedelta(days=1), data_missing=True)]
    out = emotion.classify(series)
    assert out[1].phase == "高潮" and out[1].buy_window == "ENHANCED"
    assert out[1].phase_inherited is True
    assert out[1].reason == "·缺数据（derived_bar 当日整体缺失，phase 仅延续）"


def test_seed_not_rejudged():
    """W1：seed（昨日已落库快照）不得被规则引擎用占位默认值重判成冰点。"""
    seed = _day(date=_D, phase="高潮", buy_window="ENHANCED", is_seed=True,
                bomb_rate=0.1, oneword_ratio=0.2)
    day1 = _day(date=_D + timedelta(days=1), limit_up_count=20,
                max_limit_days=2, has_candidate=True)
    out = emotion.classify([seed, day1])
    assert out[0].phase == "高潮"           # seed 原样保留
    assert out[0].reason == ""              # seed 不重写 reason
    assert out[1].phase == "高潮"           # 继承 seed 的 phase（若被重判则成冰点）
    assert out[1].phase_inherited is True


def test_classify_accepts_market_stat_frame():
    """工单签名：market_stat 形状 DataFrame 入参，与列表入参同结果。"""
    rows = [_day(date=_D, limit_up_count=81, max_limit_days=5, bomb_rate=0.1,
                 bomb_threshold=0.3, zt_performance=2.0),
            _day(date=_D + timedelta(days=1), limit_up_count=50, max_limit_days=4,
                 bomb_rate=0.1, bomb_threshold=0.3, zt_performance=2.0)]
    df = pd.DataFrame([{c: getattr(r, c) for c in emotion._COLS} for r in rows])
    from_frame = emotion.classify(df)
    from_list = emotion.classify([_day(date=_D, limit_up_count=81,
                                       max_limit_days=5, bomb_rate=0.1,
                                       bomb_threshold=0.3, zt_performance=2.0),
                                  _day(date=_D + timedelta(days=1),
                                       limit_up_count=50, max_limit_days=4,
                                       bomb_rate=0.1, bomb_threshold=0.3,
                                       zt_performance=2.0)])
    assert [(d.date, d.phase, d.buy_window, d.reason) for d in from_frame] == \
        [(d.date, d.phase, d.buy_window, d.reason) for d in from_list]
    assert from_frame[0].date == _D          # 列内 date 逐行装配
    ts_frame = df.copy()
    ts_frame["date"] = pd.to_datetime(ts_frame["date"])
    assert [d.date for d in emotion.classify(ts_frame)] == [_D, _D + timedelta(days=1)]


def test_neg_feedback_f6_none_not_counted():
    """负反馈计数：None 项既不算发生也不算没发生（基数 0~5）。"""
    y = _day(date=_D, bomb_rate=0.2, zt_performance=1.0, max_limit_days=5,
             limit_down_count=2)
    t = _day(date=_D + timedelta(days=1), bomb_rate=None, zt_performance=0.5,
             max_limit_days=4, limit_down_count=3, top_broke=None)
    assert emotion._neg_feedback(t, y) == 3  # 高度降 + 表现降 + 跌停升
    assert emotion._neg_feedback(t, None) == 0
    # 昨日无人触板（bomb_rate=None）而今日有人触板：不得抛 TypeError（lkl 原实现会）
    quiet_prev = _day(date=_D, bomb_rate=None, zt_performance=1.0,
                      max_limit_days=5, limit_down_count=2)
    assert emotion._neg_feedback(y, quiet_prev) == 0


# ── 指标层 ────────────────────────────────────────────
def test_bomb_threshold_memory_and_fallback():
    """自适应阈值 = 前30日 median+σ(ddof=1)；样本不足回退固定值；None/NaN 不入窗口。"""
    rates = [0.2 + i / 100 for i in range(30)]
    s = pd.Series(rates, dtype=float)
    expected = float(s.median() + s.std(ddof=1))
    assert emotion._bomb_threshold(_D, rates) == pytest.approx(expected)
    assert emotion._bomb_threshold(_D, [*rates, None, float("nan")]) == \
        pytest.approx(expected)                        # 窗外噪声不参与
    assert emotion._bomb_threshold(_D, [None, float("nan"), *rates]) == \
        CONFIG.BOMB_RATE_FALLBACK                      # 窗内缺值 → 有效样本不足
    assert emotion._bomb_threshold(_D, rates[:29]) == CONFIG.BOMB_RATE_FALLBACK


def test_bomb_threshold_db_path(monkeypatch):
    """prior_rates=None 回落读库：满窗口用 median+σ，不足窗口回退固定值。"""
    rates = [0.3] * 30
    rates[0] = 0.1
    fake = FakeDB({}, [], prior_rows=tuple(
        (date(2026, 1, 1) + timedelta(days=i), rates[i], 0.2)
        for i in range(30)))
    monkeypatch.setattr(emotion, "query_df", fake)
    s = pd.Series(rates, dtype=float)
    assert emotion._bomb_threshold(_D) == pytest.approx(
        float(s.median() + s.std(ddof=1)))
    short = FakeDB({}, [], prior_rows=fake.prior_rows[:29])
    monkeypatch.setattr(emotion, "query_df", short)
    assert emotion._bomb_threshold(_D) == CONFIG.BOMB_RATE_FALLBACK


def test_indicators_fields(monkeypatch):
    """单日指标装配：判据取中位数（F5）、炸板率分母、双口径高度、一字占比。"""
    f = day_facts(zt=55, zb=11, touched=110, dt=3, h=6, amp=17.5, mean=0.8,
                  med=-0.11, broke=True, has=True, oneword=0.4, tradable_h=4,
                  accelerate=True)
    prev = date(2026, 8, 31)
    fake = FakeDB({_D: f, prev: day_facts(broke=True)}, [prev, _D])
    monkeypatch.setattr(emotion, "query_df", fake)
    monkeypatch.setattr(emotion.accelerate, "detect", FakeDetect({_D: f}))
    s = emotion.indicators(_D, {"bomb_rates": [0.2], "onewords": [0.3]})
    assert s.limit_up_count == 55 and s.limit_down_count == 3
    assert s.bomb_rate == pytest.approx(0.1)          # 11/110
    assert s.zt_performance == -0.11                  # EMOTION_PERF_BASIS=median
    assert (s.zt_performance_mean, s.zt_performance_median) == (0.8, -0.11)
    assert s.max_limit_days == 6 and s.tradable_max_days == 4
    assert s.top_amplitude == 17.5 and s.top_broke is True
    assert s.has_candidate is True and s.oneword_ratio == 0.4
    assert s.accelerate is True and s.accel_facts["tradable_h"] == 4
    assert s.bomb_threshold == CONFIG.BOMB_RATE_FALLBACK   # hist 仅 1 个样本
    assert s.data_missing is False


def test_indicators_zero_touch_bomb_rate_none(monkeypatch):
    """V3/F6：无人触板 → 炸板率 None（假 0 会污染 30 日自适应阈值）。"""
    f = day_facts(zt=0, zb=0, touched=0, h=0)
    fake = FakeDB({_D: f}, [_D])
    monkeypatch.setattr(emotion, "query_df", fake)
    monkeypatch.setattr(emotion.accelerate, "detect", FakeDetect({_D: f}))
    s = emotion.indicators(_D, {"bomb_rates": [], "onewords": []})
    assert s.bomb_rate is None and s.max_limit_days == 0 and s.top_amplitude == 0.0
    assert s.top_broke is None


def test_indicators_data_missing_short_circuits(monkeypatch):
    """V3：derived_bar 当日 0 行 → 直接返回缺数据标记，不再发其它查询。"""
    fake = FakeDB({_D: day_facts(n_rows=0)}, [_D])
    monkeypatch.setattr(emotion, "query_df", fake)
    s = emotion.indicators(_D)
    assert s.data_missing is True and s.phase == "" and s.date == _D
    assert len(fake.calls) == 1


def test_row_mapping_and_inf():
    """落库行：列序对齐 _COLS、None 透传（F6）、inf 阈值→NULL、浮点 4 位。"""
    s = _day(date=_D, limit_up_count=55, bomb_rate=1 / 3, zt_performance=None,
             max_limit_days=6, phase="高潮", buy_window="ENHANCED",
             bomb_threshold=float("inf"), reason="高潮(假)",
             zt_performance_mean=None, tradable_max_days=4, oneword_ratio=0.4)
    row = emotion._row(s)
    assert len(row) == len(emotion._COLS) == 23
    assert dict(zip(emotion._COLS, row)) == {
        "date": _D, "limit_up_count": 55, "bomb_rate": 0.3333,
        "zt_performance": None, "max_limit_days": 6, "limit_down_count": 0,
        "phase": "高潮", "buy_window": "ENHANCED", "force_liquidate": False,
        "reason": "高潮(假)", "top_amplitude": 0.0, "top_broke": None,
        "bomb_threshold": None, "has_candidate": False, "neg_feedback": 0,
        "diverge": False, "zt_performance_mean": None,
        "zt_performance_median": None, "tradable_max_days": 4,
        "oneword_ratio": 0.4, "accelerate": False, "accel_reason": "",
        "phase_inherited": False}
    assert emotion._upsert_rows([]) == 0


def test_upsert_rejects_wrong_row_width():
    """行宽校验：错位行必须报错，不得静默写脏数据。"""
    with pytest.raises(ValueError, match="行宽"):
        emotion._upsert_rows([(1, 2, 3)])


def test_fmt_none_not_zero():
    assert emotion._fmt(None) == "—" and emotion._fmt(1.25) == "1.2"
    assert emotion._fmt(4, 0) == "4"


# ── 编排与落库 ────────────────────────────────────────
def test_persist_writes_only_target_day(patch_io, monkeypatch):
    """短跑：F8 warm-up 只作前情，只回写目标日；seed 续接昨日已落库 phase。

    warm-up 日与目标日均**不命中任何规则**（h=4 排除冰点、涨停 50 不到高潮），
    故三日的 phase 只能来自 seed（昨日已落库 高潮）——若 seed 被规则引擎重判
    （占位默认值会命中冰点）本测试即失败（W1 回归守卫）。
    """
    seed_day = date(2026, 8, 31)
    warm = [seed_day + timedelta(days=i) for i in range(1, 6)]   # 5 个 warm-up 交易日
    target = seed_day + timedelta(days=6)
    calendar = [seed_day, *warm, target]
    quiet = day_facts(zt=50, touched=100, zb=10, h=4, med=2.0, has=True)
    facts = {d: {**quiet} for d in [*warm, target]}
    fake = FakeDB(facts, calendar,
                  seed_row={"date": seed_day, "phase": "高潮",
                            "bomb_rate": 0.2, "oneword_ratio": 0.2})
    written = patch_io(fake, calendar)
    assert emotion.persist(target) is None
    assert len(written) == 1
    row = dict(zip(emotion._COLS, written[0]))
    assert row["date"] == target
    assert row["limit_up_count"] == 50 and row["max_limit_days"] == 4
    assert row["phase"] == "高潮"            # 当日无规则命中 → 延续 seed（昨日落库）
    assert row["buy_window"] == "ENHANCED"
    assert row["phase_inherited"] is True
    assert "延续" in row["reason"]
    assert row["bomb_rate"] == pytest.approx(0.1)
    assert row["zt_performance"] == 2.0
    assert row["bomb_threshold"] == pytest.approx(CONFIG.BOMB_RATE_FALLBACK)
    assert row["neg_feedback"] == 0          # 指标全平：断板/高度/炸率/表现/跌停均未恶化


def test_run_range_equals_replay(patch_io):
    """W1/A1：同一天在增量链（seed+warm-up）与全量重放链（零读库）判出同一行。"""
    start = date(2026, 1, 5)
    calendar = [start + timedelta(days=i) for i in range(36)]
    facts = {}
    for i, d in enumerate(calendar):
        zt = 30 + (i % 5) * 15
        touched = zt + 4
        facts[d] = day_facts(zt=zt, zb=zt // 4, touched=touched, dt=i % 4,
                             h=2 + i % 5, amp=4.0 + i % 12,
                             mean=0.4, med=2.0 if i % 3 else -3.0,
                             broke=(i % 4 == 0), has=(i % 3 == 0),
                             oneword=0.2 + (i % 5) / 100, tradable_h=2)
    target_start, target_end = calendar[33], calendar[35]
    prior = tuple((d, facts[d]["zb"] / facts[d]["touched"], facts[d]["oneword"])
                  for d in calendar[:33])
    fake = FakeDB(facts, calendar, prior_rows=prior)
    written = patch_io(fake, calendar)

    n_inc = emotion.run_range(target_start, target_end)
    inc = list(written)
    written.clear()
    n_rep = emotion.replay(target_start, target_end)
    rep = list(written)

    assert n_inc == n_rep == 3
    assert [r[0] for r in inc] == [target_start, calendar[34], target_end]
    assert inc == rep                       # 逐列等价（含 reason/阈值/标记）


def test_run_range_empty_interval_returns_zero(patch_io):
    """A8：区间无交易日（假期/未来日期）早退 0，不越界取 days[0]。"""
    fake = FakeDB({}, [])
    written = patch_io(fake, [])
    assert emotion.run_range(date(2026, 10, 1), date(2026, 10, 7)) == 0
    assert written == []
    assert len(fake.calls) == 1             # 只发了 warm-up 边界查询


def test_rolling_helpers_use_single_query(monkeypatch):
    """日历回退：warm-up / 30 日滚动窗口各一次查询，不逐日开连接。"""
    calendar = [date(2026, 1, 5) + timedelta(days=i) for i in range(40)]
    fake = FakeDB({}, calendar)
    monkeypatch.setattr(emotion, "query_df", fake)
    assert emotion._rolling_days(calendar[35], 30) == calendar[5:35]
    fake.calls.clear()
    assert emotion._warmup_start(calendar[35], 5) == calendar[30]
    assert len(fake.calls) == 1
    assert emotion._warmup_start(calendar[2], 5) == calendar[0]   # 不足则回退到最早
