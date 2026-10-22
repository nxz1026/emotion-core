from emotion_core.utils.db import query_df
q2 = '''SELECT phase, buy_window, count(*) n, round(avg(max_limit_days),2) avg_h,
       round(avg(limit_up_count),1) avg_zt, round(avg(bomb_rate),3) avg_bomb,
       round(avg(limit_down_count),1) avg_ld
FROM market_stat GROUP BY 1,2 ORDER BY n DESC'''
print(query_df(q2).to_string(index=False))
print()
q3 = '''WITH x AS (SELECT date, phase, max_limit_days,
      (SELECT max(cont_days) FROM ladder_day l WHERE l.date = (SELECT max(d2.date) FROM ladder_day d2 WHERE d2.date < ms.date)) yh
    FROM market_stat ms)
SELECT phase, count(*) n, round(avg(CASE WHEN max_limit_days > yh THEN 1 ELSE 0 END),3) 高度上升率
FROM x WHERE yh IS NOT NULL GROUP BY 1 ORDER BY n DESC'''
print(query_df(q3).to_string(index=False))
