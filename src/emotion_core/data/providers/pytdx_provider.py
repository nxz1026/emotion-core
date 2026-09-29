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
import time
from datetime import date

import pandas as pd

from emotion_core.data.providers.base import (
    DailyBarProvider,
    DataError,
    ProviderError,
    normalize_frame,
)

# TDX 服务器列表（与 lkl config.py 保持一致）
_TDX_HOSTS = [("180.153.18.170", 7709), ("119.147.212.81", 7709),
              ("124.160.88.183", 7709), ("218.85.139.19", 7727),
              ("115.238.90.165", 7709)]

# 重试参数（值同 lkl config.py: FETCH_RETRY=3, TIME_SLEEP=0.5）
_FETCH_RETRY = 3
_TIME_SLEEP = 0.5

_tls = threading.local()


def _retry(fn, *args, **kw):
    """带重试调用：_FETCH_RETRY 次，指数退避；末次异常上抛。"""
    last: Exception | None = None
    for i in range(_FETCH_RETRY):
        try:
            time.sleep(_TIME_SLEEP)
            return fn(*args, **kw)
        except DataError:
            raise                             # 协议变化，重试无意义
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            last = exc
            if "只能获取最近" in str(exc):  # EM 池历史硬限，重试无意义
                raise
            time.sleep(2 ** i)
    raise last  # type: ignore[misc]


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
        page = _retry(_tdx_bars, market, code, offset)
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
