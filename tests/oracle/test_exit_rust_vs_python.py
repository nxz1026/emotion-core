"""对账测试：exit Rust vs Python 同输入同输出。

覆盖 3 个纯逻辑函数 + 1 个编排函数：
- sell_signal(code, when, reason, buy_window) — 信号构造（exec_hint 恒 CLOSE_ALL）
- sell(pos, stat, day) — 退潮(b) / 断板(a) / 无建议(c) 判定
- suggestions(trade_date, stat, positions, bars) — 组合路径（退潮优先 / 逐仓断板）

Python 参考：emotion_core/algorithms/exit.py 的 _sell_signal / _sell / suggestions
（suggestions 的 SQL 取数用 monkeypatch 桩掉，与 tests/unit/test_exit.py 同法）。
Rust 侧 SQL 取数同样不在对账范围：suggestions 的 stat / positions / bars 由
调用方注入（对应 Python 的 _stat / _open_positions / _derived 结果）。
"""
from __future__ import annotations

import sys
from datetime import date
from enum import Enum
from typing import Any

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import exit  # noqa: E402
from emotion_core.algorithms.exit import Holding, _sell, _sell_signal  # noqa: E402
from emotion_core.domain.bar import DerivedBar  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402

D = date(2026, 9, 4)          # 建议日
BAR_DATE = date(2026, 8, 28)  # 判据行日期
POS_DATE = date(2026, 8, 20)  # 开仓日

STAT_EBB = {"phase": "退潮", "buy_window": "NONE", "force_liquidate": True}
STAT_CLIMAX = {"phase": "高潮", "buy_window": "ENHANCED", "force_liquidate": False}


def pos(code: str = "000017", entry: date = POS_DATE) -> Holding:
    return Holding(code=code, entry_date=entry)


def day(up: bool = True, one_word: bool = False, cont: int = 6,
        code: str = "000017", d: date = BAR_DATE) -> DerivedBar:
    return DerivedBar(code=code, date=d, is_limit_up=up, is_limit_down=False,
                      is_one_word=one_word, is_exchange=up and not one_word,
                      is_bomb=False, touched_limit=up, cont_days=cont, amplitude=3.2)


def rust_stat(force: bool) -> rust.MarketStat:
    """MarketStat 只读 force_liquidate 判定，phase/buy_window 随意填。"""
    return rust.MarketStat("退潮" if force else "高潮", "NONE" if force else "ENHANCED", force)


def rust_day(d: DerivedBar | None):
    """domain.DerivedBar → Rust 判据行三元组 (date, is_limit_up, cont_days)；None 透传。

    Rust 侧不持有 domain.DerivedBar 的 pyclass 镜像（同 accelerate.rs：输入基本类型），
    判定只读 date / is_limit_up / cont_days 三列。
    """
    if d is None:
        return None
    return (str(d.date), d.is_limit_up, d.cont_days)


def _val(v: Any) -> str:
    """枚举取 .value（Python 3.11+ str(StrEnum) 返回 "Cls.MEMBER" 而非值）；Rust 侧为 str。"""
    return v.value if isinstance(v, Enum) else str(v)


def sig_dict(sig: Any) -> dict:
    """SellSignal → 可比对字典（Python datetime.date 与 Rust ISO 字符串统一）。"""
    return {
        "code": sig.code,
        "date": sig.date if isinstance(sig.date, str) else sig.date.isoformat(),
        "action": _val(sig.action),
        "source": _val(sig.source),
        "status": sig.status,
        "reason": sig.reason,
        "buy_window": sig.buy_window,
        "exec_hint": sig.exec_hint,
    }


def assert_same_signal(rust_sig, py_sig) -> None:
    """逐字段比对 Rust/Python SellSignal；checklist 契约单独断言（恒六项 UNKNOWN）。"""
    r, p = sig_dict(rust_sig), sig_dict(py_sig)
    assert r == p, f"rust={r}\npy  ={p}"
    cl = py_sig.checklist
    assert [cl.c1_uniqueness, cl.c2_exchange, cl.c3_elimination,
            cl.c4_min_days, cl.c5_strength_diverge, cl.w1_crowding] == [None] * 6
    assert cl.passed is True  # 六项 UNKNOWN：无否决条件（空真）


# ────────────────────────── sell_signal ──────────────────────────

@pytest.mark.parametrize("code,when,reason,buy_window", [
    ("000017", POS_DATE, "退潮期清仓建议", "NONE"),
    ("601988", BAR_DATE, "断板建议（原6连板今日未封）", ""),
    ("000001", D, "断板建议（原连板今日未封）", ""),
    ("688432", D, "自定义理由", "STANDARD"),
])
def test_sell_signal(code, when, reason, buy_window):
    py_sig = _sell_signal(code, when, reason, buy_window)
    rust_sig = rust.sell_signal(code, str(when), reason, buy_window)
    assert_same_signal(rust_sig, py_sig)
    assert rust_sig.exec_hint == "CLOSE_ALL"  # 契约 v2：SELL 恒清仓
    assert rust_sig.action == "SELL" and rust_sig.source == "live"
    assert rust_sig.status == "SUGGESTED"


# ────────────────────────── sell ──────────────────────────

