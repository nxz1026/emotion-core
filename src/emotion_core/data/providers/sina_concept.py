"""新浪概念板块源（免费，消耗 0 Wind 额度）。

用途：`theme_tag`（逐票题材标签）与 `theme_spot`（每日概念板块热点榜）的**唯一数据源**。
这是「题材 / 热点」两类 gap 的补口——此前 `theme_service` 走 NullProvider 直接
`return 0`，`theme_tag` / `theme_group` 长期各 0 行（2026-10-14 审计）。

为什么不用东财概念板块：`push2` 概念接口（`m:90 t:3`）在本机出口是**持久 502**
（2026-10-14 实测 3/3 全 502），与 `providers/sina_industry.py:8-12` 记的同一坑；
同花顺 `stock_board_concept_name_ths` 有 375 个概念但 akshare 1.18.97 缺成分股接口。
新浪这两个节点在本机 100% 可用（实测）。

接口（均为 GBK / JS 变量赋值，不是 JSON 接口）：

1. 概念节点清单 ``newFLJK.php?param=class``：``var S_Finance_bankuai_class = {...}``，
   值形如 ``"gn_hwqc,华为汽车,97,22.03,-0.58,-2.559,1673795867,25497037032,
   sz002886,4.210,22.030,0.890,沃特股份"``。实测 **175 个概念**，与 akshare
   ``stock_sector_spot('概念')`` 同源同量。
2. 概念成分股 ``Market_Center.getHQNodeData``（与 `sina_industry.py` 同一接口，只换 node）。

**历史 PIT 不可得**（2026-10-14 用户已拍板接受，见 docs/04-数据源层讨论.md:171,175）：
这两个接口只返回**当前**名单与**当前**涨幅，无法按历史日期回放。故本源的产物是
「从接入日起逐日累积的快照」，不回填历史日期——回填会把今天的概念成分当成昨天的，
制造出不存在的历史（这也是 `theme_service` 拒绝写空/拒绝盖历史的原因）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests

from emotion_core.utils.config import CONFIG
from emotion_core.utils.errors import DataError, FetchError
from emotion_core.utils.fetch import fetch_json_list, retry_fetch

log = logging.getLogger("emotion_core.data.providers.sina_concept")

_FETCH_RETRY: int = getattr(CONFIG, "FETCH_RETRY", 3)
_TIME_SLEEP: float = getattr(CONFIG, "TIME_SLEEP", 0.5)

#: 概念节点清单。`param=class` 才是概念；`param=gn` 返回的是行业节点（同新浪文件里的旧命名）。
_SINA_CONCEPT_LIST = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"
#: 概念成分股。与 `sina_industry.py` / `eastmoney.py` 同一个接口，只换 node。
_SINA_HQ_NODE = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
#: 与 `sina_industry.py` 的 `_HEADERS` 一致：缺 Referer 会被新浪拒。
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

#: `var S_Finance_bankuai_class = {...};` —— 取等号右侧的对象字面量。
_JS_OBJECT = re.compile(r"=\s*(\{.*\})\s*;?\s*$", re.S)

_DEFAULT_PAGE_SIZE = 100
_DEFAULT_MAX_PAGES = 60

#: 源标识：写进 `theme_tag.source` / `theme_spot.source`，与默认值 'wind' 区分开。
SOURCE = "sina_concept"


@dataclass(frozen=True)
class ConceptNode:
    """一个概念板块节点（清单行）。"""

    node: str
    name: str
    member_count: int
    pct_chg: float | None
    amount: float | None
    leading_code: str | None
    leading_name: str | None


def _decode_gbk(resp: requests.Response) -> str:
    """新浪这两个接口是 GBK：`resp.json()` 会报错、`resp.text` 会乱码。"""
    return resp.content.decode("gbk", errors="replace")


def _to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _node_code(raw: str) -> str | None:
    """领涨股 ``sz002886`` → ``002886``；非 8 位带前缀形态一律丢弃。"""
    s = str(raw or "").strip().lower()
    if len(s) >= 8 and s[:2] in {"sh", "sz", "bj"} and s[2:8].isdigit():
        return s[2:8]
    return None


def fetch_concept_nodes() -> list[ConceptNode]:
    """概念节点清单（按新浪返回顺序，稳定）。

    Raises:
        FetchError: 网络/HTTP 失败（可重试）。
        DataError: 返回体不再是预期的对象字面量（协议变化，不重试）。
    """

    def _get() -> str:
        try:
            resp = requests.get(_SINA_CONCEPT_LIST, timeout=15, headers=_HEADERS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FetchError("新浪概念清单请求失败", exc) from exc
        return _decode_gbk(resp)

    text = retry_fetch(_get, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP)
    m = _JS_OBJECT.search(text)
    if m is None:
        raise DataError(f"新浪概念清单格式变化：取不到对象字面量（{len(text)} 字节）")
    try:
        raw = json.loads(m.group(1))
    except ValueError as exc:
        raise DataError(f"新浪概念清单非 JSON: {str(exc)[:60]}") from exc
    if not isinstance(raw, dict):
        raise DataError(f"新浪概念清单响应非 dict: {type(raw).__name__}")

    nodes: list[ConceptNode] = []
    for node, payload in raw.items():
        parts = str(payload).split(",")
        if len(parts) < 8:
            continue
        name = parts[1].strip()
        code = str(parts[0]).strip()
        if not code or not name or not node:
            continue
        try:
            count = int(parts[2])
        except ValueError:
            count = 0
        nodes.append(
            ConceptNode(
                node=node,
                name=name,
                member_count=count,
                pct_chg=_to_float(parts[5]),
                amount=_to_float(parts[7]),
                leading_code=_node_code(parts[8]) if len(parts) > 8 else None,
                leading_name=parts[12].strip() if len(parts) > 12 else None,
            )
        )
    if not nodes:
        raise DataError("新浪概念清单解析出 0 个概念（格式变化）")
    return nodes


def fetch_concept_members(
    node: str, page_size: int = _DEFAULT_PAGE_SIZE, max_pages: int = _DEFAULT_MAX_PAGES
) -> list[str]:
    """单个概念节点的成分股代码（6 位）。`page_size` 是服务端单页上限（实测 100）。"""
    codes: list[str] = []
    for pn in range(1, max_pages + 1):
        rows = retry_fetch(
            fetch_json_list,
            _SINA_HQ_NODE,
            {"page": pn, "num": page_size, "sort": "symbol", "asc": 1, "node": node},
            fetch_retry=_FETCH_RETRY,
            time_sleep=_TIME_SLEEP,
            headers=_HEADERS,
        )
        if not rows:
            break
        for r in rows:
            # 先判数字再补零：反过来会把空值变成 "000000" 写进库（sina_industry 同款坑）。
            raw = str(r.get("code") or "").strip()
            if raw.isdigit() and 0 < len(raw) <= 6:
                codes.append(raw.zfill(6))
        if len(rows) < page_size:
            break
    else:
        log.warning("概念节点 %s 分页达上限 %d 页：取 %d 只", node, max_pages, len(codes))
    return codes


def fetch_concept_map(
    nodes: list[ConceptNode] | None = None,
) -> tuple[list[ConceptNode], dict[str, list[ConceptNode]]]:
    """``(节点清单, {6 位代码: [所属节点…]})``。

    一只股可属多个概念（与 `algorithms/theme.py` 的「一股可属多题材」口径一致），
    故值是**列表**且按清单顺序去重（先到先得，结果稳定可复现）。
    """
    nodes = fetch_concept_nodes() if nodes is None else nodes
    out: dict[str, list[ConceptNode]] = {}
    for n in nodes:
        members = fetch_concept_members(n.node)
        if n.member_count and len(members) != n.member_count:
            log.info(
                "概念 %s(%s)：清单 %d 只，实取 %d 只", n.name, n.node, n.member_count, len(members)
            )
        for code in members:
            bucket = out.setdefault(code, [])
            if n not in bucket:
                bucket.append(n)
    log.info("新浪概念：%d 个节点，覆盖 %d 只个股", len(nodes), len(out))
    return nodes, out
