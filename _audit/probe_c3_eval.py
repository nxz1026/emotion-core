"""放宽 c3 后新增的 86 天「独苗延续」收益，与现行通过的 21 天对比。"""
import pandas as pd
from emotion_core.utils.db import query_df

SQL = """
SELECT t.date, t.code, t.y_top_group_count::int AS g,
       t.y_top_survivor_count::int AS s,
       CASE WHEN y.code IS NULL THEN 0 ELSE 1 END AS in_y_top
FROM ladder_day t
LEFT JOIN LATERAL (
  SELECT l2.code FROM ladder_day l2
  WHERE l2.date = (SELECT MAX(d.date) FROM ladder_day d WHERE d.date < t.date)
    AND l2.is_top
) y ON y.code = t.code
WHERE t.is_sole_top AND t.is_exchange AND t.cont_days >= 4
"""
tag = query_df(SQL)
tag["date"] = pd.to_datetime(tag["date"])
tag["code"] = tag["code"].astype(str).str.zfill(6)

m = pd.read_csv("_audit/fwd_181.csv", parse_dates=["date"])
m["date"] = pd.to_datetime(m["date"])
m["code"] = m["code"].astype(str).str.zfill(6)
j = m.merge(tag, on=["date", "code"], how="left")
print("合并行数:", len(j), "| 未匹配:", int(j.in_y_top.isna().sum()))
j = j[j.in_y_top.notna()]
j["in_y_top"] = j.in_y_top.astype(int)


def rep(label, sub):
    for col, name in [("enter_exit3", "T+1开盘→T+3"), ("t1_gap", "T+1高开"),
                      ("c5", "T+5(相对T日收盘)"), ("dd5", "T+5最大回撤")]:
        s = pd.Series(sub[col]).dropna()
        if not len(s):
            continue
        print(f"    {name}: n={len(s)} mean={s.mean():+.2f}% med={s.median():+.2f}% win={100*(s>0).mean():.1f}%")
    zt = 1 - (1 - sub.passed.astype(bool)).mean() if len(sub) else 0
    print(f"    差值样本 passed 占比: {100*sub.passed.mean():.1f}%")


print("\n=== 按「是否在昨日最高组」分组（决定 c3 身份校验去留）===")
for k, sub in j.groupby("in_y_top"):
    print(f"\n in_y_top={k} n={len(sub)}" + ("  ← 新插队高标（身份不成立）" if k == 0 else "  ← 昨日高标延续（身份成立）"))
    rep(str(k), sub)

print("\n=== 放宽A（在昨日组）分层：g=1 独苗 vs g>=2 竞争 ===")
a = j[j.in_y_top == 1]
for name, sub in [("g=1 独苗延续", a[a.g == 1]), ("g>=2 有竞争", a[a.g >= 2])]:
    print(f"\n {name} n={len(sub)}")
    rep(name, sub)

print("\n=== 现行 c3 通过组 vs 放宽A 新增组 ===")
cur = j[(j.g >= 2) & (j.s == 1) & (j.in_y_top == 1)]
new = j[(j.in_y_top == 1) & ~((j.g >= 2) & (j.s == 1))]
for name, sub in [("现行 c3 通过", cur), ("放宽A 新增", new)]:
    print(f"\n {name} n={len(sub)}")
    rep(name, sub)
j.to_csv("_audit/c3_eval.csv", index=False)
