"""个股相对强弱（RS）排名服务。

RS = 个股区间涨幅 / 基准指数区间涨幅（或排序分位）。
基准默认沪深 300（000300），可配置。

指数源：Wind（通过指数日行情接口）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from emotion_core.utils.db import connect as db_connect

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK = "000300"  # 沪深 300
RS_PERIODS = [20, 60]  # 20 日 + 60 日


def _get_prices(conn, code: str, start_date: date, end_date: date) -> list[tuple[date, float]]:
    """获取某股某区间收盘价。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date, close FROM daily_bar
               WHERE code = %s AND date BETWEEN %s AND %s
               ORDER BY date""",
            (code, start_date, end_date),
        )
        return [(row[0], float(row[1])) for row in cur.fetchall() if row[1] is not None]


def _period_return(prices: list[tuple[date, float]], period: int) -> float | None:
    """计算最近 N 日涨幅。"""
    if len(prices) < 2:
        return None
    # 取最近 period 个交易日
    recent = prices[-period:] if len(prices) >= period else prices
    if len(recent) < 2:
        return None
    start_price = recent[0][1]
    end_price = recent[-1][1]
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price


def calculate_rs(as_of_date: date | None = None, benchmark: str = DEFAULT_BENCHMARK) -> int:
    """计算所有个股相对强弱并写入 ref_rs。

    Args:
        as_of_date: 计算日期，None 表示今天。
        benchmark: 基准指数 code。

    Returns:
        写入行数。
    """
    if as_of_date is None:
        as_of_date = date.today()

    # 回溯足够长（60 交易日 ≈ 90 自然日）
    lookback_start = as_of_date - timedelta(days=120)

    with db_connect() as conn:
        # 获取基准指数涨幅
        bench_prices = _get_prices(conn, benchmark, lookback_start, as_of_date)
        bench_returns = {}
        for period in RS_PERIODS:
            bench_returns[period] = _period_return(bench_prices, period)

        # 获取所有有效股票代码
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code FROM stock_basic WHERE in_market = true LIMIT 5000"
            )
            codes = [row[0] for row in cur.fetchall()]

        # 批量获取区间行情
        with conn.cursor() as cur:
            cur.execute(
                """SELECT code, date, close FROM daily_bar
                   WHERE date BETWEEN %s AND %s AND close IS NOT NULL
                   ORDER BY code, date""",
                (lookback_start, as_of_date),
            )
            all_prices: dict[str, list[tuple[date, float]]] = {}
            for row in cur.fetchall():
                code, d, close = row[0], row[1], float(row[2])
                if code not in all_prices:
                    all_prices[code] = []
                all_prices[code].append((d, close))

        # 计算每只股票的 RS
        rows = []
        for code in codes:
            prices = all_prices.get(code, [])
            if not prices:
                continue

            rs_values = {}
            has_any = False
            for period in RS_PERIODS:
                stock_ret = _period_return(prices, period)
                bench_ret = bench_returns.get(period)
                if stock_ret is None or bench_ret is None:
                    rs_values[period] = None
                elif bench_ret == 0:
                    rs_values[period] = None
                else:
                    rs_values[period] = stock_ret / bench_ret
                    has_any = True

            if has_any:
                rows.append((code, as_of_date, rs_values.get(20), rs_values.get(60), benchmark))

        # 按 20 日 RS 排序赋 rank
        rows_with_rank = []
        sorted_rows = sorted(enumerate(rows), key=lambda x: x[1][2] if x[1][2] is not None else -999, reverse=True)
        rank_map = {orig_idx: rank + 1 for rank, (orig_idx, _) in enumerate(sorted_rows)}
        for idx, row in enumerate(rows):
            code, d, rs20, rs60, bm = row
            rows_with_rank.append((code, d, rs20, rs60, rank_map.get(idx), bm))

        # 批量写入
        with conn.cursor() as cur:
            for row in rows_with_rank:
                cur.execute(
                    """INSERT INTO ref_rs (code, as_of_date, rs_20d, rs_60d, rs_rank, benchmark)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (code, as_of_date, benchmark) DO UPDATE
                       SET rs_20d = EXCLUDED.rs_20d,
                           rs_60d = EXCLUDED.rs_60d,
                           rs_rank = EXCLUDED.rs_rank""",
                    row,
                )

    return len(rows_with_rank)


def get_top_rs(
    as_of_date: date | None = None,
    period: int = 20,
    limit: int = 20,
    benchmark: str = DEFAULT_BENCHMARK,
) -> list[dict]:
    """查询 RS 排名靠前的个股。"""
    if as_of_date is None:
        as_of_date = date.today()

    rs_field = "rs_20d" if period == 20 else "rs_60d"

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT code, {rs_field}, rs_rank FROM ref_rs
                    WHERE as_of_date = %s AND benchmark = %s AND {rs_field} IS NOT NULL
                    ORDER BY rs_rank NULLS LAST LIMIT %s""",
                (as_of_date, benchmark, limit),
            )
            return [
                {"code": row[0], "rs": row[1], "rank": row[2]}
                for row in cur.fetchall()
            ]


def main(argv: list[str] | None = None) -> int:
    """CLI：`python -m emotion_core.services.ref_rs [--calculate] [--top N]`。

    缺省只读排名（`get_top_rs`）；`--calculate` 才会真算并写 `ref_rs`。
    返回码：0 成功 / 1 无数据或算不出。
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="emotion-core-ref-rs",
        description="相对强度（RS）：计算并写入 ref_rs，或查看排名靠前个股",
    )
    parser.add_argument("--calculate", action="store_true", help="计算并写入 ref_rs（默认只读）")
    parser.add_argument("--as-of", dest="as_of", help="基准日 YYYY-MM-DD，缺省今天")
    parser.add_argument(
        "--benchmark", default=DEFAULT_BENCHMARK, help=f"基准代码，默认 {DEFAULT_BENCHMARK}"
    )
    parser.add_argument("--period", type=int, default=20, choices=RS_PERIODS, help="RS 周期")
    parser.add_argument("--top", type=int, default=20, help="展示条数，默认 20")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError:
        print(f"日期格式错误：{args.as_of}（应为 YYYY-MM-DD）", file=sys.stderr)
        return 2

    if args.calculate:
        n = calculate_rs(as_of_date=as_of, benchmark=args.benchmark)
        print(f"ref_rs 已写入 {n} 行（as_of={as_of} benchmark={args.benchmark}）")
        if n == 0:
            return 1

    rows = get_top_rs(
        as_of_date=as_of, period=args.period, limit=args.top, benchmark=args.benchmark
    )
    if not rows:
        print(f"ref_rs 无 {as_of} / {args.benchmark} 数据（先跑 --calculate）")
        return 1
    print(f"RS 排名 TOP{len(rows)}（rs_{args.period}d，as_of={as_of}，benchmark={args.benchmark}）")
    for r in rows:
        rank = "—" if r["rank"] is None else str(r["rank"])
        print(f"  {rank:>5}  {r['code']}  rs={r['rs']:.2f}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
