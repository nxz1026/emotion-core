"""回填 `stock_basic.list_date`（上市日期）。

源：`data/providers/exchange_list.py` —— 沪 + 深交易所官方列表，**3 次 HTTP 请求**
覆盖全市场，0 Wind 额度、0 逐股调用。相对 `fill_industry.py` 的逐股巨潮，本模块没有
额度与耗时顾虑，**每次全量覆盖**即可；写入仍带 `IS DISTINCT FROM` 守卫，只更新取值
真正变化的行，重复跑 rowcount 为 0。

为什么必须独立于 `fill_stock_basic.py`：后者走东财 f26 是要塞进 clist 的字段，
而本机出口对 push2 / push2delay 持久 502，回退新浪源又没有该字段；把上市日期绑在
一条 502 链上等于永远补不上。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_list_date            # 全量
    PYTHONPATH=src python -m emotion_core.data.fill_list_date --only-missing
    PYTHONPATH=src python -m emotion_core.data.fill_list_date --dry-run
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator, Sequence
from datetime import date

from emotion_core.data.providers import exchange_list
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.data.fill_list_date")

#: 单条 UPDATE 的最大 (code, list_date) 对数（与 fill_industry 同量级：几十 KB）。
_CHUNK = 1000


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def upsert_list_date(rows: Sequence[tuple[str, date]]) -> int:
    """按 code 更新 `stock_basic.list_date`；只动库里已存在的股票，幂等。

    只写取值变化的行（``IS DISTINCT FROM``）：上市日期是不可变事实，二次运行
    应当一行不写。分块提交，中途失败保留已完成分块。

    Returns:
        实际更新的行数（`stock_basic` 里没有的代码、以及取值未变的行都不计入）。
    """
    if not rows:
        return 0
    from psycopg import sql

    total = 0
    for chunk in _chunks(rows, _CHUNK):
        values = sql.SQL(", ").join(
            sql.SQL("({}, {})").format(sql.Literal(code), sql.Literal(d))
            for code, d in chunk
        )
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE stock_basic SET list_date = v.d, updated_at = now() "
                        "FROM (VALUES {}) AS v(code, d) "
                        "WHERE stock_basic.code = v.code "
                        "  AND stock_basic.list_date IS DISTINCT FROM v.d"
                    ).format(values)
                )
                total += cur.rowcount
    return total


def all_codes() -> list[str]:
    """库里全部股票代码（升序）。"""
    return [str(c) for c in query_df("SELECT code FROM stock_basic ORDER BY code")["code"]]


def missing_codes() -> list[str]:
    """`list_date` 仍为 NULL 的股票代码（升序）。"""
    return [str(c) for c in
            query_df("SELECT code FROM stock_basic WHERE list_date IS NULL ORDER BY code")["code"]]


def backfill(*, only_missing: bool = False, dry_run: bool = False) -> int:
    """抓取并回填，返回实际更新行数（`dry_run` 时不写库）。"""
    got = exchange_list.fetch_list_date_map()
    log.info("交易所列表：解析出 %d 个上市日期", len(got))
    targets = set(missing_codes() if only_missing else all_codes())
    rows = sorted((c, d) for c, d in got.items() if c in targets)
    log.info("待写 %d 行（only_missing=%s，库内目标 %d 只，源覆盖 %d 只）",
             len(rows), only_missing, len(targets), len(got))
    if dry_run:
        log.info("dry-run：样例 %s", rows[:3])
        return 0
    return upsert_list_date(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填 stock_basic.list_date")
    parser.add_argument("--only-missing", action="store_true",
                        help="只补 list_date IS NULL 的（增量续跑用）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    n = backfill(only_missing=args.only_missing, dry_run=args.dry_run)
    if not args.dry_run:
        log.info("stock_basic.list_date 更新 %d 行", n)
    return 0


if __name__ == "__main__":
    main()
