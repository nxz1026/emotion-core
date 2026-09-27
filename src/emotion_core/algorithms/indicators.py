"""判据 + 连板。语义逐字照搬 lkl/services/derive.py。

关键口径（不得改动）：
- is_limit_up: close >= limit_up_price(pre_close)
- is_one_word: low == limit_up_price（一字板）
- is_exchange: is_limit_up AND low < limit_price（换手板）
- is_bomb: touched_limit AND NOT is_limit_up（炸板）
- touched_limit: high >= limit_up_price
- cont_days: 停牌断档不打断（只遍历实际存在的行，不按日历补齐）

数据来源：lkl derive.py:48-95（SQL 整数式 + 窗口函数）
对账 oracle：tests/oracle/fixtures/streaks_sample.json
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/indicators.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

from emotion_core.domain.bar import Bar, DerivedBar
from emotion_core.utils.price import (
    is_limit_down,
    is_limit_up,
    limit_down_price_cents,
    limit_up_price_cents,
)


def compute_derived(bars: list[Bar]) -> list[DerivedBar]:
    """从原始日线计算判据 + 连板。

    停牌断档处理：只遍历实际存在的行，遇到非涨停行重置计数。
    停牌日无行，自然实现「停牌不断」（C1）。
    """
    # 按 code 分组，保持日期升序
    by_code: dict[str, list[Bar]] = {}
    for b in bars:
        by_code.setdefault(b.code, []).append(b)

    result: list[DerivedBar] = []
    for code, rows in by_code.items():
        rows.sort(key=lambda b: b.date)
        cont = 0
        for b in rows:
            lim_up = limit_up_price_cents(b.pre_close_cents, code)
            lim_down = limit_down_price_cents(b.pre_close_cents, code)
            lu = is_limit_up(b.close_cents, b.pre_close_cents, code)
            ld = is_limit_down(b.close_cents, b.pre_close_cents, code)
            ow = b.low_cents >= lim_up  # 一字板
            ex = lu and b.low_cents < lim_up  # 换手板
            tl = b.high_cents >= lim_up  # 曾触板
            bomb = tl and not lu  # 炸板
            if lu:
                cont += 1
            else:
                cont = 0
            # 振幅 = (high - low) / pre_close * 100
            amp = (b.high_cents - b.low_cents) / b.pre_close_cents * 100 if b.pre_close_cents else 0.0
            result.append(DerivedBar(
                code=code, date=b.date,
                is_limit_up=lu, is_limit_down=ld,
                is_one_word=ow, is_exchange=ex, is_bomb=bomb,
                touched_limit=tl, cont_days=cont,
                amplitude=round(amp, 4),
            ))
    return result
