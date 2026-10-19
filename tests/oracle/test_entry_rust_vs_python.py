"""对账测试：买入信号（entry）Rust vs Python 逐条对账，含 note 文本。

覆盖 entry.rs 的全部纯判定函数（ctx 由本文件手工构造，不连库、不碰 ladder.build）：
- c1_uniqueness / c2_exchange / c3_elimination / c4_min_days / c5_strength_diverge
- w1_crowding（恒 True 的警告行）
- passed_of（Rust 收行列表）与 split_checklist（条件/警告结构分离）

Python 参考：`src/emotion_core/algorithms/entry.py`（SQL/落库/`_ctx` 的 IO 不在范围）。

接口差异（有意保留，测试按各自形状调用）：
- `passed_of`：Rust 收 `Vec<(label, Option<bool>, note)>`（行），Python 收 `Checklist`
  （落库契约）。两者聚合语义相同：过滤 None 后 all True。
- `split_checklist`：Rust 直接从行列表切分并投影（条件 None→False，警告 None→True）；
  Python 从 `Checklist` 拆出 `WarningOnly`。
- c3：Python 有 `REQUIRE_YESTERDAY_COMPETITION` 开关（默认 True）与 Rust 未实现的
  `_c3_secondary` / `c3_loose`（SECONDARY / RECOMMEND 两档）。本文件钉住「默认开关 +
  主版 c3」这条 Rust 已实现的路径，缺口由 `test_rust_gap_documented` 显式标记。
- `diverge_min` 必须是 float（5.0）：Rust 是 f64，格式化为 `{:?}` 才能与 Python
  `f"{threshold}%"` 的 `"5.0%"` 对齐；传 int 5 会分叉（Rust 恒 5.0）。

钉子（历史漂移，勿删）：
- `test_c1_name_fallback_pin`：2026-10-19 前 Rust 在 ctx.name 为空时回落 `cand.code`，
  note 变成 `600825(600825) 5板`，Python 是 `(600825) 5板`。
- `test_c5_threshold_format_pin`：Rust 用 `{}` 打印 f64 会给 `5%`，Python 给 `5.0%`；
  已改 `{:?}` 对齐。
"""
from __future__ import annotations

import sys
from datetime import date

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import entry  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402

D1 = date(2026, 3, 2)
CAND_CODE = "600825"
OTHER = "000001"

COND_LABELS = [label for _, _, label in entry._CONDITIONS]
WARN_LABELS = [label for _, _, label in entry._WARNINGS]
ALL_LABELS = COND_LABELS + WARN_LABELS


def _py_cand(code: str = CAND_CODE, days: int = 5, ex: bool = True):
    return entry.LadderDay(date=D1, code=code, cont_days=days, is_exchange=ex,
                           is_top=False, is_sole_top=False, y_top_group_count=0,
                           y_top_survivor_count=0, is_suspended=False)


def _rs_cand(code: str = CAND_CODE, days: int = 5, ex: bool = True):
    return rust.LadderDay(D1.isoformat(), code, days, ex, False, False, 0, 0, False)


def _ctx(**over):
    """构造 (py_ctx dict, rs EntryCtx)，默认「标准窗口全过」。"""
    code = over.pop("code", CAND_CODE)
    days = over.pop("days", 5)
    ex = over.pop("ex", True)
    p = {"sole": _py_cand(code, days, ex), "rows": [_py_cand(code, days, ex)],
         "y_comp": (2, 1), "y_surv": ({code}, {code}), "y_peer": 2,
         "window": "NORMAL", "min_days": 4, "diverge_min": 5.0,
         "c5_verifiable": True, "name": "", "turnover_rate": None, "bomb_times": None}
    p.update(over)

    def _rs_of(c):
        return None if c is None else _rs_cand(c.code, c.cont_days, c.is_exchange)

    y_codes, survivors = p["y_surv"]
    r = {"sole": _rs_of(p["sole"]), "rows": [_rs_of(c) for c in p["rows"]],
         "y_codes": set(y_codes), "survivors": set(survivors)}
    for k in ("y_comp", "y_peer", "window", "min_days", "diverge_min", "c5_verifiable",
              "name", "turnover_rate", "bomb_times"):
        r[k] = p[k]
    return p, rust.EntryCtx(**r)


def _cmp(cand, funcs, py_ctx, rs_ctx, what=""):
    """逐函数比对 (ok, note)；funcs = [(名称, Python fn, Rust fn)]。"""
    rs_cand = _rs_cand(cand.code, cand.cont_days, cand.is_exchange)
    for name, py_fn, rs_fn in funcs:
        py = py_fn(cand, py_ctx)
        rs = rs_fn(rs_cand, rs_ctx)
        assert tuple(rs) == tuple(py), f"{what} {name}\n  Rust   {rs}\n  Python {py}"


