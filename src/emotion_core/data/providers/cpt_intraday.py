"""读 CPT 盘中落库表的 provider（ADR 0003 E4 第 4 条的预留口）。

## 现状

CPT 的盘中采集器**还没上线**（2026-10-22 实测：CPT 仓 `scripts/intraday_probe.py`
头注「没有 systemd 单元、没有 cron、没有路由、没有表」；本机 public/lab/asel
三套 schema 里 `intraday` / `quote` / `minute` / `tick` 相关表只有
`public.cpt_track_snapshot`）。

所以这个 provider 现在**读不到任何行**。它先落地，是为了：
1. 把「读 CPT 表」的口径（表名、列名、缺数怎么表示）先钉住，
   等 CPT 落表时只需改 SQL 一处；
2. 让上层（判据、编排）现在就按「provider 可能返回空」来写，
   避免上线时才发现上游假设了「一定有数据」。

## 契约

- **表不存在 → 返回 `None`**，不抛异常、不报警。表还没建是正常状态，
  不是故障（这是本仓对「缺数」一贯的处理：F6，缺数给 `None`）。
- **表存在但没数据 → 返回 `{}`**。
- **列缺失 → 返回 `None` 并记 warning**：CPT 改表是它的事，本仓不猜。
- 读连接走 `utils.db.connect_ro()`（服务端强制只读）。
"""

from __future__ import annotations

import logging
from typing import Any, Final

from emotion_core.utils.db import connect_ro

log = logging.getLogger("emotion_core.data.providers.cpt_intraday")

__all__ = ["CPT_INTRADAY_TABLE", "CptIntradayProvider", "fetch_quotes_from_cpt"]

#: CPT 落表名（ADR 0003 D2/G）。**表还没建** —— 读不到就返回 None。
#: ⚠️ CPT 建表时**必须按 ADR 0003 §D2 的 DDL 建**（列名/单位/可空性），
#: 否则这里读不到列。列清单见 `_COLUMNS`。
CPT_INTRADAY_TABLE: Final[str] = "intraday_quote"

#: 一次取多少行（取最近若干帧的同一批票）。
DEFAULT_LIMIT: Final[int] = 500

#: 读取列。缺列 → 返回 None 并记 warning（CPT 改表是它的事，本仓不猜）。
#: 读取列。**必须与 ADR 0003 §D2 的 DDL 对齐** —— CPT 落表时按那张表建。
_COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "ts",
    "code",
    "name",
    "price",
    "prev_close",
    "chg_pct",
    "bid1",
    "bid1_vol",
    "ask1",
    "ask1_vol",
    "at_limit",
    "src",
)

_SELECT_SQL: Final[str] = f"""
SELECT {", ".join(_COLUMNS)}
FROM {CPT_INTRADAY_TABLE}
ORDER BY ts DESC
LIMIT %s
"""


class CptIntradayProvider:
    """从 CPT 落库表读盘中五档。"""

    name = "cpt_intraday"

    def fetch_quotes(self, codes: list[str] | None = None) -> dict[str, dict[str, Any]] | None:
        """取最近若干帧的 quote。

        Args:
            codes: 可选，只要这些 6 位代码的。`None` = 全要。

        Returns:
            ``{6位代码: quote}``；**表不存在 → `None`**；表存在但无行 → `{}``。
        """
        wanted = set(codes) if codes else None
        out: dict[str, dict[str, Any]] = {}
        try:
            with connect_ro() as conn, conn.cursor() as cur:
                cur.execute(_SELECT_SQL, (DEFAULT_LIMIT,))
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
            # 只要最新的一帧：rows 按 ts DESC 排，首次遇到即为最新。
            out.setdefault(code, record)
        return out


def fetch_quotes_from_cpt(
    codes: list[str] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """便捷入口（模块级函数形态，与 `SinaQuoteProvider.fetch_quotes` 对齐）。"""
    return CptIntradayProvider().fetch_quotes(codes)
