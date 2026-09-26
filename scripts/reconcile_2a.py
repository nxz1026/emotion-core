"""Phase 2a 对账脚本：lkl vs emotion-core 状态机逐日对账。

用法：
    PYTHONPATH=src /home/ubuntu/DSH/longkonglong/.venv/bin/python scripts/reconcile_2a.py

输出：
    - 对账报告（pass/fail 计数 + 差异详情）
    - 差异写入 discrepancies.csv
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

# lkl imports
sys.path.insert(0, "/home/ubuntu/DSH/longkonglong")
from lkl.services.emotion import indicators as lkl_indicators
from lkl.utils import db as lkl_db

# emotion-core imports
sys.path.insert(0, "/home/ubuntu/DSH/emotion-core/src")
from emotion_core.algorithms.emotion import indicators as ec_indicators
from emotion_core.utils.db import query_df as ec_query_df


def reconcile_dates(dates: list[date]) -> dict:
    """对账指定日期列表。"""
    results = {
        "total": len(dates),
        "phase_match": 0,
        "phase_mismatch": [],
        "buy_window_match": 0,
        "buy_window_mismatch": [],
        "limit_up_count_match": 0,
        "limit_up_count_mismatch": [],
        "max_limit_days_match": 0,
        "max_limit_days_mismatch": [],
        "errors": [],
    }

    for d in dates:
        try:
            # lkl
            lkl_state = lkl_indicators(d)
            # emotion-core
            ec_state = ec_indicators(d)

            # 比较 phase
            if lkl_state.phase == ec_state.phase:
                results["phase_match"] += 1
            else:
                results["phase_mismatch"].append(
                    {"date": d, "lkl": lkl_state.phase, "ec": ec_state.phase}
                )

            # 比较 buy_window
            if lkl_state.buy_window == ec_state.buy_window:
                results["buy_window_match"] += 1
            else:
                results["buy_window_mismatch"].append(
                    {"date": d, "lkl": lkl_state.buy_window, "ec": ec_state.buy_window}
                )

            # 比较 limit_up_count
            if lkl_state.limit_up_count == ec_state.limit_up_count:
                results["limit_up_count_match"] += 1
            else:
                results["limit_up_count_mismatch"].append(
                    {"date": d, "lkl": lkl_state.limit_up_count, "ec": ec_state.limit_up_count}
                )

            # 比较 max_limit_days
            if lkl_state.max_limit_days == ec_state.max_limit_days:
                results["max_limit_days_match"] += 1
            else:
                results["max_limit_days_mismatch"].append(
                    {"date": d, "lkl": lkl_state.max_limit_days, "ec": ec_state.max_limit_days}
                )

        except Exception as exc:
            results["errors"].append({"date": d, "error": str(exc)})

    return results


def main() -> None:
    """主函数：读取 market_stat 日期列表，对账，输出报告。"""
    # 读取 market_stat 日期列表
    df = ec_query_df("SELECT date FROM market_stat ORDER BY date")
    dates = [d.date() if hasattr(d, "date") else d for d in df["date"].tolist()]

    print(f"对账日期数：{len(dates)}")
    print(f"日期范围：{dates[0]} ~ {dates[-1]}")
    print()

    # 对账
    results = reconcile_dates(dates)

    # 输出报告
    print("=" * 60)
    print("Phase 2a 对账报告")
    print("=" * 60)
    print(f"总日期数：{results['total']}")
    print()
    print(f"phase 匹配：{results['phase_match']}/{results['total']}")
    print(f"buy_window 匹配：{results['buy_window_match']}/{results['total']}")
    print(f"limit_up_count 匹配：{results['limit_up_count_match']}/{results['total']}")
    print(f"max_limit_days 匹配：{results['max_limit_days_match']}/{results['total']}")
    print()
    print(f"错误数：{len(results['errors'])}")

    # 输出差异详情
    if results["phase_mismatch"]:
        print()
        print("--- phase 差异 ---")
        for m in results["phase_mismatch"][:10]:
            print(f"  {m['date']}: lkl={m['lkl']}, ec={m['ec']}")
        if len(results["phase_mismatch"]) > 10:
            print(f"  ... 还有 {len(results['phase_mismatch']) - 10} 条")

    if results["buy_window_mismatch"]:
        print()
        print("--- buy_window 差异 ---")
        for m in results["buy_window_mismatch"][:10]:
            print(f"  {m['date']}: lkl={m['lkl']}, ec={m['ec']}")
        if len(results["buy_window_mismatch"]) > 10:
            print(f"  ... 还有 {len(results['buy_window_mismatch']) - 10} 条")

    if results["limit_up_count_mismatch"]:
        print()
        print("--- limit_up_count 差异 ---")
        for m in results["limit_up_count_mismatch"][:10]:
            print(f"  {m['date']}: lkl={m['lkl']}, ec={m['ec']}")
        if len(results["limit_up_count_mismatch"]) > 10:
            print(f"  ... 还有 {len(results['limit_up_count_mismatch']) - 10} 条")

    if results["max_limit_days_mismatch"]:
        print()
        print("--- max_limit_days 差异 ---")
        for m in results["max_limit_days_mismatch"][:10]:
            print(f"  {m['date']}: lkl={m['lkl']}, ec={m['ec']}")
        if len(results["max_limit_days_mismatch"]) > 10:
            print(f"  ... 还有 {len(results['max_limit_days_mismatch']) - 10} 条")

    if results["errors"]:
        print()
        print("--- 错误 ---")
        for e in results["errors"][:10]:
            print(f"  {e['date']}: {e['error']}")
        if len(results["errors"]) > 10:
            print(f"  ... 还有 {len(results['errors']) - 10} 条")

    # 写入 discrepancies.csv
    discrepancies = []
    for key in ["phase_mismatch", "buy_window_mismatch", "limit_up_count_mismatch", "max_limit_days_mismatch"]:
        for m in results[key]:
            discrepancies.append({
                "date": m["date"],
                "field": key.replace("_mismatch", ""),
                "lkl": m["lkl"],
                "ec": m["ec"],
            })

    if discrepancies:
        out = Path("discrepancies_2a.csv")
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "field", "lkl", "ec"])
            writer.writeheader()
            writer.writerows(discrepancies)
        print()
        print(f"差异已写入：{out}")

    # 总结
    print()
    print("=" * 60)
    total_checks = results["total"] * 4
    passed = (
        results["phase_match"]
        + results["buy_window_match"]
        + results["limit_up_count_match"]
        + results["max_limit_days_match"]
    )
    print(f"总检查：{total_checks}")
    print(f"通过：{passed}")
    print(f"失败：{total_checks - passed - len(results['errors'])}")
    print(f"错误：{len(results['errors'])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
