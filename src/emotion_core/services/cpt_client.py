"""CPT（Chan_Pattern_Trader）HTTP 取数客户端 —— R3 持仓决策的**唯一外部依赖**。

设计见 `docs/17-持仓决策-功能与代码设计.md` §4；决策 D4 = v1 就实接 CPT HTTP。

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
  显式传 `?ensure_factors=1` 才联网补因子并写库 ⇒ 本客户端**永远不传** `ensure_factors`。
- `GET /api/dashboard/a-share/recommendation`：⚠️ **会写库**。`a_share_routes.py:270` 每次
  GET 都调 `_persist_recommendation`；`:362 _should_persist_recommendation` 做了内容去重 +
  心跳（同内容同 `(code, level)` 在一个心跳窗口内不重复写）⇒ 稳态约「每心跳每标的 1 行」
  落 `cpt_recommendation`。本模块**只在需要 `price_ratio` 时才调它**，并由
  `holding_service` 暴露开关关闭（§8.2 `CPT_RECOMMENDATION_ENABLED`）。
- **明确不用** `GET /api/dashboard/track/{code}/advice`：它要求该票已在 `cpt_track`（否则
  404，`track_api.py:308`），且每次调用都写一条 `cpt_track_snapshot`（`:313`）。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

log = logging.getLogger("emotion_core.services.cpt_client")

#: 只走回环直连；不接受调用方传 host（§4.5 第 3 条）。
CPT_BASE: Final = "http://127.0.0.1:8010"
CPT_TIMEOUT: Final = 3.0

_SNAPSHOT_PATH: Final = "/api/dashboard/a-share/snapshot"
_RECOMMENDATION_PATH: Final = "/api/dashboard/a-share/recommendation"

#: `overlays` 的四个数组，全空即视为无结构数据（§4.3）。
_OVERLAY_KEYS: Final = ("fractals", "bis", "zhongshus", "trend_types")

_FALSEY: Final = {"0", "false", "no", "off"}


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


def _get_json(path: str, params: dict[str, str], timeout: float) -> dict[str, Any] | None:
    """GET → dict；任何异常/非 dict 响应 → None + warning。**绝不抛。**"""
    url = f"{CPT_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
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
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("CPT %s 响应不是合法 JSON：%s", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("CPT %s 响应不是 JSON 对象（%s）", path, type(data).__name__)
        return None
    return data


def fetch_snapshot(code: str, *, timeout: float = CPT_TIMEOUT) -> dict[str, Any] | None:
    """`overlays`（adj 域）+ `candles` + `signal` + `data_quality`；失败 → None。

    **永不传 `ensure_factors`**：保持 CPT 侧的默认只读路径（见模块 docstring）。
    """
    norm = _norm_code(code)
    if norm is None:
        return None
    return _get_json(_SNAPSHOT_PATH, {"code": norm}, timeout)


def fetch_recommendation(code: str, *, timeout: float = CPT_TIMEOUT) -> dict[str, Any] | None:
    """CPT 结论原样（D7 并排展示）+ `price_ratio`；失败 → None。

    ⚠️ 该端点每次 GET 都会调 `_persist_recommendation`；调用方须先过
    `recommendation_enabled()` 与用户开关（§8.2）。
    """
    norm = _norm_code(code)
    if norm is None:
        return None
    return _get_json(_RECOMMENDATION_PATH, {"code": norm}, timeout)


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
