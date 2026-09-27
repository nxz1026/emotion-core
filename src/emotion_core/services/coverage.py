"""覆盖率硬门槛（asel A12）：`covered / in_market < MIN_COVERAGE` → 拒绝装配。

为什么需要这道门槛
------------------
`daily` 链上的 `derive/market/ladder` 全部以「当日有多少行 daily_bar」为输入。
回填只跑一半时（asel 2026-09-24 实测一度只有 180 只 / 5569），衍生与状态机照样
产出结构完整、字段齐全的结果——**看起来正常的半截数据比直接报错危险得多**。
故在 sync 之后、derive 之前硬阻断，退出码 76。

判据（逐字照 asel `scripts/asel-coverage-gate.py`）
--------------------------------------------------
* ``covered``   = ``count(DISTINCT code) FROM daily_bar WHERE date = 目标交易日``
* ``in_market`` = ``count(*) FROM stock_basic WHERE in_market``

阈值 = ``CONFIG.MIN_COVERAGE``（0.90；asel 实测正常交易日 0.9978，半截数据 0.032）。
退出码 76 由编排层 `orchestration/daily.py` 使用（`EXIT_COVERAGE_BLOCKED`）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.coverage")

EXIT_COVERAGE_BLOCKED = 76


class CoverageBlocked(RuntimeError):
    """覆盖率不足（或分母不可用）：拒绝装配，编排层据此返回退出码 76。"""


def decide(covered: int, in_market: int,
           min_pct: float | None = None) -> tuple[bool, str]:
    """纯判定（不碰 DB）：返回 (是否达标, 单行报告)。"""
    min_pct = CONFIG.MIN_COVERAGE if min_pct is None else min_pct
    ratio = (covered / in_market) if in_market else 0.0
    line = (f"coverage covered={covered} in_market={in_market}"
            f" ratio={ratio:.4f} min={min_pct:g}")
    if in_market <= 0:
        return False, line + " -> 拒绝装配（stock_basic 无在市代码）"
    if ratio < min_pct:
        return False, line + " -> 拒绝装配（覆盖率不足）"
    return True, line


def measure(trade_date: date) -> tuple[int, int]:
    """只读度量当日 (covered, in_market)。"""
    c = query_df("SELECT count(DISTINCT code) AS n FROM daily_bar"
                 " WHERE date = %s", (trade_date,))
    m = query_df("SELECT count(*) AS n FROM stock_basic WHERE in_market")
    covered = int(c["n"].iloc[0]) if not c.empty else 0
    in_market = int(m["n"].iloc[0]) if not m.empty else 0
    return covered, in_market


def gate(trade_date: date) -> str:
    """门槛：达标返回报告行；不达标抛 :class:`CoverageBlocked`。"""
    ok, line = decide(*measure(trade_date))
    log.info(line)
    if not ok:
        raise CoverageBlocked(line)
    return line
