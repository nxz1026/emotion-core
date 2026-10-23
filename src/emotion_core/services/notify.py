"""V9 通知层：webhook 推送，默认关闭（webhook 地址为空=静默跳过）。

摘要只送结论，**不含持仓明细**（V5 LLM 脱敏纪律：实盘仓位不进第三方渠道）。

语义逐字照搬 lkl/services/notify.py。与 lkl 的差异仅 IO 适配：
- `from lkl import config` → `from emotion_core.utils.config import CONFIG`；
- webhook 地址经 `_cfg("LKL_WEBHOOK_URL", "")` 读取（CONFIG 尚未收录该键，
  取 lkl config.py 默认空串 → 推送关闭，同 lkl 默认静默跳过），与 doctor.py 同先例。
"""
from __future__ import annotations

import hashlib
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


def push_text(text: str) -> bool:
    """推一条**纯文本**，不走 LLM 改写。

    给机器事实用（盘中告警、结算摘要）：这些内容不需要润色，也不能被润色 ——
    改写有可能把「8 分钟没数据」说成别的意思，而告警的价值全在字面准确。

    未配置 webhook / 发送失败都返回 False 且不抛（调用方记日志即可）。
    """
    url = _cfg("LKL_WEBHOOK_URL", "")
    if not url or not text:
        return False
    channel = _guess_channel(url)
    try:
        import requests

        resp = requests.post(url, json=_payload(channel, text), timeout=10)
        ok = _resp_ok(channel, resp)
        if not ok:
            log.warning("推送失败（%s）：%s", channel, resp.text[:120])
        return ok
    except Exception as exc:  # noqa: BLE001 - 推送失败不影响主流程
        log.warning("推送异常：%s", exc)
        return False


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
    # R58-5：先走一次 LLM 把摘要翻译成人话；LLM 不可用时回退原文
    text = _humanize_alert(text, kind="daily")
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


# ---- R58-5 飞书报警走一次 LLM（人话） ----
# daily 主链结尾的 `push(report_md)` 现在会先经本模块把摘要"说人话"再推。
# 缓存键 (kind, md5(text)) 24h 内复用同一篇不重复调 LLM；LLM 不可用 / 解析失败 /
# 任何阻断都回退原文（不影响日报落盘）。

_HUMANIZE_DEDUP: dict[tuple[str, str], float] = {}
_HUMANIZE_TTL = 86400  # 24h

_HUMANIZE_PROMPT = """你是给一线交易员发警报的助手。把下面这段龙空龙复盘系统的当日日报摘要，**用 2-3 句自然中文**讲清楚两件事：

1. 今天的 A 股盘面现在在什么阶段、买入窗口是否打开；
2. 今天产生了哪些信号（BUY 推荐几只、SECONDARY/RECOMMEND 观察几只）；没有信号时只说"今日无 BUY"即可。

⚠️ 铁律：**只做格式美化，不做语义扩展。**
- 禁止添加原文中不存在的信息、因果关系或解释。
- 禁止把客观状态（如"对账差异 2 行"）改写为因果叙述（如"系统暂缓信号生成"）。
- 只允许改写句式、调整语序、把 markdown 译成口语化中文。

风格要求：
- 用「今天」「当前」开头的句子，第二人称省略；
- 数字（涨停家数、最高档数、信号数）保留原文；
- **数字后面的股票代码与名称必须原样保留，不得概括为「某股」**——速览里的最高板括注（如「4（新华传媒 600825 一字）」）是原文，改写只能调语序，不能丢掉代码与票名；
- 如果有 BUY 推荐，第一句必须出现「BUY」字样；
- 如果没 BUY，只说"今日无 BUY 推荐"——除非原文明确给出了原因，否则不要添加任何因果解释；
- 不要写 markdown 标题或列表，只输出 2-3 句纯文本；
- 不要复述任何 RAG/算法/字段名。

摘要原文如下：
"""


_HUMANIZE_BUY_PROMPT = """你是给一线交易员发盘后观察记录的助手。把下面这条简报，**用 2-3 句自然中文**说清楚：

1. 观察哪只票（代码+名称+几板）；
2. 它的硬条件是否全部通过（身位、换手、板数）。

⚠️ 铁律：**只做格式美化，不做语义扩展。**
- 禁止添加原文中不存在的信息、因果、目标价、买卖价位、仓位建议或收益承诺。
- **股票代码、票名、板数必须原样保留**，不得概括为「某股」。
- 不得把观察记录写成「可以买入」「建议买入」；原文没有买点就是没有买点，不许补。
- 只允许改写句式、调整语序、把 checklist 符号译成口语。

风格：不用 markdown 标题/列表，只输出 2-3 句纯文本；不复述 c1/c2/c3 这类字段名。

简报原文如下：
"""