_ALL = [("c1", entry.c1_uniqueness, rust.c1_uniqueness),
        ("c2", entry.c2_exchange, rust.c2_exchange),
        ("c3", entry.c3_elimination, rust.c3_elimination),
        ("c4", entry.c4_min_days, rust.c4_min_days),
        ("c5", entry.c5_strength_diverge, rust.c5_strength_diverge),
        ("w1", entry.w1_crowding, rust.w1_crowding)]


def test_default_ctx_all_conditions():
    """默认 ctx（标准窗口、全过）六条逐字一致，含 note。"""
    p, r = _ctx()
    cand = _py_cand()
    _cmp(cand, _ALL, p, r, "默认")


def test_c1_name_and_sole():
    p, r = _ctx()
    _cmp(_py_cand(), [("c1", entry.c1_uniqueness, rust.c1_uniqueness)], p, r, "c1")
    # sole 为 None / 另有其票
    p, r = _ctx(sole=None)
    _cmp(_py_cand(), [("c1", entry.c1_uniqueness, rust.c1_uniqueness)], p, r, "sole=None")
    p, r = _ctx(sole=_py_cand(OTHER, 6, True))
    _cmp(_py_cand(), [("c1", entry.c1_uniqueness, rust.c1_uniqueness)], p, r, "sole=other")


def test_c1_name_fallback_pin():
    """ctx.name 为空 → `(600825) 5板`（不是 `600825(600825) 5板`）；有 name 时带上。"""
    p, r = _ctx()
    assert entry.c1_uniqueness(_py_cand(), p) == (True, "唯一换手高标: (600825) 5板")
    assert rust.c1_uniqueness(_rs_cand(), r) == (True, "唯一换手高标: (600825) 5板")
    p, r = _ctx(name="平安银行")
    note = "唯一换手高标: 平安银行(600825) 5板"
    assert entry.c1_uniqueness(_py_cand(), p) == (True, note)
    assert rust.c1_uniqueness(_rs_cand(), r) == (True, note)


def test_c3_matrix():
    """c3 五条分支：竞争不足 / 幸存不唯一 / 候选不在昨日组 / 幸存者非候选 / 通过。"""
    cases = [
        {"y_comp": (1, 1)},                                  # 昨日组仅 1 只 → 无竞争
        {"y_comp": (2, 2)},                                  # 幸存 2 只 → 不唯一
        {"y_surv": ({OTHER}, {CAND_CODE})},                   # 候选不在昨日最高组
        {"y_surv": ({CAND_CODE}, {OTHER})},                   # 幸存者非候选
        {"y_comp": (3, 1), "y_surv": ({CAND_CODE, OTHER}, {CAND_CODE})},  # 通过
    ]
    for over in cases:
        p, r = _ctx(**over)
        _cmp(_py_cand(), [("c3", entry.c3_elimination, rust.c3_elimination)], p, r,
             f"c3 {over}")


def test_c3_competition_switch_pinned_on():
    """Rust 无 REQUIRE_YESTERDAY_COMPETITION 开关：必须确认 Python 侧是默认 True。"""
    assert entry.REQUIRE_YESTERDAY_COMPETITION is True
    p, r = _ctx(y_comp=(1, 1))
    # 开关 True → 竞争不足判 False；两侧一致
    assert entry.c3_elimination(_py_cand(), p) == rust.c3_elimination(_rs_cand(), r)
    assert entry.c3_elimination(_py_cand(), p)[0] is False


def test_c4_threshold():
    for days, md in [(5, 4), (4, 4), (3, 4), (3, 3), (4, 5)]:
        p, r = _ctx(days=days, min_days=md)
        _cmp(_py_cand(days=days), [("c4", entry.c4_min_days, rust.c4_min_days)], p, r,
             f"c4 days={days} md={md}")


@pytest.mark.parametrize("over", [
    {"window": "NORMAL"},                                  # 标准窗口不适用
    {"window": "ENHANCED"},                                # 缺换手 → ⚠ 无法核验
    {"window": "ENHANCED", "bomb_times": 2},               # 炸板回封
    {"window": "ENHANCED", "turnover_rate": 6.25},         # 换手达标
    {"window": "ENHANCED", "turnover_rate": 2.5},          # 换手不足
    {"window": "ENHANCED", "turnover_rate": 6.255},        # 三位小数取整
    {"window": "ENHANCED", "turnover_rate": float("nan")},  # NaN = 缺失
    {"window": "ENHANCED", "c5_verifiable": False},        # EM 池窗口外 → UNKNOWN
    {"window": "ENHANCED", "diverge_min": 3.5, "turnover_rate": 3.5},  # 阈值边界（>=）
    {"window": "ENHANCED", "diverge_min": 3.5, "turnover_rate": 3.49},
])
def test_c5_matrix(over):
    p, r = _ctx(**over)
    _cmp(_py_cand(), [("c5", entry.c5_strength_diverge, rust.c5_strength_diverge)], p, r,
         f"c5 {over}")


