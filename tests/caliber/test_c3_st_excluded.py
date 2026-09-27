"""C3 口径守护：ST 整体剔除，且剔除只有一处门（derive 的 JOIN 条件）。

裁决（docs/13 §S2 C3）：ST 股不进判据层。实现是 `algorithms/derive.py` 的
`WHERE NOT s.is_st`——`derived_bar` 表本身**没有** is_st 列，所以下游任何模块
（emotion 计数、ladder、promotion、market_service）拿到的都已是剔 ST 的口径，
不存在「某处忘了剔」的第二条路。

反向证据：`derived_bar` DDL 一旦出现 is_st 列，就说明有人把 ST 判定下沉到派生层，
本测试即失败——那意味着下游必须各自重剔一次，口径开始分叉。
"""
from __future__ import annotations

from emotion_core.algorithms import derive
from emotion_core.data import schema


def test_derive_sql_excludes_st_at_the_join():
    sql = derive.render_sql()
    assert "JOIN stock_basic s ON s.code = b.code" in sql
    assert "NOT s.is_st" in sql, "ST 剔除必须落在 derive 的 JOIN 上"


def test_derived_bar_has_no_st_column():
    ddl = schema.DDL["derived_bar"]
    assert "is_st" not in ddl, (
        "derived_bar 不得有 is_st 列：ST 只能由 derive 一次性剔除，"
        "下沉到派生层会让每个下游各自重剔一次")


def test_stock_basic_owns_the_st_flag():
    ddl = schema.DDL["stock_basic"]
    assert "is_st" in ddl
    assert "NOT NULL DEFAULT false" in ddl.replace("  ", " ")


def test_emotion_counts_read_derived_bar_only():
    """情绪家数从 derived_bar 取（已剔 ST），不再回查 daily_bar 自行过滤。"""
    src = (schema.DDL["derived_bar"])
    assert "is_limit_up" in src and "is_bomb" in src and "is_limit_down" in src
    import inspect

    from emotion_core.algorithms import emotion

    counts_src = inspect.getsource(emotion._counts)
    assert "FROM derived_bar" in counts_src
    assert "daily_bar" not in counts_src
