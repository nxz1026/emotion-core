"""制度规则表 ref_limit_rule 种子数据 + 查询服务。

A 股涨跌幅限制是交易所制度，不来自 Wind，直接录入已知规则：
- 主板（沪/深）：±10%
- 创业板：±20%（2020-08-24 起）
- 科创板：±20%
- 北交所：±30%
- ST 股：±5%
- 新股上市首日：特殊规则（主板 44%，创业板/科创板/北交所无限制但有盘中临停）

## 使用场景

C4/C5（梯队判定）需要按股票所在板块与上市日期匹配正确的涨跌幅阈值，
不再硬编码 magic number。
"""
from __future__ import annotations

from datetime import date

from emotion_core.utils.db import connect

# 制度规则种子数据（按生效日期排序）
# 格式：(market, board, rule_type, limit_pct, effective_from, effective_to, note)
SEED_RULES: list[tuple[str, str, str, float, date, date | None, str]] = [
    # 主板（上海 + 深圳合并记录，因涨跌幅一致）
    ("SSE", "main", "limit_pct", 10.0, date(1996, 12, 16), None,
     "上海主板 ±10%（1996-12-16 起）"),
    ("SZSE", "main", "limit_pct", 10.0, date(1996, 12, 16), None,
     "深圳主板 ±10%（1996-12-16 起）"),
    # 创业板
    ("SZSE", "chinext", "limit_pct", 10.0, date(2009, 10, 30), date(2020, 8, 23),
     "创业板 ±10%（2009-10-30 ~ 2020-08-23）"),
    ("SZSE", "chinext", "limit_pct", 20.0, date(2020, 8, 24), None,
     "创业板注册制 ±20%（2020-08-24 起）"),
    # 科创板
    ("SSE", "star", "limit_pct", 20.0, date(2019, 7, 22), None,
     "科创板 ±20%（2019-07-22 起）"),
    # 北交所
    ("BSE", "bse", "limit_pct", 30.0, date(2021, 11, 15), None,
     "北交所 ±30%（2021-11-15 起）"),
    # ST 股（全板块统一 ±5%）
    ("SSE", "st", "limit_pct", 5.0, date(1998, 4, 22), None,
     "ST 股 ±5%（1998-04-22 起，沪深统一）"),
    ("SZSE", "st", "limit_pct", 5.0, date(1998, 4, 22), None,
     "ST 股 ±5%（1998-04-22 起，沪深统一）"),
    # 新股上市首日（主板 44% 涨幅限制）
    ("SSE", "ipo_main", "limit_pct", 44.0, date(2014, 6, 13), None,
     "新股首日涨幅限制 44%（2014-06-13 起）"),
    ("SZSE", "ipo_main", "limit_pct", 44.0, date(2014, 6, 13), None,
     "新股首日涨幅限制 44%（2014-06-13 起）"),
]


def seed_ref_limit_rule() -> int:
    """幂等写入制度规则种子数据，返回写入条数。"""
    with connect() as conn:
        with conn.cursor() as cur:
            for market, board, rule_type, limit_pct, eff_from, eff_to, note in SEED_RULES:
                cur.execute(
                    """INSERT INTO ref_limit_rule
                       (market, board, rule_type, limit_pct, effective_from, effective_to, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (market, board, effective_from) DO UPDATE
                       SET limit_pct = EXCLUDED.limit_pct,
                           note = EXCLUDED.note,
                           effective_to = EXCLUDED.effective_to""",
                    (market, board, rule_type, limit_pct, eff_from, eff_to, note),
                )
    return len(SEED_RULES)


def get_limit_pct(market: str, board: str, as_of: date) -> float | None:
    """查询指定市场/板块在指定日期的涨跌幅限制。

    Args:
        market: 交易所代码（SSE/SZSE/BSE）
        board: 板块代码（main/chinext/star/bse/st/ipo_main）
        as_of: 查询日期

    Returns:
        涨跌幅百分比（如 10.0 表示 ±10%），未找到返回 None。
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT limit_pct FROM ref_limit_rule
                   WHERE market = %s AND board = %s
                     AND effective_from <= %s
                     AND (effective_to IS NULL OR effective_to >= %s)
                   ORDER BY effective_from DESC
                   LIMIT 1""",
                (market, board, as_of, as_of),
            )
            row = cur.fetchone()
            if row:
                return float(row[0])
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI：`python -m emotion_core.services.ref_limit_rule [--seed] [--query ...]`。

    缺省打印已落库规则；`--seed` 幂等写入 SEED_RULES；`--query` 查单点阈值。
    返回码：0 成功 / 1 查询未命中或表为空。
    """
    import argparse
    import logging
    import sys

    parser = argparse.ArgumentParser(
        prog="emotion-core-ref-limit-rule",
        description="制度规则表 ref_limit_rule：种子写入与涨跌幅阈值查询",
    )
    parser.add_argument("--seed", action="store_true", help="幂等写入 SEED_RULES")
    parser.add_argument(
        "--query", nargs=2, metavar=("MARKET", "BOARD"), help="查询阈值，如 SSE main"
    )
    parser.add_argument("--as-of", dest="as_of", help="查询日期 YYYY-MM-DD，缺省今天")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.seed:
        n = seed_ref_limit_rule()
        print(f"ref_limit_rule 种子已写入 {n} 条（幂等 upsert）")

    if args.query:
        market, board = args.query
        try:
            as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        except ValueError:
            print(f"日期格式错误：{args.as_of}（应为 YYYY-MM-DD）", file=sys.stderr)
            return 2
        pct = get_limit_pct(market, board, as_of)
        if pct is None:
            print(f"{market}/{board} @ {as_of}：未匹配规则（表空则先跑 --seed）")
            return 1
        print(f"{market}/{board} @ {as_of}：±{pct:g}%")
        return 0

    if args.seed:
        return 0

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT market, board, limit_pct, effective_from, effective_to
                   FROM ref_limit_rule ORDER BY market, board, effective_from"""
            )
            rows = cur.fetchall()
    if not rows:
        print("ref_limit_rule 为空（跑 --seed 写入种子）")
        return 1
    print(f"ref_limit_rule 共 {len(rows)} 条：")
    for market, board, pct, eff_from, eff_to in rows:
        span = f"{eff_from}~{eff_to or '至今'}"
        print(f"  {market:<5} {board:<9} ±{float(pct):g}%  {span}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
