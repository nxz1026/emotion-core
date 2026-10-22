"""181 日：T+1 开盘买入，按「不涨停即次日出」锁定规则，算持有天数与费后收益。"""
import pandas as pd
from emotion_core.utils.db import query_df

m = pd.read_csv("_audit/fwd_181.csv", parse_dates=["date"])
m["date"] = pd.to_datetime(m["date"])
m["code"] = m["code"].astype(str).str.zfill(6)
lo, hi = m["date"].min(), m["date"].max()
hi2 = hi + pd.Timedelta(days=35)
px = query_df("SELECT code, date, open, high, low, close FROM daily_bar"
              " WHERE date >= %s AND date <= %s", (lo, hi2))
zt = query_df("SELECT code, date, is_limit_up FROM derived_bar"
              " WHERE date >= %s AND date <= %s", (lo, hi2))
px["date"] = pd.to_datetime(px["date"])
zt["date"] = pd.to_datetime(zt["date"])
px = px.merge(zt, on=["code", "date"], how="left")
px["open"] = px["open"].astype(float); px["close"] = px["close"].astype(float)
px["high"] = px["high"].astype(float); px["low"] = px["low"].astype(float)
px = px.sort_values(["code", "date"])

FEE = 0.0015  # 双边费率合计（佣金+印花税+滑点近似）
rows = []
for r in m.itertuples():
    sub = px[(px.code == r.code) & (px.date >= r.date)].head(12)
    if len(sub) < 3:
        continue
    entry_i = 1
    if sub.iloc[1]["open"] >= float(sub.iloc[0]["close"]) * 1.098:
        entry_i = 2                                   # T+1 一字买不到 → T+2 开盘
    if len(sub) <= entry_i + 1:
        continue
    entry = float(sub.iloc[entry_i]["open"])
    # 买入当日须涨停（打板成功）；未封板视为当日止损离场；封板则等第一次断板次日开盘出
    sell_i, reason = None, None
    for i in range(entry_i, len(sub)):
        row = sub.iloc[i]
        if not bool(row["is_limit_up"]):
            sell_i = i + 1 if i + 1 < len(sub) else i
            reason = "买入当日未封板·次日出" if i == entry_i else "断板后开盘出"
            break
    if sell_i is None:
        sell_i, reason = min(len(sub) - 1, 10), "持有10日上限"
    exit_px = float(sub.iloc[sell_i]["open"])
    seg = sub.iloc[entry_i + 1: sell_i + 1]
    future_limited = bool(seg["is_limit_up"].any()) if len(seg) else False
    rows.append({"date": r.date, "window": r.window, "cont_days": r.cont_days,
                 "entry_i": entry_i, "future_limited": future_limited,
                 "hold_days": sell_i - entry_i, "ret": (exit_px / entry - 1) * 100 - FEE * 100,
                 "reason": reason, "passed": r.passed})
d = pd.DataFrame(rows)


def rep(label, s):
    s = pd.Series(s).dropna()
    if not len(s):
        print(f"  {label}: n=0"); return
    print(f"  {label}: n={len(s)} mean={s.mean():+.2f}% med={s.median():+.2f}% win={100*(s>0).mean():.1f}%")


print("== 全部 181 日：T+1开盘买 + 断板次日出（费后 %.2f%%）==" % (FEE * 100))
rep("收益", d["ret"])
print("  持有时长分布:", d["hold_days"].value_counts().sort_index().to_dict())
print("  离场原因:", d["reason"].value_counts().to_dict())
print("\n== T+1 板后仍有连板（真龙）vs 断板（打板失败）==")
for flag, sub in d.groupby("future_limited"):
    rep(("真龙(板后再板 %d 只)" if flag else "断板(打板失败 %d 只)") % len(sub), sub["ret"])
    print("     中位持有天:", sub["hold_days"].median(), "| 次日一字买不到占比: %.1f%%" % (100 * (sub.entry_i == 2).mean()))
print("\n== 按窗口 ==")
for w, sub in d.groupby("window"):
    rep(w, sub["ret"]); print("     中位持有天:", sub["hold_days"].median())
print("\n== 按板数（4-8）==")
for h, sub in d[d.cont_days.between(4, 8)].groupby("cont_days"):
    rep(f"H={h}", sub["ret"])
d.to_csv("_audit/hold_dist.csv", index=False)
print("\n已存 _audit/hold_dist.csv")
