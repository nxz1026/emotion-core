"""对账测试：Python 梯队实现 vs lkl 产物（ladder_day 表）。

fixture：
- fixtures/ladder_day.json：lkl ladder_day 全量快照（8656 行 / 661 天）。
- fixtures/ladder_inputs.json：3 天（含最大日 239 行）的候选行快照 + 当日换手代码集，
  由 data/loader.load_ladder_candidates / load_exchange_codes 从 DB 导出。

验证 fold() 逐字段重现 lkl 的行集合与 is_top / is_sole_top / R2 计数。
SQL 行选择（主板 / 次新 / ST 过滤）由同一 SQL 导出输入，另有 661 天全量 DB 回放
验证过 0 处不一致（见模块 docstring）。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from emotion_core.algorithms.ladder import fold

FIXTURES = Path(__file__).parent / "fixtures"
FIELDS = ("cont_days", "is_exchange", "is_top", "is_sole_top",
          "y_top_group_count", "y_top_survivor_count")


def _load(name: str):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_fold_matches_lkl_ladder_day():
    expected = {}
    for r in _load("ladder_day.json"):
        expected.setdefault(r["date"], {})[r["code"]] = r
    inputs = _load("ladder_inputs.json")

    mismatches: list[str] = []
    for ds, inp in sorted(inputs.items()):
        assert date.fromisoformat(inp["prev"]) < date.fromisoformat(ds)
        got = fold(date.fromisoformat(ds),
                   [tuple(r) for r in inp["rows"]],
                   [tuple(r) for r in inp["prev_rows"]],
                   set(inp["today_exchange"]))
        gmap, exp = {r.code: r for r in got}, expected[ds]
        if set(gmap) != set(exp):
            mismatches.append(
                f"{ds} 行集合: py_only={sorted(set(gmap) - set(exp))[:5]} "
                f"lkl_only={sorted(set(exp) - set(gmap))[:5]}")
            continue
        for code, r in gmap.items():
            for field in FIELDS:
                if getattr(r, field) != exp[code][field]:
                    mismatches.append(f"{ds} {code} {field}: "
                                      f"py={getattr(r, field)} lkl={exp[code][field]}")
    assert not mismatches, "对账失败 %d 处:\n%s" % (
        len(mismatches), "\n".join(mismatches[:20]))


def test_fold_output_sorted_by_board_desc():
    """输出契约：按 (cont_days DESC, code ASC) 排序，与 lkl SQL 的 ORDER BY 一致。"""
    inputs = _load("ladder_inputs.json")
    for ds, inp in sorted(inputs.items()):
        got = fold(date.fromisoformat(ds),
                   [tuple(r) for r in reversed(inp["rows"])],
                   [], set())
        assert [(r.cont_days, r.code) for r in got] == sorted(
            ((r.cont_days, r.code) for r in got), key=lambda t: (-t[0], t[1]))
