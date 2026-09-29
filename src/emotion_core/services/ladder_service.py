"""梯队数据访问服务（胶水层）。

职责：封装 data/loader.py 中与梯队相关的取数/落库函数，
供 algorithms/ladder.py 调用，避免 algorithms 层直连 data 层（架构纪律：docs/11 §4）。

依赖方向：algorithms → services → data → domain
"""
from __future__ import annotations

from datetime import date

from emotion_core.data.loader import (
    count_derived_rows as _count_derived_rows,
    load_exchange_codes as _load_exchange_codes,
    load_ladder_candidates as _load_ladder_candidates,
    replace_ladder_day as _replace_ladder_day,
)


def load_ladder_candidates(d: date) -> list[tuple[str, int, bool]]:
    """梯队候选行 (code, cont_days, is_exchange)：主板 + 剔 ST + cont_days>=2。"""
    return _load_ladder_candidates(d)


def load_exchange_codes(d: date) -> set[str]:
    """当日主板换手板代码集（幸存口径）。"""
    return _load_exchange_codes(d)


def count_derived_rows(d: date) -> int:
    """derived_bar 当日行数（上游完整性凭证）。"""
    return _count_derived_rows(d)


def replace_ladder_day(d: date, rows: list) -> int:
    """梯队判定结果写 ladder_day（按日全量替换），返回写入行数。"""
    return _replace_ladder_day(d, rows)
