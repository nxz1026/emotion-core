"""读 CPT 盘中落库表的 provider（契约：CPT `docs/intraday-seal-contract.md`）。

## 表长什么样（CPT R93，2026-10-23，9 列）

ADR 0003 §G 原方案是本仓判涨停、CPT 交**原始五档**（`intraday_quote`，13 列）。
CPT 实际交付的是**每一轮的封板结论** `public.intraday_seal_snapshot`，R90 起 5 列，
R93 按本仓 N5 需求补到 **9 列**（CPT `docs/n5-reply-to-emotion.md`）：

```
at           timestamptz      这一轮采集时刻（UTC 存、UTC 输出）
code         text             6 位纯代码
sealed       boolean          这一轮是否封在板上
bid1_lots    double precision 买一量（**手**，不是股；判不了为 NULL）
limit_price  double precision 本轮用的涨停价（CPT 的 Decimal + 半分进位口径）
price        double precision R93 这一轮现价（判不了 NULL）
prev_close   double precision R93 昨收（判不了 NULL）
distance_pct double precision R93 距板百分比（0=在板上，负数=超了，正数=还差；百分数不是小数）
is_st        boolean          R93 ST 判定结果（NULL = 没判，**别当 False**）
```

R93 两处换法要注意（CPT 回复 §3）：

- **不给 `chg_pct`**：`(price - prev_close) / prev_close` 的原料我们都有，存一份就是
  重复事实、两份迟早不一致；改给 `distance_pct`，因为它依赖 CPT 的涨停价口径
  （浮点 `10.0 * 1.1 = 11.000000000000002`），我们自己除会差一分钱。
- **不给 `name`**：名字会变（`600xxx` 改名成 `ST某某` 后，同一 `at` 的历史行会自相
  矛盾）；改给 `is_st` 布尔。判据仍在 CPT 的 `is_st_name`，本仓不解析字符串。

存量（2026-10-22 那 3 轮）四列**全是 NULL 且不回填** —— 补出来的是推算值不是采集值。
判据里遇到 NULL 要当「不知道」，不能当 `False` / `0`。

主键 `(at, code)`，append-only，索引 `(code, at)` 与 `(at DESC)`。
改表必须同步三处：契约文件 → CPT `cpt/storage/intraday_seal_store.py::_INSERT_SQL` →
本文件的 `_COLUMNS` / `_SELECT_SQL`。

## 三条读表铁律（契约 §3，别自己想当然）

1. **没行 = 判不了，不是「没封板」**。候选之外、停牌、缺档位都不会有行；
   把「缺行」当「开板」会凭空造出开板证据。
2. **`sealed=False` 是采到了且这一轮没封**（开板证据），必须与「这一轮没采到」分开。
   另有一条：`sealed=False` 且 `bid1_lots` 为 NULL = **判不了**（停牌/缺档位），
   见契约 §7 最后一行 —— 用 :func:`sealed_state` 一次判完这三种情况。
3. **按 `at` 分组取最新一轮**，不要按 `code` 单点查最新 —— 跨轮混算会把
   「上一轮封着、这一轮开板」读成两只票。

补充：`at` 是 UTC，**算「当天」一律按北京日切**（分界 UTC 16:00 = 北京次日 00:00）；
单轮 HTTP 失败 = 这一轮没行，**轮次缺失 ≠ 开板**。

再补充（契约 §9）：表里可能出现**哨兵行**（`code` 以 `0x01` 开头，测试直接写真库的残留）。
本文件的 `_SELECT_SQL` 已在子查询与主查询两处排除；自己写裸 SQL 读这张表时必须照做，
否则 `max(at)` 只会读到那一行假的，整轮真票被遮蔽。

## 本 provider 只做「读」

- **封板与否由 CPT 判**，本仓不再用 `algorithms/intraday.py` 重算 CPT 来的数据
  （那套纯函数保留：§C4 定档门禁、回测、自采兜底还要用）。
- **`bid1_lots` 单位是手**。本仓 `SEAL_BID1_VOL_MIN` 是股，比较前用
  :func:`bid1_vol_shares` 换算，别拿手直接比股。

## 返回值契约（沿用 F6「缺数给 None」）

- **表不存在 → `None`**，不抛、不报警（表还没建是正常状态，不是故障）。
- **表存在但一轮都没有 → `(None, {})`**（区分「没建表」与「还没采」）。
- 读连接走 `utils.db.connect_ro()`（服务端强制只读）。
"""

from __future__ import annotations

import logging
from typing import Any, Final

from emotion_core.utils.db import connect_ro

log = logging.getLogger("emotion_core.data.providers.cpt_intraday")

__all__ = [
    "CPT_INTRADAY_TABLE",
    "SHARES_PER_LOT",
    "CptIntradayProvider",
    "bid1_vol_shares",
    "fetch_quotes_from_cpt",
    "fetch_round_from_cpt",
    "is_st_flag",
    "sealed_state",
    "to_quote",
]

#: CPT 落表的表名（契约 §1）。
#: ⚠️ 不是 §G 里的 `intraday_quote` —— 那张表 CPT 没建，也不要再按 §G 去建。
CPT_INTRADAY_TABLE: Final[str] = "intraday_seal_snapshot"

