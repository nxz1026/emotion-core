"""快照日期（snapshot date）解析与日历视图数据。

展示层所有页面都挂在一个**快照日期**上：默认取库里最后一个有快照的交易日
（不是 `date.today()` —— 这正是 2026-09-28 那次「直观层显示未知/今日无推荐」的
真因：最后快照是 09-24，而页面查的是当天）。

产出三样东西，供模板渲染：
- `available_dates()` / `latest_date()`：可选快照日（来源 market_stat，即"有快照的交易日"）
- `resolve_date(raw)`：把 URL 上的 `date=YYYY-MM-DD` 收紧到合法快照日（非法/无数据 → 回最新）
- `calendar_ctx()` / `banner_ctx()`：月历网格与"当前快照日期"提示
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.dash.snapshot")

_CACHE: dict[str, tuple[float, object]] = {}
_TTL_SEC = 60  # 日更落新快照后最多 60s 就在网页可见

_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def _cached(key: str, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL_SEC:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    """清缓存（测试与运维用）。"""
    _CACHE.clear()


def available_dates() -> list[date]:
    """有快照的交易日（降序）。来源 market_stat —— 只有跑过的交易日才有行。"""
    def _load() -> list[date]:
        df = query_df("SELECT date FROM market_stat ORDER BY date DESC")
        return [r for r in df["date"].tolist() if r is not None]

    return _cached("dates", _load)


def latest_date() -> date | None:
    """最新快照日。"""
    dates = available_dates()
    return dates[0] if dates else None


def is_trading_day(d: date) -> bool:
    """该日是否有快照（等价于"是否跑过且是交易日"）。

    不查独立交易日历：日历要高亮的是"**有快照**的日子"，这比"是否开市"更贴近用户
    需求（开市但没跑的日子点了也是空的）。交易日历仍由 `data/trade_calendar` 负责。
    """
    return d in set(available_dates())


def resolve_date(raw: str | None, default: date | None = None) -> date | None:
    """把 URL 参数收紧成合法快照日。

    - 空 → 最新快照日
    - 合法日期且有快照 → 该日
    - 合法日期但无快照（非交易日 / 那天没跑）→ 最新快照日（并由调用方在页面上提示）
    - 非法字符串 → 最新快照日
    """
    fallback = default if default is not None else latest_date()
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        log.warning("非法快照日期参数：%r，回退到 %s", raw, fallback)
        return fallback
    if is_trading_day(parsed):
        return parsed
    log.info("快照日期 %s 无快照，回退到 %s", parsed, fallback)
    return fallback


def _parse_month(raw: str | None, anchor: date) -> date:
    """"YYYY-MM" → 该月 1 日；非法/缺省 → 锚点日所在月。"""
    text = (raw or "").strip()
    if text:
        try:
            y, m = text[:7].split("-")
            return date(int(y), int(m), 1)
        except (ValueError, AttributeError):
            log.warning("非法 month 参数：%r", raw)
    return anchor.replace(day=1)


def _shift_month(first: date, delta: int) -> date:
    y, m = first.year, first.month + delta
    if m < 1:
        y, m = y - 1, 12
    elif m > 12:
        y, m = y + 1, 1
    return date(y, m, 1)


def calendar_ctx(selected: date | None, month: str | None = None,
                 today: date | None = None) -> dict:
    """渲染月历所需的数据（周一为每周第一天，与国内看盘习惯一致）。"""
    dates = available_dates()
    have = set(dates)
    anchor = selected or (dates[0] if dates else (today or date.today()))
    first = _parse_month(month, anchor)
    nxt = _shift_month(first, 1)
    prev = _shift_month(first, -1)

    weeks: list[list[dict]] = []
    day = first - timedelta(days=first.weekday())      # 补到该周周一
    last_day_of_month = (nxt - timedelta(days=1)).day
    while True:
        week = []
        for _ in range(7):
            week.append({
                "iso": day.isoformat(),
                "day": day.day,
                "in_month": day.month == first.month,
                "has_data": day in have,
                "is_selected": day == selected,
                "is_latest": bool(dates) and day == dates[0],
                "is_today": day == (today or date.today()),
            })
            day += timedelta(days=1)
        weeks.append(week)
        # 收尾：已经盖过本月最后一天，且整周都在下月时停
        if day.month != first.month and day > nxt:
            break
        if len(weeks) > 6:                              # 安全阀
            break

    return {
        "month_label": f"{first.year} 年 {first.month} 月",
        "month_value": f"{first.year:04d}-{first.month:02d}",
        "weekday_labels": _WEEKDAY_CN,
        "weeks": weeks,
        "prev_month": f"{prev.year:04d}-{prev.month:02d}",
        "next_month": f"{nxt.year:04d}-{nxt.month:02d}",
        "prev_has_data": any(d.year == prev.year and d.month == prev.month for d in dates),
        "next_has_data": any(d.year == nxt.year and d.month == nxt.month for d in dates),
        "month_days": sum(1 for w in weeks for c in w
                          if c["in_month"] and c["has_data"]),
        "month_total": last_day_of_month,
    }


def recent_snapshots_ctx(selected: date | None, limit: int = 10) -> list[dict]:
    """最近快照日列表（日历侧栏用）：iso + 星期 + 是否最新/选中。"""
    dates = available_dates()
    return [
        {
            "iso": d.isoformat(),
            "weekday": _WEEKDAY_CN[d.weekday()],
            "is_latest": bool(dates) and d == dates[0],
            "is_selected": d == selected,
        }
        for d in dates[:limit]
    ]


def banner_ctx(selected: date | None) -> dict:
    """「当前快照日期」提示条所需数据。"""
    dates = available_dates()
    latest = dates[0] if dates else None
    if selected is None:
        return {"selected": None, "latest": latest, "is_latest": False,
                "prev": None, "next": None, "days_behind": None,
                "weekday": ""}
    try:
        idx = dates.index(selected)
    except ValueError:
        idx = -1
    prev_d = dates[idx + 1] if 0 <= idx < len(dates) - 1 else None
    next_d = dates[idx - 1] if idx > 0 else None
    return {
        "selected": selected,
        "selected_iso": selected.isoformat(),
        "weekday": _WEEKDAY_CN[selected.weekday()],
        "latest": latest,
        "is_latest": selected == latest,
        "prev": prev_d,
        "next": next_d,
        "days_behind": (latest - selected).days if latest else None,
    }


def date_links(selected: date | None) -> dict:
    """导航/按钮用的日期串。"""
    return {"date_query": f"?date={selected.isoformat()}" if selected else ""}