@pytest.mark.parametrize("stat,day_kwargs,expected", [
    # (stat, day 参数, 期望：None 或 (reason 关键字, date, buy_window))
    (None, None, None),                                             # 缺行 = 缺数，无建议（F6）
    (None, dict(up=True), None),                                    # 涨停继续持有
    (None, dict(up=True, one_word=True), None),                     # 一字涨停也持有
    (None, dict(up=False, cont=0), ("断板", BAR_DATE, "")),          # 0 板：reason 不留数字
    (None, dict(up=False, cont=6), ("断板", BAR_DATE, "")),
    (None, dict(up=False, cont=1, one_word=True), ("断板", BAR_DATE, "")),  # 一字今日未封=断板
    (STAT_EBB, None, ("清仓", POS_DATE, "NONE")),                   # 退潮：date=开仓日
    (STAT_EBB, dict(up=True), ("清仓", POS_DATE, "NONE")),          # 退潮优先：封板也清
    (STAT_EBB, dict(up=False, cont=3), ("清仓", POS_DATE, "NONE")),  # 退潮优先于断板
    ({"force_liquidate": False}, None, None),                       # 非退潮 + 缺行 → 无建议
    ({"force_liquidate": False}, dict(up=False, cont=2), ("断板", BAR_DATE, "")),
    ({"force_liquidate": False}, dict(up=True), None),
])
def test_sell(stat, day_kwargs, expected):
    py_day = day(**day_kwargs) if day_kwargs is not None else None
    py_sig = _sell(pos(), stat, py_day)
    rust_sig = rust.sell(rust.Holding("000017", str(POS_DATE)),
                         rust_stat(stat["force_liquidate"]) if stat else None,
                         rust_day(py_day))
    if expected is None:
        assert py_sig is None and rust_sig is None
    else:
        assert py_sig is not None and rust_sig is not None
        assert_same_signal(rust_sig, py_sig)
        reason_key, d, bw = expected
        assert reason_key in rust_sig.reason
        assert rust_sig.date == str(d)
        assert rust_sig.buy_window == bw


# ────────────────────────── suggestions ──────────────────────────

def patch_io(monkeypatch, *, stat, positions, bars) -> dict[str, Any]:
    calls: dict[str, Any] = {"derived": []}

    def fake_derived(d: date, code: str) -> DerivedBar | None:
        calls["derived"].append((d, code))
        return bars.get(code)

    monkeypatch.setattr(exit, "_stat", lambda d: stat)
    monkeypatch.setattr(exit, "_open_positions", lambda: list(positions))
    monkeypatch.setattr(exit, "_derived", fake_derived)
    return calls


@pytest.mark.parametrize("stat,positions,bars,expected_codes", [
    # 退潮：全部清仓，date=建议日，不读判据行
    (STAT_EBB, [pos("601988"), pos("000001")], {}, ["601988", "000001"]),
    # 退潮：即使判据行是涨停也清仓（退潮不读判据）
    (STAT_EBB, [pos("601988")], {"601988": day(up=True, code="601988")}, ["601988"]),
    # 非退潮：逐仓断板检查，只出断板
    (STAT_CLIMAX, [pos("601988"), pos("000001")],
     {"601988": day(up=True, code="601988"),
      "000001": day(up=False, cont=0, code="000001")}, ["000001"]),
    # stat=None：退化断板检查
    (None, [pos("000001")], {"000001": day(up=False, cont=2, code="000001")}, ["000001"]),
    # 缺判据行 = 缺数，不当断板（F6）
    (STAT_CLIMAX, [pos("000001")], {"000001": None}, []),
    # bars 缺 code：同缺行
    (STAT_CLIMAX, [pos("000001")], {}, []),
    # 空持仓
    (STAT_EBB, [], {}, []),
    # 多持仓全断板
    (STAT_CLIMAX, [pos("601988"), pos("000001")],
     {"601988": day(up=False, cont=1, code="601988"),
      "000001": day(up=False, cont=4, code="000001")}, ["601988", "000001"]),
])
def test_suggestions(monkeypatch, stat, positions, bars, expected_codes):
    calls = patch_io(monkeypatch, stat=stat, positions=positions, bars=bars)
    py_sigs = exit.suggestions(D)
    rust_sigs = rust.suggestions(
        str(D),
        rust_stat(stat["force_liquidate"]) if stat else None,
        [rust.Holding(p.code, str(p.entry_date)) for p in positions],
        {c: rust_day(b) for c, b in bars.items()},
    )
    assert [s.code for s in py_sigs] == expected_codes
    assert [s.code for s in rust_sigs] == expected_codes
    for r, p in zip(rust_sigs, py_sigs):
        assert_same_signal(r, p)
    # 调用轨迹：退潮不读判据行；非退潮逐仓读（含最终无建议的持仓）
    if stat and stat["force_liquidate"]:
        assert calls["derived"] == []
    else:
        assert calls["derived"] == [(D, p.code) for p in positions]


def test_suggestions_liquidate_date_rewrite(monkeypatch):
    """退潮建议日期 = 建议日（不是开仓日）：replace(s, date=trade_date) 语义。"""
    patch_io(monkeypatch, stat=STAT_EBB, positions=[pos("601988", entry=POS_DATE)], bars={})
    py_sigs = exit.suggestions(D)
    rust_sigs = rust.suggestions(str(D), rust_stat(True),
                                 [rust.Holding("601988", str(POS_DATE))], {})
    assert len(py_sigs) == len(rust_sigs) == 1
    assert py_sigs[0].date == D and rust_sigs[0].date == str(D)
    assert_same_signal(rust_sigs[0], py_sigs[0])
