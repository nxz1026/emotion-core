"""对账测试：Python 参考实现 vs lkl derived_bar。

验证 compute_derived() 的输出与 lkl 的 derived_bar 表逐行一致。
这是阶段 2a 的核心验收标准。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from emotion_core.algorithms.indicators import compute_derived
from emotion_core.domain.bar import Bar

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.load(open(FIXTURES / name, encoding="utf-8"))


def test_streaks_vs_lkl():
    """连板对账：3 只高连板票，Python vs lkl derived_bar。"""
    derived = load_json("streaks_sample.json")
    bars_raw = load_json("daily_bar_sample.json")

    # 转换为 domain.Bar（daily_bar_sample 价格已是分）
    bars = []
    for r in bars_raw:
        bars.append(Bar(
            code=r["code"],
            date=date.fromisoformat(r["date"]),
            open_cents=int(r["open"]),
            high_cents=int(r["high"]),
            low_cents=int(r["low"]),
            close_cents=int(r["close"]),
            pre_close_cents=int(r["pre_close"]),
            volume=int(r["volume"]),
            turnover_rate=0.0,
        ))

    # 运行 Python 参考实现
    result = compute_derived(bars)

    # 按 (code, date) 索引
    result_map = {(r.code, r.date.isoformat()): r for r in result}

    # 对账
    mismatches = []
    for d in derived:
        key = (d["code"], d["date"])
        if key not in result_map:
            continue
        r = result_map[key]
        for field in ("is_limit_up", "is_limit_down", "is_one_word", "is_exchange",
                       "is_bomb", "touched_limit", "cont_days"):
            if getattr(r, field) != d[field]:
                mismatches.append({
                    "code": d["code"], "date": d["date"], "field": field,
                    "python": getattr(r, field), "lkl": d[field],
                })

    if mismatches:
        pytest.fail(f"对账失败 {len(mismatches)} 处:\n" + "\n".join(
            f"  {m['code']} {m['date']} {m['field']}: python={m['python']} lkl={m['lkl']}"
            for m in mismatches[:20]
        ))
