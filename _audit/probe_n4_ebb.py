"""N4 前后对照：把 `_rule_ebb.two_day` 加上「跌停 > EBB_LD_MIN」这道闸，值不值。

只读。对照两套规则在同样 675 天上的相位序列：
- old：原来那条（最高板连着两天炸板就退潮）
- new：现行代码（再加「当天跌停 > 15」）

看三件事：
1) 相位分布与「高潮↔退潮」来回切的次数（抖动是判据不稳的直接症状）；
2) 被收紧掉的那些天（old=退潮、new≠退潮）次日到底有多差 —— 如果它们
   次日照样跌停一片、最高板照样断，那收紧就是错的；
3) 保留的退潮日 vs 收紧掉的日子的次日高度延续率对比。
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import date

from emotion_core.algorithms import state as stt
from emotion_core.utils.db import query_df

START, END = date(2024, 1, 1), date(2026, 10, 22)


def load() -> list[stt.DayMetrics]:
    df = query_df(
        "SELECT date, limit_up_count, bomb_rate, zt_performance, max_limit_days,"
        "       limit_down_count, top_amplitude, top_broke, bomb_threshold,"
        "       has_candidate, tradable_max_days, oneword_ratio"
        "  FROM market_stat WHERE date >= %s AND date <= %s ORDER BY date",
        (START, END))
    out = []
    for r in df.itertuples(index=False):
        out.append(stt.DayMetrics(
            date=r.date, limit_up_count=int(r.limit_up_count or 0),
            bomb_rate=None if r.bomb_rate is None else float(r.bomb_rate),
            zt_performance=None if r.zt_performance is None else float(r.zt_performance),
            max_limit_days=int(r.max_limit_days or 0),
            limit_down_count=int(r.limit_down_count or 0),
            top_amplitude=None if r.top_amplitude is None else float(r.top_amplitude),
            top_broke=None if r.top_broke is None else bool(r.top_broke),
            bomb_threshold=float(r.bomb_threshold or 0.42),
            has_candidate=bool(r.has_candidate),
            tradable_max_days=int(r.tradable_max_days or 0),
            oneword_ratio=None if r.oneword_ratio is None else float(r.oneword_ratio)))
    return out


def _old_ebb(t, y, b, r=stt.DEFAULT_RULES) -> bool:
    two_day = bool(t.top_broke) and y is not None and bool(y.top_broke)
    perf = stt._lt(t.zt_performance, r.ebb_zt_perf)
    ld = (y is not None and t.limit_down_count > r.ebb_ld_min
          and t.limit_down_count > y.limit_down_count * r.ebb_ld_mult)
    return two_day or perf or ld


def classify(series, old: bool):
    saved = stt._RULES
    if old:
        stt._RULES = ((_old_ebb, "退潮"),) + saved[1:]
    try:
        return stt.classify_series(series, None)
    finally:
        stt._RULES = saved


def dist(states) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in states:
        out[s.phase] = out.get(s.phase, 0) + 1
    return out


def flips(states) -> int:
    n = 0
    for a, b in zip(states, states[1:]):
        if {a.phase, b.phase} == {"高潮", "退潮"}:
            n += 1
    return n


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 3) if xs else None


def main() -> int:
    series = load()
    print(f"样本 {len(series)} 天  {series[0].date} ~ {series[-1].date}")
    old = classify(series, True)
    new = classify(series, False)

    for tag, states in (("old", old), ("new", new)):
        print(f"\n[{tag}] 相位分布 {dist(states)}  高潮↔退潮切换 {flips(states)} 次")

    # 被收紧掉的日子
    dropped = [i for i, (a, b) in enumerate(zip(old, new)) if a.phase == "退潮" and b.phase != "退潮"]
    kept = [i for i, (a, b) in enumerate(zip(old, new)) if a.phase == "退潮" and b.phase == "退潮"]
    print(f"\n旧退潮 {sum(1 for s in old if s.phase == '退潮')} 天 → 新退潮 {sum(1 for s in new if s.phase == '退潮')} 天"
          f"；收紧掉 {len(dropped)} 天，保留 {len(kept)} 天")

    def day_at(i):
        return series[i] if 0 <= i < len(series) else None

    def nxt(i):
        return day_at(i + 1)

    def profile(idx: list[int], label: str):
        ld_next = [nxt(i).limit_down_count for i in idx if nxt(i)]
        zt_next = [nxt(i).zt_performance for i in idx if nxt(i)]
        up = [1 if (nxt(i) and nxt(i).max_limit_days > series[i].max_limit_days) else 0
              for i in idx if nxt(i)]
        same = [1 if (nxt(i) and nxt(i).max_limit_days >= series[i].max_limit_days) else 0
                for i in idx if nxt(i)]
        print(f"  {label:14s} n={len(idx):3d}  次日跌停中位 {med(ld_next)}"
              f"  次日涨停表现中位 {med(zt_next)}"
              f"  次日高度上升率 {sum(up)/len(up):.2%}" if up else "")
        print(f"  {'':14s}      次日高度不降率 {sum(same)/len(same):.2%}" if same else "")

    profile(dropped, "收紧掉的")
    profile(kept, "仍判退潮的")

    # 全部日子的次日跌停 / 高度延续，按 next-phase 分组作参照
    print("\n[参照] 按当日相位分组（新口径）的次日高度不降率：")
    for ph in ("冰点", "发酵", "退潮", "高潮"):
        idx = [i for i, s in enumerate(new) if s.phase == ph and i + 1 < len(new)]
        if not idx:
            continue
        same = [1 if new[i + 1] and series[i + 1].max_limit_days >= series[i].max_limit_days else 0 for i in idx]
        print(f"  {ph}: n={len(idx):3d}  次日高度不降率 {sum(same)/len(same):.2%}"
              f"  次日跌停中位 {med([series[i+1].limit_down_count for i in idx])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
