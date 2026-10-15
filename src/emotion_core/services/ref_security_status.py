"""股票安全状态表 ref_security_status 回填服务。

ST/*ST 历史数据来自 Wind get_stock_events。

## 列名映射

Wind get_stock_events 返回两种事件格式（列名不同）：

1. **ST 变动事件**（查询含 "ST变动" / "风险警示"）：
   Wind代码, 证券简称, 实施ST后简称, 实施ST前简称, 实施ST日期, 实施ST原因

2. **违规处分事件**（查询含 "违规" / "处分"）：
   Wind代码, 证券简称, 证监会行业名称, Wind中国行业名称,
   违规主体与上市公司关系, 违规处分类型, 违规公告日期,
   违规涉及相关法规, 违规处分措施, 违规主体, 违规处罚金额, 违规行为

两种格式都解析，写入 ref_security_status。

## 配额纪律

Wind 每次调用消耗真实额度。ST 历史回填是低频运维任务（非 daily），
所以：
- 只针对当前 is_st=true 的股票回填（避免对 5000+ 只全量调用）
- 批量串行，每次调用间隔 0.5s 避免限流
- 落库时记录原始响应与配额消耗
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta

from emotion_core.services.wind_client import (
    WindClient,
    WindQuotaError,
    WindSourceError,
    WindUnavailableError,
)
from emotion_core.services.wind_manifest import record_call
from emotion_core.utils.db import connect as db_connect

logger = logging.getLogger(__name__)


def _parse_events(data: dict) -> list[dict]:
    """解析 Wind get_stock_events 返回的事件列表（兼容 ST 变动 + 违规处分两种格式）。

    格式 1 — ST 变动（查询含 "ST变动" / "风险警示"）：
        Wind代码, 证券简称, 实施ST后简称, 实施ST前简称, 实施ST日期, 实施ST原因

    格式 2 — 违规处分（查询含 "违规" / "处分"）：
        Wind代码, 证券简称, 违规处分类型, 违规公告日期, 违规行为, ...

    返回标准化事件列表：[{"code": ..., "status": ..., "effective_from": ..., "reason": ...}]
    """
    events: list[dict] = []
    inner = data.get("data", {})
    if not inner:
        return events
    # 处理 "没找到数据" 文本
    if isinstance(inner, str) and "没找到数据" in inner:
        return events
    tables = inner.get("data", [])
    if not tables:
        return events
    for table in tables:
        columns = [c.get("name", "") for c in table.get("columns", [])]
        rows = table.get("rows", [])
        for row in rows:
            raw = dict(zip(columns, row))
            # 格式 1: ST 变动
            if "实施ST日期" in raw:
                event_date = raw.get("实施ST日期", "")
                if not event_date:
                    continue
                try:
                    effective_from = date.fromisoformat(str(event_date)[:10])
                except (ValueError, TypeError):
                    continue
                events.append({
                    "code": raw.get("Wind代码", "").split(".")[0],
                    "status": raw.get("实施ST后简称", ""),
                    "effective_from": effective_from,
                    "reason": raw.get("实施ST原因", ""),
                })
            # 格式 2: 违规处分
            elif "违规公告日期" in raw:
                event_date = raw.get("违规公告日期", "")
                if not event_date:
                    continue
                try:
                    effective_from = date.fromisoformat(str(event_date)[:10])
                except (ValueError, TypeError):
                    continue
                event_type = raw.get("违规处分类型", "")
                # 只保留 ST/风险警示/撤销相关
                if not any(kw in event_type for kw in ("ST", "风险警示", "撤销")):
                    continue
                events.append({
                    "code": raw.get("Wind代码", "").split(".")[0],
                    "status": event_type,
                    "effective_from": effective_from,
                    "reason": raw.get("违规行为", ""),
                })
    return events


def _is_st_client() -> WindClient | None:
    """创建 WindClient，不可用时返回 None。"""
    client = WindClient()
    available, reason = client.availability()
    if not available:
        logger.warning("Wind 不可用：%s", reason)
        return None
    return client


def backfill_security_status(
    codes: list[str] | None = None,
    sleep_sec: float = 0.5,
) -> tuple[int, int]:
    """回填 ST 股票的安全状态历史。

    Args:
        codes: 要回填的 code 列表，None 表示所有当前 is_st=true 的股票。
        sleep_sec: 每次调用间隔（秒）。

    Returns:
        (成功数, 跳过数)
    """
    client = _is_st_client()
    if not client:
        return 0, 0

    if codes is None:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT code FROM stock_basic WHERE is_st = true")
                codes = [row[0] for row in cur.fetchall()]

    inserted = 0
    skipped = 0

    for code in codes:
        # 转换为 Wind 格式（加交易所后缀）
        wind_code = _to_wind_code(code)
        if not wind_code:
            skipped += 1
            continue

        try:
            call = client.call(
                "stock_data",
                "get_stock_events",
                {"question": f"查询{wind_code}的ST变动、风险警示记录"},
            )
            # 落库原始响应
            record_call(call)

            events = _parse_events(call.data)
            if events:
                for event in events:
                    # 只写入当前 code 的事件（Wind 可能返回多只股票）
                    if event["code"] == code:
                        _upsert_events(code, [event])
                inserted += 1
            else:
                skipped += 1
        except WindQuotaError as exc:
            logger.warning("Wind 额度耗尽，停止回填：%s", exc)
            break
        except WindSourceError as exc:
            logger.warning("Wind 调用失败（%s）：%s", code, exc)
            skipped += 1
        except WindUnavailableError as exc:
            logger.warning("Wind 不可用：%s", exc)
            break

        time.sleep(sleep_sec)

    return inserted, skipped


def _to_wind_code(code: str) -> str | None:
    """将 6 位 code 转换为 Wind 格式（600519.SH）。"""
    if len(code) != 6:
        return None
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    elif code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SZ"  # 默认深市


def _upsert_events(code: str, events: list[dict]) -> None:
    """将 ST 事件写入 ref_security_status。"""
    with db_connect() as conn:
        with conn.cursor() as cur:
            for event in events:
                event_date = event.get("effective_from")
                status = event.get("status", "")
                reason = event.get("reason", "")

                if not event_date or not status:
                    continue

                cur.execute(
                    """INSERT INTO ref_security_status
                       (code, status, effective_from, reason, source)
                       VALUES (%s, %s, %s, %s, 'wind')
                       ON CONFLICT (code, status, effective_from) DO UPDATE
                       SET reason = EXCLUDED.reason""",
                    (code, status, event_date, reason),
                )
            conn.commit()


# ---------------------------------------------------------------- 批量枚举路径
#
# 逐股 get_stock_events 要烧 5000+ 次调用；ST 事件本身是**稀疏且可枚举**的，
# 用 search_stocks 按日期窗口批量问「这段时间实施/撤销 ST 的股票」即可，
# 全量 2024 起只要 ~20~30 次调用。search_stocks 单次返回硬上限 100 行，
# 超了就把窗口二分再问（见 _enumerate_window）。
_ROW_CAP = 100
_KIND_IMPLEMENT = "实施"
_KIND_REVERT = "撤销"

_STATUS_ST = "ST"
_STATUS_STAR_ST = "*ST"
_STATUS_NORMAL = "NORMAL"

# 起始日快照按板块拆，避开 100 行硬上限
_SEED_GROUPS = ("沪市主板", "深市主板", "创业板", "科创板", "北交所")


def _normalize_status(name: str) -> str:
    """从证券简称推风险警示档位：含 *ST → ``*ST``；含 ST → ``ST``；否则 ``NORMAL``。"""
    upper = (name or "").upper().replace(" ", "")
    if "*ST" in upper or "S*ST" in upper:
        return _STATUS_STAR_ST
    if "ST" in upper:
        return _STATUS_ST
    return _STATUS_NORMAL


def _pick(raw: dict, *needles: str) -> str:
    """按「列名包含 needle」取值。

    Wind 的日期列名是**动态**的：窗口边界不是自然周期时，列名会变成
    ``2025年1月1日到2025年6月30日撤销日期``（实测），所以不能按全名取。
    """
    for needle in needles:
        for key, value in raw.items():
            if needle in key and value not in (None, ""):
                return str(value)
    return ""


def _bulk_question(kind: str, start: date, end: date) -> str:
    span = f"{start:%Y年%m月%d日}至{end:%Y年%m月%d日}"
    if kind == _KIND_IMPLEMENT:
        return f"筛选{span}期间实施ST（被实施风险警示）的A股股票，列出实施ST日期"
    return f"筛选{span}期间被撤销ST（摘帽）的A股股票，列出撤销ST日期"


def _parse_bulk_rows(columns: list[str], rows: list[list], kind: str) -> list[dict]:
    """解析 search_stocks 批量行 → 事件 ``{code,date,kind,status,reason}``。

    ``status=None`` 表示「Wind 没给事件后简称、档位不可知」——撤销事件的精简列形
    （``股票编号/证券名称/…撤销日期/证券简称``）就是这种，其中 ``证券简称`` 是
    **当前**简称而非当时简称，不可用来推断档位，故留 None 由区间折叠层按
    「延续上一档位」保守处理。
    """
    events: list[dict] = []
    for row in rows:
        raw = dict(zip(columns, row, strict=False))
        code = _pick(raw, "Wind代码", "股票编号").split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        if kind == _KIND_IMPLEMENT:
            date_text = _pick(raw, "实施ST日期")
            after = _pick(raw, "实施ST后简称")
            # 实施事件无论如何都是风险警示板 → 拿不到档位时按 ST 记
            status = _normalize_status(after) if after else _STATUS_ST
            reason = _pick(raw, "实施ST原因")
        else:
            date_text = _pick(raw, "撤销ST日期", "撤销日期")
            after = _pick(raw, "撤销ST后简称")
            before = _pick(raw, "撤销ST前简称", "撤销前简称")
            status = _normalize_status(after) if after else None
            reason = f"撤销ST：{before}→{after or '（Wind 未返回后简称）'}"
        if not date_text:
            continue
        try:
            event_date = date.fromisoformat(date_text[:10])
        except (ValueError, TypeError):
            continue
        events.append({
            "code": code,
            "date": event_date,
            "kind": kind,
            "status": status,
            "reason": reason[:200] or f"{kind}ST",
        })
    return events


def _build_intervals(events: list[dict]) -> list[dict]:
    """把同一个 code 的事件序列折成 ST 区间（左闭右开 ``[from, to)``）。

    事件按日期排序（同日「实施」先于「撤销」），每个事件终结上一个区间，并按
    「事件后档位」决定是否开启新区间：``NORMAL`` 只终结不开启；``None``（不可知）
    按延续上一档位处理——这是保守方向：宁可多算一段 ST，也不漏掉 ±5% 限制期。
    """
    if not events:
        return []
    order = {_KIND_IMPLEMENT: 0, _KIND_REVERT: 1}
    ordered = sorted(events, key=lambda e: (e["date"], order.get(e["kind"], 9)))
    out: list[dict] = []
    cur: dict | None = None
    current = _STATUS_NORMAL
    for ev in ordered:
        if cur is not None:
            cur["effective_to"] = ev["date"]
            if cur["effective_to"] > cur["effective_from"]:
                out.append(cur)
            cur = None
        after = ev["status"] if ev["status"] is not None else current
        current = after or _STATUS_NORMAL
        if current != _STATUS_NORMAL:
            cur = {
                "code": ev["code"],
                "status": current,
                "effective_from": ev["date"],
                "effective_to": None,
                "reason": ev["reason"],
            }
    if cur is not None:
        out.append(cur)
    return out


def _fetch_rows_for_question(
    client: WindClient, question: str
) -> list[tuple[list[str], list[list]]]:
    call = client.call("stock_data", "search_stocks", {"question": question})
    record_call(call)
    inner = call.data.get("data", {}) if isinstance(call.data, dict) else {}
    if isinstance(inner, str) or not inner:
        return []
    return [
        ([c.get("name", "") for c in t.get("columns", [])], t.get("rows", []))
        for t in inner.get("data", [])
    ]


def _fetch_bulk_rows(
    client: WindClient, kind: str, start: date, end: date
) -> list[tuple[list[str], list[list]]]:
    return _fetch_rows_for_question(client, _bulk_question(kind, start, end))


def _fetch_with_retry(
    client: WindClient, question: str, *, attempts: int = 2
) -> list[tuple[list[str], list[list]]]:
    """取数并重试；网络类失败返回空（窗口留给下次重跑），额度类失败上抛。"""
    for i in range(attempts):
        try:
            return _fetch_rows_for_question(client, question)
        except WindQuotaError:
            raise
        except WindSourceError as exc:
            logger.warning("Wind 取数失败（第 %d/%d 次）：%s | %s", i + 1, attempts, exc, question)
            time.sleep(2.0 * (i + 1))
    return []


def _enumerate_window(
    client: WindClient, kind: str, start: date, end: date, *, depth: int = 0
) -> list[dict]:
    """枚举一个日期窗口的 ST 事件；返回行数触顶就二分窗口再问。"""
    tables = _fetch_with_retry(client, _bulk_question(kind, start, end))
    events: list[dict] = []
    total = 0
    for columns, rows in tables:
        total += len(rows)
        events.extend(_parse_bulk_rows(columns, rows, kind))
    if total < _ROW_CAP or depth >= 4 or start >= end:
        return events
    mid = start + (end - start) // 2
    left = _enumerate_window(client, kind, start, mid, depth=depth + 1)
    right = _enumerate_window(client, kind, mid + timedelta(days=1), end, depth=depth + 1)
    return left + right


def _seed_question(group: str, day: date) -> str:
    return (f"筛选{day:%Y年%m月%d日}处于ST（风险警示）状态的{group}A股股票，"
            f"列出证券简称")


def _parse_seed_rows(columns: list[str], rows: list[list], day: date) -> list[dict]:
    """起始日快照行 → 实施事件（档位取当日简称）。"""
    events: list[dict] = []
    for row in rows:
        raw = dict(zip(columns, row, strict=False))
        code = _pick(raw, "Wind代码", "股票编号").split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        st = _normalize_status(_pick(raw, "证券简称", "证券名称"))
        events.append({
            "code": code,
            "date": day,
            "kind": _KIND_IMPLEMENT,
            "status": st if st != _STATUS_NORMAL else _STATUS_ST,
            "reason": "起始日已在风险警示板（种子）",
        })
    return events


def _seed_events(client: WindClient, day: date) -> list[dict]:
    """起始日快照：当日已在风险警示板的股票（区间自 day 起）。

    没有这一步，2024-01-01 之前就 ST、且在窗口内只有「撤销」事件的股票会被
    误判为 2024 全年非 ST。按板块拆问以避开 100 行硬上限。
    """
    events: list[dict] = []
    for group in _SEED_GROUPS:
        for columns, rows in _fetch_with_retry(client, _seed_question(group, day)):
            if len(rows) >= _ROW_CAP:
                logger.warning("种子窗口触顶（%s）：%s 行，建议按更细板块重跑", group, len(rows))
            events.extend(_parse_seed_rows(columns, rows, day))
    logger.info("ST 种子（%s）：%d 只", day, len(events))
    return events


def _write_intervals(code: str, intervals: list[dict]) -> int:
    """按 code 全量替换区间（单事务）。"""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ref_security_status WHERE code = %s", (code,))
            if intervals:
                cur.executemany(
                    """INSERT INTO ref_security_status
                       (code, status, effective_from, effective_to, reason, source)
                       VALUES (%s, %s, %s, %s, %s, 'wind')
                       ON CONFLICT (code, status, effective_from) DO UPDATE
                       SET effective_to = EXCLUDED.effective_to,
                           reason = EXCLUDED.reason""",
                    [
                        (
                            i["code"],
                            i["status"],
                            i["effective_from"],
                            i["effective_to"],
                            i["reason"],
                        )
                        for i in intervals
                    ],
                )
        conn.commit()
    return len(intervals)


_SEED_PREFIX = "seed:"
_DEFAULT_CALL_BUDGET = 40            # 与逐日涨跌停对账共用同一个日额度闸

_WINDOW_OK = "ok"
_WINDOW_EXHAUSTED = "exhausted"      # 本地预算用尽（不是 Wind 报错）
_WINDOW_QUOTA = "quota"              # Wind 报额度/积分不足
_WINDOW_ERROR = "error"              # 单窗口取数失败（可重跑）


class _BudgetExhausted(Exception):
    """本地调用预算用尽（自己设的闸，不是 Wind 报错）。"""


class _BudgetClient:
    """给 WindClient 套一层**本地调用预算**。

    Wind 日额度有限（m00753「慢慢拉」），而 `_enumerate_window` 内部会按需二分，
    一个「窗口」可能变成 2^k 次调用——只在**每次真实调用**处计数才拦得住。
    """

    def __init__(self, client: WindClient, budget: int | None) -> None:
        self._client = client
        self.budget = budget
        self.calls = 0

    def call(self, server_type: str, tool_name: str, params: dict):
        if self.budget is not None and self.calls >= self.budget:
            raise _BudgetExhausted(f"本地预算 {self.budget} 次调用已用尽")
        self.calls += 1
        return self._client.call(server_type, tool_name, params)


def _default_call_budget() -> int:
    """日调用预算：``EMOTION_WIND_DAILY_CALL_BUDGET``，非法值退回默认并记 warning。"""
    raw = os.environ.get("EMOTION_WIND_DAILY_CALL_BUDGET", "").strip()
    if not raw:
        return _DEFAULT_CALL_BUDGET
    try:
        return int(raw)
    except ValueError:
        logger.warning("EMOTION_WIND_DAILY_CALL_BUDGET=%r 不是整数，改用默认 %d",
                       raw, _DEFAULT_CALL_BUDGET)
        return _DEFAULT_CALL_BUDGET


def _half_end(d: date) -> date:
    """半年窗口右端：1~6 月 → 6/30，7~12 月 → 12/31。"""
    if d.month > 6:
        return date(d.year, 12, 31)
    return date(d.year, 6, 30)


def window_plan(start: date, end: date) -> list[tuple[str, date, date]]:
    """待办窗口清单：5 个板块种子（kind ``seed:<板块>``）+ 实施/撤销的半年窗。"""
    plan = [(f"{_SEED_PREFIX}{g}", start, start) for g in _SEED_GROUPS]
    for kind in (_KIND_IMPLEMENT, _KIND_REVERT):
        cursor = start
        while cursor <= end:
            chunk_end = min(_half_end(cursor), end)
            plan.append((kind, cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
    return plan


def _window_status() -> dict[tuple[str, date, date], str]:
    """窗口进度：``{(kind, start, end): status}``。"""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT kind, start_date, end_date, status"
                        " FROM ref_security_status_window")
            return {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}


def _mark_window(kind: str, start: date, end: date, status: str, n_events: int,
                 calls: int, note: str | None) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ref_security_status_window
                   (kind, start_date, end_date, status, n_events, calls, note, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (kind, start_date, end_date) DO UPDATE
                   SET status = EXCLUDED.status, n_events = EXCLUDED.n_events,
                       calls = EXCLUDED.calls, note = EXCLUDED.note,
                       fetched_at = now()""",
                (kind, start, end, status, n_events, calls, note))
        conn.commit()


