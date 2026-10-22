"""验证 W1/W8/W11 报告层：⓪ 操作卡 + ⑥ 明日参考 是否真的说人话、带绝对价与时间。

只读：不改库、不改源码。signal 用内存注入（真实 BUY 行写库要跑日线任务，验证不必）。
"""
from __future__ import annotations

import sys
from datetime import date

from emotion_core.algorithms import entry, review
from emotion_core.domain.signal import Action, Checklist, Signal

D = date(2026, 10, 20)


def show(title: str, lines) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    print(lines if isinstance(lines, str) else "\n".join(lines))


d = review.collect(D)
print("数据就绪：date=%s phase=%s window=%s sole=%s" % (
    d["date"], (d["stat"] or {}).get("phase"), d["window"],
    getattr(d["sole"], "code", None)))

# ── 场景 1：不出手（真实数据） ──
show("场景 1｜⓪ 速览（今天不出手）", review._sec_overview(d))
show("场景 1｜⑥ 明日参考（今天不出手）", review._sec_advice(d))

# ── 场景 2：注入一条带操作卡的 BUY 信号 ──
cand = d["sole"]
if cand is None:
    print("\n（当日无候选，用合成候选演示⑥段的「有信号」形态）")
    pt = entry.position_tier("退潮", False, entry.TIER_INHERIT)
    d2 = dict(d)
    d2["signal"] = {
        "code": "002724", "reason": entry.plan_text("002724", 9.26, pt),
        "buy_window": "STANDARD",
        "checklist": [
            ["c1 唯一换手高标", True, "唯一换手高标: 海洋王(002724) 5板"],
            ["c2 换手板非一字", True, "换手板(非一字)"],
            ["c3 身份档（不否决）", None, "身份 身位延续：候选在昨日最高组内，昨日无竞争"],
            ["c4 最低板数门槛", True, "5板 >= 门槛4"],
            ["c5 强度与分歧补偿", True, "换手 8.31%≥阈值5.0%"],
            ["W1 同身位扎堆", True, "同身位 1 只"],
        ],
    }
    show("场景 2｜⓪ 速览（有买入信号）", review._sec_overview(d2))
    show("场景 2｜⑥ 明日参考（有买入信号）", review._sec_advice(d2))
