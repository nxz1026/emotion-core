"""R67-3 验收：当日停牌/无行情的标的，即使 LLM 返回 BUY，也不得落 strategy_signal。

用假 LLM（恒返回 BUY 92/0.85）替换 _chat_with_retry，验证第二层硬闸门。
快照目录重定向到 _audit/r67_3_reports，避免覆盖生产报告。
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "src")
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

from emotion_core.utils.config import CONFIG  # noqa: E402
from emotion_core.utils.db import query_df  # noqa: E402

object.__setattr__(CONFIG, "STRATEGY_ENABLED", True)
outdir = Path("_audit/r67_3_reports")
outdir.mkdir(parents=True, exist_ok=True)
object.__setattr__(CONFIG, "STRATEGY_REPORTS_DIR", str(outdir.resolve()))

from emotion_core.services.strategy import runner  # noqa: E402

SQL = (
    "SELECT trade_date, code, strategy, action, score FROM strategy_signal "
    "WHERE trade_date=%s AND code=%s ORDER BY strategy"
)


class FakeReply:
    model = "fake-llm"
    text = (
        '{"action":"BUY","score":92,"confidence":0.85,'
        '"reason":"fake BUY, verify the hard gate","evidence":{}}'
    )


def fake_chat(client, messages, base_delay=None):
    return FakeReply()


runner._chat_with_retry = fake_chat
runner.time.sleep = lambda s: None

TARGET = date(2026, 10, 13)

before = query_df(SQL, (TARGET, "600825"))
print("=== BEFORE ===")
print(f"rows={len(before)}")
print(before.to_string(index=False))

n = runner.run_for_date(TARGET, codes=["600825"])
print(f"=== run_for_date returned: {n} ===")

after = query_df(SQL, (TARGET, "600825"))
print("=== AFTER ===")
print(f"rows={len(after)}")
print(after.to_string(index=False))

snap = outdir / f"strategy_{TARGET.isoformat()}.json"
print("=== SNAPSHOT ===")
print(snap.read_text(encoding="utf-8")[:1500])

ok_rows = len(after) == len(before)
ok_no_new_buy = not (
    len(after[after["action"] == "BUY"]) > len(before[before["action"] == "BUY"])
)
print(f"rows unchanged: {ok_rows}")
print(f"no new BUY: {ok_no_new_buy}")
assert ok_rows and ok_no_new_buy, "R67-3 FAILED"
print("R67-3 ACCEPT OK")
