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
