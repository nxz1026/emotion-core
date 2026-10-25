"""Lab 自己的飞书摘要推送（不复用主线 `services/notify.py`）。

为什么不复用：主线 `notify.push` 的模板、24h 去重、LLM 人话改写都是为"日报/观察记录"
服务的，Lab 只需要一条纯文本摘要；混用会让任何一侧改模板时投鼠忌器（架构测试也
禁止 `lab` 反向依赖 `services`）。

webhook 取 `EMOTION_LAB_WEBHOOK_URL`，缺省回落到主线的 `EMOTION_LKL_WEBHOOK_URL`
（同一个群、两个来源，靠正文前缀区分）。没配 webhook 就静默返回 False——Lab 的
定时任务不该因为推送失败而判 red。

推送格式按群机器人类型自动切：飞书要 `{"msg_type":"text","content":{"text":…}}`
（2026-10-07 教训：发通用 `{"text":…}` 会 HTTP 200 + `code:19001` 静默失败）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

PUSH_ENABLED = os.environ.get("EMOTION_LAB_PUSH", "1").strip() in ("1", "true", "True")
MAX_CHARS = 1800


def _url() -> str:
    return (
        os.environ.get("EMOTION_LAB_WEBHOOK_URL") or os.environ.get("EMOTION_LKL_WEBHOOK_URL") or ""
    ).strip()


def _payload(text: str, url: str) -> dict[str, Any]:
    """按 webhook 域名选格式（与主线 `_guess_channel` 判据同源）。"""
    if "feishu" in url or "larksuite" in url:
        return {"msg_type": "text", "content": {"text": text}}
    if "weixin" in url:  # 企业微信
        return {"msgtype": "text", "text": {"content": text}}
    if "dingtalk" in url:
        return {"msgtype": "text", "text": {"content": text}}
    return {"text": text}


def digest(out: dict[str, Any]) -> str:
    """把一次搜索的结果压成一条消息（无提案也要发：沉默会掩盖流水线停了）。"""
    base = out["baseline"]
    props = out["proposals"]
    head = (
        f"【Lab 周报】变体 {out['vars']} 个 · 过门禁 {len(props)} 个\n"
        f"基线 hold-out：n={base['hold']['n']} 每信号 {base['hold']['mean']}% "
        f"LCB={base['hold']['lcb']}%"
    )
    if props:
        lines = [
            f"提案 {p['variant_id']}：hold {p['hold']['mean']}% （t={p['hold_t']}）" for p in props
        ]
        tail = "待批：http://127.0.0.1:8091/ ④ 区拍板"
    else:
        top = out["results"][0] if out["results"] else None
        lines = ["本轮没有变体过七道门禁——这是有效结论，不是故障。"]
        if top:
            lines.append(
                f"选参区间最好的是 {top['variant_id']}"
                f"（{top['sel']['mean']}%），"
                f"但 hold-out {top['hold']['mean']}%"
                f"（{top['reasons'][0] if top['reasons'] else ''}）"
            )
        tail = "无提案可拍板"
    text = "\n".join([head, *lines, tail])
    return text[:MAX_CHARS]


def push(text: str) -> bool:
    """发一条纯文本；无 webhook / 开关关闭 → False（不算失败）。"""
    if not PUSH_ENABLED:
        log.info("Lab 推送关闭（EMOTION_LAB_PUSH=0）")
        return False
    url = _url()
    if not url:
        log.info("Lab 无 webhook（EMOTION_LAB_WEBHOOK_URL / EMOTION_LKL_WEBHOOK_URL）")
        return False
    body = json.dumps(_payload(text, url), ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
        ok = resp.status == 200
        if ok and ("feishu" in url or "larksuite" in url):
            try:
                ok = json.loads(raw).get("code") == 0
            except json.JSONDecodeError:
                ok = False
        log.info("Lab 推送 %s（HTTP %s）", "成功" if ok else "失败", resp.status)
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("Lab 推送异常：%s", exc)
        return False
