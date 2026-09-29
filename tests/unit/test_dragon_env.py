"""生态评级（dragon_env）单元测试。

无 DB：query_df 由替身接管（同 test_accelerate.py 范式），锁判定边界与降级分支——
G1/G4 窗口不足、G2 首板维度未实现的 UNKNOWN、G3 样本门槛、G4/B2/B3/B4/B5 三态、
_verdict 优先级、_ser 的 None→null、persist jsonb 载荷、以及 ladder_health /
promotion_strength 两个只读汇总视图。真实库差分对账（vs lkl.services.dragon_env）
见模块 docstring。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from emotion_core.algorithms import accelerate, dragon_env

D = date(2026, 9, 24)


class FakeQuery:
    """query_df 替身：按 SQL 特征分派；可断言某路径未读库。"""

    def __init__(self):
        self.series: list[int] = []
        self.g2_ok: list[tuple[str, int, int, int]] = []
        self.g2_top: list[tuple[str, int]] = []
        self.fail_perf: list[float] = []
        self.divergence: float | None = None
        self.tradable: list[tuple[str, str | None, int]] = []
        self.cross: tuple[int, int, int] = (0, 0, 0)
        self.b3: tuple[float | None, int] = (None, 0)
        self.ladder_rows: list[tuple[str, int, bool, bool]] = []
        self.promo_rows: list[tuple] = []
        self.days: list[date] = []
        self.calls: list[str] = []
        self.params: list[tuple] = []
        self.forbid_reads = False

    def __call__(self, sql: str, params=()) -> pd.DataFrame:
        self.calls.append(sql)
        self.params.append(tuple(params))
        if self.forbid_reads:
            raise AssertionError(f"该路径不应读库: {sql}")
        if "max(CASE WHEN is_exchange THEN cont_days END)" in sql:
            # 真 SQL 是 ORDER BY date DESC LIMIT n，_series 内部再 reversed()；
            # 替身照此契约取「最近 n 日、倒序」返回，测试里 self.series 按时间升序给定。
            newest_first = list(reversed(self.series))[:int(params[-1])]
            return pd.DataFrame({"h": newest_first})
        if "member_count >= %s" in sql:
            return pd.DataFrame(
                self.g2_ok, columns=["theme", "member_count",
                                     "first_board_count", "highest_board"])
        if "SELECT theme, member_count FROM theme_group" in sql:
            return pd.DataFrame(self.g2_top, columns=["theme", "member_count"])
        if "SELECT fail_perf FROM promotion_day" in sql:
            return pd.DataFrame({"fail_perf": list(self.fail_perf)})
        if "SELECT max(divergence) d FROM promotion_day" in sql:
            return pd.DataFrame({"d": [self.divergence]})
        if "COALESCE(s.name, l.code)" in sql:
            return pd.DataFrame(self.tradable, columns=["code", "theme", "mc"])
        if "count(DISTINCT t.primary_theme)" in sql:
            n, c, c_ok = self.cross
            return pd.DataFrame({"n": [n], "c": [c], "c_ok": [c_ok]})
        if "WITH tops AS" in sql:
            p, c = self.b3
            return pd.DataFrame({"p": [p], "c": [c]})
        if "SELECT code, cont_days, is_exchange, is_sole_top FROM ladder_day" in sql:
            return pd.DataFrame(self.ladder_rows,
                                columns=["code", "cont_days", "is_exchange", "is_sole_top"])
        if "FROM promotion_day WHERE date = %s ORDER BY layer" in sql:
            return pd.DataFrame(self.promo_rows, columns=[
                "layer", "promote_nominal", "promote_exchange",
                "rate_nominal", "rate_exchange", "divergence", "fail_perf"])
        if "SELECT date FROM market_stat WHERE date BETWEEN" in sql:
            return pd.DataFrame({"date": list(self.days)})
        raise AssertionError(f"未预期的 SQL: {sql}")


@pytest.fixture
def fake(monkeypatch) -> FakeQuery:
    f = FakeQuery()
    monkeypatch.setattr(dragon_env, "query_df", f)
    return f


# ── G1 可交易高度扩张 ──────────────────────────────────────────────


def test_g1_true_when_monotone_and_rising(fake):
    fake.series = [2, 3, 3]
    assert dragon_env.g1_height_expanding(D) == (True, "H 2→3→3")


@pytest.mark.parametrize("series, expected", [([3, 3, 2], False), ([3, 3, 3], False)])
def test_g1_false_when_falling_or_flat(fake, series, expected):
    """单调不降且至少一日上升——平走不算扩张，回落不算扩张。"""
    fake.series = series
    ok, note = dragon_env.g1_height_expanding(D)
    assert ok is expected and note == "H " + "→".join(map(str, series))


def test_g1_unknown_when_window_short(fake):
    fake.series = [2, 3]
    assert dragon_env.g1_height_expanding(D) == (None, "H 序列仅 2 日，不足 3")


# ── G4 胜者上方有空间 ──────────────────────────────────────────────


@pytest.mark.parametrize("last, expected", [(5, True), (6, False)])
def test_g4_headroom_boundary(fake, last, expected):
    """窗口 20（门槛 10 日样本）；H <= 近 20 日最高 - 1 才算有空间。"""
    fake.series = [4, 4, 5, 5, 6, 6, 5, 5, 5, last]
    ok, note = dragon_env.g4_headroom(D)
    assert ok is expected and note == f"H={last} 近20日最高=6"


def test_g4_unknown_when_reference_window_half(fake):
    fake.series = [4] * 9
    assert dragon_env.g4_headroom(D) == (None, "参照窗口仅 9 日")


# ── G2 主线梯队完整 ────────────────────────────────────────────────


def test_g2_unknown_when_first_board_dimension_missing(fake):
    """成员数达标也只记 UNKNOWN：首板维度未实现，不证伪也不成立。"""
    fake.g2_ok = [("机器人", 5, 0, 4)]
    ok, note = dragon_env.g2_theme_ladder(D)
    assert ok is None and "首板维度未实现" in note and "机器人" in note


def test_g2_false_when_no_theme_reaches_threshold(fake):
    fake.g2_top = [("O2O", 1)]
    ok, note = dragon_env.g2_theme_ladder(D)
    assert ok is False and note == "最强题材 O2O 成员1（<3，无达标主线）"


def test_g2_unknown_when_theme_group_empty(fake):
    assert dragon_env.g2_theme_ladder(D) == (None, "theme_group 当日无数据")


# ── G3 断板负反馈温和 ──────────────────────────────────────────────


@pytest.mark.parametrize("perfs, expected", [
    ([-3.0, -2.0], True), ([-5.0, -4.0], False), ([-3.0, -3.0], False)])
def test_g3_threshold_strict_greater(fake, perfs, expected):
    """严格大于阈值：等值不算温和（lkl 原式 avg > DRAGON_G3_MIN_PERF）。"""
    fake.fail_perf = perfs
    ok, note = dragon_env.g3_break_feedback(D)
    assert ok is expected
    assert note == f"最高层失败股均涨幅 {round(sum(perfs) / len(perfs), 2)}%"


def test_g3_unknown_when_samples_insufficient(fake):
    fake.fail_perf = [-1.0]
    assert dragon_env.g3_break_feedback(D) == (None, "断板反馈样本 1 不足")


# ── B1/B2 ─────────────────────────────────────────────────────────


def test_b1_mirrors_accel_hit(fake):
    assert dragon_env.b1_acceleration(D, True) == (True, "加速事件命中")
    assert dragon_env.b1_acceleration(D, False) == (False, "无加速事件")


def test_b2_false_without_a3_and_no_db_read(fake):
    fake.forbid_reads = True
    assert dragon_env.b2_oneword_made(D, False) == (False, "A3 未命中")


def test_b2_unknown_when_divergence_missing(fake):
    fake.divergence = float("nan")
    assert dragon_env.b2_oneword_made(D, True) == (None, "背离度无数据")


@pytest.mark.parametrize("d, expected", [(0.3, True), (0.29, False)])
def test_b2_threshold_inclusive(fake, d, expected):
    fake.divergence = d
    assert dragon_env.b2_oneword_made(D, True) == (expected, f"最大背离 {d:.2f}")


# ── B4 无板块支持 ─────────────────────────────────────────────────


def test_b4_false_when_top_group_has_theme_followers(fake):
    fake.tradable = [("600001", "机器人", 5), ("000002", "固态电池", 1)]
    ok, note = dragon_env.b4_no_sector(D)
    assert ok is False and note == "换手最高板 600001(机器人) 成员5——板块有跟随"


def test_b4_true_when_all_isolated(fake):
    """成员数 >= 2 才算跟随：成员 1 = 孤标。"""
    fake.tradable = [("600001", "机器人", 1), ("000002", "固态电池", 1)]
    ok, note = dragon_env.b4_no_sector(D)
    assert ok is True and note == "换手最高板全孤立：600001(机器人)成员1、000002(固态电池)成员1"


def test_b4_unknown_when_theme_not_covered(fake):
    """无题材标签 → 证据不足，不得当「全孤立」成立（旧版真值陷阱）。"""
    fake.tradable = [("600001", None, 0)]
    ok, note = dragon_env.b4_no_sector(D)
    assert ok is None and "无题材标签" in note


def test_b4_unknown_when_top_group_empty(fake):
    assert dragon_env.b4_no_sector(D) == (None, "换手最高板组无数据（梯队断层或 theme 未跑）")


# ── B5 高标跨题材 ─────────────────────────────────────────────────


def test_b5_unknown_when_no_top_group(fake):
    assert dragon_env.b5_cross_theme(D) == (None, "当日无同身位组")


def test_b5_unknown_when_all_tags_low_confidence(fake):
    fake.cross = (2, 3, 0)
    ok, note = dragon_env.b5_cross_theme(D)
    assert ok is None and "confidence<0.6" in note


def test_b5_true_when_credible_themes_differ(fake):
    fake.cross = (2, 3, 2)
    assert dragon_env.b5_cross_theme(D) == (True, "同身位 3 只（可信标注 2）跨 2 题材")


def test_b5_false_when_single_theme(fake):
    fake.cross = (1, 1, 1)
    assert dragon_env.b5_cross_theme(D) == (False, "同身位 1 只（可信标注 1）跨 1 题材")


# ── B3 胜出次日核按钮 ─────────────────────────────────────────────


def test_b3_live_reads_no_future(fake):
    """live：上界 = trade_date 本身，次收盘读不到 → UNKNOWN。"""
    fake.b3 = (None, 0)
    assert dragon_env.b3_next_day_dump(D, "live") == (None, "胜出者次日样本 0 不足")
    assert fake.params[-1][2] == D


def test_b3_replay_extends_bound_and_flags_mode(fake):
    fake.b3 = (-8.5, 4)
    ok, note = dragon_env.b3_next_day_dump(D, "replay")
    assert ok is True and note == "近4次胜出次日均涨幅 -8.5%（复盘态：读到次日收盘，当日实盘不可见）"
    assert fake.params[-1][2] == D + timedelta(days=3)


@pytest.mark.parametrize("p, expected", [(-7.0, False), (-7.01, True)])
def test_b3_threshold_strict_less(fake, p, expected):
    fake.b3 = (p, 3)
    ok, _ = dragon_env.b3_next_day_dump(D, "live")
    assert ok is expected


def test_b3_unknown_when_samples_short(fake):
    fake.b3 = (-9.0, 1)
    assert dragon_env.b3_next_day_dump(D, "live") == (None, "胜出者次日样本 1 不足")


# ── 裁决与顶层入口 ────────────────────────────────────────────────


def _cond(name, status):
    return (name, status, "")


def test_verdict_unfavorable_beats_falsified_goods():
    goods = [_cond("G1 可交易高度扩张", False)]
    bads = [_cond("B1 加速事件", True)]
    assert dragon_env._verdict(goods, bads) == "UNFAVORABLE"


def test_verdict_neutral_when_good_falsified():
    goods = [_cond("G1 可交易高度扩张", True), _cond("G2 主线梯队完整", False)]
    bads = [_cond("B1 加速事件", False)]
    assert dragon_env._verdict(goods, bads) == "NEUTRAL"


def test_verdict_favorable_when_core_true_and_enhancements_unknown():
    """增强条件 UNKNOWN 不再封死 FAVORABLE（theme_group 08-31 才上线）。"""
    goods = [_cond("G1 可交易高度扩张", True), _cond("G2 主线梯队完整", None),
             _cond("G3 断板负反馈温和", None), _cond("G4 胜者上方有空间", True)]
    bads = [_cond("B1 加速事件", False), _cond("B4 无板块支持", None)]
    assert dragon_env._verdict(goods, bads) == "FAVORABLE"


def test_verdict_neutral_when_all_unknown():
    goods = [_cond("G1 可交易高度扩张", None), _cond("G4 胜者上方有空间", None)]
    assert dragon_env._verdict(goods, []) == "NEUTRAL"


def test_rate_uses_passed_accel_and_locks_condition_order(fake, monkeypatch):
    monkeypatch.setattr(accelerate, "detect", lambda d: pytest.fail("不应重复查询加速事件"))
    fake.series = [6, 2, 2, 2, 2, 2, 2, 4, 4, 5]   # G1: 4→4→5 扩张；G4: H=5 <= 6-1
    fake.g2_ok = [("机器人", 5, 0, 4)]
    fake.fail_perf = [-2.0, -2.5]
    monkeypatch.setattr(dragon_env, "_tradable_theme_members",
                        lambda d: [("600001", "机器人", 5)])
    fake.cross = (1, 1, 1)
    fake.b3 = (None, 0)

    r = dragon_env.rate(D, accel=(False, "无加速事件", {"a3": False}))
    assert r["rating"] == "FAVORABLE"
    assert [n for n, _, _ in r["goods"]] == ["G1 可交易高度扩张", "G2 主线梯队完整",
                                             "G3 断板负反馈温和", "G4 胜者上方有空间"]
    assert [n for n, _, _ in r["bads"]] == ["B1 加速事件", "B2 高度靠一字制造",
                                            "B3 胜出次日核按钮", "B4 无板块支持",
                                            "B5 高标跨题材"]
    assert {n: s for n, s, _ in r["bads"]}["B4 无板块支持"] is False


def test_rate_calls_accelerate_when_accel_absent(fake, monkeypatch):
    calls: list[date] = []
    monkeypatch.setattr(accelerate, "detect",
                        lambda d: (calls.append(d), (True, "命中", {"a3": False}))[1])
    fake.series = []
    fake.divergence = 0.5
    r = dragon_env.rate(D)
    assert calls == [D]
    assert {n: s for n, s, _ in r["bads"]}["B1 加速事件"] is True
    assert r["rating"] == "UNFAVORABLE"


def test_ser_none_status_serializes_null():
    assert dragon_env._ser([("G1", None, "证据不足"), ("B1", False, "无")]) == [
        {"cond": "G1", "ok": None, "note": "证据不足"},
        {"cond": "B1", "ok": False, "note": "无"}]


def test_persist_writes_rating_and_condition_payload(monkeypatch):
    captured: dict = {}

    def fake_update(d, rating, reasons, risks):
        captured.update(date=d, rating=rating, reasons=reasons, risks=risks)
        return 1

    monkeypatch.setattr(dragon_env, "rate", lambda d, a, m: {
        "rating": "NEUTRAL",
        "goods": [("G1 可交易高度扩张", None, "H 序列仅 2 日")],
        "bads": [("B1 加速事件", False, "无加速事件")]})

    assert dragon_env.persist(D, persist_fn=fake_update) == 1
    assert captured["rating"] == "NEUTRAL"
    # None 状态必须落 jsonb null（F6 可审计），不能丢键
    assert '"ok": null' in captured["reasons"]
    assert '"cond": "B1 加速事件"' in captured["risks"]


def test_run_range_replays_only_existing_market_stat_days(fake, monkeypatch):
    """A8：遍历的是日期值而非 Series，且回填一律 replay 口径。"""
    fake.days = [date(2026, 9, 23), date(2026, 9, 24)]
    seen: list[tuple[date, str]] = []
    monkeypatch.setattr(dragon_env, "persist",
                        lambda d, accel=None, mode="live", *, persist_fn=None: seen.append((d, mode)) or 1)
    assert dragon_env.run_range(date(2026, 9, 1), D) == 2
    assert seen == [(date(2026, 9, 23), "replay"), (date(2026, 9, 24), "replay")]


# ── 只读汇总视图 ──────────────────────────────────────────────────


def test_ladder_health_aggregates_height_groups_and_sole_top(fake):
    fake.ladder_rows = [("601811", 4, True, True), ("001234", 3, False, False),
                        ("002614", 3, True, False), ("000850", 2, False, False),
                        ("002119", 2, True, False)]
    h = dragon_env.ladder_health(D)
    assert h["rows"] == 5
    assert h["height_nominal"] == 4 and h["height_exchange"] == 4
    assert h["groups"] == {4: 1, 3: 2, 2: 2}
    assert h["gaps"] == []                      # 4/3/2 层都有换手成员
    assert h["sole_top"] == "601811" and h["top_group"] == ["601811"]


def test_ladder_health_reports_gap_levels(fake):
    """断层 = 2~换手最高板之间没有换手成员的板数层。"""
    fake.ladder_rows = [("601811", 4, True, True), ("000850", 2, True, False)]
    h = dragon_env.ladder_health(D)
    assert (h["height_nominal"], h["height_exchange"]) == (4, 4)
    assert h["gaps"] == [3]


def test_ladder_health_without_exchange_members(fake):
    fake.ladder_rows = [("001234", 3, False, False)]
    h = dragon_env.ladder_health(D)
    assert h["height_nominal"] == 3 and h["height_exchange"] is None
    assert h["gaps"] == [] and h["top_group"] == [] and h["sole_top"] is None


def test_ladder_health_empty_day_is_not_legal_zero(fake):
    h = dragon_env.ladder_health(D)
    assert h == {"date": D, "rows": 0, "height_nominal": None, "height_exchange": None,
                 "groups": {}, "gaps": [], "sole_top": None, "top_group": []}


PROMO = [("1->2", 35, 9, 0.2571, 0.2286, 0.0285, -2.1969),
         ("2->3", 7, 3, 0.4286, 0.1429, 0.2857, -8.5351),
         ("3->4", 2, 1, None, None, None, None)]


def test_promotion_strength_deep_layer_and_none_rates(fake):
    fake.promo_rows = PROMO
    s = dragon_env.promotion_strength(D)
    assert [r["layer"] for r in s["layers"]] == ["1->2", "2->3", "3->4"]
    assert s["layers"][2]["rate_nominal"] is None and s["layers"][2]["promote_exchange"] == 1
    assert s["deep_layer"] == "2->3"           # 3->4 晋级率无数据（分母不足），不算「接得上」
    assert s["deep_rate_exchange"] == pytest.approx(0.1429)
    assert s["deep_delta_exchange"] is None


def test_promotion_strength_delta_from_prior_in_memory(fake):
    fake.promo_rows = PROMO
    s = dragon_env.promotion_strength(
        D, prior=[{"layer": "2->3", "rate_exchange": 0.10},
                  {"layer": "3->4", "rate_exchange": None}])
    assert s["deep_delta_exchange"] == pytest.approx(0.0429)
    assert s["layers"][0]["delta_exchange"] is None      # prior 缺该层 → 不补零
    assert s["layers"][2]["delta_exchange"] is None      # 当层无值


def test_promotion_strength_empty_day(fake):
    s = dragon_env.promotion_strength(D)
    assert s == {"date": D, "layers": [], "deep_layer": None,
                 "deep_rate_nominal": None, "deep_rate_exchange": None,
                 "deep_delta_exchange": None}
