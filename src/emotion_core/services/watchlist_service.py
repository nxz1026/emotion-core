"""R4 关注列表服务 —— 真源 = CPT `cpt_track`（docs/18 §7.2）。

所有读写都走 `cpt_client`（CPT HTTP）；**绝不**回落本地 `watchlist` 表（N1 定案）。
CPT 不可达时：读返回 ``([], False)`` / ``None``；写返回 ``(False, "CPT 不可达：…")``。

既有 ``services/watchlist.py`` 维护的是本地 ``watchlist`` 表（供 review / strategy
universe 使用），与本模块是两套独立的存储；本模块迁移期间两者并行运行。
"""
from __future__ import annotations

import logging
from typing import Any

from emotion_core.services.cpt_client import (
    DEFAULT_USER,
    track_add,
    track_list,
    track_remove,
    track_restore,
)

log = logging.getLogger("emotion_core.services.watchlist_service")

#: `note` 字段长度上限（与 CPT `track_store.add` 同口径）。
TRACK_NOTE_MAX: int = 200


def _norm_code(code: Any) -> str | None:
    """6 位数字代码；非法 → None。"""
    if isinstance(code, bool) or code is None:
        return None
    s = str(code).strip()
    if not s.isdigit() or len(s) > 6:
        return None
    return s.zfill(6)


def list_codes(*, user: str | None = DEFAULT_USER) -> tuple[list[dict[str, Any]], bool]:
    """CPT 关注列表 → ``(items, cpt_available)``。

    - CPT 可达 → ``(items, True)``（items 可能为空列表）。
    - CPT 不可达 → ``([], False)``（调用方据此走降级 / 提示用户）。

    绝不回落本地 `watchlist` 表。
    """
    items = track_list(user=user)
    if items is None:
        return [], False
    return items, True


def add(code: str, *, user: str | None = DEFAULT_USER, note: str = "") -> tuple[bool, str]:
    """加入 CPT 关注列表 → ``(success, message)``。

    - 非法 code → ``(False, "非法 code：'xxx'（需 6 位数字）")``（不调 CPT）。
    - CPT 不可达 → ``(False, "CPT 不可达：…")``。
    - 成功（含幂等：重复加入已活跃的票）→ ``(True, "已加入关注列表")``。
    """
    norm = _norm_code(code)
    if norm is None:
        return False, f"非法 code：{code!r}（需 6 位数字）"
    if note:
        note = str(note)[:TRACK_NOTE_MAX]
    ok = track_add(norm, user=user, note=note)
    if ok:
        return True, "已加入关注列表"
    return False, "CPT 不可达：track_add 返回失败"


def remove(code: str, *, user: str | None = DEFAULT_USER) -> tuple[bool, str]:
    """移出 CPT 关注列表（进回收站，可恢复） → ``(success, message)``。"""
    norm = _norm_code(code)
    if norm is None:
        return False, f"非法 code：{code!r}（需 6 位数字）"
    ok = track_remove(norm, user=user)
    if ok:
        return True, "已移出关注列表"
    return False, "CPT 不可达：track_remove 返回失败"


def restore(code: str, *, user: str | None = DEFAULT_USER) -> tuple[bool, str]:
    """从回收站恢复 → ``(success, message)``。"""
    norm = _norm_code(code)
    if norm is None:
        return False, f"非法 code：{code!r}（需 6 位数字）"
    ok = track_restore(norm, user=user)
    if ok:
        return True, "已恢复关注"
    return False, "CPT 不可达：track_restore 返回失败"


def is_tracked(code: str, *, user: str | None = DEFAULT_USER) -> bool | None:
    """该票是否在 CPT 关注列表里（活跃状态）。

    - ``True`` / ``False`` = CPT 可达，且在/不在列表里。
    - ``None`` = CPT 不可达（调用方据此走降级 / 提示用户）。
    """
    norm = _norm_code(code)
    if norm is None:
        return False
    items = track_list(user=user)
    if items is None:
        return None
    return any(isinstance(item, dict) and item.get("code") == norm for item in items)
