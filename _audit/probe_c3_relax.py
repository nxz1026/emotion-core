"""放宽 c3 后的信号量测算：只要「候选 ∈ 昨日最高组 ∧ 今日幸存」即可（含 g==1 独苗延续）。"""
from emotion_core.utils.db import query_df

SQL = """
WITH t AS (
  SELECT l.date, l.code, l.cont_days, l.is_sole_top, l.is_exchange,
         l.y_top_group_count::int AS g, l.y_top_survivor_count::int AS s
  FROM ladder_day l
  WHERE l.is_sole_top AND l.is_exchange AND l.cont_days >= 4
), w AS (
  SELECT date, buy_window FROM market_stat
)
SELECT t.date, t.code, t.g, t.s, w.buy_window,
       CASE WHEN y.code IS NULL THEN 0 ELSE 1 END AS in_y_top
FROM t LEFT JOIN LATERAL (
  SELECT l2.code, l2.is_top FROM ladder_day l2
  WHERE l2.date = (SELECT MAX(d.date) FROM ladder_day d WHERE d.date < t.date)
) y ON y.code = t.code AND y.is_top
LEFT JOIN w ON w.date = t.date
WHERE w.buy_window IS NOT NULL AND w.buy_window <> 'NONE'
ORDER BY t.date
"""
df = query_df(SQL)
print("可买日唯一换手高标候选 n=%d" % len(df))
print(df.groupby(["g", "in_y_top"]).size().rename("n").to_string())
print()
for label, mask in [
    ("现行 c3（g>=2 ∧ s==1 ∧ 在昨日组）", (df.g >= 2) & (df.s == 1) & (df.in_y_top == 1)),
    ("放宽A（在昨日组 ∧ 幸存>=1）", (df.s >= 1) & (df.in_y_top == 1)),
    ("放宽B（不要求昨日组，只要求 s>=1）", (df.s >= 1)),
    ("放宽C（完全不看 c3）", df.g.notna()),
]:
    print(f"  {label}: n={int(mask.sum()):3d}  ({100*mask.mean():.1f}%)")
print()
print("g==1 且 in_y_top==1 的（独苗延续）:", int(((df.g == 1) & (df.in_y_top == 1)).sum()))
print("g==1 且 in_y_top==0 的（梯子换代）:", int(((df.g == 1) & (df.in_y_top == 0)).sum()))
