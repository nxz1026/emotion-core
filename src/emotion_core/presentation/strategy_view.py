"""策略观察台：从 reports 文件读取独立策略快照。

从 longkonglong/dashboard/strategyview.py 剥离，依赖替换为 emotion_core 等价物：
- lkl.utils.db.query_df  → emotion_core.utils.db.query_df
- loader.REPORTS         → CONFIG.STRATEGY_REPORTS_DIR
- loader._safe_date      → datetime.date.fromisoformat + 异常捕获
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

REPORTS = Path(CONFIG.STRATEGY_REPORTS_DIR)
_DATE_RE = re.compile(r"strategy_(\d{4}-\d{2}-\d{2})\.json$")


def _safe_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def strategy_dates() -> list[str]:
    """返回策略快照日期，倒序且只接受有效 ISO 日期。"""
    dates = []
    try:
        paths = list(REPORTS.glob("strategy_*.json"))
    except OSError:
        return []
    for path in paths:
        match = _DATE_RE.fullmatch(path.name)
        if not match or not _safe_date(match.group(1)):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("items"):
            dates.append(match.group(1))
    return sorted(set(dates), reverse=True)


def load_strategy(date_str: str) -> dict | None:
    """读取指定日期策略快照；日期非法或文件损坏返回 None。"""
    safe = _safe_date(date_str)
    if not safe:
        return None
    path = REPORTS / f"strategy_{safe.isoformat()}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def to_api(date_str: str) -> dict:
    """生成策略页面只读 API 数据。"""
    payload = load_strategy(date_str)
    if payload is None:
        return {"items": [], "dates": [], "note": "当日无策略产出（开关未开或休市）"}
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    codes = sorted({str(item.get("code")) for item in items if item.get("code")})
    names: dict[str, str] = {}
    if codes:
        try:
            frame = query_df(
                "SELECT code, name FROM stock_basic WHERE code = ANY(%s)", (codes,)
            )
            names = {
                str(row.code): row.name
                for row in frame.itertuples()
                if row.name is not None
            }
        except Exception:
            names = {}
    items = [dict(item, stock_name=names.get(str(item.get("code")))) for item in items]
    result = dict(payload)
    result["items"] = sorted(items, key=lambda item: item.get("score", 0), reverse=True)
    result["dates"] = strategy_dates()
    return result
