"""Wind 逐日涨跌停状态拉取（权威对账源；配额友好、可断点续跑）。

为什么要它
----------
`derived_bar.is_limit_up` / `is_limit_down` 是**自算口径**（照搬 lkl：
`utils/config.py` 的 `LIMIT_PCT_DEFAULT=110`、`LIMIT_PCT_BY_PREFIX={"68":120,"30":120}`），
**没有 ST 的 ±5% 档**。2026-10-14 实测 2025-06-03：Wind 判涨停 83 只 vs 自算 67 只，
自算无假阳性、差的 12 只当日涨幅均 ≈ +5%，即当日低估约 15.2%（已记 `docs/04 §3`）。
本模块把 Wind 的逐日名单拉下来落 `ref_limit_state`，供对账与后续口径修正。

Wind 侧能力（2026-10-14 实测 `stock_data.search_stocks`）
--------------------------------------------------------
- 问句「筛选2025-06-03涨停的A股股票」→ `[Wind代码, 证券简称, <日期>涨跌停状态]`，
  状态 1 = 涨停；实测 2025-06-03 得 83 只。
- **硬上限 100 行**：实测 2024-10-08 恰好返回 100 行（当日真实涨停远超 100）⇒ 被截断。
- 加市值约束可切片：「筛选2024-10-08涨停且总市值在20亿元到100亿元之间的A股股票」，
  同时返回 `<日期>总市值`（单位亿元，实测中国人寿 11180.1376）。故按市值**对数二分**
  可拆到每片 <100 行：实测同日 >1000亿 29 行、<20亿 55 行、20~100亿 100 行（触顶）。

调用模型
--------
平日 1 次调用；触顶则二分成 2 片、递归，叶子仍触顶（市值无区分度或超深度）才标
`truncated`。**调用预算交给调用方**（`max_calls`，脚本默认取日额度），预算耗尽返回
`exhausted=True` 且该日不写 `ok` 状态 → 次日续跑即可，「额度每天有限制，慢慢拉」。

只读/只写口径：本模块不写 `derived_bar`（那是生产判定），只写参考名单 + 台账。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from emotion_core.services.wind_client import WindClient
from emotion_core.utils.db import query_df, transaction

SERVER_TYPE = "stock_data"
TOOL_NAME = "search_stocks"
#: Wind 单次返回行数硬上限（实测恰好 100 行即被截断）。
ROW_CAP = 100
#: 二分最大深度（防御市值无区分度时无限递归）。
MAX_DEPTH = 16
#: 单日单方向的最大叶子数硬闸（防「切片后结果没变」导致的指数级调用爆炸）。
MAX_LEAVES = 64
#: 方向 → 问句用词。
LIMIT_WORD = {"up": "涨停", "down": "跌停"}
DIRECTIONS = tuple(LIMIT_WORD)
#: 根问句的市值下界（亿元）。实测**不带市值约束**的问句回包只有
#: `[Wind代码, 证券简称, 〈日期〉涨跌停状态]` 三列、**不含总市值**，触顶时无从二分
#: （2026-08-17 因此被判 `truncated`）；加一个极宽下界即可让回包带上总市值列，
#: 对 A 股等价于不设限（没有总市值 <1 亿元的票）。
#:
#: 取 1.0 而非 0.01：实测下界写成 `0.01亿元` 时**问句解析失败**（回 `没找到数据`），
#: 左子片会静默丢票（2026-08-17 实测丢 7 只，只有 only_self 能暴露）。
ROOT_CAP_FLOOR = 1.0
#: 台账状态：ok=名单完整；truncated=有叶子仍触顶（真·切不动，不值得天天重试）；
#: suspect=出现过「已知有票的区间却回空」的自相矛盾（问句解析类丢票，值钱、该重试）；
#: exhausted=预算耗尽；error=调用失败。
STATUS_OK = "ok"
STATUS_TRUNCATED = "truncated"
STATUS_SUSPECT = "suspect"
STATUS_EXHAUSTED = "exhausted"
STATUS_ERROR = "error"


@dataclass
class LimitFetchResult:
    """单日单方向的拉取结果。"""

    day: date
    direction: str
    codes: list[str] = field(default_factory=list)
    calls: int = 0
    leaves: int = 0
    truncated: bool = False
    suspect: bool = False
    exhausted: bool = False
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return STATUS_ERROR
        if self.exhausted:
            return STATUS_EXHAUSTED
        if self.suspect:
            return STATUS_SUSPECT
        if self.truncated:
            return STATUS_TRUNCATED
        return STATUS_OK

    @property
    def complete(self) -> bool:
        return self.status in (STATUS_OK, STATUS_TRUNCATED)


def to_local_code(wind_code: str) -> str:
    """``600519.SH`` → ``600519``；``920363.BJ`` → ``920363``（本库 code 无后缀）。"""
    text = str(wind_code).strip()
    return text.split(".", 1)[0] if text else ""


def build_question(
    day: date, direction: str, lo: float | None = None, hi: float | None = None
) -> str:
    """拼 Wind 自然语言问句（``lo``/``hi`` 为总市值上下界，单位亿元，None = 不设）。"""
    word = LIMIT_WORD[direction]
    base = f"筛选{day.isoformat()}{word}的A股股票"
    if lo is None and hi is None:
        return base
    if lo is not None and hi is not None:
        # 实测「总市值在 X 亿元到 Y 亿元之间」Wind 会当成没结果（回 '没找到数据'），
        # 「（大于等于 X 且）小于等于 Y」才认；闭合边界让相邻片在切点重复，
        # 由 fetch_day 的并集去重吸收，宁可重复不可漏。
        return (
            f"筛选{day.isoformat()}{word}且总市值大于等于{lo:.2f}亿元"
            f"且总市值小于等于{hi:.2f}亿元的A股股票"
        )
    if lo is not None:
        return f"筛选{day.isoformat()}{word}且总市值大于{lo:.2f}亿元的A股股票"
    return f"筛选{day.isoformat()}{word}且总市值小于等于{hi:.2f}亿元的A股股票"


def _find_table(payload: Any) -> dict | None:
    """在任意嵌套里找第一个 ``{columns, rows}`` 表块（Wind 回包层级实测会变）。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict):
        inner = payload.get("data")
        if (
            isinstance(inner, dict)
            and isinstance(inner.get("data"), list)
            and inner["data"]
            and isinstance(inner["data"][0], dict)
            and "columns" in inner["data"][0]
            and "rows" in inner["data"][0]
        ):
            return inner["data"][0]
        for value in payload.values():
            got = _find_table(value)
            if got is not None:
                return got
    elif isinstance(payload, list):
        for value in payload:
            got = _find_table(value)
            if got is not None:
                return got
    return None