def _humanize_alert(text: str, kind: str = "daily") -> str:
    """把飞书推送的 markdown 摘要送 LLM 改写为人话；失败回退原文。

    Args:
        text: 已提取的速览段（_summary 输出）或 BUY 简报正文。
        kind: "daily" / "buy" / "watchdog"——daily 与 buy 走改写，其余原样。

    Returns:
        LLM 改写后的文本；LLM 不可用时回退原文，best-effort 不抛。
    """
    if not text:
        return text
    prompt = {"daily": _HUMANIZE_PROMPT, "buy": _HUMANIZE_BUY_PROMPT}.get(kind)
    if prompt is None:
        return text  # WATCH/WATCHDOG 等其它通道保持结构化文本
    cache_key = (kind, hashlib.md5(text.encode("utf-8")).hexdigest())
    now = time.time()
    cached = _HUMANIZE_DEDUP.get(cache_key)
    if cached and now - cached[0] < _HUMANIZE_TTL:
        return cached[1]
    try:
        from emotion_core.llm import agnes
        client = agnes.AgnesClient()
        out = client.complete(
            prompt=prompt + text,
            temperature=0.3,
            max_tokens=512,
        )
        out = (out or "").strip()
        if not out:
            log.debug("LLM 返回空文本，回退原文")
            return text
        _HUMANIZE_DEDUP[cache_key] = (now, out)
        log.info("飞书推送 LLM 改写完成（%d→%d 字）", len(text), len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        # LLMNotEnabledError（无密钥）/ 网络异常 / 超时：均回退原文
        log.debug("LLM humanize 失败（回退原文）：%s", exc)
        return text


def reset_humanize_dedup() -> None:
    """测试用：清空 humanize 缓存。生产代码不应调用。"""
    _HUMANIZE_DEDUP.clear()


# ---- R58-4 观察记录即时推送（A 止血后默认关闭）----
# daily 主链结尾会调 `push(report_md, trade_date)` 推速览；本通道是观察记录
# 刚落库即触发（不经 daily 主链）。**PUSH_BUY 默认 0**：口径B 打板被证伪后
# 「出现即推」等于每天推一条负期望暗示，等 B/C 轮有真东西再打开。
# 24h 内存 dict 节流：同 (date, code) 24h 内只推一次，防 daily 重跑/回放重复轰炸。

_BUY_DEDUP: dict[tuple[str, str], float] = {}
_BUY_DEDUP_TTL = 86400  # 24h
_BUY_DEDUP_PRUNE_EVERY = 64  # 每 N 次调用清理一次过期 key


_BUY_COND_HUMAN = (
    ("c1_uniqueness", "是不是唯一最强连板股"),
    ("c2_exchange", "今天是否正常换手（非一字板）"),
    ("c3_elimination", "身位（昨天在不在最强组里）"),
    ("c4_min_days", "连板高度够不够"),
    ("c5_strength_diverge", "换手强度够不够"),
)


def _format_buy_signal(code: str, name: str, cont_days: int,
                       buy_window: str, cl: object) -> str:
    """观察记录简报（人话版）：只给标的 + 身位 + 硬条件达标情况。

    A 止血（2026-10-21）：不再带买点/卖点/仓位（口径B 打板成交假设证伪，
    每信号 −1.26%）；标题也从「买入信号」改成「观察记录」，避免读者当指令。
    不带持仓明细（与 `push` 同纪律）；不带 BUY 之外的段（避免与日报摘要重复）。
    """
    name_part = f"{name}({code})" if name else code
    lines = ["[emotion-core] 今日观察记录（不出买入建议）",
             f"{name_part}  {cont_days} 板"]
    passed = [human for field, human in _BUY_COND_HUMAN
              if getattr(cl, field, None) is True]
    failed = [human for field, human in _BUY_COND_HUMAN
              if getattr(cl, field, None) is False]
    if failed:
        lines.append("未过关：" + "、".join(failed))
    elif passed:
        lines.append("硬条件全部通过：" + "、".join(passed))
    return "\n".join(lines)


def push_buy_signal(code: str, name: str, cont_days: int,
                    buy_window: str, cl: object,
                    trade_date: _date | None = None) -> bool:
    """观察记录即时推送：默认关闭，24h 内存节流，best-effort，失败不抛。

    **默认关闭**（A 止血）：读 `EMOTION_PUSH_BUY` / `PUSH_BUY`，缺省 0。口径B 打板
    被证伪后，「BUY 出现即推」等于每天推一条负期望的操作暗示；等 B 轮找到正期望
    边缘或 C 轮补上盘中能力再打开（届时这条通道才有意义）。

    仅 BUY 记录触发（SECONDARY/RECOMMEND 不调）；同日同 code 重跑不重推。

    Args:
        code/name/cont_days: 标的与身位。
        buy_window: market_stat.buy_window 原文（NONE/ENHANCED/STANDARD）。
        cl: domain.Checklist 对象（含 5 字段 + W1）。
        trade_date: 交易日，仅用于日志；key 仍以 (date_iso, code) 为单位。

    Returns:
        bool 推送是否成功（关闭/未配置/被节流时返回 False）。
    """
    if _cfg("PUSH_BUY", "0").strip() not in ("1", "true", "True"):
        log.debug("PUSH_BUY 未开启（A 止血默认关），观察记录即时推送跳过")
        return False
    url = _cfg("LKL_WEBHOOK_URL", "")
    if not url:
        log.debug("LKL_WEBHOOK_URL 未配置，观察记录即时推送跳过")
        return False
    d_iso = (trade_date or _date.today()).isoformat()
    key = (d_iso, code)
    now = time.time()
    last = _BUY_DEDUP.get(key, 0.0)
    if now - last < _BUY_DEDUP_TTL:
        log.info("观察记录 %s/%s 24h 内已推过（ts=%.0f，距今 %.0fs），跳过",
                 d_iso, code, last, now - last)
        return False
    text = _humanize_alert(
        _format_buy_signal(code, name, cont_days, buy_window, cl),
        kind="buy")
    channel = _guess_channel(url)
    try:
        import requests

        resp = requests.post(url, json=_payload(channel, text), timeout=10)
        ok = _resp_ok(channel, resp)
        if not ok and channel == "feishu" and resp.status_code == 200:
            log.warning(
                "飞书观察记录即时推送返回 HTTP 200 但 body 非成功码：%s",
                resp.text[:120],
            )
        log.info("观察记录即时推送 %s/%s（%s）：HTTP %s 成功=%s",
                 d_iso, code, channel, resp.status_code, ok)
    except Exception as exc:  # noqa: BLE001
        log.warning("观察记录即时推送失败（不影响落盘）：%s", exc)
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


# ---- R67-4 WATCH 信号即时推送 ----
# 与 BUY 节流**分开**的字典：BUY 与 WATCH 是两条独立通道，同一 (date, code)
# 各推各的；复用 _BUY_DEDUP 会让先到者吃掉另一条的配额。
_WATCH_DEDUP: dict[tuple[str, str], float] = {}
_WATCH_DEDUP_TTL = 86400  # 24h
_WATCH_DEDUP_PRUNE_EVERY = 64
_WATCH_REASON_LIMIT = 60  # 每条策略理由摘要的截断长度
_WATCH_PICKS_PER_CODE = 3  # 每只票正文最多列几个策略


def _watch_env_int(name: str, default: int) -> int:
    """读整型 env（读法与 ``_cfg`` 一致，两种写法都认），非法值回退 default。"""
    raw = _cfg(name, str(default))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("WATCH 推送配置 %s=%r 不是整数，回退 %d", name, raw, default)
        return default


def _watch_row_name(row: dict) -> str:
    """票名：优先 ``stock_name``（runner 的 ``_item`` 会填），再退 ``name``。

    注意 runner 的 ``_item`` 里 ``name`` 是**策略显示名**，故 ``stock_name``
    存在时必须用它，否则会把「一阳三阴」印成票名。
    """
    if "stock_name" in row:
        return str(row.get("stock_name") or "")
    return str(row.get("name") or "")


def _format_watch_signal(d_iso: str, code: str, name: str,
                         picks: list[tuple[int, str, str]],
                         total: int, remain: int) -> str:
    """一只票一条消息（R67-4）：标题 + 最高分 + top3 策略理由 + 汇总行。"""
    label = f"{name}({code})" if name else code
    best = picks[0][0] if picks else 0
    lines = [f"[策略观察·WATCH] {d_iso}", f"{label} 最高 {best} 分"]
    for score, strategy, reason in picks:
        text = reason
        if len(text) > _WATCH_REASON_LIMIT:
            text = text[:_WATCH_REASON_LIMIT] + "…"
        lines.append(f"· {strategy} {score}：{text}")
    if remain > 0:
        lines.append(f"共 {total} 只，余下 {remain} 只见报告⑥段")
    else:
        lines.append(f"共 {total} 只")
    return "\n".join(lines)


def push_watch_signals(rows: list[dict] | None,
                       trade_date: _date | None = None) -> int:
    """WATCH 信号即时推送（R67-4）：按 code 聚合，一只票一条，best-effort 不抛。

    背景：``strategy_signal`` 的 WATCH 此前完全不推送。2026-10-08 600825 拿了 3 条
    WATCH（bottom_volume 75 / emotion_cycle 45 / bull_trend 30），owner 一条都没
    收到。BUY 走 ``push_buy_signal``，本函数是 WATCH 的对应通道。

    Args:
        rows: 当日 ``action='WATCH'`` 的策略行（``code`` / ``strategy`` /
              ``score`` / ``reason``；票名取 ``stock_name``）。
        trade_date: 交易日，用于文案日期与节流 key；缺省取当天。

    Returns:
        int 成功推送的票数（关闭 / 未配置 / 全部低于阈值 / 全部被节流时为 0）。
    """
    if _watch_env_int("PUSH_WATCH", 1) == 0:
        log.info("EMOTION_PUSH_WATCH=0，WATCH 推送关闭")
        return 0
    items = [row for row in (rows or []) if isinstance(row, dict)]
    if not items:
        return 0
    url = _cfg("LKL_WEBHOOK_URL", "")
    if not url:
        log.debug("LKL_WEBHOOK_URL 未配置，WATCH 即时推送跳过")
        return 0
    min_score = _watch_env_int("WATCH_PUSH_MIN_SCORE", 60)
    max_codes = _watch_env_int("WATCH_PUSH_MAX_CODES", 10)
    d_iso = (trade_date or _date.today()).isoformat()

    # 先按行过阈值，再按 code 聚合（同票多策略只出一条）。
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    names: dict[str, str] = {}
    for row in items:
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < min_score:
            continue
        code = str(row.get("code") or "")
        if not code:
            continue
        names.setdefault(code, _watch_row_name(row))
        grouped.setdefault(code, []).append(
            (score, str(row.get("strategy") or ""), str(row.get("reason") or ""))
        )
    if not grouped:
        log.info("WATCH 推送：%s 无 ≥%d 分的信号，不发", d_iso, min_score)
        return 0

    ranked = sorted(grouped, key=lambda c: (-max(s for s, _, _ in grouped[c]), c))
    total = len(ranked)
    selected = ranked[:max_codes] if max_codes > 0 else []
    remain = total - len(selected)
    channel = _guess_channel(url)
    now = time.time()
    sent = 0
    for code in selected:
        picks = sorted(grouped[code], key=lambda e: -e[0])[:_WATCH_PICKS_PER_CODE]
        key = (d_iso, code)
        last = _WATCH_DEDUP.get(key, 0.0)
        if now - last < _WATCH_DEDUP_TTL:
            log.info("WATCH 信号 %s/%s 24h 内已推过（ts=%.0f，距今 %.0fs），跳过",
                     d_iso, code, last, now - last)
            continue
        text = _format_watch_signal(d_iso, code, names.get(code, ""), picks, total, remain)
        try:
            import requests

            resp = requests.post(url, json=_payload(channel, text), timeout=10)
            ok = _resp_ok(channel, resp)
            if not ok and channel == "feishu" and resp.status_code == 200:
                log.warning(
                    "飞书 WATCH 推送返回 HTTP 200 但 body 非成功码：%s",
                    resp.text[:120],
                )
            log.info("WATCH 即时推送 %s/%s（%s）：HTTP %s 成功=%s",
                     d_iso, code, channel, resp.status_code, ok)
        except Exception as exc:  # noqa: BLE001
            log.warning("WATCH 即时推送失败（不影响 WATCH 落盘）：%s", exc)
            continue
        # 仅在 HTTP 请求**发出**后记节流——未配置/网络异常时不污染 dedup
        _WATCH_DEDUP[key] = now
        if ok:
            sent += 1
    if len(_WATCH_DEDUP) >= _WATCH_DEDUP_PRUNE_EVERY * 4:
        cutoff = now - _WATCH_DEDUP_TTL
        for k in [k for k, t in _WATCH_DEDUP.items() if t < cutoff]:
            _WATCH_DEDUP.pop(k, None)
    return sent


def reset_watch_dedup() -> None:
    """测试用：清空 WATCH 节流字典。生产代码不应调用。"""
    _WATCH_DEDUP.clear()
