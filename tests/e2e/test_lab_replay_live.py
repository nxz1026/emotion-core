"""Lab 变体回放的真库等价性（默认跳过，`EC_LIVE_DB=1` 才跑）。

为什么要这一条：Lab 的变体搜索只有在「基线变体 == 主线现行口径」时才有意义。
判据用**最硬的形态**——同样的日期区间，Lab 回放出的观察名单与 `signal` 表里
`source='replay'` 的 BUY 行必须逐条相同（集合等值，日期+代码都不许差）。

面板 pickle（`var/lab/panel.pkl`）不在版本库里：先 `python -m emotion_core.lab.panel
--build --start 2023-12-15 --end 2026-10-20` 生成，再跑本测试。

跑法：
    EC_LIVE_DB=1 PYTHONPATH=src .venv/bin/pytest tests/e2e/test_lab_replay_live.py -q
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EC_LIVE_DB") != "1",
    reason="真库校准：设 EC_LIVE_DB=1 才跑（默认 DB-free 基线不受影响）",
)

START, END = date(2024, 1, 1), date(2026, 10, 20)
#: 主线全史回放的执行日（`signal` 表 377 条 replay BUY 就是那天写进去的）：
#: c5 的「可核验」基准 = 回放当天，换一天会让 ENHANCED 窗口的 c5 结论变 UNKNOWN。
AS_OF = date(2026, 10, 21)


@pytest.fixture(scope="module")
def panel():
    from emotion_core.lab import panel as labpanel

    try:
        return labpanel.load()
    except FileNotFoundError as exc:  # pragma: no cover - 环境缺面板
        pytest.skip(str(exc))


def test_baseline_matches_signal_table(panel):
    """默认变体必须复刻主线：观察名单逐条相等。"""
    from emotion_core.lab import db as labdb
    from emotion_core.lab import replay
    from emotion_core.lab.rules import BASELINE

    prep = replay.prepare(panel)
    sig = replay.signals(prep, BASELINE, as_of=AS_OF, start=START, end=END)
    live = labdb.query_df(
        "SELECT confirm_date AS d0, code FROM signal"
        " WHERE action = 'BUY' AND source = 'replay'"
        "   AND confirm_date BETWEEN %s AND %s",
        (START, END),
    )
    live["d0"] = pd.to_datetime(live["d0"]).dt.date
    lab_set = {(r.d0, r.code) for r in sig.itertuples()}
    db_set = {(r.d0, r.code) for r in live.itertuples()}
    assert lab_set == db_set, (
        f"仅在 lab：{sorted(lab_set - db_set)[:10]}；仅在 signal：{sorted(db_set - lab_set)[:10]}"
    )


def test_baseline_phase_matches_market_stat(panel):
    """相位序列也要同源：变体默认参数下重跑的 phase/window 必须等于库里那两列。

    允许的例外只有「库里补算过后来才有的字段」，所以这里要求逐日相等——不等就说明
    面板少的列/口径对不上，变体搜索会从一开始就偏移。
    """
    from emotion_core.lab import db as labdb
    from emotion_core.lab import replay
    from emotion_core.lab.rules import BASELINE

    prep = replay.prepare(panel)
    st = replay.phases(prep, BASELINE)
    db = labdb.query_df(
        "SELECT date, phase, buy_window FROM market_stat WHERE date BETWEEN %s AND %s", (START, END)
    )
    diff = [
        (r.date, r.phase, r.buy_window, st[r.date].phase, st[r.date].buy_window)
        for r in db.itertuples()
        if r.date in st and (st[r.date].phase != r.phase or st[r.date].buy_window != r.buy_window)
    ]
    assert not diff, f"相位漂移 {len(diff)} 天，样例 {diff[:5]}"
