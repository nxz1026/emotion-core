"""对账测试：连板梯队（ladder）Rust vs Python 同输入同输出。

覆盖 ladder.rs 的三个纯函数：
- `top_group` 同身位组（换手板口径，停牌行不进竞争）
- `sole_top` 唯一换手高标（换手同身位仅 1 只 且 >= MIN_LEADER_DAYS）
- `fold_candidates` 候选 → 梯队行（is_top / is_sole_top / R2 计数 / 排序 / 停牌旁路）

Python 参考实现：`src/emotion_core/algorithms/ladder.py`（SQL 编排 build/persist 不在对账范围）。
另有 `tests/oracle/test_ladder_vs_lkl.py` 对的是 lkl 快照，两者互补。

钉子（历史漂移，勿删）：
- `test_fold_suspended_bypass`：R67-2 的当日停牌行（旁路展示、不计身位、不进最高组）。
  2026-09-30 编出的 .so 完全没有 is_suspended 字段、`fold_candidates` 也不收 suspended
  参数 —— 与源码分叉了一个已发布的策略特性，由本文件钉住。
"""
from __future__ import annotations

import sys
from datetime import date

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import ladder as py_ladder  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402
from emotion_core.domain.ladder import LadderDay  # noqa: E402
from emotion_core.utils.config import CONFIG  # noqa: E402

D1 = date(2026, 3, 2)

FIELDS = ("date", "code", "cont_days", "is_exchange", "is_top", "is_sole_top",
          "y_top_group_count", "y_top_survivor_count", "is_suspended")


def _py_day(d: date, code: str, days: int, ex: bool, susp: bool = False) -> LadderDay:
    return LadderDay(date=d, code=code, cont_days=days, is_exchange=ex, is_top=False,
                     is_sole_top=False, y_top_group_count=0, y_top_survivor_count=0,
                     is_suspended=susp)


def _rs_day(d: date, code: str, days: int, ex: bool, susp: bool = False):
    return rust.LadderDay(d.isoformat(), code, days, ex, False, False, 0, 0, susp)


def _py_fields(r: LadderDay) -> tuple:
    return (str(r.date), r.code, r.cont_days, r.is_exchange, r.is_top, r.is_sole_top,
            r.y_top_group_count, r.y_top_survivor_count, r.is_suspended)


def _rs_fields(r) -> tuple:
    return (r.date, r.code, r.cont_days, r.is_exchange, r.is_top, r.is_sole_top,
            r.y_top_group_count, r.y_top_survivor_count, r.is_suspended)


def _assert_rows_same(py_rows: list[LadderDay], rs_rows: list, where: str) -> None:
    assert len(rs_rows) == len(py_rows), (
        f"{where}: 行数 Rust {len(rs_rows)} vs Python {len(py_rows)}")
    for i, (p, r) in enumerate(zip(py_rows, rs_rows, strict=True)):
        assert _rs_fields(r) == _py_fields(p), (
            f"{where}: 第 {i} 行不同\n  Rust   {_rs_fields(r)}\n  Python {_py_fields(p)}")


# ---------- 常量一致性 ----------

def test_min_leader_days_constant_in_sync():
    """Rust `ladder::MIN_LEADER_DAYS` 是硬编码 4，必须与 CONFIG 同值。"""
    assert CONFIG.MIN_LEADER_DAYS == 4
    # 双向：cont_days=4 判高标、cont_days=3 不判
    assert rust.sole_top([_rs_day(D1, "600825", 4, True)], None).code == "600825"
    assert rust.sole_top([_rs_day(D1, "600825", 3, True)], None) is None
    assert py_ladder.sole_top([_py_day(D1, "600825", 3, True)]) is None


# ---------- top_group ----------

@pytest.mark.parametrize("rows,expected", [
    ([(D1, "600825", 5, True), (D1, "000001", 3, True)], ["600825"]),
    ([(D1, "600825", 5, True), (D1, "000001", 5, True)], ["600825", "000001"]),
    ([(D1, "600825", 5, False), (D1, "000001", 3, True)], ["000001"]),   # 一字板不进最高组
    ([(D1, "600825", 5, False)], []),                                     # 全一字 → 空
    ([], []),
])
def test_top_group(rows, expected):
    py_rows = [_py_day(*r) for r in rows]
    rs_rows = [_rs_day(*r) for r in rows]
    py_top = py_ladder.top_group(py_rows)
    rs_top = rust.top_group(rs_rows)
    assert [r.code for r in py_top] == expected
    _assert_rows_same(py_top, rs_top, "top_group")


def test_top_group_excludes_suspended():
    """停牌行（is_suspended=True）不进最高组：即使它的连板数最高。"""
    rows = [(D1, "600825", 5, True), (D1, "600825", 9, False, True)]
    py_top = py_ladder.top_group([_py_day(*r) for r in rows])
    rs_top = rust.top_group([_rs_day(*r) for r in rows])
    assert [r.code for r in py_top] == ["600825"]
    assert py_top[0].cont_days == 5
    _assert_rows_same(py_top, rs_top, "top_group/suspended")


