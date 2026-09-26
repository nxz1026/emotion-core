"""ladder.build 单元测试：纯函数口径（不连 DB）。

口径来源：lkl/services/ladder.py + docs/02 §3.4（H 取换手口径）。
"""
from __future__ import annotations

from datetime import date

from emotion_core.algorithms.ladder import build, sole_top, top_group
from emotion_core.utils.config import CONFIG

D = date(2026, 9, 24)


def row(code: str, cont_days: int, *, is_exchange: bool = True,
        is_one_word: bool = False, is_bomb: bool = False) -> dict:
    """derived_bar 行（dict，键 = 列名）。"""
    return {"date": D, "code": code, "cont_days": cont_days,
            "is_exchange": is_exchange, "is_one_word": is_one_word,
            "is_bomb": is_bomb}


def test_build_sorts_desc_and_marks_unique_top():
    """正常场景：唯一换手最高板（5 板 >= 4）→ 标 is_sole_top，按板数降序。"""
    ladder = build([row("000002", 3), row("600001", 5), row("600003", 4)])

    assert [r.code for r in ladder] == ["600001", "600003", "000002"]
    assert [r.is_top for r in ladder] == [True, False, False]
    assert [r.is_sole_top for r in ladder] == [True, False, False]
    assert sole_top(ladder).code == "600001"
    assert [r.code for r in top_group(ladder)] == ["600001"]


def test_no_sole_top_when_height_tied():
    """无唯一最高板：两只换手板同高度 → 无唯一高标，均不标记。"""
    ladder = build([row("600001", 5), row("000002", 5)])

    assert sole_top(ladder) is None
    assert [r.code for r in top_group(ladder)] == ["000002", "600001"]
    assert [r.is_top for r in ladder] == [True, True]   # 同身位全员 is_top
    assert not any(r.is_sole_top for r in ladder)


def test_one_word_excluded_from_height():
    """一字板排除：一字 6 板不参与最高层判定，H 取换手板 4 板。"""
    ladder = build([row("600009", 6, is_exchange=False, is_one_word=True),
                    row("000002", 4)])

    one_word = next(r for r in ladder if r.code == "600009")
    assert not one_word.is_exchange           # 一字板：非换手，不进最高层
    assert one_word.is_top is False           # 板数虽最高（6>4），不算换手最高层
    assert one_word.is_sole_top is False      # 一字板可保留在梯队，但不是高标
    assert [r.code for r in top_group(ladder)] == ["000002"]
    assert sole_top(ladder).code == "000002"


def test_min_leader_days_gate():
    """MIN_LEADER_DAYS 门槛：唯一换手最高板不足 4 板 → None，可显式放宽。"""
    ladder = build([row("600001", 3), row("000002", 2)])

    assert CONFIG.MIN_LEADER_DAYS == 4
    assert sole_top(ladder) is None
    assert not any(r.is_sole_top for r in ladder)
    assert sole_top(ladder, min_days=3).code == "600001"

    at_gate = build([row("600001", 4)])
    assert sole_top(at_gate).code == "600001"
    assert at_gate[0].is_sole_top is True


def test_no_exchange_board_means_empty_top_group():
    """全一字 / 空输入：同身位组为空，无高标。"""
    assert build([]) == []
    assert top_group(build([])) == []
    assert sole_top(build([])) is None

    all_one_word = build([row("600009", 5, is_exchange=False, is_one_word=True)])
    assert top_group(all_one_word) == []
    assert sole_top(all_one_word) is None
    assert all_one_word[0].is_sole_top is False
