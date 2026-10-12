"""回填 `stock_basic.industry`。

`data/fill_stock_basic.py` 走东财 f100 取行业，但本机出口对东财是**持久 502**，
回退到新浪全市场源后该源又没有 f100 → 缺列一律 None、upsert 的 COALESCE 保留旧值
→ `industry` 恒为 NULL（2026-10-12 实测 0/5221）。本模块独立走免费行情源补这一段，
**不消耗任何 Wind 额度**。

两个源（`--source`）：

- ``cninfo``（默认）：巨潮证监会行业分类，**覆盖全市场**，是统一口径的正解。
  实测 1.17s/只、20/20 成功，5221 只 4 并发约 25 分钟。
- ``sina``：新浪行业分类，一次约 62 个请求、只覆盖约 58%（**不含科创板**）。
  留作备用与对比，不建议与 ``cninfo`` 结果混用 —— 两者口径不同。

写入策略：**抓一批写一批**（`--batch`，默认 500）。巨潮是逐股接口，全量要跑 20
分钟以上，先攒完再写会让任何一次中断都白跑；分批后每批一个幂等 UPDATE 事务，
中断后重跑 `--only-missing` 即可续。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_industry              # 全量统一口径
    PYTHONPATH=src python -m emotion_core.data.fill_industry --only-missing
    PYTHONPATH=src python -m emotion_core.data.fill_industry --dry-run --limit 20
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator, Sequence
from typing import Any

from emotion_core.data.providers import cninfo_industry, sina_industry
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.data.fill_industry")

#: 单条 UPDATE 的最大 (code, industry) 对数。全量 5221 只写成一条 SQL 会到 200KB
#: 量级，分块后单语句稳定在几十 KB，也让 rowcount 对账更细。
_CHUNK = 1000

#: 巨潮逐股抓取的默认批大小：约 10 分钟一批（500 × 1.17s / 4 并发）。
DEFAULT_BATCH = 500


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def upsert_industry(rows: Sequence[tuple[str, str]]) -> int:
    """按 code 更新 `stock_basic.industry`；只动库里已存在的股票，幂等。

    分块提交（每块一个事务）：本操作是幂等 UPDATE 而非「DELETE + INSERT 全量替换」，
    中途失败保留已完成分块反而利于断点续跑。

    Returns:
        实际更新的行数（`stock_basic` 里没有的代码不计入）。
    """
    if not rows:
        return 0
    from psycopg import sql

    def _lit(v: Any):
        return sql.Literal(v) if v is not None else sql.SQL("NULL")

    total = 0
    for chunk in _chunks(rows, _CHUNK):
        values = sql.SQL(", ").join(
            sql.SQL("({}, {})").format(_lit(code), _lit(industry))
            for code, industry in chunk
        )
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE stock_basic SET industry = v.industry, updated_at = now() "
                        "FROM (VALUES {}) AS v(code, industry) "
                        "WHERE stock_basic.code = v.code"
                    ).format(values)
                )
                total += cur.rowcount
    return total


def all_codes() -> list[str]:
    """库里全部股票代码（升序）。用于统一口径全量回填。"""
    return [str(c) for c in query_df("SELECT code FROM stock_basic ORDER BY code")["code"]]


def missing_codes() -> list[str]:
    """`industry` 仍为 NULL 的股票代码（升序）。用于增量补缺。"""
    return [str(c) for c in
            query_df("SELECT code FROM stock_basic WHERE industry IS NULL ORDER BY code")["code"]]


def cninfo_targets(*, only_missing: bool = False, limit: int = 0) -> list[str]:
    """巨潮源要查的代码清单（升序）。`limit` 在抓取前截断，便于冒烟试跑。"""
    codes = missing_codes() if only_missing else all_codes()
    return codes[:limit] if limit else codes


def backfill(source: str = "cninfo", *, only_missing: bool = False,
             workers: int = cninfo_industry.DEFAULT_WORKERS,
             batch: int = DEFAULT_BATCH, limit: int = 0, dry_run: bool = False) -> int:
    """抓取并回填，返回实际更新行数（`dry_run` 时不写库）。

    巨潮源按 `batch` 分批：每批「抓 → 写 → 记日志」，中断只损失当前批。
    """
    if source == "sina":
        rows = sorted(sina_industry.fetch_industry_map().items())
        log.info("sina 源抓取 %d 只", len(rows))
        return 0 if dry_run else upsert_industry(rows)

    codes = cninfo_targets(only_missing=only_missing, limit=limit)
    log.info("巨潮行业：待查 %d 只（only_missing=%s，并发 %d，每批 %d）",
             len(codes), only_missing, workers, batch)
    total = 0
    for i, chunk in enumerate(_chunks(codes, max(1, batch)), start=1):
        got = cninfo_industry.fetch_industry_map(chunk, workers=workers, progress_every=0)
        rows = sorted(got.items())
        if dry_run:
            log.info("第 %d 批（dry-run）：%d 只 → 取到 %d 只；样例 %s",
                     i, len(chunk), len(rows), rows[:3])
            continue
        n = upsert_industry(rows)
        total += n
        log.info("第 %d 批：%d 只 → 取到 %d 只，写库 %d 行（累计 %d 行）",
                 i, len(chunk), len(rows), n, total)
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填 stock_basic.industry")
    parser.add_argument("--source", choices=("cninfo", "sina"), default="cninfo",
                        help="取数源；cninfo = 巨潮证监会分类（覆盖全市场，默认）")
    parser.add_argument("--only-missing", action="store_true",
                        help="只补 industry IS NULL 的，保留已有值（增量续跑用）")
    parser.add_argument("--workers", type=int, default=cninfo_industry.DEFAULT_WORKERS,
                        help="并发数（仅 cninfo 源）")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help="每批抓取只数（仅 cninfo 源）；抓一批写一批")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟/试跑）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not args.dry_run:
        n = backfill(args.source, only_missing=args.only_missing, workers=args.workers,
                     batch=args.batch, limit=args.limit)
        log.info("stock_basic.industry 更新 %d 行", n)
    else:
        backfill(args.source, only_missing=args.only_missing, workers=args.workers,
                 batch=args.batch, limit=args.limit, dry_run=True)
    return 0


if __name__ == "__main__":
    main()
