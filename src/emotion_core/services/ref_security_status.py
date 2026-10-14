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


def _seed_events(client: WindClient, day: date) -> list[dict]:
    """起始日快照：当日已在风险警示板的股票（区间自 day 起）。

    没有这一步，2024-01-01 之前就 ST、且在窗口内只有「撤销」事件的股票会被
    误判为 2024 全年非 ST。按板块拆问以避开 100 行硬上限。
    """
    events: list[dict] = []
    for group in _SEED_GROUPS:
        question = (
            f"筛选{day:%Y年%m月%d日}处于ST（风险警示）状态的{group}A股股票，列出证券简称"
        )
        for columns, rows in _fetch_with_retry(client, question):
            if len(rows) >= _ROW_CAP:
                logger.warning("种子窗口触顶（%s）：%s 行，建议按更细板块重跑", group, len(rows))
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


def backfill_security_status_bulk(
    start: date | None = None,
    end: date | None = None,
    sleep_sec: float = 0.5,
) -> tuple[int, int]:
    """批量枚举 ST 事件并重建 ``ref_security_status`` 区间。

    与 ``backfill_security_status``（逐股）互补：本函数只烧 ~20~30 次调用。

    Returns:
        (写入区间的 code 数, 写入区间行数)
    """
    client = _is_st_client()
    if not client:
        return 0, 0
    if start is None:
        start = date(2024, 1, 1)
    if end is None:
        end = date.today()

    events: list[dict] = list(_seed_events(client, start))
    aborted = False
    for kind in (_KIND_IMPLEMENT, _KIND_REVERT):
        if aborted:
            break
        cursor = start
        while cursor <= end:
            chunk_end = min(
                date(cursor.year, 12 if cursor.month > 6 else 6, 30 if cursor.month <= 6 else 31),
                end,
            )
            try:
                got = _enumerate_window(client, kind, cursor, chunk_end)
            except WindQuotaError as exc:
                logger.warning("Wind 额度耗尽，改用已取到的数据落库：%s", exc)
                aborted = True
                break
            logger.info("ST %s %s~%s：%d 事件", kind, cursor, chunk_end, len(got))
            events.extend(got)
            cursor = chunk_end + timedelta(days=1)
            time.sleep(sleep_sec)
        time.sleep(sleep_sec)

    by_code: dict[str, list[dict]] = {}
    for ev in events:
        by_code.setdefault(ev["code"], []).append(ev)

    codes_written = 0
    rows_written = 0
    for code, code_events in by_code.items():
        intervals = _build_intervals(code_events)
        rows_written += _write_intervals(code, intervals)
        codes_written += 1
    logger.info(
        "ST 批量回填完成：%d 个 code，%d 条区间（原始事件 %d）",
        codes_written,
        rows_written,
        len(events),
    )
    return codes_written, rows_written


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
