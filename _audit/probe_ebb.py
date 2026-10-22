"""退潮判据拆解：_rule_ebb 三个分支各自命中多少天，及各分支次日高度延续率。"""
from emotion_core.utils.db import query_df
q = '''
WITH d AS (
  SELECT ms.date, ms.phase, ms.max_limit_days, ms.limit_up_count, ms.limit_down_count,
         ms.zt_performance, ms.bomb_rate, ms.top_broke,
         lag(ms.top_broke) OVER (ORDER BY ms.date) y_top_broke,
         lag(ms.limit_down_count) OVER (ORDER BY ms.date) y_ld,
         lag(ms.zt_performance) OVER (ORDER BY ms.date) y_perf,
         lag(ms.phase) OVER (ORDER BY ms.date) y_phase
  FROM market_stat ms)
SELECT
  count(*) FILTER (WHERE phase='退潮' AND top_broke AND y_top_broke) 两日top_broke,
  count(*) FILTER (WHERE phase='退潮' AND zt_performance <= -2.0) 昨涨停表现低,
  count(*) FILTER (WHERE phase='退潮' AND y_ld IS NOT NULL AND limit_down_count > 15 AND limit_down_count > y_ld*2) 跌停激增,
  count(*) FILTER (WHERE phase='退潮') n_ebb
FROM d'''
print(query_df(q).to_string(index=False))
q2 = '''
SELECT date, phase, limit_down_count, y_ld, round(zt_performance,2) perf, top_broke, y_tb
FROM (SELECT ms.*, lag(limit_down_count) OVER (ORDER BY date) y_ld,
             lag(top_broke) OVER (ORDER BY date) y_tb FROM market_stat ms) z
WHERE phase='退潮' ORDER BY date DESC LIMIT 8'''
print(query_df(q2).to_string(index=False))
