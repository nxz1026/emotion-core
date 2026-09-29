"""emotion-core 全局异常定义。

将原本分散在 data.providers.base / services/ingest.py / data.providers.eastmoney.py
的异常类收归一处，消除模块间循环依赖。

分层语义（P3-4）：
- FetchError：网络/HTTP 层失败（超时、5xx、连接拒绝）——可重试
- DataError：协议/数据结构非法（非 JSON、非 dict、字段缺失）——重试无意义
- ProviderError：Provider 网络、解析或数据错误
"""
from __future__ import annotations


class ProviderError(Exception):
    """Provider 网络、解析或数据错误。"""


class FetchError(Exception):
    """网络/HTTP 层失败（超时、5xx、连接拒绝）——可重试。"""

    def __init__(self, msg, cause):
        super().__init__(f"{msg}: {cause}")
        self.cause = cause


class DataError(Exception):
    """协议/数据结构非法（非 JSON、非 dict、字段缺失）——重试无意义。"""
