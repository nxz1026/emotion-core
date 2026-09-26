"""涨停价唯一实现。单位：分（int）。板块感知：主板10% / 创业科创20% / 北交所30%。

口径来源：lkl utils/price.py:9（Decimal 参考）+ derive.py:48-52（SQL 整数式，生产用）。
两者语义等价；本实现用整数分运算，与 SQL 整数式逐分一致（审核文档 S5）。

SQL 整数式（lkl 生产）：
    up_c = (round(pre_close*100)::bigint * pct + 50) / 100
    其中 pre_close 单位元，pct=110（主板），/ 100 是整数除法（截断）

Python 等价式（本实现）：
    limit_up_price_cents = (pre_close_cents * (1000 + pct_milli) + 500) // 1000
    其中 pre_close_cents 单位分，pct_milli=100（主板 10%）

验证等价：
    pre_close=10.00 元=1000 分, 主板 pct_milli=100:
      SQL: (1000 * 110 + 50) / 100 = 110050 / 100 = 1100 (整数除法)
      Python: (1000 * 1100 + 500) // 1000 = 1100500 // 1000 = 1100 ✓
    pre_close=10.01 元=1001 分:
      SQL: (1001 * 110 + 50) / 100 = 110160 / 100 = 1101
      Python: (1001 * 1100 + 500) // 1000 = 1101600 // 1000 = 1101 ✓

纪律：
- 全项目唯一涨停价实现。守护测试（tests/caliber/test_price_unique_impl.py）
  用 AST 扫描禁止本文件之外出现 `* 1.1` / `* 1.2` 形态。
- 涨停价是 is_limit_up 判定的基础，错一分钱全错。
"""
from __future__ import annotations


def board_pct_milli(code: str) -> int:
    """返回涨跌幅的千分比（主板 100 = 10.0%，创业/科创 200 = 20%，北交所 300 = 30%）。

    北交所代码格式：纯代码 8xxxxx/4xxxxx，或 akshare symbol 前缀 bj。
    """
    if code.startswith("bj"):
        return 300  # 北交所 30%（akshare symbol 格式）
    if code.startswith(("30", "68")):
        return 200  # 创业板/科创板 20%
    if code.startswith(("4", "8")):
        return 300  # 北交所 30%（纯代码格式）
    return 100  # 主板 10%


def limit_up_price_cents(pre_close_cents: int, code: str) -> int:
    """涨停价（分）。四舍五入与 SQL (x*pct + 50)/100 一致。

    Args:
        pre_close_cents: 昨收价，单位分（int）
        code: 6 位股票代码
    Returns:
        涨停价，单位分（int）
    """
    pct = board_pct_milli(code)
    return (pre_close_cents * (1000 + pct) + 500) // 1000


def limit_down_price_cents(pre_close_cents: int, code: str) -> int:
    """跌停价（分）。"""
    pct = board_pct_milli(code)
    return (pre_close_cents * (1000 - pct) + 500) // 1000


def is_limit_up(close_cents: int, pre_close_cents: int, code: str) -> bool:
    """是否涨停（收盘 >= 涨停价）。"""
    return close_cents >= limit_up_price_cents(pre_close_cents, code)


def is_limit_down(close_cents: int, pre_close_cents: int, code: str) -> bool:
    """是否跌停（收盘 <= 跌停价）。"""
    return close_cents <= limit_down_price_cents(pre_close_cents, code)