def test_c5_unknown_not_veto():
    """c5 返回 None（不可核验）时两侧同为 None，且不参与 passed。"""
    p, r = _ctx(window="ENHANCED", c5_verifiable=False)
    py = entry.c5_strength_diverge(_py_cand(), p)
    rs = rust.c5_strength_diverge(_rs_cand(), r)
    assert py[0] is None and rs[0] is None
    assert py[1] == rs[1]


def test_c5_threshold_format_pin():
    """阈值文案必须是 `5.0%` / `3.5%`（Rust `{}` 会给 `5%`）。"""
    for threshold in (5.0, 3.5):
        p, r = _ctx(window="ENHANCED", diverge_min=threshold)
        py = entry.c5_strength_diverge(_py_cand(), p)
        rs = rust.c5_strength_diverge(_rs_cand(), r)
        assert f"阈值{threshold}%" in py[1] and f"阈值{threshold}%" in rs[1]
        assert py == rs


def test_w1_crowding():
    """扎堆 / 不扎堆 / 无行：恒 True，只有 note 不同。"""
    cases = [
        ([_py_cand(CAND_CODE, 5), _py_cand(OTHER, 5)], "扎堆"),
        ([_py_cand(CAND_CODE, 5), _py_cand(OTHER, 4)], "不扎堆"),
        ([], "无行"),
        ([_py_cand(CAND_CODE, 9), _py_cand(OTHER, 9), _py_cand("002415", 9)], "三只扎堆"),
    ]
    for rows, what in cases:
        p, r = _ctx(rows=rows)
        py = entry.w1_crowding(rows[0] if rows else _py_cand(), p)
        rs = rust.w1_crowding(_rs_cand(), r)
        assert py == rs, f"w1 {what}\n  Rust   {rs}\n  Python {py}"
        assert py[0] is True


# ---------- 聚合：passed_of / split_checklist ----------

def _rows(oks: list) -> list:
    """按真实标签顺序构造行：5 条件 + W1 警告。"""
    return [(label, ok, f"note-{label}") for label, ok in zip(ALL_LABELS, oks, strict=True)]


@pytest.mark.parametrize("oks,expected", [
    ([True, True, True, True, True, True], True),
    ([True, False, True, True, True, True], False),
    ([True, None, True, True, True, True], True),   # UNKNOWN 不否决
    ([None, None, None, None, None, None], True),   # 全 None 也算过（显式契约）
    ([True, True, True, True, True, False], True),  # 警告 False 也不否决
])
def test_passed_of_parity(oks, expected):
    rows = _rows(oks)
    assert rust.passed_of(rows) is expected
    assert entry.passed_of(entry._to_checklist(rows)) is expected


def test_split_checklist_parity():
    """Rust 切分投影（条件 None→False，警告 None→True）与 Python 结构分离同语义。"""
    rows = _rows([True, False, None, True, True, None])
    conds, warns = rust.split_checklist(rows)
    assert conds == [(COND_LABELS[0], True, "note-" + COND_LABELS[0]),
                     (COND_LABELS[1], False, "note-" + COND_LABELS[1]),
                     (COND_LABELS[2], False, "note-" + COND_LABELS[2]),
                     (COND_LABELS[3], True, "note-" + COND_LABELS[3]),
                     (COND_LABELS[4], True, "note-" + COND_LABELS[4])]
    assert warns == [(WARN_LABELS[0], True, "note-" + WARN_LABELS[0])]

    cl, wo = entry.split_checklist(entry._to_checklist(rows))
    assert cl.w1_crowding is None                      # 警告被移出条件列
    assert wo.w1_crowding is None
    assert (cl.c1_uniqueness, cl.c2_exchange, cl.c3_elimination,
            cl.c4_min_days, cl.c5_strength_diverge) == (True, False, None, True, True)
    assert cl.passed is False and rust.passed_of(rows) is False


def test_split_checklist_warning_never_vetoes():
    """警告行返回 False 也不否决：Rust warns 保留 False，passed 仍 True。"""
    rows = _rows([True, True, True, True, True, False])
    conds, warns = rust.split_checklist(rows)
    assert warns == [(WARN_LABELS[0], False, "note-" + WARN_LABELS[0])]
    assert rust.passed_of(rows) is True
    cl, wo = entry.split_checklist(entry._to_checklist(rows))
    assert wo.w1_crowding is False and cl.passed is True


def test_rust_gap_documented():
    """Rust 未实现 Python 的次级/准买入 c3 变体——缺口显式标记。

    哪天 Rust 补上这两个函数，本测试会红：届时把 `_c3_secondary` / `c3_loose`
    纳入对账并删掉本测试。
    """
    assert callable(entry._c3_secondary) and callable(entry.c3_loose)
    for name in ("_c3_secondary", "c3_loose", "c3_secondary"):
        assert not hasattr(rust, name), f"Rust 已实现 {name}，请并入对账"
