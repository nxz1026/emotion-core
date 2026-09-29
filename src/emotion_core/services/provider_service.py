"""数据源提供商服务 facade。

本模块是 `data/providers/*` 的唯一对外服务接口。
`algorithms/` 需要访问数据源时，只经本服务，不直接 import `data/providers/*`，
遵守架构方向：algorithms → services → data。

Provider 实例采用函数内延迟 import，避免模块级循环依赖。
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

log = logging.getLogger("emotion_core.services.provider")


def get_provider(name: str):
    """按名称获取 Provider 实例。

    支持：eastmoney / pytdx / sina。
    """
    if name == "eastmoney":
        from emotion_core.data.providers.eastmoney import PROVIDER
        return PROVIDER
    if name == "pytdx":
        from emotion_core.data.providers.pytdx_provider import PROVIDER
        return PROVIDER
    if name == "sina":
        from emotion_core.data.providers.sina import PROVIDER
        return PROVIDER
    raise ValueError(f"未知 provider: {name}")


def fetch_daily_bars(
    provider_name: str,
    code: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """从指定 provider 获取日线数据。"""
    return get_provider(provider_name).fetch_daily_bars(code, start, end)


def get_provider_error():
    """返回 ProviderError 异常类（供 catch 使用）。"""
    from emotion_core.data.providers.base import ProviderError
    return ProviderError
