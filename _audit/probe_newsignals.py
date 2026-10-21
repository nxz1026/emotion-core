"""只读探针：校验全量重算后的 BUY 信号集，用口径B（T+1 封板价买 → T+2 开盘卖）实测。

不写库。输出：总览 / 封板率 / 每信号期望 / 按相位 / 按身份档 / 按年度。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date

from emotion_core.utils.db import query_df
from emotion_core.utils.dates import prev_trading_day, next_trading_day
from emotion_core.utils.price import limit_down_price_cents, limit_up_price_cents

FEE = 0.0015
_TIER = re.compile(r"身份\s*(\S+?)：")


def cents(x) -> int | None:
    return None if x is None else int(round(float(x) * 100))


def load_bars(codes, start, end):
    df = query_df(
        "SELECT code, date, open, high, low, close, pre_close FROM daily_bar"
        " WHERE code = ANY(%s) AND date BETWEEN %s AND %s", (codes, start, end))
    out = defaultdict(dict)
    for r in df.itertuples():
        out[r.code][r.date] = r
    return out


def main() -> None:
    df = query_df(
        "SELECT s.confirm_date, s.code, s.action, s.checklist, m.phase,"
        " m.buy_window, m.diverge"
        " FROM signal s LEFT JOIN market_stat m ON m.date = s.confirm_date"
        " WHERE s.source = 'replay' AND s.action = 'BUY'"
        " ORDER BY s.confirm_date")
    print(f"BUY 信号 {len(df)} 条")
    codes = sorted(set(df["code"]))
    bars = load_bars(codes, date(2023, 12, 1), date(2026, 12, 31))

    rows = []
    for r in df.itertuples():
        c, d = r.code, r.confirm_date
        b0 = bars[c].get(d)
        t1 = next_trading_day(d)
        t2 = next_trading_day(t1) if t1 else None
        t3 = next_trading_day(t2) if t2 else None
        b1, b2, b3 = (bars[c].get(t1), bars[c].get(t2), bars[c].get(t3))
        if b0 is None or b1 is None or t2 is None:
            continue
        lim = limit_up_price_cents(cents(b0.close), c)
        filled = cents(b1.high) >= lim
        ret = None
        if filled:
            entry = lim / 100
            exit_px = None
            if b2 is not None:
                ld = limit_down_price_cents(cents(b1.close), c)
                locked = (cents(b2.open) <= ld and cents(b2.close) <= ld)
                if locked and b3 is not None:
                    exit_px = float(b3.open)
                elif locked:
                    continue
                else:
                    exit_px = float(b2.open)
            if exit_px is None:
                continue
            ret = (exit_px / entry - 1) * 100 - FEE * 100
        tier = "新插队"
        try:
            for lab, _ok, note in json.loads(r.checklist or "[]"):
                m = _TIER.search(note or "")
                if m:
                    tier = m.group(1)
                    break
        except (ValueError, TypeError):
            pass
        rows.append({"date": d, "code": c, "filled": filled, "ret": ret,
                     "phase": r.phase, "window": r.buy_window,
                     "diverge": r.diverge, "tier": tier})

    n = len(rows)
    fill = [x for x in rows if x["filled"]]
    rets = [x["ret"] for x in fill]
    per = sum(rets) / n if n else 0.0
    win = sum(1 for v in rets if v > 0) / len(rets) if rets else 0.0
    srt = sorted(rets)
    print(f"\n总览：可评估 {n} 条 / 封板 {len(fill)}（封板率 {len(fill)/n:.1%}）"
          f" / 每信号期望 {per:+.2f}%")
    if rets:
        print(f"封板后：均值 {sum(rets)/len(rets):+.2f}%  中位 {srt[len(srt)//2]:+.2f}%"
              f"  胜率 {win:.0%}  最差 {srt[0]:+.2f}%  最好 {srt[-1]:+.2f}%")

    def block(title, key):
        g = defaultdict(list)
        for x in rows:
            g[key(x)].append(x)
        print(f"\n{title}")
        print("| 分组 | 信号 | 封板率 | 每信号 | 封板后均值 | 胜率 |")
        print("|---|---|---|---|---|---|")
        for k in sorted(g, key=lambda k: (str(g[k][0][key(x_key)]),)):
            xs = g[k]
            fs = [x["ret"] for x in xs if x["filled"]]
            per_k = sum(fs) / len(xs)
            mean_k = sum(fs) / len(fs) if fs else 0.0
            w = sum(1 for v in fs if v > 0) / len(fs) if fs else 0.0
            print(f"| {k} | {len(xs)} | {len(fs)/len(xs):.0%} | {per_k:+.2f}% |"
                  f" {mean_k:+.2f}% | {w:.0%} |")

    x_key = lambda x: x["phase"]  # noqa: E731
    block("按相位", lambda x: x["phase"])
    x_key = lambda x: f'{x["phase"]}×{x["tier"]}'  # noqa: E731
    block("按相位×身份档", lambda x: f'{x["phase"]}×{x["tier"]}')
    x_key = lambda x: x["date"].year  # noqa: E731
    block("按年度", lambda x: x["date"].year)


if __name__ == "__main__":
    main()
