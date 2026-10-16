"""巨潮资讯行业分类源（证监会分类标准，免费，消耗 0 Wind 额度）。

用途：为 `stock_basic.industry` 回填行业名。相对 `sina_industry.py`（新浪行业分类，
实测只覆盖 3035 只、且**完全不覆盖科创板**、创业板只覆盖三成），本模块**覆盖全市场**，
是 A 第二段的主源。

为什么逐股：巨潮批量接口 `p_stock2110` 不传 `scode` 时 `records=0`（2026-10-12 实测
空 / 缺参 / `all` 三种写法都是 0 条），只能一只一只查；代价实测 1.17s/只、20/20 成功。

为什么不用东财 f100 一次拿全市场：见 `sina_industry.py` 模块文档 —— 本机出口对
`push2delay` / `push2` 是**持久 502**，编号镜像 `1./82.push2` 直接断连，`push2his`
读超时，只有 `datacenter-web` 通但它不认 `/api/qt/clist/get` 路径。

口径：巨潮 `所属行业` 是证监会 2012 分类的**大类**（如"计算机、通信和其他电子设备
制造业"），比交易所官方列表的门类级（如"C 制造业"，全市场去重仅 19 个）细得多，
适合做面板展示与个股诊断。

额度纪律：本模块只发巨潮公开 HTTP 请求，**不消耗任何 Wind 额度**。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

import akshare as ak

from emotion_core.utils.config import CONFIG
from emotion_core.utils.errors import DataError, FetchError
from emotion_core.utils.fetch import retry_fetch

log = logging.getLogger("emotion_core.data.providers.cninfo_industry")

_FETCH_RETRY: int = getattr(CONFIG, "FETCH_RETRY", 3)
_TIME_SLEEP: float = getattr(CONFIG, "TIME_SLEEP", 0.5)

#: akshare `stock_profile_cninfo` 返回里的行业列（证监会大类）。
_INDUSTRY_COL = "所属行业"

#: 默认并发数。实测 4 并发稳定、无 429/封禁；再高收益递减（瓶颈在单只 1.17s 往返）。
DEFAULT_WORKERS = 4


def fetch_industry(code: str) -> str | None:
    """单只股票的证监会行业大类；该股确实无行业数据时返回 None。

    Raises:
        DataError: 响应结构变化（缺 `所属行业` 列），不重试。
        FetchError: 网络/解析失败，重试 `_FETCH_RETRY` 次后仍失败则上抛。
    """

    def _get() -> str | None:
        try:
            df = ak.stock_profile_cninfo(symbol=code)
        except Exception as exc:  # noqa: BLE001 —— akshare 内部异常类型不定
            raise FetchError(f"巨潮公司概况请求失败: {code}", exc) from exc
        if df is None or len(df) == 0:
            raise FetchError(f"巨潮公司概况空响应: {code}", ValueError("records=0"))
        if _INDUSTRY_COL not in df.columns:
            raise DataError(f"巨潮公司概况缺列 {_INDUSTRY_COL}: {list(df.columns)[:8]}")
        value = df.iloc[0].get(_INDUSTRY_COL)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        text = str(value).strip()
        return text or None

    return retry_fetch(_get, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP)


def fetch_industry_map(
    codes: Sequence[str],
    *,
    workers: int = DEFAULT_WORKERS,
    progress_every: int = 200,
) -> dict[str, str]:
    """批量取行业 → ``{6 位代码: 行业名}``。

    单只失败只记 warning 并跳过，**不中断整轮回填**（这类回填跑 20 分钟以上，
    让一只退市股把整轮打掉不可接受）。返回字典只含取到值的股票，调用方据此
    与入参代码集合求差即得缺口。

    Args:
        codes: 待查代码（6 位）。调用方负责去重。
        workers: 并发数，见 `DEFAULT_WORKERS`。
        progress_every: 每处理这么多只打一条 INFO 进度（0 = 不打）。
    """
    out: dict[str, str] = {}
    failed: list[str] = []
    done = 0

    def _one(code: str) -> tuple[str, str | None]:
        try:
            return code, fetch_industry(code)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring：单只失败不拖垮整轮
            log.warning("巨潮行业取数失败 %s: %s", code, str(exc)[:80])
            return code, None

    total = len(codes)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        # pool.map 保序且惰性消费，进度是按完成批次推进的
        for code, industry in pool.map(_one, codes):
            done += 1
            if industry:
                out[code] = industry
            else:
                failed.append(code)
            if progress_every and done % progress_every == 0:
                log.info(
                    "巨潮行业进度 %d/%d（成功 %d，未取到 %d）", done, total, len(out), len(failed)
                )

    if failed:
        log.warning("巨潮行业未取到 %d 只（前 5：%s）", len(failed), failed[:5])
    return out
