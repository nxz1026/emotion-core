"""只读探针：口径B 的「可成交性」到底该怎么定义？

同一批 BUY 信号、同一个买入价（T+1 涨停价），只改「什么情况下算买到了」：
  S1 触及涨停即可成交（含买后炸板）—— 最贴近真实打板
  S2 触及且非一字（一字板无卖盘，买不到）
  S3 触及且当日收盘封住涨停（probe_exec 的 V-C 口径，事后知情）
  S4 收盘封住涨停（同 S3，入场价改用收盘价）
输出各口径的「每信号期望」（未成交=0）与封板后统计。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from emotion_core.utils.db import query_df
from emotion_core.utils.dates import next_trading_day
from emotion_core.utils.price import limit_down_price_cents, limit_up_price_cents

FEE = 0.0015


def cents(x):
    return None if x is None else int(round(float(x) * 100))


def main() -> None:
    sig = query_df(
        "SELECT confirm_date, code FROM signal"
        " WHERE source='replay' AND action='BUY' ORDER BY confirm_date")
    cohort = query_df(
        "SELECT date, code FROM ladder_day"
        " WHERE is_sole_top AND is_exchange AND cont_days >= 4 ORDER BY date")
    for name, df in (("BUY 信号", sig), ("唯一高标宇宙", cohort)):
        dcol = "confirm_date" if "confirm_date" in df.columns else "date"
        codes = sorted(set(df["code"]))
        px = query_df(
            "SELECT code, date, open, high, low, close FROM daily_bar"
            " WHERE code = ANY(%s) AND date BETWEEN %s AND %s",
            (codes, date(2023, 12, 1), date(2026, 12, 31)))
        bars = defaultdict(dict)
        for r in px.itertuples():
            bars[r.code][r.date] = r
        rows = []
        for r in df.itertuples():
            c, d = r.code, getattr(r, dcol)
            b0 = bars[c].get(d)
            t1 = next_trading_day(d)
            t2 = next_trading_day(t1) if t1 else None
            t3 = next_trading_day(t2) if t2 else None
            if b0 is None or t1 is None or t2 is None:
                continue
            b1, b2, b3 = bars[c].get(t1), bars[c].get(t2), bars[c].get(t3)
            if b1 is None:
                continue
            lim = limit_up_price_cents(cents(b0.close), c)
            hi, lo, op, cl = (cents(b1.high), cents(b1.low),
                              cents(b1.open), cents(b1.close))
            touched = hi >= lim
            oneword = op >= lim and lo >= lim
            sealed = cl >= lim
            exit_px, defer = (None, None)
            if b2 is not None:
                ld = limit_down_price_cents(cents(b1.close), c)
                locked = cents(b2.open) <= ld and cents(b2.close) <= ld
                if locked and b3 is not None:
                    exit_px, defer = float(b3.open), True
                elif not locked:
                    exit_px, defer = float(b2.open), False
            ret = (None if exit_px is None else
                   (exit_px / (lim / 100) - 1) * 100 - FEE * 100)
            ret_sealed = ret
            rows.append({"date": d, "touched": touched, "oneword": oneword,
                         "sealed": sealed, "ret": ret, "defer": defer})
        n = len(rows)
        print(f"\n=== {name}：可评估 {n} 条 ===")
        variants = {
            "S1 触及即成交": lambda x: x["touched"],
            "S2 触及且非一字": lambda x: x["touched"] and not x["oneword"],
            "S3 收盘封住(probe V-C)": lambda x: x["sealed"],
            "S3′ 收盘封住且非一字": lambda x: x["sealed"] and not x["oneword"],
        }
        print("| 口径 | 成交数 | 成交率 | 每信号期望 | 成交后均值 | 中位 | 胜率 | 最差 |")
        print("|---|---|---|---|---|---|---|---|")
        for label, fn in variants.items():
            hit = [x for x in rows if fn(x)]
            rets = [x["ret"] for x in hit if x["ret"] is not None]
            per = sum(rets) / n if n else 0.0
            if not rets:
                print(f"| {label} | 0 | 0% | — | — | — | — | — |")
                continue
            srt = sorted(rets)
            mean = sum(rets) / len(rets)
            win = sum(1 for v in rets if v > 0) / len(rets)
            print(f"| {label} | {len(hit)} | {len(hit)/n:.0%} | {per:+.2f}% |"
                  f" {mean:+.2f}% | {srt[len(srt)//2]:+.2f}% | {win:.0%} | {srt[0]:+.2f}% |")
        # 炸板桶：触及但未收盘封住
        broke = [x for x in rows if x["touched"] and not x["sealed"]
                 and x["ret"] is not None]
        if broke:
            rets = [x["ret"] for x in broke]
            print(f"  炸板桶（触及后未封住）n={len(broke)} 均值 {sum(rets)/len(rets):+.2f}%"
                  f" 胜率 {sum(1 for v in rets if v > 0)/len(rets):.0%}"
                  f" 最差 {min(rets):+.2f}%")
        st = [x for x in rows if x["touched"] and x["sealed"] and not x["oneword"]
              and x["ret"] is not None]
        if st:
            rets = [x["ret"] for x in st]
            print(f"  封住桶（非一字）n={len(st)} 均值 {sum(rets)/len(rets):+.2f}%"
                  f" 胜率 {sum(1 for v in rets if v > 0)/len(rets):.0%}")
        tw = [x for x in rows if x["oneword"]]
        print(f"  一字桶 n={len(tw)}（不可成交）")


if __name__ == "__main__":
    main()
