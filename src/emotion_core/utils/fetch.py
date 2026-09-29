"""网络请求工具函数（共享）。

抽取 services/ingest.py、data/providers/eastmoney.py、data/providers/pytdx_provider.py
中重复的 _retry / _get_json / _get_json_list / _positive，统一维护。
"""
from __future__ import annotations

import time
from typing import Any

import requests

from emotion_core.utils.errors import DataError, FetchError

__all__ = [
    "fetch_json",
    "fetch_json_list",
    "is_positive",
    "retry_fetch",
]

_DEFAULT_UA = "Mozilla/5.0"


def _do_request(url, *, params=None, timeout=15, headers=None):
    """HTTP GET 包装（便于测试 mock）。"""
    return requests.get(url, params=params, timeout=timeout, headers=headers)


def fetch_json(
    url: str,
    params: dict,
    *,
    timeout: int = 15,
    headers: dict | None = None,
) -> dict:
    """GET + JSON 解析（dict 响应）。

    边界分层：
    - 网络故障 → FetchError（可重试）
    - 协议变化 → DataError（不重试，直接上抛）
    """
    try:
        resp = _do_request(
            url,
            params=params,
            timeout=timeout,
            headers=headers or {"User-Agent": _DEFAULT_UA},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError("HTTP 请求失败", exc) from exc
    try:
        j = resp.json()
    except ValueError as exc:
        raise DataError(f"非 JSON 响应: {str(exc)[:60]}") from exc
    if not isinstance(j, dict):
        raise DataError(f"响应非 dict: {type(j).__name__}")
    return j


def fetch_json_list(
    url: str,
    params: dict,
    *,
    timeout: int = 20,
    headers: dict | None = None,
) -> list:
    """GET + JSON 解析（list 响应，如新浪 Market_Center）。"""
    try:
        resp = _do_request(
            url,
            params=params,
            timeout=timeout,
            headers=headers or {"User-Agent": _DEFAULT_UA},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError("新浪请求失败", exc) from exc
    try:
        j = resp.json()
    except ValueError as exc:
        raise DataError(f"非 JSON 响应: {str(exc)[:60]}") from exc
    if not isinstance(j, list):
        raise DataError(f"响应非 list: {type(j).__name__}")
    return j


def is_positive(v: Any) -> bool:
    """数值可转且 > 0。新浪 JSON 里数字是字符串（"1685.000"）。"""
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def retry_fetch(
    fn,
    *args,
    fetch_retry: int = 3,
    time_sleep: float = 0.5,
    **kw,
):
    """带重试调用：fetch_retry 次，指数退避；末次异常上抛。

    - DataError 立即上抛（协议变化，重试无意义）
    - "只能获取最近" 硬限错误立即上抛
    """
    last: Exception | None = None
    for i in range(fetch_retry):
        try:
            time.sleep(time_sleep)
            return fn(*args, **kw)
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            last = exc
            if "只能获取最近" in str(exc):
                raise
            time.sleep(2 ** i)
    raise last  # type: ignore[misc]
