"""CPT（Chan_Pattern_Trader）HTTP 取数客户端 —— R3/R4 的**唯一外部依赖**。

设计见 `docs/17-持仓决策-功能与代码设计.md` §4；决策 D4 = v1 就实接 CPT HTTP；
R4 关注列表扩展见 `docs/18-情绪+缠论整合-功能与代码设计.md` §7.1。

硬要求（§4.5，改这个文件前先读一遍）
------------------------------------
1. **只用 stdlib** `urllib.request`，超时 3s（CPT 看板同机，慢就是不正常）；不做连接复用。
2. **绝不抛**：任何失败 → `None` + `log.warning`。`holding_service.decide()` 必须能在
   CPT 全挂时照常出结果（走 §5.4 的乙档降级）。
3. **只允许回环**：host 由模块常量 `CPT_BASE` 拼，**不接受调用方传 host**。
4. **不缓存**：快照随行情变；同一次 `decide()` 内两个端点各只调一次（由调用方保证）。
5. 只走本机直连 `127.0.0.1:8010`，**不走 nginx** —— nginx 的 `/cpt/api/` 挂了
   `auth_basic`，直连回环可绕过（这是刻意的边界：本设计不支持远端 CPT）。

写副作用披露（§4.2，必须保留在代码里）
--------------------------------------
- `GET /api/dashboard/a-share/snapshot`：**默认只读**。`a_share_routes.py:142` 的 R59 修复
  让 `ensure_factors` 默认 `None`，只有环境变量 `CPT_ASHARE_ONDEMAND_FACTOR` 显式开启或
  显式传 `?ensure_factors=1` 才联网补因子并写库 ⇒ 本客户端**永远不传** `ensure_factors`
  （只有 `holding_service` 显式调 `fetch_snapshot(..., ensure_factors=True)` 时才拼）。
- `GET /api/dashboard/a-share/recommendation`：⚠️ **会写库**。`a_share_routes.py:270` 每次
  GET 都调 `_persist_recommendation`；`:362 _should_persist_recommendation` 做了内容去重 +
  心跳（同内容同 `(code, level)` 在一个心跳窗口内不重复写）⇒ 稳态约「每心跳每标的 1 行」
  落 `cpt_recommendation`。本模块**只在需要 `price_ratio` 时才调它**，并由
  `holding_service` 暴露开关关闭（§8.2 `CPT_RECOMMENDATION_ENABLED`）。
- `POST /api/dashboard/track`（add/remove/restore）：**写 `cpt_track`**。必须由调用方
  （`watchlist_service`）校验后再调；`holding_service` 不调这些。
- `GET /api/dashboard/track/{code}/advice`：要求该票已在 `cpt_track`（否则 404，
  `track_api.py:308`），且每次调用都写一条 `cpt_track_snapshot`（`:313`）。
  `cpt_client` 只在用户明确查看时才调它（`--ensure-factors` / 前端「刷新」）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

log = logging.getLogger("emotion_core.services.cpt_client")

#: 只走回环直连；不接受调用方传 host（§4.5 第 3 条）。
CPT_BASE: Final = "http://127.0.0.1:8010"
CPT_TIMEOUT: Final = 3.0

#: `ensure_factors=True` 走的是联网补因子路径，慢且写库；给更宽松的超时（§7.1）。
CPT_ENSURE_TIMEOUT: Final = 30.0

_SNAPSHOT_PATH: Final = "/api/dashboard/a-share/snapshot"
_RECOMMENDATION_PATH: Final = "/api/dashboard/a-share/recommendation"

#: `overlays` 的四个数组，全空即视为无结构数据（§4.3）。
_OVERLAY_KEYS: Final = ("fractals", "bis", "zhongshus", "trend_types")

_FALSEY: Final = {"0", "false", "no", "off"}

#: CPT 关注列表（R4 真源，N1 定案）。
_TRACK_PREFIX: Final = "/api/dashboard/track"

#: 默认用户（CPT `track_api.DEFAULT_USER_ID` = "default"，两边保持一致）。
DEFAULT_USER: Final = "default"

#: 与 CPT `track_api.extract_user_id` 同口径：`[A-Za-z0-9._-]{1,32}`。
_USER_MAX_LEN: Final = 32
_USER_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

__all__ = (
    "cpt_enabled",
    "recommendation_enabled",
    "fetch_snapshot",
    "fetch_recommendation",
    "overlay_usable",
    "snapshot_overlays",
    "data_quality_note",
    "price_ratio_of",
    "recommendation_view",
    # ── R4 / docs/18 §7.1 ──
    "CPT_ENSURE_TIMEOUT",
    "DEFAULT_USER",
    "track_list",
    "track_add",
    "track_remove",
    "track_restore",
    "track_advice",
    "track_speak",
    "points_view",
    "human_text",
    "points_empty_reason",
)


def cpt_enabled() -> bool:
    """总开关：环境变量 `CPT_ENABLED=0/false/no/off` → 全关；未设或其它值 → 开。"""
    return os.environ.get("CPT_ENABLED", "").strip().lower() not in _FALSEY


def recommendation_enabled() -> bool:
    """`recommendation` 端点开关（它会写 `cpt_recommendation`，见模块 docstring）。

    `CPT_RECOMMENDATION_ENABLED=0/false/no/off` → 不调它；此时拿不到 `price_ratio`，
    结构位自然降级为百分比兜底（§6.2）。
    """
    return os.environ.get("CPT_RECOMMENDATION_ENABLED", "").strip().lower() not in _FALSEY


def _norm_code(code: Any) -> str | None:
    """6 位数字代码；非法 → None（调用方据此直接返回 None，不发请求）。"""
    if isinstance(code, bool) or code is None:
        return None
    s = str(code).strip()
    if not s.isdigit() or len(s) > 6:
        return None
    return s.zfill(6)


def _norm_user(user: str | None) -> str:
    """与 CPT `track_api.extract_user_id` 同口径的 user 归一化。

    - 缺 / 非 str / 空串 / 超 `_USER_MAX_LEN` / 字符不在 `[A-Za-z0-9._-]` → `"default"`。
    - 不做 strip（CPT 那边 strip 过了，我们直接校验白名单）。
    """
    if not isinstance(user, str):
        return DEFAULT_USER
    if not user or len(user) > _USER_MAX_LEN:
        return DEFAULT_USER
    return user if _USER_RE.match(user) else DEFAULT_USER


def _user_headers(user: str | None) -> dict[str, str]:
    """带 `X-CPT-User` 的 Accept 头（CPT 追踪端点凭此识用户）。"""
    return {"Accept": "application/json", "X-CPT-User": _norm_user(user)}


def _get_json(
    path: str,
    params: dict[str, str],
    timeout: float,
    *,
    user: str | None = None,
) -> dict[str, Any] | None:
    """GET → dict；任何异常/非 dict 响应 → None + warning。**绝不抛。**"""
    data = _get_json_any(path, params, timeout, user=user)
    if not isinstance(data, dict):
        name = params.get("code") or user or "-"
        log.warning("CPT %s 响应不是 JSON 对象（%s, data=%s）", path, type(data).__name__, name)
        return None
    return data


def _get_json_any(
    path: str,
    params: dict[str, str],
    timeout: float,
    *,
    user: str | None = None,
) -> Any:
    """GET → any JSON value（dict / list / str / ...）；异常 → None + warning。**绝不抛。**"""
    url = f"{CPT_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers=_user_headers(user))  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
    except urllib.error.HTTPError as exc:
        log.warning("CPT %s → HTTP %s（%s）", path, exc.code, params.get("code"))
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("CPT %s 请求失败：%s（%s）", path, exc, params.get("code"))
        return None
    except Exception as exc:  # noqa: BLE001 - 契约是「绝不抛」，兜底也要把它换成 None
        log.warning("CPT %s 未预期异常：%s: %s（%s）", path, type(exc).__name__, exc,
                    params.get("code"))
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("CPT %s 响应不是合法 JSON：%s", path, exc)
        return None


def _post_json(
    path: str,
    body: dict[str, Any],
    *,
    user: str | None = None,
    timeout: float = CPT_TIMEOUT,
) -> dict[str, Any] | None:
    """POST JSON body → dict；任何异常/非 dict 响应 → None + warning。**绝不抛。**"""
    url = f"{CPT_BASE}{path}"
    data_bytes = json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(  # noqa: S310
            url,
            data=data_bytes,
            headers={**_user_headers(user), "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        log.warning("CPT %s → HTTP %s（user=%s）", path, exc.code, _norm_user(user))
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("CPT %s 请求失败：%s（user=%s）", path, exc, _norm_user(user))
        return None
    except Exception as exc:  # noqa: BLE001 - 契约是「绝不抛」
        log.warning("CPT %s 未预期异常：%s: %s（user=%s）", path, type(exc).__name__, exc,
                    _norm_user(user))
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("CPT %s 响应不是合法 JSON：%s", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("CPT %s 响应不是 JSON 对象（%s）", path, type(data).__name__)
        return None
    return data


def fetch_snapshot(
    code: str,
    *,
    timeout: float = CPT_TIMEOUT,
    ensure_factors: bool | None = None,
    user: str | None = None,
) -> dict[str, Any] | None:
    """`overlays`（adj 域）+ `candles` + `signal` + `data_quality`；失败 → None。

    ``ensure_factors=True`` 才拼 ``?ensure_factors=1``（联网补因子 + 写库，慢），
    ``False``/``None`` 恒不拼（默认只读，D5 / 模块 docstring）。
    ``user`` → `X-CPT-User` 头（用户维度的关注列表/追踪）。
    """
    norm = _norm_code(code)
    if norm is None:
        return None
    params: dict[str, str] = {"code": norm}
    if ensure_factors is True:
        params["ensure_factors"] = "1"
    return _get_json(_SNAPSHOT_PATH, params, timeout, user=user)


def fetch_recommendation(code: str, *, timeout: float = CPT_TIMEOUT,
                         user: str | None = None) -> dict[str, Any] | None:
    """CPT 结论原样（D7 并排展示）+ `price_ratio`；失败 → None。

    ⚠️ 该端点每次 GET 都会调 `_persist_recommendation`；调用方须先过
    `recommendation_enabled()` 与用户开关（§8.2）。
    `user` → `X-CPT-User` 头。
    """
    norm = _norm_code(code)
    if norm is None:
        return None
    return _get_json(_RECOMMENDATION_PATH, {"code": norm}, timeout, user=user)


def overlay_usable(snapshot: dict[str, Any] | None) -> bool:
    """结构位是否可用：非降级 且 `overlays` 四数组至少一个非空（§4.3）。

    降级快照（`data_quality.degraded == true`）或四数组全空 ⇒ **当作无结构数据**，
    走百分比兜底；**不得**把它当成「结构位是 0/空」。
    """
    if not isinstance(snapshot, dict):
        return False
    dq = snapshot.get("data_quality")
    if isinstance(dq, dict) and dq.get("degraded") is True:
        return False
    overlays = snapshot.get("overlays")
    if not isinstance(overlays, dict):
        return False
    return any(
        isinstance(overlays.get(k), list) and overlays[k] for k in _OVERLAY_KEYS
    )


def snapshot_overlays(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """取出可用的 `overlays`；不可用 → None（交给 `levels` 兜底）。"""
    if not overlay_usable(snapshot):
        return None
    assert snapshot is not None  # overlay_usable 已判过
    ov = snapshot.get("overlays")
    return ov if isinstance(ov, dict) else None


def data_quality_note(snapshot: dict[str, Any] | None) -> str | None:
    """CPT 侧数据质量的降级提示；正常（`severity=ok` / 无标记）→ None。

    实测 `data_quality` 有**两种形状**（2026-10-13 对 601811 / 000016 / 001381 实跑）：
    - 不可用票（`001381`）：`{"degraded": true, "reason": "no_data"}`
    - 可用票（`601811` / `000016`）：**没有 `degraded` 键**，而是
      `{"severity": "ok"|"gap", "gap": bool, "gap_count": int, "stale": bool,
        "closed_bar_count": int, "expected_trade_days": int, "gaps": [...],
        "unclosed_bar_count": int, "out_of_order_count": int, "ashare_tags": {...}}`
      （实测 `000016` 是 `severity="gap"`、`gap_count=5`：停牌期间缺 5 个交易日）

    ⇒ 只判 `degraded` 会漏报「有缺口但还能算」的票。本函数把两者都收敛成一句人读提示，
    由 `holding_service` 放进 `reasons[kind="degrade"]`。
    """
    if not isinstance(snapshot, dict):
        return None
    dq = snapshot.get("data_quality")
    if not isinstance(dq, dict) or not dq:
        return None
    if dq.get("degraded") is True:
        return f"CPT 数据不可用：{dq.get('reason') or '原因未给出'}"
    parts: list[str] = []
    sev = dq.get("severity")
    if isinstance(sev, str) and sev and sev != "ok":
        parts.append(f"severity={sev}")
    if dq.get("stale") is True:
        parts.append("stale=True")
    gap_n = dq.get("gap_count")
    if dq.get("gap") is True and isinstance(gap_n, int) and gap_n > 0:
        parts.append(f"gap_count={gap_n}")
    if not parts:
        return None
    return "CPT 数据质量：" + " ".join(parts)


def price_ratio_of(recommendation: dict[str, Any] | None) -> float | None:
    """`price_ratio`：adj→raw 的换算系数。**无效一律 None，绝不用 1.0 顶替。**

    `a_share_routes.py:230-258`：`round(快照最后一根 K 线 close ÷ 同日 daily_bar raw
    close, 4)`，要求分子分母**同一天**；判不出同日时 CPT 自己就给 null
    （「绝不给一个看着像真的错数」）。这里再加一层类型/正数校验。
    """
    if not isinstance(recommendation, dict):
        return None
    v = recommendation.get("price_ratio")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f > 0 else None


def recommendation_view(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
    """CPT 结论的展示投影（D7：两侧并排，不做冲突裁决）。

    只取展示要用的字段，**不解读**：`action` 是 `buy/sell/watch/hold` 四档，与
    emotion-core 的 `BUY/RECOMMEND/SECONDARY`（R58-4）**不是同一套词表**。
    """
    if not isinstance(recommendation, dict):
        return None
    keys = ("available", "action", "action_label", "headline", "reason", "price",
            "status", "signal_type", "divergence_status", "disclaimer")
    view = {k: recommendation.get(k) for k in keys}
    if all(v is None for v in view.values()):
        return None
    return view


# ─────────────────────────────────────────────────────────────────────────────
# R4 / docs/18 §7.1 —— CPT 关注列表（真源 = `cpt_track`）+ 人话契约
# ─────────────────────────────────────────────────────────────────────────────


def track_list(*, user: str | None = DEFAULT_USER) -> list[dict[str, Any]] | None:
    """GET `/api/dashboard/track` → 当前用户全部活跃追踪（CPT 不可达 → None）。

    CPT 返回 ``{"available": True, "items": [...], "recycle": [...]}``；
    ``available=False`` 时（DB 故障降级）返回 ``None``。

    返回 ``None`` 表示 CPT 挂了（调用方据此走降级）；``[]`` 表示 CPT 通了但该用户
    没追踪任何票。**绝不**由本模块把它翻译成布尔或空字符串。
    """
    data = _get_json_any(_TRACK_PREFIX, {}, CPT_TIMEOUT, user=user)
    if not isinstance(data, dict):
        return None
    if data.get("available") is not True:
        return None
    items = data.get("items")
    return items if isinstance(items, list) else None


def track_add(code: str, *, user: str | None = DEFAULT_USER, note: str = "") -> bool:
    """POST `/api/dashboard/track` action=add → 是否成功。

    只代表「CPT 说它收了」；调用方需先做 code 校验与重复检查。
    """
    norm = _norm_code(code)
    if norm is None:
        return False
    body = {"action": "add", "code": norm}
    if note:
        body["note"] = str(note)[:200]
    resp = _post_json(_TRACK_PREFIX, body, user=user)
    if not isinstance(resp, dict):
        return False
    # CPT handle_track_add 返回 {"code": ..., "created": True|False} 之类；
    # 只要 HTTP 200 且是 dict 就当成功。
    return True


def track_remove(code: str, *, user: str | None = DEFAULT_USER) -> bool:
    """POST `/api/dashboard/track` action=remove。"""
    norm = _norm_code(code)
    if norm is None:
        return False
    resp = _post_json(_TRACK_PREFIX, {"action": "remove", "code": norm}, user=user)
    return isinstance(resp, dict)


def track_restore(code: str, *, user: str | None = DEFAULT_USER) -> bool:
    """POST `/api/dashboard/track` action=restore。"""
    norm = _norm_code(code)
    if norm is None:
        return False
    resp = _post_json(_TRACK_PREFIX, {"action": "restore", "code": norm}, user=user)
    return isinstance(resp, dict)


def track_advice(code: str, *, user: str | None = DEFAULT_USER) -> dict[str, Any] | None:
    """GET `/api/dashboard/track/{code}/advice` → 该票的建议原样；失败/未追踪 → None。

    ⚠️ 端点要求该票已追踪（否则 404），且每次调用都写一条 `cpt_track_snapshot`
    （`track_api.py:313`）。**调用方只在用户显式查看 / 刷新时调**，不要批量轮询。
    """
    norm = _norm_code(code)
    if norm is None:
        return None
    return _get_json(f"{_TRACK_PREFIX}/{norm}/advice", {}, CPT_TIMEOUT, user=user)


def track_speak(code: str, *, user: str | None = DEFAULT_USER) -> bool:
    """POST `/api/dashboard/track/{code}/speak` → LLM 重写人话是否成功。

    最佳-effort：CPT 那边 `cpt_llm_call` 已有 ok 文本时是空跑；无则调一次 LLM。
    返回 False 不抛，调用方据此决定是否回落到静态文案。
    """
    norm = _norm_code(code)
    if norm is None:
        return False
    resp = _post_json(f"{_TRACK_PREFIX}/{norm}/speak", {}, user=user)
    return isinstance(resp, dict)


def points_view(advice: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 `track_advice` 里的建议位规整成前端契约（`docs/18 §9.3`）。

    缺字段一律 None，**不抛**。返回的「买点/卖点」是 list（可能空），前端按
    confirmed 高亮 / 否则虚线。
    """
    if not isinstance(advice, dict):
        return None
    sp = advice.get("suggested_points") if isinstance(advice, dict) else None
    if not isinstance(sp, dict):
        sp = {}

    def _side(key: str) -> list[dict[str, Any]] | None:
        raw = sp.get(key)
        if not isinstance(raw, list):
            return None
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            pl = item.get("price_level")
            if isinstance(pl, bool) or not isinstance(pl, (int, float)):
                continue
            out.append({
                "price_level": float(pl),
                "confirmed": bool(item.get("confirmed")),
            })
        return out or None

    buy = _side("buy")
    sell = _side("sell")
    stop = sp.get("stop_loss_reference") if isinstance(sp, dict) else None
    if isinstance(stop, bool) or not isinstance(stop, (int, float)):
        stop = None
    else:
        stop = float(stop) if stop is not None else None

    result = {
        "buy": buy,
        "sell": sell,
        "stop_loss": stop,
        "raw_close": _opt_float(advice.get("raw_close")),
        "price_ratio": _opt_float(advice.get("price_ratio")),
        "as_of_basis": (
            advice.get("as_of_basis") if isinstance(advice.get("as_of_basis"), str) else None
        ),
    }
    return result


def _opt_float(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def human_text(advice: dict[str, Any] | None) -> str | None:
    """`advice["human"]` —— 已追踪票的「说人话」文本；缺 → None（不现造 LLM）。

    实测 CPT 返回的 `human` 可能是 str 或 `{"text": "..."}` 字典。
    """
    if not isinstance(advice, dict):
        return None
    h = advice.get("human")
    if isinstance(h, str) and h.strip():
        return h.strip()
    if isinstance(h, dict):
        t = h.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
    return None


def points_empty_reason(advice: dict[str, Any] | None) -> str | None:
    """`advice["suggested_points"]["reason"]` —— 买点/卖点算不出的原因；缺 → None。"""
    if not isinstance(advice, dict):
        return None
    sp = advice.get("suggested_points")
    if not isinstance(sp, dict):
        return None
    r = sp.get("reason")
    return r if isinstance(r, str) and r.strip() else None
