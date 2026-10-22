"""只读探针5b：181 个唯一最高板日的 1/3/5 日前向赔率（整表载入内存）。"""
from __future__ import annotations
from datetime import date
import pandas as pd
from emotion_core.algorithms import evaluate
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import trading_days

days = trading_days(CONFIG.DATA_START, date.today())
sigs = evaluate.replay(days[0], days[-1])
print("信号日 n =", len(sigs), "| 五条件全过:", int(sigs["passed"].sum()), flush=True)
lo, hi = sigs["date"].min(), sigs["date"].max()

px = query_df("SELECT code, date, open, high, low, close FROM daily_bar"
              " WHERE date >= %s AND date <= %s + 12", (lo, hi))
zt = query_df("SELECT code, date, is_limit_up FROM derived_bar"
              " WHERE date >= %s AND date <= %s + 12", (lo, hi))
px = px.merge(zt, on=["code", "date"], how="left").sort_values(["code", "date"])
print("行情行数:", len(px), flush=True)

wide = {}
for col in ("open", "high", "low", "close", "is_limit_up"):
    for k in (1, 3, 5):
        wide[f"{col}_{k}"] = px.groupby("code")[col].shift(-k)
    wide["full_5"] = px.groupby("code")["date"].shift(-5).notna()
W = pd.DataFrame(wide, index=px.index)
px = pd.concat([px, W], axis=1)
px.to_pickle("/tmp/px_wide.pkl")
print("宽表就绪", flush=True)

m = sigs.merge(px[["code", "date", "close", "open_1", "low_1", "high_1",
                   "close_3", "close_5", "low_3", "low_5", "high_3", "high_5",
                   "is_limit_up_1", "full_5"]],
               on=["code", "date"], how="left")
m["base"] = m["close"]
m["t1_gap"] = (m["open_1"] / m["base"] - 1) * 100
m["c3"] = (m["close_3"] / m["base"] - 1) * 100
m["c5"] = (m["close_5"] / m["base"] - 1) * 100
m["dd5"] = (m[["low_1", "low_3", "low_5"]].min(axis=1) / m["base"] - 1) * 100
m["up5"] = (m[["high_1", "high_3", "high_5"]].max(axis=1) / m["base"] - 1) * 100


def rep(name, s):
    s = pd.Series(s).dropna()
    if not len(s):
        print(f"    {name}: n=0"); return
    print(f"    {name}: n={len(s)} mean={s.mean():+.2f}% med={s.median():+.2f}% win={100*(s>0).mean():.1f}%")


for label, grp in (("passed=True（五条件全过）", m[m.passed]),
                   ("passed=False（唯一最高板·未过条件）", m[~m.passed])):
    print(f"\n[{label}] n={len(grp)}")
    for c in ["t1_gap", "c3", "c5", "up5", "dd5"]:
        rep(c, grp[c])
    z = grp["is_limit_up_1"].dropna()
    if len(z):
        print(f"    次日涨停率 {100*z.mean():.1f}%（n={len(z)}）")
print("\n[窗口分层]")
for w, sub in m.groupby("window"):
    print(f"  {w} n={len(sub)} passed={int(sub.passed.sum())}")
    for c in ["t1_gap", "c3", "c5"]:
        rep(c, sub[c])
# 次日可执行性：开盘竞价上限 9%、不能直接买一字（开盘即涨停）
m["t1_zt_open"] = (m["open_1"].astype(float) >= m["base"].astype(float) * 1.098)
m["t1_low_run"] = m["low_1"]
# 次日开盘买、T+3 收盘卖
m["enter_exit3"] = (m["close_3"].astype(float) / m["open_1"].astype(float) - 1) * 100
m["enter_exit5"] = (m["close_5"].astype(float) / m["open_1"].astype(float) - 1) * 100
print("\n[次日开盘可执行性]")
print("  开盘涨停(买不到) 比例: %.1f%%" % (100 * m["t1_zt_open"].mean()))
print("  次日低开(<0) 比例: %.1f%%" % (100 * (m["t1_gap"] < 0).mean()))
print("  次日高开 >5%%: %.1f%%" % (100 * (m["t1_gap"] > 5).mean()))
rep("次日开盘买→T+3收盘卖", m["enter_exit3"])
rep("次日开盘买→T+5收盘卖", m["enter_exit5"])
m.to_csv("_audit/fwd_181.csv", index=False)
print("\n已存 _audit/fwd_181.csv")
