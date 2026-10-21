"""盘中判据单测（ADR 0003 D5 / D8）。

每条测试都对应模块里一句「为什么不这么做」的话。顺序按模块组织。
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest

from emotion_core.algorithms import intraday as it


def _q(**kw: Any) -> dict[str, Any]:
    """E3 形状的 quote 字典。只填测试关心的字段，其余按涨停封板常见形态补齐。"""
    base: dict[str, Any] = {
        "code": "sh600519",
        "name": "贵州茅台",
        "last": 11.0,
        "prev_close": 10.0,
        "date": "2026-10-21",
        "time": "09:42:00",
        "bid1_price": 11.0,
        # 默认给一个**合格**的封板量（门槛 30,000 手 = 3,000,000 股）。
        # 夹具的默认值应该是「一切正常」，要让某条不成立就当场覆盖那一个字段 ——
        # 否则每个用例都在测三件事，红的时候不知道是谁红的。
        "bid1_vol": 4_000_000.0,
        "ask1_price": None,
        "ask1_vol": None,
    }
    base.update(kw)
    return base


# ───────────────────────── 板块涨跌幅 ─────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600519", 10.0),
        ("601398", 10.0),
        ("603236", 10.0),
        ("000002", 10.0),
        ("002415", 10.0),
        ("300750", 20.0),
        ("301029", 20.0),
        ("688981", 20.0),
        ("689009", 20.0),
        ("830799", 30.0),
        ("430047", 30.0),
        # 920 是北交所 2026 年启用的新号段，不在 8/4 前缀里
        ("920296", 30.0),
    ],
)
def test_board_pct_limit_by_code(code: str, expected: float) -> None:
    """按代码前缀判板块。**±10% 一刀切是错的** —— 20%/30% 板会永远漏判。"""
    assert it.board_pct_limit(code) == expected


def test_st_on_main_board_is_five_percent() -> None:
    """ST 主板是 5%。这条只能靠名字判 —— 代码里没有任何 ST 信息。"""
    assert it.board_pct_limit("600519", name="*ST康佳") == 5.0


def test_st_detection_is_case_insensitive() -> None:
    assert it.is_st_name("*st康佳") is True
    assert it.is_st_name("贵州茅台") is False
    assert it.is_st_name(None) is False


def test_unknown_st_is_optimistic_not_pessimistic() -> None:
    """不传名字按「非 ST」算 —— 这是**有意的乐观**。

    宁可算出偏高的涨停价（判不出封板、少报），也不要偏低的（假封板、错报）。
    """
    assert it.board_pct_limit("600519") == 10.0


def test_is_main_board() -> None:
    assert it.is_main_board("600519") is True
    assert it.is_main_board("000002") is True
    assert it.is_main_board("300750") is False
    assert it.is_main_board("688981") is False


# ───────────────────────── 涨停价 ─────────────────────────


def test_limit_up_price_rounds_to_cent() -> None:
    """涨停价必须四舍五入到**分**。浮点 `10.0 * 1.1 = 11.000000000000002`。"""
    assert it.limit_price("600519", 10.0) == 11.0


@pytest.mark.parametrize(
    ("pre_close", "expected"),
    [
        (3.03, 3.33),  # 3.03 * 1.1 = 3.333 -> 3.33
        (7.77, 8.55),  # 7.77 * 1.1 = 8.547 -> 8.55
        (12.34, 13.57),  # 13.574 -> 13.57
        (1.01, 1.11),  # 1.111 -> 1.11
    ],
)
def test_limit_up_price_half_up_rounding(pre_close: float, expected: float) -> None:
    """ROUND_HALF_UP，不是 Python 默认的银行家舍入。"""
    assert it.limit_price("600519", pre_close) == pytest.approx(expected, abs=0.005)


def test_limit_up_price_returns_none_without_prev_close() -> None:
    """没有昨收就算不出涨停价 —— 返回 None，不许拍一个数。"""
    assert it.limit_price("600519", 0.0) is None
    assert it.limit_price("600519", -1.0) is None


def test_growth_board_limit_price() -> None:
    """创业板 20% 板：昨收 10 涨到 12 才是涨停，涨到 11 不是。"""
    assert it.limit_price("300750", 10.0) == 12.0


# ───────────────────────── 涨幅 ─────────────────────────


def test_chg_pct_basic() -> None:
    assert it.chg_pct(_q(last=11.0, prev_close=10.0)) == pytest.approx(10.0)


def test_chg_pct_none_without_prev_close() -> None:
    """缺昨收返回 None，**不是 0.0** —— 「没数据」与「平盘」是两件事。"""
    assert it.chg_pct(_q(last=11.0, prev_close=None)) is None
    assert it.chg_pct(_q(last=11.0, prev_close=0.0)) is None


def test_chg_pct_none_without_last() -> None:
    assert it.chg_pct(_q(last=None)) is None


# ───────────────────────── 是否涨停 ─────────────────────────


def test_is_at_limit_true_at_exact_price() -> None:
    """**`>=` 不是 `>`** —— 实盘成交价可以正好等于四舍五入后的涨停价。"""
    assert it.is_at_limit(_q(last=11.0, prev_close=10.0)) is True


def test_is_at_limit_false_below() -> None:
    assert it.is_at_limit(_q(last=10.99, prev_close=10.0)) is False


def test_is_at_limit_none_when_undecidable() -> None:
    """停牌（last/prev_close 都是 None）返回 **None 不是 False**。

    混起来会让「今天没有一只封板」和「今天没采到数据」变成同一件事。
    """
    assert it.is_at_limit(_q(last=None, prev_close=None)) is None
    assert it.is_at_limit(_q(last=11.0, prev_close=None)) is None


def test_is_at_limit_uses_code_for_board_width() -> None:
    """同一帧数据，20% 板的票在 11.0 不算涨停（要 12.0）。"""
    q = _q(last=11.0, prev_close=10.0)
    assert it.is_at_limit(q, code="300750") is False
    assert it.is_at_limit(q, code="600519") is True


def test_is_at_limit_st_uses_five_percent() -> None:
    """ST 主板 5%：昨收 10，涨停 10.5。11.0 已经**超过** ST 的涨停价 ——
    数据异常（新浪不会给超过涨停的价格），按涨停处理。
    """
    assert it.is_at_limit(_q(last=10.5, prev_close=10.0, name="*ST康佳")) is True
    assert it.is_at_limit(_q(last=10.4, prev_close=10.0, name="*ST康佳")) is False


def test_is_at_limit_accepts_explicit_code_with_prefixed_quote() -> None:
    """quote 内的 code 是全限定的 `sh600519`，也能被识别成 6 位。"""
    q = _q(code="sh600519", last=11.0, prev_close=10.0)
    assert it.is_at_limit(q) is True


# ───────────────────────── seal_hit ─────────────────────────


def test_seal_hit_all_conditions_met() -> None:
    assert it.seal_hit(_q(), 0, time(9, 42)) is True


def test_seal_hit_rejects_after_deadline() -> None:
    """10:00 之后封的不算 —— C3 的切片就是这一条。"""
    assert it.seal_hit(_q(), 0, time(10, 0, 1)) is False
    assert it.seal_hit(_q(), 0, time(10, 0)) is True


def test_seal_hit_rejects_when_prev_days_unknown() -> None:
    """`prev_days is None` **不成立**：不知道昨天是不是板，不能说今天是首板。"""
    assert it.seal_hit(_q(), None, time(9, 42)) is False


def test_seal_hit_rejects_consecutive_boards() -> None:
    """连板不是首板。ADR C3 只对首板做过统计。"""
    assert it.seal_hit(_q(), 1, time(9, 42)) is False
    assert it.seal_hit(_q(), 5, time(9, 42)) is False


def test_seal_hit_requires_large_bid() -> None:
    """买一量门槛：`SEAL_BID1_VOL_MIN` 手 = 3,000,000 股。"""
    below = it.SEAL_BID1_VOL_MIN * 100 - 1
    assert it.seal_hit(_q(bid1_vol=float(below)), 0, time(9, 42)) is False
    exact = it.SEAL_BID1_VOL_MIN * 100
    assert it.seal_hit(_q(bid1_vol=float(exact)), 0, time(9, 42)) is True


def test_seal_hit_with_missing_bid_volume_is_false_not_crash() -> None:
    """停牌时 bid1_vol 是 None —— 不成立，但不许崩。"""
    assert it.seal_hit(_q(bid1_vol=None), 0, time(9, 42)) is False


def test_seal_hit_does_not_count_unknown_limit_state() -> None:
    """判不了涨停时不是「封板」。"""
    assert it.seal_hit(_q(last=None, prev_close=None), 0, time(9, 42)) is False


# ───────────────────────── seal_weaken ─────────────────────────


def test_seal_weaken_when_price_drops() -> None:
    """跌破涨停价 = 炸板实锤。"""
    assert it.seal_weaken(_q(last=10.98), 3_000_000.0) is True


def test_seal_weaken_when_volume_decays() -> None:
    """买一量衰减到 30% 以下 = 撤单在发生。"""
    sealed = 4_000_000.0
    assert it.seal_weaken(_q(bid1_vol=sealed * 0.29), sealed) is True
    assert it.seal_weaken(_q(bid1_vol=sealed * 0.31), sealed) is False


def test_seal_weaken_volume_compares_against_trigger_not_last_tick() -> None:
    """基准是**触发那一刻**的量。逐帧比较会把自己滚成 0、永远不报松动。"""
    sealed = 4_000_000.0
    # 逐帧掉 10%，从 4,000,000 掉到 1,000,000 才响（25%）
    assert it.seal_weaken(_q(bid1_vol=2_000_000.0), sealed) is False
    assert it.seal_weaken(_q(bid1_vol=1_000_000.0), sealed) is True


def test_seal_weaken_is_false_when_undecidable() -> None:
    """不知道就别报松动 —— 否则停牌会天天误报。"""
    assert it.seal_weaken(_q(last=None, prev_close=None), 3_000_000.0) is False
    assert it.seal_weaken(_q(bid1_vol=None), 3_000_000.0) is False
    assert it.seal_weaken(_q(), 0.0) is False


# ───────────────────────── SealTracker ─────────────────────────


def test_tracker_needs_consecutive_ticks() -> None:
    """`SEAL_CONFIRM_TICKS = 2`：单帧不触发，第二帧才触发。"""
    t = it.SealTracker()
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is True


def test_tracker_resets_on_a_broken_tick() -> None:
    """中间断一帧，连续计数归零 —— 这正是「抗撤单抖动」的意思。"""
    t = it.SealTracker()
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False
    assert t.feed(_q(bid1_vol=1.0), prev_days=0, now=time(9, 42)) is False  # 断了
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False  # 重新从 1 开始
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is True


def test_tracker_fires_only_once() -> None:
    """触发之后再喂同一只票返回 False —— 不该每 30 秒重复推一条飞书。"""
    t = it.SealTracker()
    t.feed(_q(), prev_days=0, now=time(9, 42))
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is True
    for _ in range(5):
        assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False
    assert t.is_sealed("600519") is True


def test_tracker_records_sealed_volume() -> None:
    """记下触发那一刻的买一量，给 seal_weaken 当基准。"""
    t = it.SealTracker()
    t.feed(_q(bid1_vol=5_000_000.0), prev_days=0, now=time(9, 42))
    t.feed(_q(bid1_vol=5_000_000.0), prev_days=0, now=time(9, 42))
    assert t.sealed_volume("600519") == 5_000_000.0
    assert t.sealed_volume("000002") is None


def test_tracker_keeps_codes_independent() -> None:
    """两票互不干扰：A 的两帧不能让 B 触发。"""
    t = it.SealTracker()
    a = _q(code="sh600519")
    b = _q(code="sz000002")
    t.feed(a, prev_days=0, now=time(9, 42))
    t.feed(b, prev_days=0, now=time(9, 42))
    assert t.is_sealed("000002") is False
    assert t.feed(a, prev_days=0, now=time(9, 42)) is True
    assert t.is_sealed("600519") is True
    assert t.is_sealed("000002") is False


def test_tracker_reset_clears_everything() -> None:
    t = it.SealTracker()
    t.feed(_q(), prev_days=0, now=time(9, 42))
    t.feed(_q(), prev_days=0, now=time(9, 42))
    t.reset()
    assert t.is_sealed("600519") is False
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False  # 从头数


def test_tracker_custom_need() -> None:
    """`need` 可换（Lab 调参用），不改模块常量。"""
    t = it.SealTracker(need=3)
    for _ in range(2):
        assert t.feed(_q(), prev_days=0, now=time(9, 42)) is False
    assert t.feed(_q(), prev_days=0, now=time(9, 42)) is True


# ───────────────────────── 常量与 ADR 一致性 ─────────────────────────


def test_constants_match_adr_d3() -> None:
    """D3 表格里写着「改任一常量必须同步改本节表格（数字即口径）」。

    这条把表格里的数字钉在代码上 —— 改常量会红，提醒去改 ADR。
    """
    assert it.SCAN_INTERVAL_SEC == 30
    assert it.CHG_PCT_MIN == 9.7
    assert it.EARLY_SEAL_DEADLINE == time(10, 0)
    assert it.SEAL_BID1_VOL_MIN == 30_000
    assert it.SEAL_CONFIRM_TICKS == 2
    assert it.SEAL_WEAKEN_RATIO == 0.30
    assert it.MAX_WATCH_CODES == 40


def test_module_has_no_io_imports() -> None:
    """纯函数模块：不 import psycopg / requests / httpx / CONFIG。

    D10 条件 ① 没满足前这个模块就该能独立测完 —— 一旦它开始连网连库，
    「先做判据」这件事就不成立了。
    """
    import inspect

    src = inspect.getsource(it)
    for banned in ("psycopg", "requests", "httpx", "urllib.request", "utils.config"):
        assert banned not in src, f"intraday.py 里不该出现 {banned}"