# ---------- sole_top ----------

@pytest.mark.parametrize("rows,min_days,expected", [
    ([(D1, "600825", 5, True)], None, "600825"),
    ([(D1, "600825", 5, True), (D1, "000001", 3, True)], None, "600825"),
    ([(D1, "600825", 5, True), (D1, "000001", 5, True)], None, None),
    ([(D1, "600825", 3, True)], None, None),          # 未达门槛 4
    ([(D1, "600825", 3, True)], 3, "600825"),         # 门槛下探
    ([(D1, "600825", 9, False, True)], None, None),   # 停牌不算
])
def test_sole_top(rows, min_days, expected):
    py_rows = [_py_day(*r) for r in rows]
    rs_rows = [_rs_day(*r) for r in rows]
    py_sole = py_ladder.sole_top(py_rows, min_days)
    rs_sole = rust.sole_top(rs_rows, min_days)
    assert (py_sole.code if py_sole else None) == expected
    if py_sole is None:
        assert rs_sole is None
    else:
        assert _rs_fields(rs_sole) == _py_fields(py_sole)


# ---------- fold_candidates ----------

# 场景 = (rows, prev_rows, today_exchange, suspended)
FOLD_CASES = [
    # 单高标：昨日组 2 只、今日 1 只幸存
    ([("600825", 5, True), ("000001", 3, True)],
     [("600825", 4, True), ("000001", 2, True)], {"600825"}, []),
    # 一字板更高身位但不进最高组
    ([("600825", 5, False), ("000001", 3, True)], [], set(), []),
    # 同身位两只 → is_sole_top 全 False
    ([("600825", 5, True), ("000001", 5, True)], [("600825", 4, True)], {"600825"}, []),
    # 停牌旁路行：昨日组 2 只、今日换成 000001 幸存
    ([("600825", 5, True)],
     [("600825", 4, True), ("000001", 4, True)], {"000001"}, [("002415", 4)]),
    # 全空
    ([], [], set(), []),
    # 昨日组 3 只、今日 2 只幸存（R2 分子 > 1）
    ([("600825", 6, True)],
     [("600825", 5, True), ("000001", 5, True), ("002415", 5, True)],
     {"600825", "000001"}, []),
    # 排序：板数降序，同板数 code 升序（含停牌行插位）
    ([("600825", 3, True), ("000001", 5, True), ("002415", 3, True)],
     [("000001", 4, True)], {"000001"}, [("600826", 3)]),
]


@pytest.mark.parametrize("rows,prev_rows,today_exchange,suspended", FOLD_CASES)
def test_fold_candidates(rows, prev_rows, today_exchange, suspended):
    py_rows = py_ladder.fold(D1, rows, prev_rows, today_exchange, suspended)
    rs_rows = rust.fold_candidates(D1.isoformat(), rows, prev_rows, today_exchange, suspended)
    _assert_rows_same(py_rows, rs_rows, "fold_candidates")


def test_fold_sorting_order():
    """输出按 (板数降序, code 升序)——R2 计数与展示都依赖这个序。"""
    rows = [("600825", 3, True), ("000001", 5, True), ("002415", 3, True)]
    py_rows = py_ladder.fold(D1, rows, [], set(), [("600826", 3)])
    rs_rows = rust.fold_candidates(D1.isoformat(), rows, [], set(), [("600826", 3)])
    expected = ["000001", "002415", "600825", "600826"]
    assert [r.code for r in py_rows] == expected
    assert [r.code for r in rs_rows] == expected


def test_fold_suspended_bypass():
    """停牌行不改任何身位数字：is_exchange/is_top/is_sole_top 全 False，计数与他人同值。"""
    rows = [("600825", 5, True)]
    prev_rows = [("600825", 4, True), ("000001", 4, True)]
    without = py_ladder.fold(D1, rows, prev_rows, {"600825"}, [])
    with_susp = py_ladder.fold(D1, rows, prev_rows, {"600825"}, [("002415", 4)])
    rs = rust.fold_candidates(D1.isoformat(), rows, prev_rows, {"600825"}, [("002415", 4)])

    assert len(with_susp) == len(without) + 1
    susp = next(r for r in with_susp if r.code == "002415")
    assert susp.is_suspended is True
    assert susp.is_exchange is False and susp.is_top is False and susp.is_sole_top is False
    # 身位数字与非停牌版本一致（停牌行不参与 R2）
    base = next(r for r in with_susp if r.code == "600825")
    base_without = next(r for r in without if r.code == "600825")
    assert (base.cont_days, base.is_top, base.is_sole_top, base.y_top_group_count,
            base.y_top_survivor_count) == (
                base_without.cont_days, base_without.is_top, base_without.is_sole_top,
                base_without.y_top_group_count, base_without.y_top_survivor_count)
    _assert_rows_same(with_susp, rs, "fold_candidates/suspended")
