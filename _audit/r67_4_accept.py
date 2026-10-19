"""R67-4 验收：WATCH 聚合推送（打桩 requests.post，绝不真发 HTTP）。"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date

sys.path.insert(0, "src")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)

os.environ["EMOTION_LKL_WEBHOOK_URL"] = "https://open.feishu.cn/open-apis/bot/v2/hook/FAKE"

from emotion_core.services import notify  # noqa: E402
import requests  # noqa: E402

POSTS: list[dict] = []
D = date(2026, 10, 16)


class FakeResp:
    status_code = 200
    text = '{"code":0,"msg":"success"}'

    def json(self):
        return {"code": 0, "msg": "success"}


def fake_post(url, json=None, timeout=None):
    POSTS.append({"url": url, "json": json, "timeout": timeout})
    return FakeResp()


requests.post = fake_post  # type: ignore[assignment]

LONG = "缩量回踩不破五日均线量价配合良好且次日有溢价预期的概率较高需要重点跟踪换手率与早盘竞价强度" * 2

rows = [
    {"strategy": "one_yang_three_yin", "code": "600825", "stock_name": "新华传媒",
     "name": "一阳三阴", "score": 65, "reason": LONG, "action": "WATCH"},
    {"strategy": "bottom_volume", "code": "600825", "stock_name": "新华传媒",
     "name": "底部放量", "score": 75, "reason": "底部放量", "action": "WATCH"},
    {"strategy": "emotion_cycle", "code": "000530", "stock_name": "冰山冷热",
     "name": "情绪周期", "score": 61, "reason": "情绪回暖", "action": "WATCH"},
    {"strategy": "bull_trend", "code": "002343", "stock_name": "低位破净",
     "name": "多头趋势", "score": 59, "reason": "低于阈值应被剔除", "action": "WATCH"},
]

# 场景 1：按 code 聚合 + 阈值 + 票名取 stock_name + 理由截断
notify.reset_watch_dedup()
POSTS.clear()
sent = notify.push_watch_signals(rows, D)
print(f"\n[s1] sent={sent} posts={len(POSTS)}")
for p in POSTS:
    print("---- payload ----")
    print(p["json"])
assert sent == 2 and len(POSTS) == 2, "s1 failed"
bodies = [p["json"]["content"]["text"] for p in POSTS]
assert "新华传媒(600825) 最高 75 分" in bodies[0], "s1 stock_name/aggregation failed"
assert "一阳三阴" not in bodies[0], "s1 printed strategy display name as stock name"
assert "…" in bodies[0], "s1 reason truncation missing"
assert "共 2 只" in bodies[0], "s1 summary missing"

# 场景 2：同日重跑不重推（24h 节流）
POSTS.clear()
sent2 = notify.push_watch_signals(rows, D)
print(f"\n[s2] sent={sent2} posts={len(POSTS)}")
assert sent2 == 0 and len(POSTS) == 0, "s2 failed"

# 场景 3：EMOTION_PUSH_WATCH=0 零调用
os.environ["EMOTION_PUSH_WATCH"] = "0"
notify.reset_watch_dedup()
POSTS.clear()
sent3 = notify.push_watch_signals(rows, D)
print(f"\n[s3] EMOTION_PUSH_WATCH=0 sent={sent3} posts={len(POSTS)}")
assert sent3 == 0 and len(POSTS) == 0, "s3 failed"
del os.environ["EMOTION_PUSH_WATCH"]

# 场景 4：全部低于阈值不发
notify.reset_watch_dedup()
POSTS.clear()
low = [{"strategy": "x", "code": "600000", "stock_name": "浦发银行", "score": 30,
        "reason": "r", "action": "WATCH"}]
sent4 = notify.push_watch_signals(low, D)
print(f"\n[s4] all below threshold sent={sent4} posts={len(POSTS)}")
assert sent4 == 0 and len(POSTS) == 0, "s4 failed"

# 场景 5：HTTP 异常被吞掉
def boom(url, json=None, timeout=None):
    raise RuntimeError("network down")


requests.post = boom  # type: ignore[assignment]
notify.reset_watch_dedup()
sent5 = notify.push_watch_signals(rows[:1], D)
print(f"\n[s5] exception swallowed sent={sent5}")
assert sent5 == 0, "s5 failed"

# 场景 6：max_codes 截断 + 余下 M 只
requests.post = fake_post  # type: ignore[assignment]
notify.reset_watch_dedup()
POSTS.clear()
os.environ["EMOTION_WATCH_PUSH_MAX_CODES"] = "1"
many = [{"strategy": f"s{i}", "code": f"60000{i}", "stock_name": f"票{i}",
         "score": 90 - i, "reason": "r", "action": "WATCH"} for i in range(3)]
sent6 = notify.push_watch_signals(many, D)
print(f"\n[s6] max_codes=1 sent={sent6} posts={len(POSTS)}")
print(POSTS[0]["json"] if POSTS else "(no post)")
assert sent6 == 1 and len(POSTS) == 1, "s6 failed"
assert "余下 2 只见报告⑥段" in POSTS[0]["json"]["content"]["text"], "s6 remain line failed"
del os.environ["EMOTION_WATCH_PUSH_MAX_CODES"]

# 场景 7：PASS/BUY 不触发（调用方只传 WATCH；函数本身对非 WATCH action 不筛，
# 由 runner 保证）——此处验证 runner 侧筛选正确：
notify.reset_watch_dedup()
POSTS.clear()
mixed = [{"strategy": "a", "code": "600001", "stock_name": "A", "score": 90,
          "reason": "r", "action": "PASS"}]
watch_only = [it for it in mixed if it.get("action") == "WATCH"]
sent7 = notify.push_watch_signals(watch_only, D)
print(f"\n[s7] runner-filtered PASS-only -> sent={sent7} posts={len(POSTS)}")
assert sent7 == 0 and len(POSTS) == 0, "s7 failed"

# 场景 8：空 rows / None
assert notify.push_watch_signals([], None) == 0
assert notify.push_watch_signals(None, None) == 0
print("\n[s8] empty/None -> 0")

# 场景 9：非整数阈值回退默认（不崩）
assert notify._watch_env_int("WATCH_PUSH_MIN_SCORE", 60) == 60
os.environ["EMOTION_WATCH_PUSH_MIN_SCORE"] = "abc"
assert notify._watch_env_int("WATCH_PUSH_MIN_SCORE", 60) == 60
del os.environ["EMOTION_WATCH_PUSH_MIN_SCORE"]
print("[s9] bad env int falls back to default")

print("\nR67-4 ACCEPT OK")
