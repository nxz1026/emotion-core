"""新浪批量五档 provider（ADR 0003 E1/E4 第 1 条：本仓自采的对照/兜底实现）。

## 为什么本仓要有一份

ADR 0003 §E2 定的分工是「原始采集归 CPT，触发判据归本仓」。但那是在
**CPT 有稳定采集进程**的前提下。CPT 现状（2026-10-22 实测）：

- `cpt/adapters/a_share_public.py::SinaQuoteClient.fetch_quotes` 已经能批量取五档，
  但**没有落库、没有常驻进程、没有路由**（CPT 仓 `scripts/intraday_probe.py`
  头注写明「没有 systemd 单元、没有 cron、没有路由、没有表」）。
- 所以「读 CPT 的表」这条路径现在拿不到任何数据。

于是本仓留一份**纯 stdlib 的自采实现**：
1. 作为 CPT 采集器上线前的**生产路径**（否则整个盘中能力没有数据源）；
2. 作为 CPT 上线后的**对照实现**（两套口径必然打架，但只有对照才敢发现打架）。

## 为什么不直接 import CPT 的 Python 包

CPT 仓不在本仓 `pyproject.toml` 的依赖里，也不进本仓 venv —— 跨仓 import
会让部署依赖另一份 checkout，**本机一换目录就崩**。所以这里自己解析新浪响应，
格式与 CPT 逐字段对齐，并由 `tests/oracle/test_intraday_quote_vs_cpt.py`
逐条对账钉住。

## 契约（与 CPT `parse_sina_quotes` 一致）

1. **键是 6 位纯代码**（`000002`），不是 `sz000002`。
2. **缺档位是 `None` 不是 `0`**：停牌时新浪把价量一起填 0 ⇒ 整档 `None`。
   量本身是 0 保留 `0.0` —— 「没有这一档」与「挂 0 手」方向相反，不能混。
3. **超过 50 只自动切块**（`SINA_BATCH_LIMIT`），块间各自发请求。
4. **任何一段失败都抛 `ProviderError`**，不返回部分结果：批量采样本要么全拿到
   要么报警，静默返回一半会让「今天没触发」和「今天没采到」变成同一件事。
5. **不认识的代码整行不出现**在响应里 ⇒ 「请求 3 只回 2 行」是正常情况。

## 额度纪律

只打新浪公开 HTTP，**不消耗任何 Wind 额度**。新浪要求带 `Referer`，
否则 403（实测）。
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Final

from emotion_core.utils.errors import ProviderError

log = logging.getLogger("emotion_core.data.providers.sina_quote")

__all__ = ["SINA_BATCH_LIMIT", "SinaQuoteProvider", "normalize_code", "parse_sina_quotes"]

#: 新浪单次请求最多多少只代码。超过 50 只自己切块。
SINA_BATCH_LIMIT: Final[int] = 50

#: 新浪快照地址。
SINA_QUOTE_URL: Final[str] = "https://hq.sinajs.cn/list="

#: 默认超时（秒）。
DEFAULT_TIMEOUT: Final[float] = 10.0

#: 买/卖各几档。
_BID_LEVELS: Final[int] = 5
_ASK_LEVELS: Final[int] = 5

#: 响应行正则：`var hq_str_sz000002="…";`
_SINA_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r'var\s+hq_str_(?P<symbol>[a-z]{2}\d{6})="(?P<body>[^"]*)"',
    re.IGNORECASE,
)

#: 一手多少股。新浪的 volume 字段单位是**股**。
_SHARES_PER_LOT: Final[int] = 100


def normalize_code(code: str) -> str:
    """``600519`` / ``sh600519`` / ``600519.SH`` → ``sh600519``。

    **号段顺序敏感**（与 CPT `cpt/adapters/a_share_public.py::normalize_code`
    逐条一致，跨仓对账钉住）：北交所新号段 ``92/43/83/87/88`` 必须在
    ``9``（沪 B）之前判 —— 否则 ``920296`` 会被推成 ``sh920296``。
    CPT 仓 R17 修过同一个顺序 bug（`a_share_local._to_wind_code` 上真实存在）。

    Raises:
        ProviderError: 代码不是 6 位数字，或后缀无法识别。
    """
    raw = code.strip().lower()
    if "." in raw:
        body, _, suffix = raw.partition(".")
        if suffix not in ("sh", "sz", "bj"):
            # 未知后缀一律拒绝：静默丢掉 `.XX` 会去拉另一家交易所的同名代码。
            raise ProviderError(f"无法识别的交易所后缀：{code!r}（可选 SH / SZ / BJ）")
        raw = f"{suffix}{body}"
    if len(raw) == 6 and raw.isdigit():
        raw = f"__{raw}"  # 裸 6 位：市场待推断
    if len(raw) != 8 or not raw[2:].isdigit() or raw[:2] not in ("sh", "sz", "bj", "__"):
        raise ProviderError(f"无法识别的 A 股代码：{code!r}")
    market, digits = raw[:2], raw[2:]
    if market in ("sh", "sz", "bj"):
        return f"{market}{digits}"
    if digits.startswith(("92", "43", "83", "87", "88")):
        market = "bj"
    elif digits[0] in ("6", "9", "5"):
        market = "sh"
    elif digits[0] in ("0", "3", "2", "1"):
        market = "sz"
    else:
        raise ProviderError(f"无法从代码推断交易所：{code!r}")
    return f"{market}{digits}"


def _to_float(raw: str) -> float | None:
    """价格/量转 float；空串或非数一律 `None`（**不补 0**）。"""
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _price(raw: str) -> float | None:
    """**价格 <= 0 记 `None`** —— 停牌时新浪把价量一起填 0。

    量保留 `0.0`（「挂 0 手」是真的 0 手），价格记 `None`（「没有这一档」）。
    两者混起来会让「买盘消失」被读成「买一挂着 0 手」，方向完全相反。
    """
    value = _to_float(raw)
    if value is None or value <= 0:
        return None
    return value


def _level(price_raw: str, vol_raw: str) -> dict[str, float] | None:
    """一档成对视图。价格 <= 0 的整对置 `None`。"""
    price = _price(price_raw)
    vol = _to_float(vol_raw)
    if price is None:
        return None
    return {"price": price, "volume": 0.0 if vol is None else vol}


def _parse_row(symbol: str, fields: Sequence[str]) -> dict[str, Any]:
    """把一行 34 字段解析成 E3 形状的 quote。"""
    quote: dict[str, Any] = {
        "code": symbol,
        "name": fields[0],
        "open": _to_float(fields[1]),
        "prev_close": _to_float(fields[2]),
        "last": _to_float(fields[3]),
        "high": _to_float(fields[4]),
        "low": _to_float(fields[5]),
        "volume": _to_float(fields[8]),
        "amount": _to_float(fields[9]),
        "date": fields[30],
        "time": fields[31],
    }
    bids: list[dict[str, float] | None] = []
    asks: list[dict[str, float] | None] = []
    for level in range(_BID_LEVELS):
        vol_raw, price_raw = fields[10 + level * 2], fields[11 + level * 2]
        quote[f"bid{level + 1}_vol"] = _to_float(vol_raw)
        quote[f"bid{level + 1}_price"] = _price(price_raw)
        bids.append(_level(price_raw, vol_raw))
    for level in range(_ASK_LEVELS):
        vol_raw, price_raw = fields[20 + level * 2], fields[21 + level * 2]
        quote[f"ask{level + 1}_vol"] = _to_float(vol_raw)
        quote[f"ask{level + 1}_price"] = _price(price_raw)
        asks.append(_level(price_raw, vol_raw))
    quote["bids"] = bids
    quote["asks"] = asks
    return quote


def parse_sina_quotes(text: str, *, expected: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    """解析新浪批量快照响应 → ``{6位代码: quote}``。

    Args:
        text: 已解码的响应正文（GBK 由调用方解码）。
        expected: 本次请求的 symbol 列表。给了就在结尾校验「一只都没匹配上」
            就报错（说明上游没认这些代码，是异常不是正常）。

    Raises:
        ProviderError: 字段数不足（格式变了）或一只都没匹配上。
    """
    out: dict[str, dict[str, Any]] = {}
    matched: set[str] = set()
    for match in _SINA_LINE_RE.finditer(text):
        symbol = match.group("symbol").lower()
        body = match.group("body")
        matched.add(symbol)
        if not body:
            # 空 body = 停牌/未上市：整行跳过，不出 `{"last": 0}`。
            continue
        fields = body.split(",")
        if len(fields) < 32:
            raise ProviderError(f"新浪快照字段不足（{len(fields)}）：{symbol}")
        out[symbol[2:]] = _parse_row(symbol, fields)
    if expected and not (set(expected) & matched):
        raise ProviderError(
            f"新浪批量快照一只都没匹配上（请求 {len(expected)} 只，响应 {len(text)} 字符）"
        )
    return out


class SinaQuoteProvider:
    """新浪 ``hq.sinajs.cn`` 批量五档客户端。

    只提供**盘中快照**，不提供 K 线 —— 拿快照伪造日线会污染结构计算。
    """

    name = "sina_quote"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def fetch_quotes(self, codes: Sequence[str]) -> dict[str, dict[str, Any]]:
        """批量取五档，返回 ``{6位代码: quote}``。

        Raises:
            ProviderError: 任一段 HTTP 失败 / 响应体为空 / 字段不足。
                这里**不吞**：批量采样本要么全拿到要么报警。
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for code in codes:
            symbol = normalize_code(code)
            if symbol not in seen:
                seen.add(symbol)
                wanted.append(symbol)
        if not wanted:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(wanted), SINA_BATCH_LIMIT):
            chunk = wanted[start : start + SINA_BATCH_LIMIT]
            out.update(self._fetch_chunk(chunk))
        return out

    def _fetch_chunk(self, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        """取一块（<= `SINA_BATCH_LIMIT` 只）。"""
        headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        url = f"{SINA_QUOTE_URL}{','.join(symbols)}"
        # ⚠️ 必须用 Request 带 headers：``urlopen(url, ...)`` 不会带自定义头，
        # 新浪没 Referer 一律 403（2026-10-22 实测）。
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"新浪批量快照 HTTP {exc.code}：{len(symbols)} 只") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError(f"新浪批量快照不可达：{len(symbols)} 只（{exc}）") from exc
        text = raw.decode("gbk", "replace") if isinstance(raw, bytes) else raw
        return parse_sina_quotes(text, expected=symbols)
