"""pytdx 通达信日线备源（从 lkl 移植）。

移植来源：
- `_tdx_all_bars` 及 TDX 连接池辅助函数 ← lkl/services/ingest.py
- `PytdxProvider` 类 ← lkl/providers/pytdx_provider.py

去掉的 lkl 依赖：
- `from lkl.services import ingest` → 直接用本模块的 `_tdx_all_bars`
- `from lkl.providers.base import ...` → `from emotion_core.data.providers.base import ...`
- `config.FETCH_RETRY`/`config.TIME_SLEEP` → 模块级常量（值同 lkl config.py）
"""
from __future__ import annotations

import threading
import threading
from datetime import date

import pandas as pd

from emotion_core.data.providers.base import (
    DailyBarProvider,
    DataError,
    ProviderError,
    normalize_frame,
)
from emotion_core.utils.fetch import retry_fetch

# TDX 服务器列表（可通过环境变量 TDX_HOSTS 覆盖，格式 "host:port,host:port"）
import os as _os

def _parse_tdx_hosts() -> list:
    env_raw = _os.environ.get("TDX_HOSTS", "").strip()
    if not env_raw:
        return [("180.153.18.170", 7709), ("119.147.212.81", 7709),
                ("124.160.88.183", 7709), ("218.85.139.19", 7727),
                ("115.238.90.165", 7709)]
    try:
        pairs = [p.strip() for p in env_raw.split(",") if p.strip()]
        return [(h, int(p)) for h, p in (pair.rsplit(":", 1) for pair in pairs)]
    except (ValueError, IndexError):
        return [("180.153.18.170", 7709), ("119.147.212.81", 7709),
                ("124.160.88.183", 7709), ("218.85.139.19", 7727),
                ("115.238.90.165", 7709)]

_TDX_HOSTS = _parse_tdx_hosts()

_TLS = threading.local()


def _tdx():
    """线程内长连接（懒建，断线由 _tdx_bars 丢弃重建）。"""
    api = getattr(_tls, "api", None)
    if api is not None:
        return api
    from pytdx.hq import TdxHq_API
    api = TdxHq_API()
    for host, port in _TDX_HOSTS:
        try:
            if api.connect(host, port, time_out=5):
                _tls.api = api
                return api
        except Exception:  # noqa: BLE001
            continue
    raise ConnectionError("TDX 全部服务器不可达")


def _tdx_bars(market: int, code: str, offset: int = 0) -> list:
    """单页日K（TDX 单次上限 800 根，offset 往历史翻页）。"""
    try:
        return _tdx().get_security_bars(9, market, code, offset, 800)  # 9=日K
    except Exception:
        _tls.api = None  # 断线丢弃，下次重连
        raise


def _tdx_all_bars(market: int, code: str, until: date) -> list:
    """P1-7：分页拉全历史——从 offset 0 往前翻，直到首页早于 until
    （覆盖请求起点）或某页不足 800 根（到上市首日）。TDX 返回降序
    （最新在前），首页=最新页。"""
    out: list = []
    offset = 0
    while True:
        page = retry_fetch(_tdx_bars, market, code, offset, fetch_retry=3, time_sleep=0.5)
        if not page:
            break
        out.extend(page)
        oldest = min(r.get("datetime", "9999")[:10] for r in page)
        if len(page) < 800 or oldest <= until.isoformat():
            break
        offset += 800
    return out


class PytdxProvider:
    """复用 TDX 连接池，统一成交量为手、金额为元。"""

    name = "pytdx"

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            market = 1 if code.startswith("6") else 0
            bars = _tdx_all_bars(market, code, start)
            if not bars:
                return pd.DataFrame(columns=[])
            raw = pd.DataFrame(bars)
            raw["date"] = pd.to_datetime(raw["datetime"].str[:10]).dt.date
            raw = raw[(raw["date"] >= start) & (raw["date"] <= end)]
            raw = raw.rename(columns={"vol": "volume"})
            raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce")
            raw["pre_close"] = raw["close"].astype(float).shift(1)
            raw["turnover_rate"] = None
            return normalize_frame(raw, code)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"pytdx: {str(exc)[:100]}") from exc


PROVIDER: DailyBarProvider = PytdxProvider()
