"""C1 口径守护：连板跨停牌/缺日不断档（cont_days）。

裁决（docs/13 §S2 C1）：停牌、节假日等**缺行**不得打断连板——只有「非涨停」的
实际交易日才归零。实现落在 `algorithms/derive.py` 的 `_CONT_SQL`（窗口函数）与
`indicators.compute_derived`（Rust/Python 对账用）。

本测试两层：
1. **lkl 事实对账**：用 tests/oracle/fixtures/streaks_sample.json（lkl derived_bar
   原样导出，3 只高连板票 2012 行）逐行重演 `_CONT_SQL` 的窗口语义，要求与
    fixture 的 cont_days 完全一致——这是对「跨停牌不断档」最硬的证据。
2. **合成边界**：抽掉中间交易日（停牌）与插入一个非涨停日（断板），锁死二者语义不同。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from itertools import pairwise
from pathlib import Path

from emotion_core.algorithms import derive
from emotion_core.algorithms.indicators import compute_derived
from emotion_core.domain.bar import Bar
from emotion_core.utils.price import limit_up_price_cents

FIXTURE = Path(__file__).resolve().parents[1] / "oracle" / "fixtures" / "streaks_sample.json"


def simulate_cont_days(rows: list[tuple]) -> list[int]:
    """逐行重演 `_CONT_SQL` 语义：grp=非涨停累计数；同 grp 内涨停行取行号。

    与 SQL 一一对应：
      f.grp = SUM(CASE WHEN NOT is_limit_up THEN 1 ELSE 0 END)
                OVER (PARTITION BY code ORDER BY date)
      s.rn  = ROW_NUMBER() OVER (PARTITION BY code, grp ORDER BY date)
    行序 = 该股**已入库且当日有成交**的交易日序列 —— 缺行（停牌/节假日）根本不参与，
    所以不构成断档；只有真实的非涨停行才把 grp 推到下一段。

    每行取 `(date, is_limit_up)` 或 `(date, is_limit_up, suspended)`；`suspended=True`
    代表 volume=0 的顺延行：既不累加、也不推进 grp（SQL 里它被 `volume > 0` 滤掉，
    再由 zero_reset 把自己置 0）。
    """
    grp = 0
    counted: dict[int, int] = {}
    out: list[int] = []
    for row in rows:
        if len(row) == 2:
            _d, is_limit_up = row
            suspended = False
        else:
            _d, is_limit_up, suspended = row
        if suspended:
            out.append(0)
            continue
        if is_limit_up:
            counted[grp] = counted.get(grp, 0) + 1
            out.append(counted[grp])
        else:
            out.append(0)
            grp += 1
    return out


def test_cont_sql_primitives():
    """SQL 里必须只有这两种原语 + 零成交过滤；出现按日期差归零的逻辑即为语义变更。"""
    sql = derive._CONT_SQL
    assert "SUM(CASE WHEN NOT d.is_limit_up THEN 1 ELSE 0 END)" in sql
    assert "PARTITION BY d.code ORDER BY d.date" in sql
    assert "ROW_NUMBER() OVER (PARTITION BY code, grp ORDER BY date)" in sql
    assert "COALESCE(b.volume, 0) > 0" in sql, "停牌顺延行（零成交）不得打断连板"
    assert "LAG" not in sql, "连板不得用 LAG 按日期差重算（停牌会误判断档）"


def test_matches_lkl_fixture_row_by_row():
    """2012 行 lkl derived_bar 事实：重演 == cont_days（含 15 天缺口的 002693）。"""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)

    assert len(by_code) >= 3
    checked = 0
    for code, rs in by_code.items():
        rs.sort(key=lambda r: r["date"])
        sim = simulate_cont_days([(date.fromisoformat(r["date"]), r["is_limit_up"]) for r in rs])
        for r, got in zip(rs, sim, strict=False):
            assert got == r["cont_days"], f"{code} {r['date']}: 重演 {got} ≠ lkl {r['cont_days']}"
            checked += 1
    assert checked == len(rows)

    # fixture 里确实存在 ≥10 天的缺行（春节休市 + 一只停牌），否则本测试没打到点
    gaps = 0
    for rs in by_code.values():
        ds = sorted(date.fromisoformat(r["date"]) for r in rs)
        gaps += sum(1 for a, b in pairwise(ds) if (b - a).days >= 10)
    assert gaps >= 5, f"fixture 缺行样本不足（{gaps}）"


def test_missing_day_does_not_reset_but_a_flat_day_does():
    """停牌缺日：连板累加；真实非涨停日：归零。二者必须不同。"""
    d1, d3, d4 = date(2026, 1, 5), date(2026, 1, 7), date(2026, 1, 8)
    # d2 停牌（不入库）：d1 涨停 → d3 涨停，连板应继续
    assert simulate_cont_days([(d1, True), (d3, True)]) == [1, 2]
    # d2 正常交易但非涨停：断板，d3 重新从 1 起
    assert simulate_cont_days([(d1, True), (d3, False), (d4, True)]) == [1, 0, 1]


def _bar(code: str, d: date, close_cents: int, pre_cents: int, volume: int) -> Bar:
    return Bar(
        code=code,
        date=d,
        open_cents=close_cents,
        high_cents=close_cents,
        low_cents=close_cents,
        close_cents=close_cents,
        pre_close_cents=pre_cents,
        volume=volume,
        turnover_rate=1.0,
    )


def test_zero_volume_padding_row_does_not_break_chain():
    """600825 实况：10 连板 → 停牌 3 天（loader 补零成交顺延行）→ 复牌第 11 板。

    旧口径把顺延行当普通非涨停日 ⇒ 复牌当天 cont_days=1，与 `limit_pool_em`
    记的 11 对不上（review 告警「报告可用性 PARTIAL：对账差异 1 行」）。
    """
    code = "600825"
    boards = [
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 8),
        date(2026, 10, 9),
        date(2026, 10, 12),
    ]
    halted = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]

    bars: list[Bar] = []
    close = 531  # 5.31 元起板
    for d in boards:
        nxt = limit_up_price_cents(close, code)
        bars.append(_bar(code, d, nxt, close, 1000))
        close = nxt
    for d in halted:  # 停牌：loader 顺延出来的零成交行
        bars.append(_bar(code, d, close, close, 0))
    bars.append(_bar(code, date(2026, 10, 16), limit_up_price_cents(close, code), close, 2000))

    got = [b.cont_days for b in compute_derived(bars)]
    assert got == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0, 0, 0, 11]
    assert bars[-1].volume > 0 and bars[10].volume == 0  # 前提：第 11 行确实是顺延行

    # 同一序列在「缺行」口径下重演，结果必须一致
    flags: list[tuple] = [(d, True) for d in boards]
    flags += [(d, False, True) for d in halted]
    flags.append((date(2026, 10, 16), True))
    assert simulate_cont_days(flags) == got
