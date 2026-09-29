"""V9 通知层：webhook 推送，默认关闭（webhook 地址为空=静默跳过）。

摘要只送结论，**不含持仓明细**（V5 LLM 脱敏纪律：实盘仓位不进第三方渠道）。

语义逐字照搬 lkl/services/notify.py。与 lkl 的差异仅 IO 适配：
- `from lkl import config` → `from emotion_core.utils.config import CONFIG`；
- webhook 地址经 `_cfg("LKL_WEBHOOK_URL", "")` 读取（CONFIG 尚未收录该键，
  取 lkl config.py 默认空串 → 推送关闭，同 lkl 默认静默跳过），与 doctor.py 同先例。
"""
from __future__ import annotations

import logging
import os
import re

from emotion_core.utils.config import CONFIG

log = logging.getLogger("emotion_core.notify")
_POS_RE = re.compile(r"\*\*当前持仓\*\*：.*(?=\n|$)")


def _cfg(name: str, default: str) -> str:
    """读配置：环境变量优先，其次 CONFIG 项，都没有才用 default。

    ★2026-09-29 修：原实现只有 `getattr(CONFIG, name, default)`，而 CONFIG 是
    frozen dataclass 且**从不收录** `LKL_WEBHOOK_URL` → 恒返回空串 → webhook 推送
    恒被跳过。这条通路自 V9 写下起就没通过一次，2026-09-27~09-28 连续 4 次 daily
    失败因此一条都没推出去。

    为什么读环境变量而**不**给 CONFIG 加这个字段：CONFIG 的字段集合参与
    `config_hash()` 计算，加字段会让 pipeline_state / signal / eval_result 里
    已落库的策略指纹整体换代，跨版本不可比。webhook 地址属于部署期密配置
    （本机 systemd EnvironmentFile），本就应当走环境变量，与策略配置正交。
    """
    env_key = f"EMOTION_{name}"
    return os.environ.get(env_key) or os.environ.get(name) or getattr(CONFIG, name, default)


def _guess_channel(url: str) -> str:
    if "qyapi.weixin" in url: return "wecom"
    if "oapi.dingtalk" in url: return "dingtalk"
    return "generic"


def _payload(channel: str, text: str) -> dict:
    if channel == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": text[:4000]}}
    if channel == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": "龙空龙复盘",
                                                    "text": text[:18000]}}
    return {"text": text}


def _summary(md: str, limit: int = 600) -> str:
    """从报告 MD 提取速览段作推送摘要；持仓行剥离。"""
    m = re.search(r"## ⓪ 速览.*?(?=\n## |\Z)", md, re.S)
    text = m.group(0) if m else md[:limit]
    return _POS_RE.sub("", text).strip()


def push(md: str, trade_date) -> bool:
    """推送当日结论摘要；未配置/失败均不抛（不影响主报告落盘）。"""
    url = _cfg("LKL_WEBHOOK_URL", "")
    if not url:
        log.debug("LKL_WEBHOOK_URL 未配置，推送跳过")
        return False
    text = _summary(md)
    if not text:
        log.warning("摘要提取为空，推送跳过")
        return False
    try:
        import requests
        resp = requests.post(url, json=_payload(_guess_channel(url), text), timeout=10)
        ok = resp.status_code == 200
        log.info("webhook 推送 %s：HTTP %s", trade_date, resp.status_code)
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook 推送失败（不影响报告落盘）：%s", exc)
        return False
