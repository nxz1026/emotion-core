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
import time
from datetime import date as _date

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
    """按 URL 判定渠道。⚠️ 认不出就退 generic，而各家的报文格式并不通用——
    见 `_payload`：格式猜错时推送会**静默失败**（对方返回 200 + 错误体）。"""
    if "qyapi.weixin" in url:
        return "wecom"
    if "oapi.dingtalk" in url:
        return "dingtalk"
    if "open.feishu.cn" in url or "larksuite.com" in url:
        return "feishu"
    return "generic"


def _payload(channel: str, text: str) -> dict:
    """各渠道的报文格式。

    ⚠️ 2026-10-07 补飞书：原实现只认 wecom / dingtalk，其余一律 generic 发
    `{"text": ...}`。**飞书自定义机器人不接受这个格式**，它要
    `{"msg_type": "text", "content": {"text": ...}}` ⇒ 配了飞书地址会静默失败
    （HTTP 200 但 body 里是 `{"code": 19001, ...}`），看起来像推送成功。
    """
    if channel == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": text[:4000]}}
    if channel == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {"title": "龙空龙复盘", "text": text[:18000]},
        }
    if channel == "feishu":
        return {"msg_type": "text", "content": {"text": text[:4000]}}
    return {"text": text}


def _resp_ok(channel: str, resp) -> bool:
    """渠道级成功判定。

    飞书在报文非法时仍返回 **HTTP 200**，真正的成败在 body 的 `code` 字段
    （0 = 成功）。只看状态码会把「格式错」判成「已送达」——而这正是本函数
    存在的理由：`push` 的返回值决定调用方记不记成功日志。
    """
    if resp.status_code != 200:
        return False
    if channel == "feishu":
        try:
            return int(resp.json().get("code", -1)) == 0
        except Exception:  # noqa: BLE001 —— body 不是 JSON 就算没送达
            return False
    return True


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
    channel = _guess_channel(url)
    try:
        import requests

        resp = requests.post(url, json=_payload(channel, text), timeout=10)
        ok = _resp_ok(channel, resp)
        if not ok and channel == "feishu" and resp.status_code == 200:
            log.warning(
                "飞书返回 HTTP 200 但 body 非成功码（多半是报文格式错）：%s",
                resp.text[:120],
            )
        log.info("webhook 推送 %s（%s）：HTTP %s 成功=%s",
                 trade_date, channel, resp.status_code, ok)
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook 推送失败（不影响报告落盘）：%s", exc)
        return False


# ---- R58-4 BUY 即时推送：与日报摘要并行，专注 BUY 信号简报 ----
# daily 主链结尾会调 `push(report_md, trade_date)` 推速览；本函数是 BUY 信号
# 刚落库即触发（不经 daily 主链），给「BUY 出现就第一时间看到」的用户一个
# 独立时点。24h 内存 dict 节流：同 (date, code) 24h 内只推一次，防 daily 重
# 跑/回放/replay 重复轰炸。进程重启节流清空，重启后第一笔 BUY 仍会推。

_BUY_DEDUP: dict[tuple[str, str], float] = {}
_BUY_DEDUP_TTL = 86400  # 24h
_BUY_DEDUP_PRUNE_EVERY = 64  # 每 N 次调用清理一次过期 key


def _format_buy_signal(code: str, name: str, cont_days: int,
                       buy_window: str, cl: object) -> str:
    """BUY 信号简报（飞书 text 格式，标题+代码+板数+5 条件 checklist）。

    不带持仓明细（与 `push` 同纪律）；不带 BUY 之外的段（避免与日报摘要重复）。
    checklist 用 Checklist 对象（5 字段），每条 True / False / None 三态可见。
    """
    rows = []
    for label, field in (
        ("c1 唯一换手高标", "c1_uniqueness"),
        ("c2 换手板非一字", "c2_exchange"),
        ("c3 淘汰赛身份", "c3_elimination"),
        ("c4 最低板数门槛", "c4_min_days"),
        ("c5 强度与分歧补偿", "c5_strength_diverge"),
    ):
        v = getattr(cl, field, None)
        mark = "✓" if v is True else ("✗" if v is False else "—")
        rows.append(f"{mark} {label}")
    name_part = f"{name}({code})" if name else code
    body = (f"[emotion-core] BUY 信号\n"
            f"标的：{name_part}  |  {cont_days}板\n"
            f"窗口：{buy_window or 'NONE'}\n"
            + "\n".join(rows))
    return body


def push_buy_signal(code: str, name: str, cont_days: int,
                     buy_window: str, cl: object,
                     trade_date: _date | None = None) -> bool:
    """BUY 信号即时推送：24h 内存节流，best-effort，失败不抛。

    仅 BUY 信号触发（SECONDARY/RECOMMEND 不调）；同日同 code 重跑不重推。
    与 `push(report_md)` 的关系：日报摘要仍由 daily 主链推送，本函数是
    独立时点的 BUY 简报——可在 BUY 落库后立即看到，不必等 daily 跑完。

    Args:
        code/name/cont_days: 信号标的与身位。
        buy_window: market_stat.buy_window 原文（NONE/ENHANCED/STANDARD）。
        cl: domain.Checklist 对象（含 5 字段 + W1）。
        trade_date: 交易日，仅用于日志；key 仍以 (date_iso, code) 为单位。

    Returns:
        bool 推送是否成功（被节流时返回 False）。
    """
    url = _cfg("LKL_WEBHOOK_URL", "")
    if not url:
        log.debug("LKL_WEBHOOK_URL 未配置，BUY 即时推送跳过")
        return False
    d_iso = (trade_date or _date.today()).isoformat()
    key = (d_iso, code)
    now = time.time()
    last = _BUY_DEDUP.get(key, 0.0)
    if now - last < _BUY_DEDUP_TTL:
        log.info("BUY 信号 %s/%s 24h 内已推过（ts=%.0f，距今 %.0fs），跳过",
                 d_iso, code, last, now - last)
        return False
    text = _format_buy_signal(code, name, cont_days, buy_window, cl)
    channel = _guess_channel(url)
    try:
        import requests

        resp = requests.post(url, json=_payload(channel, text), timeout=10)
        ok = _resp_ok(channel, resp)
        if not ok and channel == "feishu" and resp.status_code == 200:
            log.warning(
                "飞书 BUY 即时推送返回 HTTP 200 但 body 非成功码：%s",
                resp.text[:120],
            )
        log.info("BUY 即时推送 %s/%s（%s）：HTTP %s 成功=%s",
                 d_iso, code, channel, resp.status_code, ok)
    except Exception as exc:  # noqa: BLE001
        log.warning("BUY 即时推送失败（不影响 BUY 落盘）：%s", exc)
        return False
    # 仅在 HTTP 请求**发出**后记节流——未配置/网络异常时不污染 dedup
    _BUY_DEDUP[key] = now
    if len(_BUY_DEDUP) >= _BUY_DEDUP_PRUNE_EVERY * 4:
        cutoff = now - _BUY_DEDUP_TTL
        for k in [k for k, t in _BUY_DEDUP.items() if t < cutoff]:
            _BUY_DEDUP.pop(k, None)
    return ok


def reset_buy_dedup() -> None:
    """测试用：清空 BUY 节流字典。生产代码不应调用。"""
    _BUY_DEDUP.clear()