def _index_of(names: list[str], pred: Callable[[str], bool]) -> int | None:
    for i, name in enumerate(names):
        if pred(name):
            return i
    return None


def has_table(payload: Any) -> bool:
    """回包是否含可解析的表块。

    Wind 对「零匹配」直接回字符串 ``'没找到数据'``（无表），与问句没解析出来**同形**，
    故无表只能说明「本次没有行」，不能说明「通道坏了」。
    """
    return _find_table(payload) is not None


def parse_rows(payload: Any) -> list[tuple[str, float | None]]:
    """回包 → ``[(本地代码, 总市值亿元|None)]``（认不出代码的行丢弃）。

    市值列名实测带序号后缀（``2026年8月17日总市值1``，旁边还有 ``市值币种``），
    故用**包含**匹配而非 ``endswith("总市值")``；单位实测为亿元（中国人寿 11180.1376）。
    """
    block = _find_table(payload)
    if not block:
        return []
    names = [str(c.get("name", "")) for c in block.get("columns", []) or []]
    idx_code = _index_of(names, lambda n: "代码" in n)
    idx_cap = _index_of(names, lambda n: "总市值" in n)
    if idx_code is None:
        return []
    out: list[tuple[str, float | None]] = []
    for row in block.get("rows", []) or []:
        if idx_code >= len(row):
            continue
        code = to_local_code(row[idx_code])
        if not code:
            continue
        cap: float | None = None
        if idx_cap is not None and idx_cap < len(row):
            try:
                cap = float(row[idx_cap])
            except (TypeError, ValueError):
                cap = None
        out.append((code, cap))
    return out


