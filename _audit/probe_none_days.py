"""全部交易日（含窗口 NONE）的唯一换手高标：按相位看隔夜打板收益。"""
import pandas as pd
from emotion_core.utils.db import query_df

SQL = """
SELECT l.date, l.code, l.cont_days, l.y_top_group_count::int g,
       l.y_top_survivor_count::int s, ms.phase, ms.buy_window,
       CASE WHEN y.code IS NULL THEN 0 ELSE 1 END in_y_top
FROM ladder_day l
JOIN market_stat ms ON ms.date = l.date
LEFT JOIN LATERAL (
  SELECT l2.code FROM ladder_day l2
  WHERE l2.date = (SELECT MAX(d.date) FROM ladder_day d WHERE d.date < l.date)
    AND l2.is_top
) y ON y.code = l.code
WHERE l.is_sole_top AND l.is_exchange AND l.cont_days >= 4
ORDER BY l.date
"""
c = query_df(SQL)
c["date"] = pd.to_datetime(c.date); c["code"] = c.code.astype(str).str.zfill(6)
print("全史唯一换手高标(≥4板) n=%d" % len(c))
print(c.groupby(["phase", "buy_window"]).size().rename("n").to_string())

lo = c.date.min(); hi = c.date.max() + pd.Timedelta(days=40)
px = query_df("SELECT code, date, open, close FROM daily_bar WHERE date >= %s AND date <= %s", (lo, hi))
px["date"] = pd.to_datetime(px.date)
px["open"] = px.open.astype(float); px["close"] = px.close.astype(float)
px = px.sort_values(["code", "date"])

FEE = 0.0015
rows = []
for r in c.itertuples():
    sub = px[(px.code == r.code) & (px.date >= r.date)].head(3).reset_index(drop=True)
    if len(sub) < 2:
        continue
    entry = float(sub.iloc[0]["close"])
    gap = (float(sub.iloc[1]["open"]) / entry - 1) * 100 - FEE * 100
    rows.append({"date": r.date, "code": r.code, "cont_days": r.cont_days,
                 "phase": r.phase, "window": r.buy_window, "g": r.g, "s": r.s,
                 "in_y_top": r.in_y_top, "ret": gap})
d = pd.DataFrame(rows)


def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s): print(f"  {name}: n=0"); return
    print(f"  {name:26s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% "
          f"win={100*(s>0).mean():4.0f}% 最差={s.min():+6.2f}%")


print("\n=== 按相位（全部打板候选，不论窗口）===")
for p, sub in d.groupby("phase"):
    blk(p, sub.ret)
print("\n=== 退潮日再切：窗口 NONE vs 其它 ===")
ebb = d[d.phase == "退潮"]
for w, sub in ebb.groupby("window"):
    blk(f"退潮·{w}", sub.ret)
print("\n=== 窗口 NONE 日（全部相位）===")
blk("NONE 全部", d[d.window == "NONE"].ret)
blk("NONE 退潮", d[(d.window == "NONE") & (d.phase == "退潮")].ret)
blk("NONE 冰点", d[(d.window == "NONE") & (d.phase == "冰点")].ret)
blk("NONE 高潮分歧降级", d[(d.window == "NONE") & (d.phase == "高潮")].ret)
print("\n=== 可买日对照 ===")
blk("ENHANCED", d[d.window == "ENHANCED"].ret)
blk("STANDARD", d[d.window == "STANDARD"].ret)
d.to_csv("_audit/none_days.csv", index=False)
