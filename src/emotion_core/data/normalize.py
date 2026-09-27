"""Pure normalization for common Sina/Tencent daily bars.

从 asel/sources/free_daily.py 移植，去掉 asel 依赖。
本模块只负责归一化，不含网络/文件/数据库代码。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None
    adjust_type: str | None
    code: str
    source: str
    source_ref: str | None
    fetched_at: datetime | str | None
    quality: str = "valid"


_DATE_KEYS = ("trade_date", "date", "日期", "day", "time")
_OPEN_KEYS = ("open", "开盘", "开盘价", "o")
_HIGH_KEYS = ("high", "最高", "最高价", "h")
_LOW_KEYS = ("low", "最低", "最低价", "l")
_CLOSE_KEYS = ("close", "收盘", "收盘价", "c")
_VOLUME_KEYS = ("volume", "成交量", "成交量(股)", "成交量(手)", "vol", "v")
_AMOUNT_KEYS = ("amount", "成交额", "成交额(元)", "成交额(万元)", "turnover")
_ADJUST_KEYS = ("adjust_type", "adjust", "复权", "复权类型", "前复权")


def _pick(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid {field}: {value!r}")
    return result


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    if len(text) >= 10:
        text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid trade_date: {value!r}") from exc


def normalize_daily_rows(
    rows: Iterable[Mapping[str, Any]],
    code: str,
    source: str,
    source_ref: str | None = None,
    fetched_at: datetime | str | None = None,
) -> tuple[DailyBar, ...]:
    """Normalize fixture-like Sina/Tencent rows without silently repairing data.

    Missing fields or malformed dates/numbers raise ``ValueError``.
    Numeric quality violations are represented explicitly as ``quality='invalid'``;
    their original values are retained (no clamping, swapping, or sign fixes).
    """
    result: list[DailyBar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("daily row must be a mapping")
        values = {
            "trade_date": _pick(row, _DATE_KEYS),
            "open": _pick(row, _OPEN_KEYS),
            "high": _pick(row, _HIGH_KEYS),
            "low": _pick(row, _LOW_KEYS),
            "close": _pick(row, _CLOSE_KEYS),
            "volume": _pick(row, _VOLUME_KEYS),
        }
        missing = [name for name, value in values.items() if value is None]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        trade_date = _date(values["trade_date"])
        open_ = _decimal(values["open"], "open")
        high = _decimal(values["high"], "high")
        low = _decimal(values["low"], "low")
        close = _decimal(values["close"], "close")
        volume = _decimal(values["volume"], "volume")
        amount_raw = _pick(row, _AMOUNT_KEYS)
        amount = None if amount_raw is None else _decimal(amount_raw, "amount")
        invalid = (
            open_ <= 0 or high <= 0 or low <= 0 or close <= 0 or volume < 0
            or high < max(open_, close, low) or low > min(open_, close, high)
            or (amount is not None and amount < 0)
        )
        result.append(DailyBar(
            trade_date, open_, high, low, close, volume, amount,
            _pick(row, _ADJUST_KEYS), code, source, source_ref, fetched_at,
            "invalid" if invalid else "valid",
        ))
    return tuple(result)
