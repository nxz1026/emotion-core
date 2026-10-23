"""N4 补充：被收紧掉的 72 天到底是哪一类，会不会漏掉真跌停潮。只读。"""
from __future__ import annotations

import statistics as st
import sys

import _audit.probe_n4_ebb as base

from emotion_core.algorithms import state as stt


def main() -> int:
    series = base.load()
    old = base.classify(series, True)
    new = base.classify(series, False)
    dropped = [i for i, (a, b) in enumerate(zip(old, new)) if a.phase == "退潮" and b.phase != "退潮"]

    only_two = []      # 旧规则里只有 two_day 成立（perf/ld 都不成立）
    ld_high = []       # 收紧掉但当天跌停 > 15
    for i in dropped:
        t = series[i]
        y = series[i - 1] if i >= 1 else None
        perf = stt._lt(t.zt_performance, stt.DEFAULT_RULES.ebb_zt_perf)
        ld = (y is not None and t.limit_down_count > stt.DEFAULT_RULES.ebb_ld_min
              and t.limit_down_count > y.limit_down_count * stt.DEFAULT_RULES.ebb_ld_mult)
        if not perf and not ld:
            only_two.append(i)
        if t.limit_down_count > stt.DEFAULT_RULES.ebb_ld_min:
            ld_high.append(i)

    print(f"收紧掉 {len(dropped)} 天：其中「只有 two_day 成立」{len(only_two)} 天，"
          f"当天跌停 >15 的 {len(ld_high)} 天")
    for label, idx in (("只有two_day", only_two), ("跌停>15", ld_high)):
        if not idx:
            continue
        lds = [series[i].limit_down_count for i in idx]
        zts = [series[i].zt_performance for i in idx if series[i].zt_performance is not None]
        print(f"  {label}: n={len(idx)} 当日跌停 中位 {st.median(lds)} 最高 {max(lds)}"
              f"；当日涨停表现 中位 {round(st.median(zts),2) if zts else None}")
    # 新口径的退潮是否覆盖了全部「跌停 >15」的日子
    all_ld = [i for i, t in enumerate(series) if t.limit_down_count > 15]
    miss = [i for i in all_ld if new[i].phase != "退潮"]
    print(f"\n全样本跌停>15 的日子 {len(all_ld)} 天；新口径仍判退潮 "
          f"{len(all_ld) - len(miss)} 天，漏判 {len(miss)} 天")
    if miss:
        for i in miss[:10]:
            print(f"   漏判 {series[i].date} 跌停{series[i].limit_down_count} "
                  f"→ {new[i].phase}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
