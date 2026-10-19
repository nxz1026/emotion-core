"""R67-1 dry-run 推送验收：摘要正文仍带最高板票名（打桩 requests.post + humanize）。"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date

sys.path.insert(0, "src")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)
os.environ["EMOTION_LKL_WEBHOOK_URL"] = "https://open.feishu.cn/open-apis/bot/v2/hook/FAKE"

from emotion_core.algorithms import review as rv  # noqa: E402
from emotion_core.services import notify  # noqa: E402
import requests  # noqa: E402


class FakeResp:
    status_code = 200
    text = "ok"

    def json(self):
        return {"code": 0}


SENT: list[dict] = []


def fake_post(url, json=None, timeout=None):
    SENT.append(json)
    return FakeResp()


requests.post = fake_post  # type: ignore[assignment]

D = date(2026, 10, 16)
md = rv.render_markdown(D)

# 1) 摘要段本身必须带票名（humanize 之前的原文）
summary = notify._summary(md)
print("=== _summary 速览段 ===")
print(summary)
assert "新华传媒 600825" in summary, "summary lost the top-board stock name"
assert "最高板" in summary

# 2) 走完整 push 通路（humanize 打桩成恒等，避免真调 LLM），正文必须仍带票名
notify._humanize_alert = lambda text, kind="daily": text  # type: ignore[assignment]
notify.reset_humanize_dedup() if hasattr(notify, "reset_humanize_dedup") else None
ok = notify.push(md, D)
print(f"\n=== push -> ok={ok} sent={len(SENT)} ===")
body = SENT[0]["content"]["text"]
print(body[:400])
assert ok is True
assert "新华传媒 600825" in body, "pushed body lost the top-board stock name"

print("\nR67-1 PUSH DRY-RUN OK")
