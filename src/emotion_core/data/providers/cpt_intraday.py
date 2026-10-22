"""读 CPT 盘中落库表的 provider（ADR 0003 §H）。

## 表变了（2026-10-22 晚，CPT R90）

ADR 0003 §G 原方案是本仓判涨停、CPT 交**原始五档**（`intraday_quote`，13 列）。
CPT 实际交付的是**每一轮的封板结论** `public.intraday_seal_snapshot`（5 列）：

```
at          timestamptz      这一轮采集时刻
code        text             6 位纯代码
sealed      boolean          这一轮是否封在板上
bid1_lots   double precision 封单量（**手**，不是股；判不了为 NULL）
limit_price double precision 本轮用的涨停价（存下来自证口径）
```

理由见 §H.1：判据口径只能有一处，CPT 已经在算 `seal_strength()`。

## 本 provider 只做「读」

- **封板与否由 CPT 判**，本仓不再用 `algorithms/intraday.py` 重算 CPT 来的数据
  （那套纯函数保留：§C4 定档门禁、回测、自采兜底还要用）。
- **`bid1_lots` 单位是手**。本仓 `SEAL_BID1_VOL_MIN` 是股，比较前要 ×100。

## 契约（沿用 F6「缺数给 None」）

- **表不存在 → `None`**，不抛、不报警（表还没建是正常状态，不是故障）。
- **表存在但没行 → `{}`**。
- **按 `at` 取最新一轮**，不按 `code` 单点查最新 —— 跨轮混算会把「上一轮封着、
  这一轮开板」读成两只票（§H.3 第 3 条）。
- 读连接走 `utils.db.connect_ro()`（服务端强制只读）。
"""

from __future__ import annotations

import logging
from typing import Any, Final

from emotion_core.utils.db import connect_ro

log = logging.getLogger("emotion_core.data.providers.cpt_intraday")

__all__ = [
    "CPT_INTRADAY_TABLE",
    "CptIntradayProvider",
    "fetch_quotes_from_cpt",
    "fetch_round_from_cpt",
]

#: CPT 落表的表名（ADR 0003 §H.2）。
#: ⚠️ 不是 §G 里的 `intraday_quote` —— 那张表 CPT 没建，也不要再按 §G 去建。
CPT_INTRADAY_TABLE: Final[str] = "intraday_seal_snapshot"

#: 读取列。缺列 → 返回 None 并记 warning（CPT 改表是它的事，本仓不猜）。
_COLUMNS: Final[tuple[str, ...]] = ("at", "code", "sealed", "bid1_lots", "limit_price")

#: 只取最新一轮：`at` 是整轮的时间戳，同一轮内所有 code 共享同一个值。
_SELECT_SQL: Final[str] = f"""
SELECT {", ".join(_COLUMNS)}
FROM {CPT_INTRADAY_TABLE}
WHERE at = (SELECT max(at) FROM {CPT_INTRADAY_TABLE})
ORDER BY code
"""


class CptIntradayProvider:
    """从 CPT 落库表读「最新一轮的封板结论」。"""

    name = "cpt_intraday"

    def fetch_round(
        self, codes: list[str] | None = None
    ) -> tuple[Any, dict[str, dict[str, Any]]] | None:
        """取最新一轮的整批结论。

        Args:
            codes: 可选，只要这些 6 位代码的。`None` = 全要。

        Returns:
            ``(round_at, {6位代码: record})``；**表不存在 → `None`**；
            表存在但一轮都没有 → `(None, {})`。
        """
        wanted = set(codes) if codes else None
        out: dict[str, dict[str, Any]] = {}
        round_at: Any = None
        try:
            with connect_ro() as conn, conn.cursor() as cur:
                cur.execute(_SELECT_SQL)
                rows = cur.fetchall()
                names = [d.name for d in cur.description]
        except Exception as exc:  # noqa: BLE001 - 表不存在是正常状态
            msg = str(exc)
            if "does not exist" in msg or "不存在" in msg:
                log.info("CPT 盘中表 %s 尚未建立，跳过读盘中数据", CPT_INTRADAY_TABLE)
                return None
            log.warning("读 CPT 盘中表失败：%s", exc)
            return None
        for row in rows:
            record = dict(zip(names, row))
            code = str(record.get("code") or "").strip()
            if not code:
                continue
            if wanted is not None and code not in wanted:
                continue
            if round_at is None:
                round_at = record.get("at")
            out[code] = record
        return round_at, out

    def fetch_quotes(self, codes: list[str] | None = None) -> dict[str, dict[str, Any]] | None:
        """只要最新一轮的 `{code: record}`（`fetch_round` 的薄封装）。"""
        got = self.fetch_round(codes)
        if got is None:
            return None
        return got[1]


def fetch_quotes_from_cpt(
    codes: list[str] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """便捷入口（模块级函数形态，与 `SinaQuoteProvider.fetch_quotes` 对齐）。"""
    return CptIntradayProvider().fetch_quotes(codes)


def fetch_round_from_cpt(
    codes: list[str] | None = None,
) -> tuple[Any, dict[str, dict[str, Any]]] | None:
    """便捷入口：带 `round_at` 的版本。"""
    return CptIntradayProvider().fetch_round(codes)