def _persist_events(events: list[dict]) -> int:
    """原始事件落表（幂等）：断点续跑靠它，区间重建也只看它。"""
    if not events:
        return 0
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ref_security_status_event
                   (code, date, kind, status, reason, source, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, 'wind', now())
                   ON CONFLICT (code, date, kind) DO UPDATE
                   SET status = EXCLUDED.status, reason = EXCLUDED.reason""",
                [(e["code"], e["date"], e["kind"], e.get("status"), e.get("reason"))
                 for e in events])
        conn.commit()
    return len(events)


def rebuild_intervals_from_events() -> tuple[int, int]:
    """按事件表**全量重建** ST 区间（单事务）。

    只在所有窗口都 ``ok`` 之后调用：事件不全时重建会把「撤销事件还没拉到」的
    股票写成永不闭合的开区间——2026-10-14 首次实跑就是这么得到 297 条开区间、
    随后整表回滚的（CHANGELOG 有记）。宁可不动 `ref_security_status`。
    """
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT code, date, kind, status, reason"
                        " FROM ref_security_status_event ORDER BY code, date")
            rows = cur.fetchall()
            if not rows:
                return 0, 0
            by_code: dict[str, list[dict]] = {}
            for code, day, kind, status, reason in rows:
                by_code.setdefault(code, []).append(
                    {"code": code, "date": day, "kind": kind,
                     "status": status, "reason": reason})
            cur.execute("DELETE FROM ref_security_status WHERE source = 'wind'")
            written = 0
            for code, events in by_code.items():
                intervals = _build_intervals(events)
                if not intervals:
                    continue
                cur.executemany(
                    """INSERT INTO ref_security_status
                       (code, status, effective_from, effective_to, reason, source)
                       VALUES (%s, %s, %s, %s, %s, 'wind')
                       ON CONFLICT (code, status, effective_from) DO UPDATE
                       SET effective_to = EXCLUDED.effective_to,
                           reason = EXCLUDED.reason""",
                    [(code, i["status"], i["effective_from"], i["effective_to"],
                      i["reason"]) for i in intervals])
                written += len(intervals)
        conn.commit()
    logger.info("ST 区间重建：%d 个 code，%d 条区间", len(by_code), written)
    return len(by_code), written


def bulk_progress(start: date | None = None, end: date | None = None) -> dict:
    """窗口进度摘要（脚本/巡检用）：total / ok / failed / pending / first_pending。"""
    if start is None:
        start = date(2024, 1, 1)
    if end is None:
        end = date.today()
    plan = window_plan(start, end)
    done = _window_status()
    ok = [w for w in plan if done.get(w) == _WINDOW_OK]
    failed = [w for w in plan
              if done.get(w) in (_WINDOW_ERROR, _WINDOW_QUOTA, _WINDOW_EXHAUSTED)]
    return {
        "total": len(plan),
        "ok": len(ok),
        "failed": len(failed),
        "pending": len(plan) - len(ok) - len(failed),
        "first_pending": next((w for w in plan if done.get(w) != _WINDOW_OK), None),
    }


def backfill_security_status_bulk(
    start: date | None = None,
    end: date | None = None,
    sleep_sec: float = 0.5,
    budget_calls: int | None = None,
    force: bool = False,
) -> tuple[int, int]:
    """**预算化、可断点续跑**的批量 ST 回填（与逐股路径互补）。

    进度记在 `ref_security_status_window`、原始事件记在
    `ref_security_status_event`：同一条命令重复跑即续拉，窗口全部 ``ok`` 才重建
    `ref_security_status`（见 `rebuild_intervals_from_events` 为何不能半途重建）。

    Args:
        start/end: 覆盖区间（默认 2024-01-01 ~ 今天）。
        budget_calls: 本次最多烧几次 Wind 调用；None → 环境变量
            ``EMOTION_WIND_DAILY_CALL_BUDGET``，再退回 40。
        force: 忽略已完成进度，全部窗口重抓。

    Returns:
        (写入区间的 code 数, 写入区间行数)；窗口未跑完时返回 (0, 0)（区间表不动）。
    """
    client = _is_st_client()
    if not client:
        return 0, 0
    if start is None:
        start = date(2024, 1, 1)
    if end is None:
        end = date.today()
    budget = budget_calls if budget_calls is not None else _default_call_budget()
    plan = window_plan(start, end)
    done = _window_status()
    todo = [w for w in plan if force or done.get(w) != _WINDOW_OK]
    logger.info("ST 批量回填：窗口 %d 个、待办 %d 个、本次预算 %s 次调用",
                len(plan), len(todo), budget)

    wrapped = _BudgetClient(client, budget)
    for kind, win_start, win_end in todo:
        before = wrapped.calls
        try:
            if kind.startswith(_SEED_PREFIX):
                group = kind[len(_SEED_PREFIX):]
                tables = _fetch_with_retry(wrapped, _seed_question(group, win_start))
                events = [e for columns, rows in tables
                          for e in _parse_seed_rows(columns, rows, win_start)]
            else:
                events = _enumerate_window(wrapped, kind, win_start, win_end)
        except _BudgetExhausted as exc:
            _mark_window(kind, win_start, win_end, _WINDOW_EXHAUSTED, 0,
                         wrapped.calls - before, str(exc))
            logger.info("本地预算用尽，停在 %s %s~%s（本次已用 %d 次）",
                        kind, win_start, win_end, wrapped.calls)
            break
        except WindQuotaError as exc:
            _mark_window(kind, win_start, win_end, _WINDOW_QUOTA, 0, 0, str(exc)[:200])
            logger.warning("Wind 额度耗尽，停在 %s %s~%s：%s",
                           kind, win_start, win_end, exc)
            break
        except (WindSourceError, WindUnavailableError) as exc:
            _mark_window(kind, win_start, win_end, _WINDOW_ERROR, 0,
                         wrapped.calls - before, str(exc)[:200])
            logger.warning("窗口取数失败（下次重跑）：%s %s~%s：%s",
                           kind, win_start, win_end, exc)
            continue
        _persist_events(events)
        _mark_window(kind, win_start, win_end, _WINDOW_OK, len(events),
                     wrapped.calls - before, None)
        logger.info("ST %s %s~%s：%d 事件（累计 %d 次调用）",
                    kind, win_start, win_end, len(events), wrapped.calls)
        time.sleep(sleep_sec)

    done = _window_status()
    pending = [w for w in plan if done.get(w) != _WINDOW_OK]
    if pending:
        logger.info("ST 批量回填未完成：还剩 %d 个窗口（重复跑同一条命令即续拉）",
                    len(pending))
        return 0, 0
    return rebuild_intervals_from_events()


def st_codes_on(conn, day: date) -> dict[str, str]:
    """返回 ``day`` 当日的风险警示档位 ``{code: 'ST'|'*ST'}``（PIT）。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT code, status FROM ref_security_status
               WHERE status IN ('ST', '*ST')
                 AND effective_from <= %s
                 AND (effective_to IS NULL OR effective_to > %s)""",
            (day, day),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def populate_is_st_from_wind(
    codes: list[str] | None = None,
    sleep_sec: float = 0.5,
) -> tuple[int, int]:
    """从 Wind get_stock_basicinfo 回填 stock_basic.is_st。

    使用 "是否属于风险警示板" 字段判断 ST 状态。
    is_st 全 False 时调用此函数初始化。

    Returns:
        (更新为 ST 数, 跳过数)
    """
    client = _is_st_client()
    if not client:
        return 0, 0

    if codes is None:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT code FROM stock_basic")
                codes = [row[0] for row in cur.fetchall()]

    st_count = 0
    skipped = 0

    for code in codes:
        wind_code = _to_wind_code(code)
        if not wind_code:
            skipped += 1
            continue

        try:
            call = client.call(
                "stock_data",
                "get_stock_basicinfo",
                {"question": f"查询{wind_code}是否属于风险警示板、是否ST"},
            )
            record_call(call)

            # 解析返回数据
            data = call.data
            inner = data.get("data", {})
            if isinstance(inner, str):
                skipped += 1
                continue
            tables = inner.get("data", [])
            if not tables:
                skipped += 1
                continue

            for table in tables:
                columns = [c.get("name", "") for c in table.get("columns", [])]
                for row in table.get("rows", []):
                    raw = dict(zip(columns, row))
                    risk_flag = raw.get("是否属于风险警示板", "")
                    if risk_flag == "是":
                        with db_connect() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE stock_basic SET is_st = true WHERE code = %s",
                                    (code,)
                                )
                                conn.commit()
                        st_count += 1
                        break
        except (WindQuotaError, WindUnavailableError):
            break
        except WindSourceError:
            skipped += 1

        time.sleep(sleep_sec)

    return st_count, skipped