#: 读取列。缺列 → 返回 None 并记 warning（CPT 改表是它的事，本仓不猜）。
_COLUMNS: Final[tuple[str, ...]] = (
    "at",
    "code",
    "sealed",
    "bid1_lots",
    "limit_price",
    "price",
    "prev_close",
    "distance_pct",
    "is_st",
)

#: 一手 = 100 股。`bid1_lots` 存的是手，本仓阈值是股。
SHARES_PER_LOT: Final[int] = 100

#: 只取最新一轮：`at` 是整轮的时间戳，同一轮内所有 code 共享同一个值。
# 契约 §9：生产表里可能出现哨兵行（测试直接写真库时留下的 `code` 以 0x01 开头）。
# 裸 SQL 读方必须自己排除 —— 否则 `SELECT max(at)` 只会读到那 1 行假的，
# 整轮 33 只真实票被遮蔽（CPT R91 实测过）。子查询与主查询都得过滤。
_SENTINEL_PATTERN: Final[str] = r"E'\x01%'"

_SELECT_SQL: Final[str] = f"""
SELECT {", ".join(_COLUMNS)}
FROM {CPT_INTRADAY_TABLE}
WHERE at = (
    SELECT max(at) FROM {CPT_INTRADAY_TABLE}
     WHERE code NOT LIKE {_SENTINEL_PATTERN}
)
  AND code NOT LIKE {_SENTINEL_PATTERN}
ORDER BY code
"""


def sealed_state(record: dict[str, Any] | None) -> bool | None:
    """这一轮到底封没封 —— 三态，`None` 表示「判不了」。

    契约 §3 第 1、2 条 + §7 最后一行合成一个入口，调用方不该自己拼条件：

    - `record is None`（**没行**）→ `None`：候选之外/停牌/缺档位，**不是没封板**。
    - `sealed=False` 且 `bid1_lots is None` → `None`：采到了但缺档位，判不了。
    - 其余按 `sealed` 原值。
    """
    if record is None:
        return None
    sealed = record.get("sealed")
    if sealed is None:
        return None
    if not bool(sealed) and record.get("bid1_lots") is None:
        return None
    return bool(sealed)


def is_st_flag(record: dict[str, Any] | None) -> bool | None:
    """这一轮 CPT 判出来的 ST 标记 —— 三态。

    - `record is None` 或 `is_st` 为 NULL → `None` **不知道**（R93 之前的老行全这样）。
    - `True` / `False` 为 CPT 的判定结果。

    ⚠️ `None` 不许当 `False` 用：ST 的涨停是 5%，当成非 ST 会算出 10% 的价位，
    「已封板」会被误判成「还没到」（2026-10-07 北交所 ST 那次就是这么翻的车）。
    """
    if record is None:
        return None
    flag = record.get("is_st")
    return None if flag is None else bool(flag)


def to_quote(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 CPT 记录转成本仓 E3 形状的 quote（`algorithms/intraday.py` 直接能吃）。

    映射：`price → last`、`prev_close → prev_close`、`bid1_lots × 100 → bid1_vol`、
    `is_st=True → name="ST"`（本仓 `is_st_name` 只看名字里有没有 ST）。
    `limit_price` 原样带过去，判「到板没有」可以直接比它，不必再用本仓公式复算
    （CPT 用 Decimal 半分进位，本仓用整数分四舍五入，两者逐分一致但没必要赌）。

    Returns:
        quote 字典；`price` 缺失（判不了）→ `None`。
    """
    if record is None:
        return None
    price = record.get("price")
    if price is None:
        return None
    st = is_st_flag(record)
    return {
        "code": str(record.get("code") or "").strip(),
        "name": "ST" if st else None,
        "last": float(price),
        "prev_close": None if record.get("prev_close") is None else float(record["prev_close"]),
        "bid1": None,
        "bid1_vol": bid1_vol_shares(record),
        "ask1": None,
        "limit_price": None if record.get("limit_price") is None else float(record["limit_price"]),
        "distance_pct": record.get("distance_pct"),
        "is_st": st,
    }


def bid1_vol_shares(record: dict[str, Any] | None) -> float | None:
    """把 `bid1_lots`（手）换算成股；缺数给 `None`（不是 0）。"""
    if record is None:
        return None
    lots = record.get("bid1_lots")
    if lots is None:
        return None
    return float(lots) * SHARES_PER_LOT


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
            表存在但一轮都没有 → `(None, {})`；有轮次但过滤后为空 → `(round_at, {})`
            —— `round_at` 反映「这一轮存在」，不因为没命中代码就变 `None`。
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
            # 先记轮次时间戳，再看 code 是否命中 —— 过滤不该抹掉「这一轮存在」。
            if round_at is None:
                round_at = record.get("at")
            if wanted is not None and code not in wanted:
                continue
            out[code] = record
        return round_at, out

    def fetch_quotes(self, codes: list[str] | None = None) -> dict[str, dict[str, Any]] | None:
        """只要最新一轮的 `{code: record}`（`fetch_round` 的薄封装）。

        ⚠️ 返回的 dict 里**没有**某个代码 = 那一轮没采到它（判不了），
        不等于「它没封板」。判封板用 :func:`sealed_state`。
        """
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
