"""股票分红除权表 ref_dividend 回填服务。

除权除息日数据来自 Wind get_stock_events（与 ref_security_status 同源）。
emotion-core 不复权体系，此表供分析参考（LLM 策略观察 / 公告事件关联）。

## 配额纪律

Wind 每次调用消耗真实额度。分红除权回填是低频运维任务（非 daily），
所以：
- 只针对有分红历史的股票回填（从 stock_basic 取 list_date 在 2024 年前的）
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


def _parse_dividend_events(data: dict) -> list[dict]:
    """解析 Wind get_stock_events 返回的分红除权事件列表。

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
            # 只保留分红除权相关事件
            event_type = event.get("事件类型", "")
            keywords = ["分红", "送股", "转增", "派息", "除权", "除息", "红利"]
            if any(kw in event_type for kw in keywords):
                events.append(event)
    return events


def _wind_client_or_none() -> WindClient | None:
    """创建 WindClient，不可用时返回 None。"""
    client = WindClient()
    available, reason = client.availability()
    if not available:
        logger.warning("Wind 不可用：%s", reason)
        return None
    return client


def backfill_dividend(
    codes: list[str] | None = None,
    sleep_sec: float = 0.5,
) -> tuple[int, int]:
    """回填股票分红除权历史。

    Args:
        codes: 要回填的 code 列表，None 表示从 stock_basic 取老股。
        sleep_sec: 每次调用间隔（秒）。

    Returns:
        (成功数, 跳过数)
    """
    client = _wind_client_or_none()
    if not client:
        return 0, 0

    if codes is None:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code FROM stock_basic WHERE list_date < '2024-01-01' LIMIT 500"
                )
                codes = [row[0] for row in cur.fetchall()]

    inserted = 0
    skipped = 0

    for code in codes:
        wind_code = _to_wind_code(code)
        if not wind_code:
            skipped += 1
            continue

        try:
            call = client.call(
                "stock_data",
                "get_stock_events",
                {"question": f"查询{wind_code}的分红送股转增历史"},
            )
            record_call(call)

            events = _parse_dividend_events(call.data)
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
    return f"{code}.SZ"


def _upsert_events(code: str, events: list[dict]) -> None:
    """将分红除权事件写入 ref_dividend。"""
    with db_connect() as conn:
        with conn.cursor() as cur:
            for event in events:
                event_date_str = event.get("事件日期", "")
                if not event_date_str:
                    continue

                try:
                    event_date = date.fromisoformat(str(event_date_str)[:10])
                except (ValueError, TypeError):
                    continue

                # 从描述中提取数值（简化处理）
                description = event.get("事件描述", "")
                dividend = _extract_digit(description, "每股派")
                bonus = _extract_digit(description, "送股")
                rights = _extract_digit(description, "转增")

                cur.execute(
                    """INSERT INTO ref_dividend
                       (code, record_date, dividend, bonus_shares, rights_issue, source)
                       VALUES (%s, %s, %s, %s, %s, 'wind')
                       ON CONFLICT (code, record_date) DO UPDATE
                       SET dividend = EXCLUDED.dividend,
                           bonus_shares = EXCLUDED.bonus_shares,
                           rights_issue = EXCLUDED.rights_issue""",
                    (code, event_date, dividend, bonus, rights),
                )


def _extract_digit(text: str, keyword: str) -> float | None:
    """从描述文本中提取 keyword 后的数字。"""
    import re
    pattern = rf"{keyword}\s*([0-9.]+)"
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None
