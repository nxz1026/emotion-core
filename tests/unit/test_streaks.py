"""连板数纯函数：全断/全连/中间停牌/首日是板。"""
from emotion_core.algorithms.derive import _streaks


def test_all_false():
    assert _streaks([False, False, False]) == [0, 0, 0]


def test_all_true():
    assert _streaks([True, True, True]) == [1, 2, 3]


def test_break_in_middle():
    assert _streaks([True, True, False, True]) == [1, 2, 0, 1]


def test_first_day_board():
    assert _streaks([True, False, True, True]) == [1, 0, 1, 2]


def test_empty():
    assert _streaks([]) == []
