"""B 轮探针 4（向量化重写）：妖股回调低吸 / 炸板次日 / 龙头断板次日（只读）。

四个族共用「T+1 开盘买 → T+2 开盘卖，费后 0.15%」这一执行口径：

  A 妖股回调：T 日跌<=-7% 且非跌停（横截面再切 T-1/T-2 是否涨停）
  C 炸板次日：T 日摸板未封（derived_bar.is_bomb）
  D 龙头断板：T-1 是唯一最高板，T 日未封板

先前的慢的原因：SQL 用 LATERAL 逐行找前一日（933s 未返回）→ 改用窗口函数；
Python 逐行模拟 210 万行 → 改用 pandas shift + merge 向量化。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015

SQL = """
WITH seq AS (
  SELECT d.code, d.date, d.is_limit_up, d.cont_days,
         lag(d.date)           OVER w AS prev_date,
         lag(d.is_limit_up)    OVER w AS zt_t1,
         lag(d.cont_days)      OVER w AS cont_t1,
         lag(d.is_limit_up, 2) OVER w AS zt_t2
  FROM derived_bar d
  WHERE d.date >= '2023-12-01'
  WINDOW w AS (PARTITION BY d.code ORDER BY d.date)
), sc AS (
  SELECT b.date, b.code,
         (b.close / b.pre_close - 1) * 100 AS drop_pct,
         d.is_limit_down, d.is_bomb, d.cont_days,
         ms.phase, ms.buy_window, ms.diverge,
         COALESCE(s.zt_t1::int, 0)  AS zt_t1,
         COALESCE(s.zt_t2::int, 0)  AS zt_t2,
         COALESCE(s.cont_t1, 0)     AS cont_t1,
         CASE WHEN lt.date IS NULL THEN 0 ELSE 1 END AS was_sole_top_t1
  FROM daily_bar b
  JOIN derived_bar d  ON d.code = b.code AND d.date = b.date
  JOIN seq s          ON s.code = b.code AND s.date = b.date
  JOIN market_stat ms ON ms.date = b.date
  LEFT JOIN ladder_day lt ON lt.date = s.prev_date AND lt.code = b.code AND lt.is_sole_top
  WHERE b.date >= '2024-01-01' AND b.close > 0 AND b.pre_close > 0 AND b.volume > 0
    AND left(b.code, 3) IN ('600','601','603','605','000','001','002')
)
SELECT * FROM sc
WHERE (drop_pct <= -7 AND NOT is_limit_down)
   OR is_bomb
   OR (was_sole_top_t1 = 1 AND cont_days = 0)
"""

u = query_df(SQL)
u["date"] = pd.to_datetime(u.date)
u["code"] = u.code.astype(str).str.zfill(6)
print(f"信号行 {len(u)}", flush=True)

codes = list(u.code.unique())
px = query_df(
    "SELECT code, date, open, close FROM daily_bar "
    "WHERE date >= '2024-01-01' AND code = ANY(%s) AND close > 0 AND volume > 0",
    (codes,))
px["date"] = pd.to_datetime(px.date)
px["code"] = px.code.astype(str).str.zfill(6)
for k in ("open", "close"):
    px[k] = px[k].astype(float)
px = px.sort_values(["code", "date"])
g = px.groupby("code", sort=False)
px["o1"] = g["open"].shift(-1)      # T+1 开盘（买入价）
px["o2"] = g["open"].shift(-2)      # T+2 开盘（卖出价）
px["c1"] = g["close"].shift(-1)
px = px[["code", "date", "o1", "o2", "c1"]]

d = u.merge(px, on=["code", "date"], how="left")
d = d.dropna(subset=["o1", "o2"])
d = d[(d.o1 > 0) & (d.o2 > 0)]
d["ret"] = (d.o2 / d.o1 - 1) * 100 - FEE * 100
d["t1_up"] = (d.c1 / d.o1 - 1) * 100
d.to_csv("_audit/b_reversal.csv", index=False)
print(f"可评估 {len(d)}", flush=True)


def blk(name: str, s: pd.Series) -> None:
    v = s.dropna()
    if len(v) < 5:
        print(f"  {name:26s} n={len(v):5d} （样本不足）")
        return
    print(f"  {name:26s} n={len(v):5d} 均值{v.mean():+6.2f}% 中位{v.median():+6.2f}% "
          f"胜率{100 * (v > 0).mean():4.0f}% 最差{v.min():+7.2f}% 最好{v.max():+7.2f}%")


drop = d[(d.drop_pct <= -7) & (~d.is_limit_down)]
print("\n=== A 妖股回调（T 日跌<=-7% 且非跌停）→ T+1 开盘买 → T+2 开盘卖 ===")
blk("全样本", drop.ret)
blk("T-1 涨停", drop[drop.zt_t1 == 1].ret)
blk("T-1/T-2 有涨停", drop[(drop.zt_t1 == 1) | (drop.zt_t2 == 1)].ret)
blk("T-1 涨停且>=2板", drop[(drop.zt_t1 == 1) & (drop.cont_t1 >= 2)].ret)
blk("T-1 唯一最高板", drop[drop.was_sole_top_t1 == 1].ret)

sub = drop[(drop.zt_t1 == 1) | (drop.zt_t2 == 1)]
print(" -- 相位（T-1/T-2 有涨停）--")
for p, x in sub.groupby("phase"):
    blk(p, x.ret)
print(" -- 年度（T-1/T-2 有涨停）--")
for y, x in sub.groupby(sub.date.dt.year):
    blk(str(y), x.ret)
print(" -- 跌幅档（T-1/T-2 有涨停）--")
for lo, hi, lab in ((-100, -9.5, "<=-9.5%"), (-9.5, -8.5, "-9.5~-8.5%"), (-8.5, -7.0, "-8.5~-7%")):
    blk(lab, sub[(sub.drop_pct > lo) & (sub.drop_pct <= hi)].ret)

print("\n=== C 炸板次日（T 日摸板未封）===")
blk("全样本", d[d.is_bomb].ret)
blk("炸板且 T-1 涨停", d[d.is_bomb & (d.zt_t1 == 1)].ret)
for p, x in d[d.is_bomb].groupby("phase"):
    blk(p, x.ret)

print("\n=== D 龙头断板次日（T-1 唯一最高板，T 未封）===")
nb = d[(d.was_sole_top_t1 == 1) & (d.cont_days == 0)]
blk("全样本", nb.ret)
blk("且 T 日跌<=-5%", nb[nb.drop_pct <= -5].ret)
for p, x in nb.groupby("phase"):
    blk(p, x.ret)
for y, x in nb.groupby(nb.date.dt.year):
    blk(str(y), x.ret)