def _window_contains(caps: list[float], lo: float | None, hi: float | None) -> bool:
    """已知市值里是否有落在 ``(lo, hi)`` 内（严格开区间，切点上的值不算）。"""
    return any((lo is None or cap > lo) and (hi is None or cap < hi) for cap in caps)


def split_point(lo: float | None, hi: float | None, values: list[float]) -> float | None:
    """触顶切片点：按**观测到的**市值区间取对数中点（无区分度 → None）。

    取对数中点是因为 A 股总市值跨 3 个数量级，等比切分才能把「千亿大盘」
    与「十亿小票」摊平到同一片里各 <100 行。

    优先用观测值而非窗口边界：子窗口在切片那一侧**不继承父窗口的窄边界**
    （见 `fetch_day`），所以按观测区间切仍然完整覆盖父窗口；反之若拿一个离观测
    很远的边界当端点（例如根窗口下界 0.01 亿元 vs 观测最小 8 亿元），对数中点会
    退化到几乎不动，二分不收敛。观测值无区分度时才退回窗口边界。
    """
    vals = sorted(v for v in values if v is not None and v > 0)
    if vals:
        a, b = vals[0], vals[-1]
    else:
        a = lo if lo is not None and lo > 0 else None
        b = hi if hi is not None and hi > 0 else None
        if a is None or b is None:
            return None
    if not (b > a):
        a = lo if lo is not None and lo > 0 else a
        b = hi if hi is not None and hi > 0 else b
    if not (b > a):
        return None
    mid = math.sqrt(a * b)
    if not (a < mid < b):
        mid = (a + b) / 2.0
    return mid if a < mid < b else None


def fetch_day(
    client: WindClient,
    day: date,
    direction: str = "up",
    max_calls: int | None = None,
    sleep_sec: float = 0.0,
) -> LimitFetchResult:
    """拉取某日某方向的涨跌停名单（触顶则按市值二分递归）。

    Args:
        client: Wind 客户端（测试可注入假实现）。
        day: 交易日。
        direction: ``"up"`` 涨停 / ``"down"`` 跌停。
        max_calls: 本次预算上限（None = 不限）。耗尽则 ``exhausted=True``。
        sleep_sec: 每次调用后的礼貌间隔。

    Returns:
        LimitFetchResult（``codes`` 去重排序）。
    """
    if direction not in LIMIT_WORD:
        raise ValueError(f"未知方向：{direction!r}（可选 {DIRECTIONS}）")
    result = LimitFetchResult(day=day, direction=direction)
    seen: set[str] = set()
    known_caps: list[float] = []
    signatures: set[tuple[int, float | None, float | None]] = set()
    stack: list[tuple[float | None, float | None, int]] = [(ROOT_CAP_FLOOR, None, 0)]
    while stack:
        if max_calls is not None and result.calls >= max_calls:
            result.exhausted = True
            break
        lo, hi, depth = stack.pop()
        question = build_question(day, direction, lo, hi)
        try:
            call = client.call(SERVER_TYPE, TOOL_NAME, {"question": question})
            result.calls += 1
            if getattr(call, "ok", True) is False:
                result.error = (
                    f"Wind 调用失败 code={getattr(call, 'code', '')} {getattr(call, 'message', '')}"
                )[:200]
                break
            if not has_table(call.data):
                # Wind 对「真的没有匹配」与「问句没解析出来」回同一句 '没找到数据'，形状
                # 完全一样。根问句（只带一个极宽下界）在全 A 口径下不可能一只都没有，
                # 故根问句无表 = 问句/通道异常；子片的空由下面的 known_caps 交叉验证兜。
                if depth == 0:
                    result.error = f"根问句无结果表：{question}"[:200]
                    break
                rows = []
            else:
                rows = parse_rows(call.data)
        except Exception as exc:  # noqa: BLE001 —— 记错并停：预算/额度问题由调用方判定
            result.calls += 1
            result.error = f"{type(exc).__name__}: {exc}"[:200]
            break
        result.leaves += 1
        seen.update(code for code, _ in rows)
        if len(rows) < ROW_CAP:
            if not rows and _window_contains(known_caps, lo, hi):
                # 空片落在一个「父触顶片明明有票」的市值区间里 → 不是真的空，
                # 是问句没被解析（实测 2026-08-17 左子片就这么静默丢了 7 只）。
                result.suspect = True
            if sleep_sec:
                time.sleep(sleep_sec)
            continue
        # 触顶：本片已知先收下，再二分继续（并集去重，不依赖边界是否闭区间）
        caps = [cap for _, cap in rows if cap is not None]
        known_caps.extend(caps)
        signature = (len(rows), min(caps) if caps else None, max(caps) if caps else None)
        no_progress = signature in signatures
        signatures.add(signature)
        cut = split_point(lo, hi, caps)
        if cut is None or depth >= MAX_DEPTH or no_progress or result.leaves >= MAX_LEAVES:
            # 无进展 = 子片回包与已见片完全同形（市值区间没起作用），再切只会指数爆炸。
            result.truncated = True
            if sleep_sec:
                time.sleep(sleep_sec)
            continue
        stack.append((lo, cut, depth + 1))
        stack.append((cut, hi, depth + 1))
        if sleep_sec:
            time.sleep(sleep_sec)
    result.codes = sorted(seen)
    return result


