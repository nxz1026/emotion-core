"""新浪行业分类源（免费，消耗 0 Wind 额度）。

用途：为 `stock_basic.industry` 回填行业名。

为什么不用东财 f100（`data/fill_stock_basic.py:33` 的 EM_FIELDS 里那个）一次拿全市场：
2026-10-12 实测本机出口（日本大阪 Oracle Cloud）对 `push2delay` 是**持久 502**
（连打 3 轮、3/3 全 502），编号镜像 `1./2./82.push2` 直接连接失败——已不是
`data/providers/eastmoney.py:73-79` 记的"间歇抖动"，是硬失败。同期新浪接口 100% 正常。

覆盖口径（2026-10-12 实测）：`newSinaHy.php` 返回 49 个行业节点、成分股合计
3035 只，**不覆盖全部 5221 只 A 股**；且这套是行情软件的实用分类，含"次新股"
这类非行业桶（已在 `_NON_INDUSTRY_NAMES` 剔除）。故它只承担第一段回填，
剩余缺口留给后续源（pytdx 通达信板块 / Wind 精准补）。

额度纪律：本模块只发新浪公开 HTTP 请求，**不消耗任何 Wind 额度**。
"""
from __future__ import annotations

import json
import logging
import re

import requests

from emotion_core.utils.config import CONFIG
from emotion_core.utils.errors import DataError, FetchError
from emotion_core.utils.fetch import fetch_json_list, retry_fetch

log = logging.getLogger("emotion_core.data.providers.sina_industry")

_FETCH_RETRY: int = getattr(CONFIG, "FETCH_RETRY", 3)
_TIME_SLEEP: float = getattr(CONFIG, "TIME_SLEEP", 0.5)

#: 行业节点清单。是 JS 变量赋值（GBK 编码），不是 JSON 接口。
_SINA_HY_LIST = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
#: 行业节点成分股。与 `eastmoney.py:80` 的 `_SINA_CLIST` 同一个接口，只换 node。
_SINA_HQ_NODE = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                 "Market_Center.getHQNodeData")
#: 与 `eastmoney.py:87` 的 `_SINA_HEADERS` 保持一致：缺 Referer 会被新浪拒。
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

#: 新浪把这几个也放进行业节点，但它们不是行业；落进 industry 会污染面板与诊断。
_NON_INDUSTRY_NAMES = frozenset({"次新股"})

#: `var S_Finance_bankuai_sinaindustry = {...};` —— 取等号右侧的对象字面量。
_JS_OBJECT = re.compile(r"=\s*(\{.*\})\s*;?\s*$", re.S)

#: 分页参数。全市场最大行业 247 只 → num=100 时 3 页足够；上限 60 页防死循环。
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_MAX_PAGES = 60


def _decode_gbk(resp: requests.Response) -> str:
    """新浪这个接口是 GBK：`resp.json()` 会报错、`resp.text` 会乱码。"""
    return resp.content.decode("gbk", errors="replace")


def fetch_industry_nodes() -> list[tuple[str, str, int]]:
    """行业节点清单 → ``[(节点码, 行业名, 家数)]``，已剔除非行业桶。

    Raises:
        FetchError: 网络/HTTP 失败（可重试）。
        DataError: 返回体不再是预期的对象字面量（协议变化，不重试）。
    """
    def _get() -> str:
        try:
            resp = requests.get(_SINA_HY_LIST, timeout=15, headers=_HEADERS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FetchError("新浪行业清单请求失败", exc) from exc
        return _decode_gbk(resp)

    text = retry_fetch(_get, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP)
    m = _JS_OBJECT.search(text)
    if m is None:
        raise DataError(f"新浪行业清单格式变化：取不到对象字面量（{len(text)} 字节）")
    try:
        raw = json.loads(m.group(1))
    except ValueError as exc:
        raise DataError(f"新浪行业清单非 JSON: {str(exc)[:60]}") from exc
    if not isinstance(raw, dict):
        raise DataError(f"新浪行业清单响应非 dict: {type(raw).__name__}")

    nodes: list[tuple[str, str, int]] = []
    for node, payload in raw.items():
        # 值形如 "new_blhy,玻璃行业,19,15.57,..." → 取第 2/3 段
        parts = str(payload).split(",")
        if len(parts) < 3:
            continue
        name = parts[1].strip()
        try:
            count = int(parts[2])
        except ValueError:
            continue
        if not node or not name or name in _NON_INDUSTRY_NAMES:
            continue
        nodes.append((node, name, count))
    return nodes


def fetch_industry_members(node: str, page_size: int = _DEFAULT_PAGE_SIZE,
                           max_pages: int = _DEFAULT_MAX_PAGES) -> list[str]:
    """单个行业节点的成分股代码（6 位）。

    `page_size` 是服务端单页上限（实测 100），不是要取的只数。
    """
    codes: list[str] = []
    for pn in range(1, max_pages + 1):
        rows = retry_fetch(
            fetch_json_list, _SINA_HQ_NODE,
            {"page": pn, "num": page_size, "sort": "symbol", "asc": 1, "node": node},
            fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP, headers=_HEADERS,
        )
        if not rows:
            break
        for r in rows:
            # 先判数字再补零。反过来（先 zfill 再判）会把空值变成 "000000"
            # 当成合法股票写进库 —— 这是本模块唯一会静默污染数据的地方。
            raw = str(r.get("code") or "").strip()
            if raw.isdigit() and 0 < len(raw) <= 6:
                codes.append(raw.zfill(6))
        if len(rows) < page_size:
            break
    else:
        log.warning("行业节点 %s 分页达上限 %d 页：取 %d 只", node, max_pages, len(codes))
    return codes


def fetch_industry_map() -> dict[str, str]:
    """``{6 位代码: 行业名}``。

    跨节点重复时**先到先得**（按节点清单顺序），保证结果稳定且一只股只归一个行业。
    """
    out: dict[str, str] = {}
    for node, name, count in fetch_industry_nodes():
        members = fetch_industry_members(node)
        if count and len(members) != count:
            log.info("行业 %s：清单 %d 只，实取 %d 只", name, count, len(members))
        for code in members:
            out.setdefault(code, name)
    return out
