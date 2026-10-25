"""对账测试：exit Rust vs Python —— **只剩信号构造器同口径**（W3′ 后有意分叉）。

W3′（奎爷 2026-10-21 批准）把卖出规则从「退潮清仓 / 断板卖出」重写为口径B
「T+2 开盘无条件卖 / 跌停封死顺延 / T+3 强制了结」，理由见 algorithms/exit.py
模块文档（持有到断板 mean −1.90%/胜率 38.1%；T+2 开盘卖 +3.54%/68%）。

Rust `core/src/exit.rs` 停在旧规则、**不再同步**：
- 它是「参考实现 + 对账基准」，生产走 Python（services/ 只 import Python）；
- 新规则需要交易日历（held_days），而 Rust 侧刻意只接收基本类型入参（同
  accelerate.rs 先例），把日历搬进 Rust 只为一个不再上线的实现，不值得。

因此本文件保留 `sell_signal` 的逐字段对账（该构造器与规则无关，仍应一致），
并用 `test_rust_exit_frozen_at_repealed_rule` 把这个**有意分叉**钉在源码上：
Rust 若哪天同步了口径B，该测试会失败并提醒删除本条豁免。

Python 参考：emotion_core/algorithms/exit.py 的 _sell_signal / _sell（判定见
tests/unit/test_exit.py，逐分支覆盖口径B）。
"""

from __future__ import annotations

import sys
from datetime import date
from enum import Enum
from typing import Any

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms.exit import Holding, _sell_signal  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402
from emotion_core.domain.bar import DerivedBar  # noqa: E402

D = date(2026, 9, 4)  # 建议日
BAR_DATE = date(2026, 8, 28)  # 判据行日期
POS_DATE = date(2026, 8, 20)  # 开仓日

STAT_EBB = {"phase": "退潮", "buy_window": "NONE", "force_liquidate": True}
STAT_CLIMAX = {"phase": "高潮", "buy_window": "ENHANCED", "force_liquidate": False}


def pos(code: str = "000017", entry: date = POS_DATE) -> Holding:
    return Holding(code=code, entry_date=entry)


def day(
    up: bool = True, one_word: bool = False, cont: int = 6, code: str = "000017", d: date = BAR_DATE
) -> DerivedBar:
    return DerivedBar(
        code=code,
        date=d,
        is_limit_up=up,
        is_limit_down=False,
        is_one_word=one_word,
        is_exchange=up and not one_word,
        is_bomb=False,
        touched_limit=up,
        cont_days=cont,
        amplitude=3.2,
    )


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
    assert [
        cl.c1_uniqueness,
        cl.c2_exchange,
        cl.c3_elimination,
        cl.c4_min_days,
        cl.c5_strength_diverge,
        cl.w1_crowding,
    ] == [None] * 6
    assert cl.passed is True  # 六项 UNKNOWN：无否决条件（空真）


# ────────────────────────── sell_signal ──────────────────────────


@pytest.mark.parametrize(
    "code,when,reason,buy_window",
    [
        ("000017", POS_DATE, "退潮期清仓建议", "NONE"),
        ("601988", BAR_DATE, "断板建议（原6连板今日未封）", ""),
        ("000001", D, "断板建议（原连板今日未封）", ""),
        ("688432", D, "自定义理由", "STANDARD"),
    ],
)
def test_sell_signal(code, when, reason, buy_window):
    py_sig = _sell_signal(code, when, reason, buy_window)
    rust_sig = rust.sell_signal(code, str(when), reason, buy_window)
    assert_same_signal(rust_sig, py_sig)
    assert rust_sig.exec_hint == "CLOSE_ALL"  # 契约 v2：SELL 恒清仓
    assert rust_sig.action == "SELL" and rust_sig.source == "live"
    assert rust_sig.status == "SUGGESTED"


# ────────────────────── 有意分叉：Rust 停在被废止的规则上 ──────────────────────


def test_rust_exit_frozen_at_repealed_rule():
    """Rust exit.rs 仍是旧规则（退潮清仓/断板卖出），W3′ 未同步——有意为之。

    同步条件：Rust 若重新上线卖出判定，必须同时同步口径B（含 held_days 的
    交易日历入参），届时删除本条并恢复 sell/suggestions 对账。
    """
    from pathlib import Path

    src = Path("src/emotion_core/core/src/exit.rs").read_text(encoding="utf-8")
    assert "force_liquidate" in src  # 旧退潮清仓分支仍在
    assert "断板建议" in src  # 旧断板分支仍在
    assert "held_days" not in src  # 口径B 的持有天数未进 Rust
    assert "T+2" not in src  # 口径B 的卖点文案未进 Rust


def test_python_exit_rule_is_caliber_b():
    """Python 侧已切到口径B：源码里能查到卖点与强制了结两个分支。"""
    from pathlib import Path

    src = Path("src/emotion_core/algorithms/exit.py").read_text(encoding="utf-8")
    assert "SELL_AFTER_DAYS = 1" in src and "FORCE_AFTER_DAYS = 2" in src
    assert "T+2 {hit}" in src and "强制了结" in src
    assert "force_liquidate" not in src  # 相位清仓已彻底移除
