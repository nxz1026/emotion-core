"""口径守护：次新豁免地板必须 ≤ 数据起点（否则窗口起始 90 天全被剔光）。

事故形态（真库实测，2026-09-27 复核）：地板取 lkl 的 2023-11-26，而本库 daily_bar
最早 2024-01-02 → `GREATEST(目标日-90d, 地板)` 在 2024-01-02~2024-03-29 这 58 个
交易日都 < first_bar_date → **每一只股票都判为次新**，情绪计数全 0（实测
market_stat 中 58 天 limit_up_count=0，2024-04-01 起恢复 78）。地板晚于数据起点
就是口径事故，故锁死不变式并禁止 SQL 里再出现硬编码日期。
"""
from __future__ import annotations

from emotion_core.algorithms import accelerate, promotion
from emotion_core.utils.config import CONFIG


def test_floor_covers_the_data_layer_start():
    """不变式：地板 ≥ 数据层最早 bar 日期（本库 = EM 日线首日 2024-01-02）。

    地板早于数据首日 → 首日的 first_bar_date 落在 GREATEST(...) 之外 →
    头 90 天全部股票被当次新剔光（58 个交易日 limit_up_count=0）。
    """
    from datetime import date
    assert CONFIG.NEW_ISSUER_FLOOR >= date(2024, 1, 2), (
        f"地板 {CONFIG.NEW_ISSUER_FLOOR} 早于数据首日 → 窗口起始段计数全 0")


def test_floor_is_not_the_lkl_window_literal():
    from datetime import date
    assert CONFIG.NEW_ISSUER_FLOOR != date(2023, 11, 26), "不得再用 lkl 的数据起点"


def test_filters_take_the_floor_from_config():
    assert CONFIG.NEW_ISSUER_FLOOR in accelerate._NEW_ISSUER_PARAMS
    assert CONFIG.NEW_ISSUER_MIN_DAYS in accelerate._NEW_ISSUER_PARAMS
    assert "%(floor)s" in promotion._PAIRS_SQL
    assert "%(days)s" in promotion._PAIRS_SQL
    assert "2023-11-26" not in promotion._PAIRS_SQL, "硬编码日期必须删净"


def test_min_days_still_ninety():
    """90 自然日是 lkl 裁决口径，本次只修地板，不动门槛。"""
    assert CONFIG.NEW_ISSUER_MIN_DAYS == 90
