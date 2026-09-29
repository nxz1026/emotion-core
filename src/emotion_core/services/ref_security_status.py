"""股票安全状态表 ref_security_status 回填服务。

ST/*ST 历史数据来自 Wind get_stock_events。

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
from datetime import date

from emotion_core.utils.db import connect as db_connect
from emotion_core.services.wind_client import (
    WindClient,
    WindQuotaError,
    WindSourceError,
    WindUnavailableError,
)
from emotion_core.services.wind_manifest import record_call

logger = logging.getLogger(__name__)


def _parse_st_events(data: dict) -> list[dict]:
    """解析 Wind get_stock_events 返回的 ST 事件列表。

    Wind 返回格式：
    {"data": {"data": [{"columns": [...], "rows": [...]}], "error": null}}

    每行：[Wind代码, 事件类型, 事件日期, 事件描述, ...]
    """
    events = []
    inner = data.get("data", {})
    if not inner:
        return events
    tables = inner.get("data", [])
    if not tables:
        return events
    for table in tables:
        columns = [c.get("name", "") for c in table.get("columns", [])]
        rows = table.get("rows", [])
        for row in rows:
            event = dict(zip(columns, row))
            # 只保留 ST 相关事件
            event_type = event.get("事件类型", "")
            if "ST" in event_type or "风险警示" in event_type or "撤销" in event_type:
                events.append(event)
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
                {"question": f"查询{wind_code}的ST历史、风险警示记录"},
            )
            # 落库原始响应
            record_call(call)

            events = _parse_st_events(call.data)
            if events:
                _upsert_events(code, events)
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
                # 解析日期
                event_date_str = event.get("事件日期", "")
                event_type = event.get("事件类型", "")
                description = event.get("事件描述", "")

                if not event_date_str:
                    continue

                try:
                    event_date = date.fromisoformat(str(event_date_str)[:10])
                except (ValueError, TypeError):
                    continue

                cur.execute(
                    """INSERT INTO ref_security_status
                       (code, status, effective_from, reason, source)
                       VALUES (%s, %s, %s, %s, 'wind')
                       ON CONFLICT (code, status, effective_from) DO UPDATE
                       SET reason = EXCLUDED.reason""",
                    (code, event_type, event_date, description),
                )
