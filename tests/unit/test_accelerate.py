"""加速事件算法单元测试。

无 DB：SQL 行选择（主板前缀 / 次新剔除）与真实库差分对账由一次性脚本完成；
本文件锁纯逻辑与降级分支——A1/A2/A3 的边界、F6 不补 0、A8 全量取行、
V3 停牌断档归零、基线滚动窗口与预热期。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from emotion_core.algorithms import accelerate

D = date(2024, 3, 1)


class FakeQuery:
    """query_df 替身：按 SQL 特征分派；可断言某路径未读库。"""

    def __init__(self):
        self.heights_row: tuple[float | None, float | None] | None = None
        self.counts: tuple[int, int] = (0, 0)
        self.bars: list[tuple[str, date, bool, bool]] = []
        self.calendar: list[date] = []
        self.baseline: list[float] = []
        self.calls: list[str] = []
        self.forbid_reads = False

    def __call__(self, sql: str, params=()) -> pd.DataFrame:
        self.calls.append(sql)
        if self.forbid_reads:
            raise AssertionError(f"该路径不应读库: {sql}")
        if "max(d.cont_days) h" in sql:
            h, th = self.heights_row if self.heights_row else (None, None)
            return pd.DataFrame({"h": [h], "th": [th]})
        if "count(*) FILTER" in sql:
            zt, ow = self.counts
            return pd.DataFrame({"zt": [zt], "ow": [ow]})
        if "SELECT code, date, is_one_word" in sql:
            return pd.DataFrame(self.bars, columns=["code", "date", "is_one_word", "is_limit_up"])
        if "SELECT DISTINCT date" in sql:
            return pd.DataFrame({"date": list(self.calendar)})
        if "oneword_ratio FROM market_stat" in sql:
            return pd.DataFrame({"oneword_ratio": list(self.baseline)})
        raise AssertionError(f"未预期的 SQL: {sql}")


@pytest.fixture
def fake(monkeypatch) -> FakeQuery:
    f = FakeQuery()
    monkeypatch.setattr(accelerate, "query_df", f)
    return f


def _days(*offsets: int) -> list[date]:
    return [D - timedelta(days=o) for o in offsets]


class TestHeights:
    """heights：NaN 归零，不因无行报错。"""

    def test_no_data_returns_zero(self, fake):
        assert accelerate.heights(D) == (0, 0)

    def test_nominal_and_tradable(self, fake):
        fake.heights_row = (5, 3)
        assert accelerate.heights(D) == (5, 3)

    def test_no_tradable_high(self, fake):
        """全是缩量一字：换手最高板为 NULL → 0（名义高度仍保留）。"""
        fake.heights_row = (6, None)
        assert accelerate.heights(D) == (6, 0)


class TestOnewordRatio:
    """oneword_ratio：无涨停 → None（F6 不补 0）。"""

    def test_no_limit_up_is_none_not_zero(self, fake):
        fake.counts = (0, 0)
        assert accelerate.oneword_ratio(D) is None

    def test_ratio(self, fake):
        fake.counts = (10, 4)
        assert accelerate.oneword_ratio(D) == 0.4

    def test_ratio_rounded_4(self, fake):
        fake.counts = (3, 1)
        assert accelerate.oneword_ratio(D) == 0.3333


class TestTopStreak:
    """_top_streak：A8 全量取行 + V3 市场日历断档归零。"""

    def test_zero_height_skips_db(self, fake):
        assert accelerate._top_streak(D, 0) == 0
        assert fake.calls == []

    def test_consecutive_oneword(self, fake):
        ds = _days(2, 1, 0)
        fake.calendar = ds
        fake.bars = [("600000", d, True, True) for d in ds]
        assert accelerate._top_streak(D, 5) == 3

    def test_non_oneword_day_resets(self, fake):
        """断档日归零后重数；返回值是「截至今日的当前连续长度」，非历史最长段。"""
        ds = _days(3, 2, 1, 0)
        fake.calendar = ds
        fake.bars = [("600000", ds[0], True, True), ("600000", ds[1], True, True),
                     ("600000", ds[2], False, True), ("600000", ds[3], True, True)]
        assert accelerate._top_streak(D, 5) == 1
        fake.bars = [("600000", d, True, True) for d in ds[:3]] + [
            ("600000", ds[3], False, True)]
        assert accelerate._top_streak(D, 5) == 0   # 今日断档 → 0，尽管前三日连续

    def test_suspension_gap_resets(self, fake):
        """停牌缺行：相邻两行中间隔了其他交易日 → 断档归零（V3）。"""
        ds = _days(4, 2, 0)
        fake.calendar = ds + [D - timedelta(days=3), D - timedelta(days=1)]
        fake.bars = [("600000", ds[0], True, True), ("600000", ds[1], True, True),
                     ("600000", ds[2], True, True)]
        # 无停牌应为 3；calendar 里补进 gap 日后每段断开，最长段 = 1
        assert accelerate._top_streak(D, 5) == 1

    def test_max_across_codes(self, fake):
        ds = _days(2, 1, 0)
        fake.calendar = ds
        fake.bars = [("600000", d, True, True) for d in ds] + [("000001", ds[2], True, True)]
        assert accelerate._top_streak(D, 5) == 3

    def test_empty_bars_returns_zero(self, fake):
        fake.calendar = _days(1, 0)
        assert accelerate._top_streak(D, 4) == 0


class TestBaselineRatio:
    """_baseline_ratio：prior_ratios 纯内存滚动（不读库）；预热期无基线。"""

    def test_memory_path_never_reads_db(self, fake):
        fake.forbid_reads = True
        vals = [0.1 + i / 100 for i in range(accelerate.ACCEL_BASELINE_WINDOW)]
        assert accelerate._baseline_ratio(D, vals) == round(float(pd.Series(vals).median()), 4)

    def test_memory_path_uses_only_window_and_latest_first(self, fake):
        fake.forbid_reads = True
        win = accelerate.ACCEL_BASELINE_WINDOW
        head = [0.30] * win                       # 最新在前：窗口内 30 个 0.30
        tail = [0.90] * 10                        # 窗口外旧值必须被忽略
        assert accelerate._baseline_ratio(D, head + tail) == 0.30

    def test_memory_path_insufficient_samples_is_none(self, fake):
        fake.forbid_reads = True
        short = [0.2] * (accelerate.ACCEL_BASELINE_WINDOW - 1)
        assert accelerate._baseline_ratio(D, short) is None

    def test_memory_path_none_and_nan_do_not_count(self, fake):
        fake.forbid_reads = True
        win = accelerate.ACCEL_BASELINE_WINDOW
        vals = [None, float("nan")] + [0.2] * (win - 1)
        assert accelerate._baseline_ratio(D, vals) is None

    def test_db_path_median(self, fake):
        fake.baseline = [0.20] * 20 + [0.40] * 10
        assert accelerate._baseline_ratio(D) == 0.2

    def test_db_path_insufficient_is_none(self, fake):
        fake.baseline = [0.20] * (accelerate.ACCEL_BASELINE_WINDOW - 1)
        assert accelerate._baseline_ratio(D) is None


@pytest.fixture
def stub(monkeypatch):
    """替换四项 IO：直接给定判据输入，测 detect 的聚合与边界。"""
    def _apply(nominal_h=5, tradable_h=5, ratio=None, base=None, streak=0):
        monkeypatch.setattr(accelerate, "heights", lambda d: (nominal_h, tradable_h))
        monkeypatch.setattr(accelerate, "oneword_ratio", lambda d: ratio)
        monkeypatch.setattr(accelerate, "_baseline_ratio", lambda d, p=None: base)
        monkeypatch.setattr(accelerate, "_top_streak", lambda d, h: streak)
    return _apply


class TestHit:
    """命中规则 A1 and (A2 or A3)。"""

    @pytest.mark.parametrize("a1,a2,a3,expected", [
        (True, True, False, True),
        (True, False, True, True),
        (True, True, True, True),
        (True, False, False, False),
        (False, True, True, False),      # 无高度背离：A2/A3 只是表现
        (False, False, False, False),
    ])
    def test_truth_table(self, a1, a2, a3, expected):
        assert accelerate._hit(a1, a2, a3) is expected


class TestDetect:
    """detect 聚合、A1/A2/A3 边界与 facts/reason 契约。"""

    def test_gap_boundary(self, stub):
        threshold = accelerate.ACCEL_HEIGHT_GAP
        stub(nominal_h=threshold + 4, tradable_h=4, streak=accelerate.ACCEL_ONEWORD_DAYS)
        assert accelerate.detect(D)[0] is True
        stub(nominal_h=threshold + 4, tradable_h=5, streak=accelerate.ACCEL_ONEWORD_DAYS)
        assert accelerate.detect(D)[0] is False           # gap = threshold-1

    def test_streak_boundary(self, stub):
        stub(nominal_h=6, tradable_h=4, streak=accelerate.ACCEL_ONEWORD_DAYS)
        assert accelerate.detect(D)[0] is True
        stub(nominal_h=6, tradable_h=4, streak=accelerate.ACCEL_ONEWORD_DAYS - 1,
             ratio=0.9, base=0.1)
        assert accelerate.detect(D)[0] is True            # A2 差 1 日，A3 补位
        stub(nominal_h=6, tradable_h=4, streak=0, ratio=None, base=0.1)
        assert accelerate.detect(D)[0] is False

    def test_a3_needs_ratio_at_threshold_and_above_baseline(self, stub):
        thr = accelerate.ACCEL_ONEWORD_RATIO
        stub(nominal_h=6, tradable_h=4, ratio=thr, base=thr - 0.01)
        assert accelerate.detect(D)[0] is True
        stub(nominal_h=6, tradable_h=4, ratio=thr - 0.01, base=0.1)
        assert accelerate.detect(D)[0] is False           # 低于绝对阈值
        stub(nominal_h=6, tradable_h=4, ratio=thr, base=thr)
        assert accelerate.detect(D)[0] is False           # 等于基线：非「高于」

    def test_a3_false_when_ratio_or_baseline_missing(self, stub):
        stub(nominal_h=6, tradable_h=4, ratio=None, base=0.1)
        assert accelerate.detect(D)[2]["a3"] is False     # 无涨停（F6）→ A3 不成立
        stub(nominal_h=6, tradable_h=4, ratio=0.9, base=None)
        assert accelerate.detect(D)[2]["a3"] is False     # 预热期无基线 → A3 不成立

    def test_facts_and_reason(self, stub):
        stub(nominal_h=7, tradable_h=4, ratio=0.5, base=0.3, streak=3)
        hit, reason, facts = accelerate.detect(D)
        assert hit is True
        assert facts == {"nominal_h": 7, "tradable_h": 4, "gap": 3, "streak": 3,
                         "oneword_ratio": 0.5, "baseline_ratio": 0.3,
                         "a1": True, "a2": True, "a3": True}
        assert "A1高度背离3" in reason and "A2连续一字3日" in reason and "A3一字占比0.5" in reason

    def test_reason_marks_missing_values(self, stub):
        stub(nominal_h=4, tradable_h=4, ratio=None, base=None, streak=0)
        hit, reason, facts = accelerate.detect(D)
        assert hit is False
        assert facts["oneword_ratio"] is None and facts["baseline_ratio"] is None
        assert "A3一字占比—/基线—" in reason

    def test_prior_ratios_forwarded_to_baseline(self, monkeypatch):
        got = {}

        def _baseline(d, prior=None):
            got["prior"] = prior
            return 0.1

        monkeypatch.setattr(accelerate, "heights", lambda d: (6, 4))
        monkeypatch.setattr(accelerate, "oneword_ratio", lambda d: 0.5)
        monkeypatch.setattr(accelerate, "_top_streak", lambda d, h: 0)
        monkeypatch.setattr(accelerate, "_baseline_ratio", _baseline)
        rolling = [0.4] * 30
        accelerate.detect(D, rolling)
        assert got["prior"] is rolling
