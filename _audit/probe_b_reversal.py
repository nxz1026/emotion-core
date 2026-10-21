"""B 轮探针 4：把「反转」装进情绪框架——妖股回调低吸 / 炸板次日（只读）。

前三个探针的结论：追强（打板/开盘/回踩）全负，全市场情绪择时也没方向。
剩下唯一在数据里为正的是「下跌后买」(+0.56%/笔, n=52205)。本探针把「下跌后买」
与情绪/妖股属性结合，看能否横截面上把它做厚：

  A 妖股回调：T 日跌幅<=-7% 且非跌停，且 T-1/T-2 出现过涨停
  B 龙头首阴：T 日跌幅<=-7% 且非跌停，且 T-1 是 2 板以上的涨停（断板当天大跌）
  C 炸板次日：T 日 touched_limit 但未封（is_bomb），T+1 开盘买
  D 龙虎断板：T-1 是唯一最高板（sole_top），T 日未封板（断板），T+1 开盘买

全部：T+1 开盘买，卖 T+2 开盘（顺延规则不适用：开盘买入次日开盘卖出=市价单，
跌停封死时按 T+2 开盘价算，不模拟卖不掉，只在注释里标注）。费后 0.15%。

输出：_audit/b_reversal.csv。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015

SQL = """
WITH seq AS (
  SELECT d.code, d.date, d.is_limit_up, d.cont_days,
         lag(d.date)         OVER (PARTITION BY d.code ORDER BY d.date) AS prev_date,
         lag(d.is_limit_up)  OVER (PARTITION BY d.code ORDER BY d.date) AS zt_t1,
         lag(d.cont_days)    OVER (PARTITION BY d.code ORDER BY d.date) AS cont_t1,
         lag(d.is_limit_up, 2) OVER (PARTITION BY d.code ORDER BY d.date) AS zt_t2
  FROM derived_bar d
  WHERE d.date >= '2023-12-01'
)
SELECT b.date, b.code,
       (b.close / b.pre_close - 1) * 100        AS drop_pct,
       d.is_limit_down, d.is_bomb, d.touched_limit, d.cont_days,
       ms.phase, ms.buy_window, ms.diverge,
       COALESCE(s.zt_t1::int, 0)  AS zt_t1,
       COALESCE(s.zt_t2::int, 0)  AS zt_t2,
       COALESCE(s.cont_t1, 0)     AS cont_t1,
       CASE WHEN lt.date IS NULL THEN 0 ELSE 1 END AS was_sole_top_t1
FROM daily_bar b
JOIN derived_bar d   ON d.code = b.code AND d.date = b.date
JOIN seq s           ON s.code = b.code AND s.date = b.date
JOIN market_stat ms  ON ms.date = b.date
LEFT JOIN ladder_day lt ON lt.date = s.prev_date AND lt.code = b.code AND lt.is_sole_top
WHERE b.date >= '2024-01-01' AND b.close > 0 AND b.pre_close > 0 AND b.volume > 0
  AND left(b.code, 3) IN ('600','601','603','605','000','001','002')
"""

u = query_df(SQL)
u["date"] = pd.to_datetime(u.date)
u["code"] = u.code.astype(str).str.zfill(6)
print(f"基础行 {len(u)}", flush=True)

codes = u.code.unique()
px = query_df(
    "SELECT code, date, open, close, volume FROM daily_bar "
    "WHERE date >= '2024-01-01' AND code = ANY(%s) AND close > 0 AND volume > 0",
    (list(codes),))
px["date"] = pd.to_datetime(px.date)
px["code"] = px.code.astype(str).str.zfill(6)
for k in ("open", "close"):
    px[k] = px[k].astype(float)
px = px.sort_values(["code", "date"])
by = {k: v.reset_index(drop=True) for k, v in px.groupby("code")}

rows: list[dict] = []
for r in u.itertuples():
    sub = by.get(r.code)
    if sub is None:
        continue
    h = sub.index[sub["date"] == r.date]
    if not len(h):
        continue
    i = int(h[0])
    if i + 2 >= len(sub):
        continue
    t1, t2 = sub.iloc[i + 1], sub.iloc[i + 2]
    entry = float(t1["open"])
    if entry <= 0 or float(t2["open"]) <= 0:
        continue
    rows.append({
        "date": r.date, "code": r.code, "phase": r.phase, "window": r.buy_window,
        "drop": float(r.drop_pct), "is_limit_down": bool(r.is_limit_down),
        "is_bomb": bool(r.is_bomb), "cont_days": int(r.cont_days),
        "zt_t1": int(r.zt_t1), "zt_t2": int(r.zt_t2), "cont_t1": int(r.cont_t1),
        "was_sole_top_t1": int(r.was_sole_top_t1),
        "ret": (float(t2["open"]) / entry - 1) * 100 - FEE * 100,
    })

d = pd.DataFrame(rows)
d.to_csv("_audit/b_reversal.csv", index=False)
print(f"可评估 {len(d)}", flush=True)


def blk(name: str, s: pd.Series) -> None:
    v = s.dropna()
    if not len(v):
        print(f"  {name:30s} n=0")
        return
    print(f"  {name:30s} n={len(v):5d} 均值{v.mean():+6.2f}% 中位{v.median():+6.2f}% "
          f"胜率{100 * (v > 0).mean():4.0f}% 最差{v.min():+7.2f}% 最好{v.max():+7.2f}%")


drop = d[(d.drop <= -7) & (~d.is_limit_down)]
print("\n=== A 妖股回调（跌<=-7% 非跌停）→ T+1 开盘买 → T+2 开盘卖 ===")
blk("全样本", drop.ret)
blk("T-1 涨停", drop[drop.zt_t1 == 1].ret)
blk("T-1/T-2 有涨停", drop[(drop.zt_t1 == 1) | (drop.zt_t2 == 1)].ret)
blk("T-1 涨停且>=2板", drop[(drop.zt_t1 == 1) & (drop.cont_t1 >= 2)].ret)
blk("T-1 是唯一最高板", drop[drop.was_sole_top_t1 == 1].ret)

print("\n -- 按相位（T-1/T-2 有涨停）--")
sub = drop[(drop.zt_t1 == 1) | (drop.zt_t2 == 1)]
for p, g in sub.groupby("phase"):
    blk(p, g.ret)
print(" -- 按年度（T-1/T-2 有涨停）--")
for y, g in sub.groupby(sub.date.dt.year):
    blk(str(y), g.ret)

print("\n=== C 炸板次日（T 日摸板未封）→ T+1 开盘买 ===")
blk("全样本", d[d.is_bomb].ret)
blk("炸板且 T-1 涨停", d[d.is_bomb & (d.zt_t1 == 1)].ret)
for p, g in d[d.is_bomb].groupby("phase"):
    blk(p, g.ret)

print("\n=== D 龙头断板次日（T-1 唯一最高板，T 未封板）===")
nb = d[(d.was_sole_top_t1 == 1) & (d.cont_days == 0)]
blk("全样本", nb.ret)
blk("且 T 日跌幅<=-5%", nb[nb.drop <= -5].ret)
for p, g in nb.groupby("phase"):
    blk(p, g.ret)
print(" -- 按年度 --")
for y, g in nb.groupby(nb.date.dt.year):
    blk(str(y), g.ret)
