"""B 轮探针 3：市场情绪能不能变成可执行的方向性择时（只读）。

个股层面「追强」已证负期望（打板/回踩/开盘三种入场全负）。剩下一个可能：
情绪指标预测的是**全市场（等权）**的方向，而不是某只票。

如果成立，盘后可执行的表达是「T+1 开盘买宽基/等权 ETF，T+2 开盘卖」——
决策在 T 日盘后、成交在 T+1 开盘，成交假设必然触发（市价单）。

本探针用每日等权收益当市场代理（没有指数表，等权全市场 = 最接近「情绪」的
宽基口径，且与涨停家数同源）：

  eq_t       = mean(close_t / pre_close_t - 1)          当日全市场等权
  eq_oc_t+1  = mean(close_t+1 / open_t+1 - 1)          T+1 日内的等权（开盘后持有到收盘）
  eq_gap_t+1 = mean(open_t+1 / close_t - 1)            T+1 跳空（T 收盘→T+1 开盘）
  eq_cc_t+1  = mean(close_t+1 / close_t - 1)           T 收盘→T+1 收盘（可执行：T+1 开盘买近似）

按 T 日相位 / 情绪档 / 涨停家数分位分层，看 T+1 的三种口径。

输出：_audit/b_market.csv（逐日）+ 控制台分层。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

# 先取逐股行，再在 pandas 里算次日字段（不在 SQL 里 LAG）
PX_SQL = """
SELECT code, date, open, close, pre_close
FROM daily_bar
WHERE date >= '2024-01-01' AND close > 0 AND pre_close > 0 AND volume > 0
  AND left(code, 3) IN ('600','601','603','605','000','001','002')
"""

px = query_df(PX_SQL)
px["date"] = pd.to_datetime(px.date)
px["code"] = px.code.astype(str).str.zfill(6)
for k in ("open", "close", "pre_close"):
    px[k] = px[k].astype(float)
print(f"行情 {len(px)} 行 / {px.code.nunique()} 只 / {px.date.nunique()} 天", flush=True)

px = px.sort_values(["code", "date"])
px["n_open"] = px.groupby("code")["open"].shift(-1)
px["n_close"] = px.groupby("code")["close"].shift(-1)
px["n_date"] = px.groupby("code")["date"].shift(-1)
px["cc"] = px.close / px.pre_close - 1                      # 当日 收/前收
px["gap"] = px.n_open / px.close - 1                        # 次日 开/今收
px["oc"] = px.n_close / px.n_open - 1                       # 次日 收/开
px["c2c"] = px.n_close / px.close - 1                       # 今收→次日收

day = px.groupby("date").agg(
    n=("cc", "size"), eq_cc=("cc", "mean"), eq_gap=("gap", "mean"),
    eq_oc=("oc", "mean"), eq_c2c=("c2c", "mean")).reset_index()

ms = query_df(
    "SELECT date, phase, buy_window, limit_up_count, bomb_rate, diverge, "
    "force_liquidate FROM market_stat ORDER BY date")
ms["date"] = pd.to_datetime(ms.date)
day = day.merge(ms, on="date", how="left")
day = day.dropna(subset=["phase"])
day.to_csv("_audit/b_market.csv", index=False)


def blk(name: str, s: pd.Series) -> None:
    v = s.dropna() * 100
    if not len(v):
        print(f"  {name:26s} n=0")
        return
    print(f"  {name:26s} n={len(v):4d} 均值{v.mean():+6.2f}% 中位{v.median():+6.2f}% "
          f"胜率{100 * (v > 0).mean():4.0f}%")


print(f"\n全样本 {len(day)} 天  等权日收益均值 {100 * day.eq_cc.mean():+.2f}%")

print("\n=== 按 T 日相位：T+1 三种口径（%）===")
for phase, g in day.groupby("phase"):
    print(f"-- {phase} (n={len(g)}) --")
    blk("T+1 跳空 gap", g.eq_gap)
    blk("T+1 开盘→收盘 oc", g.eq_oc)
    blk("T 收盘→T+1 收盘", g.eq_c2c)
    blk("T+1 开盘买→T+2 开盘卖", g.eq_c2c.shift(0))

print("\n=== 按情绪档 ===")
for w, g in day.groupby("buy_window"):
    print(f"-- {w} (n={len(g)}) --")
    blk("T+1 开盘→收盘", g.eq_oc)
    blk("T 收盘→T+1 收盘", g.eq_c2c)

print("\n=== 按 T 日涨停家数五分位（T+1 开盘→收盘）===")
day["zt_q"] = pd.qcut(day.limit_up_count, 5, labels=["Q1 最少", "Q2", "Q3", "Q4", "Q5 最多"])
for q, g in day.groupby("zt_q", observed=True):
    blk(str(q), g.eq_oc)

print("\n=== 按 T 日等权跌幅（市场恐慌）分档：T+1 开盘→收盘 ===")
day["cc_q"] = pd.qcut(day.eq_cc, 5, labels=["Q1 最跌", "Q2", "Q3", "Q4", "Q5 最涨"])
for q, g in day.groupby("cc_q", observed=True):
    blk(str(q), g.eq_oc)

print("\n=== 高潮高位分歧日（diverge）===")
for d_, g in day.groupby(day.diverge.fillna(False).astype(bool)):
    blk("diverge" if d_ else "无分歧", g.eq_oc)

print("\n=== 年度等权日历 ===")
for y, g in day.groupby(day.date.dt.year):
    blk(str(y), g.eq_cc)