def self_limit_codes(day: date, direction: str = "up") -> set[str]:
    """本库自算口径的当日名单（``derived_bar``，只读）。"""
    column = "is_limit_up" if direction == "up" else "is_limit_down"
    df = query_df(f"SELECT code FROM derived_bar WHERE date = %s AND {column} IS TRUE", (day,))
    return {str(c) for c in df["code"]}


def persist_day(result: LimitFetchResult) -> dict:
    """落库：``ref_limit_state`` 名单 + ``ref_limit_state_run`` 台账（同日幂等重写）。

    Returns:
        与台账同形的计数字典。
    """
    self_codes = self_limit_codes(result.day, result.direction)
    wind_codes = set(result.codes)
    counts = {
        "n_wind": len(wind_codes),
        "n_self": len(self_codes),
        "n_only_wind": len(wind_codes - self_codes),
        "n_only_self": len(self_codes - wind_codes),
    }
    with transaction() as conn:
        prev = conn.execute(
            "SELECT retries FROM ref_limit_state_run WHERE date = %s AND direction = %s",
            (result.day, result.direction),
        ).fetchone()
        # retries = 该 (日期,方向) 的重抓次数：首次落库 0，之后每重写一次 +1。
        retries = (int(prev[0]) + 1) if prev else 0
        if not result.error:
            conn.execute(
                "DELETE FROM ref_limit_state WHERE date = %s AND direction = %s",
                (result.day, result.direction),
            )
            if result.codes:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO ref_limit_state (date, code, direction, source)"
                        " VALUES (%s, %s, %s, 'wind') ON CONFLICT DO NOTHING",
                        [(result.day, code, result.direction) for code in result.codes],
                    )
        conn.execute(
            "INSERT INTO ref_limit_state_run (date, direction, status, n_wind,"
            " n_self, n_only_wind, n_only_self, calls, retries, note)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (date, direction) DO UPDATE SET status = EXCLUDED.status,"
            " n_wind = EXCLUDED.n_wind, n_self = EXCLUDED.n_self,"
            " n_only_wind = EXCLUDED.n_only_wind, n_only_self = EXCLUDED.n_only_self,"
            " calls = EXCLUDED.calls, retries = EXCLUDED.retries,"
            " note = EXCLUDED.note, fetched_at = now()",
            (
                result.day,
                result.direction,
                result.status,
                counts["n_wind"],
                counts["n_self"],
                counts["n_only_wind"],
                counts["n_only_self"],
                result.calls,
                retries,
                result.error or None,
            ),
        )
    return {**counts, "status": result.status, "calls": result.calls, "retries": retries}
