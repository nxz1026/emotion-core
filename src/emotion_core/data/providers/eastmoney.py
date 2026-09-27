"""东财数据源 Provider：网络层 + EastmoneyProvider。

从 lkl 移植（只读源 /home/ubuntu/DSH/longkonglong）：
- 网络层函数逐字照 lkl/services/ingest.py：`_get_json` / `_get_json_list` /
  `_positive` / `_reject_zero_prices` / `_sina_clist` / `_em_clist_paged` /
  `_em_clist` / `_em_data_date_em` / `_sina_quote_payload` / `_sina_index_volume` /
  `_sina_quote_minutes` / `_sina_session_closed` / `_sina_last_bar_date` /
  `_sina_data_date` / `_downgrade_open_session` / `em_data_date` / `_retry` /
  `FetchError` / `DataError`。
- `EastmoneyProvider` 逐字照 lkl/providers/eastmoney.py。

lkl 依赖替换：
- `from lkl import config` + `config.FETCH_RETRY/TIME_SLEEP` → 模块级
  `_FETCH_RETRY` / `_TIME_SLEEP`（= getattr(CONFIG, name, default)，与
  emotion_core/services/ingest.py 的 `_cfg` 同款：CONFIG 尚未收录这两个键，
  缺省值取 lkl config.py 原值 3 / 0.5；若日后 CONFIG 收录则自动生效）。
- `from lkl.services import ingest` + `ingest.*` → 本模块函数直接调用
  （lkl 里那次延迟 import 是为避开 ingest→providers 循环引用，本文件同处
  一层，无循环）。
- `from lkl.providers.base import ...` → `emotion_core.data.providers.base`。
- `from lkl.utils import db` → 不需要（providers 不直接操作 DB）。

源实测记录（本机海外 IP，2026-08-31 / 2026-09-24）逐字保留在注释里，勿改。
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from emotion_core.data.providers.base import (
    BAR_COLS,
    DailyBarProvider,
    ProviderError,
    normalize_frame,
)
from emotion_core.utils.config import CONFIG

log = logging.getLogger(__name__)

#: emotion-core CONFIG 尚未收录这两个抓取参数，缺省值与 lkl config.py 对齐
#: （FETCH_RETRY=3、TIME_SLEEP=0.5）；若日后 CONFIG 收录同名键则自动生效。
_FETCH_RETRY: int = getattr(CONFIG, "FETCH_RETRY", 3)
_TIME_SLEEP: float = getattr(CONFIG, "TIME_SLEEP", 0.5)

# EM 行情列表：akshare spot 分页(56请求+sign)在本机被 reset，自建单页封装。
# push2 对本 IP 间歇 502/302；直连 302 目标 push2delay（延迟行情，盘后与终值一致）。
# fs = 深主板/创业板/沪主板/科创板。
_EM_CLIST = "https://push2delay.eastmoney.com/api/qt/clist/get"
_EM_FS_ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_SPOT_MAP = {"f12": "code", "f17": "open", "f15": "high", "f16": "low",
             "f2": "close", "f18": "pre_close", "f5": "volume",
             "f6": "amount", "f8": "turnover_rate"}


class FetchError(Exception):
    """网络/HTTP 层失败（超时、5xx、连接拒绝）——可重试。"""

    def __init__(self, msg, cause):
        super().__init__(f"{msg}: {cause}")
        self.cause = cause


class DataError(Exception):
    """协议/数据结构非法（非 JSON、非 dict、字段缺失）——重试无意义。"""


def _get_json(url: str, params: dict) -> dict:
    """GET+JSON 解析。P3-4：边界分层——网络故障→FetchError（可重试），
    协议变化→DataError（不重试直接上抛），调用层可按类型决策。"""
    try:
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
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


# ── 新浪全市场源：EM clist 高频 502 时的回退 ────────────────────────────
# 2026-09-24 实测：本机出口是日本大阪 Oracle Cloud，EM push2 / push2delay 的
# clist 路径**高频失败**——连打 20 次仅 4 次 200（16 次 502 + 1 次连接失败，
# 失败率 80%）。是间歇性抖动而非硬封锁（换域名/路径/补全浏览器头均无效，
# 502 由东财自身 nginx 返回），但配 FETCH_RETRY=3 后单次调用仍有约
# 0.8³≈51% 概率三次全挂。新浪 / 腾讯 HTTP 源同期 100% 正常，故守卫与快照
# 双线回退到新浪——回退的目的是**让守卫可靠**，不是"EM 永远不通"。
_SINA_CLIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData")
_SINA_QUOTE = "https://hq.sinajs.cn/list="
# 新浪指数日K：最后一根的日期 = 最近一个「已完成」交易日。用于盘前判定
# （hq.sinajs.cn 的日期字段是「当前交易日」，盘前已翻新但无成交）。
_SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "CN_MarketData.getKLineData")
_SINA_HEADERS = {"User-Agent": "Mozilla/5.0",
                 "Referer": "https://finance.sina.com.cn"}
#: A股收盘时间（北京时间），表示成「距零点的分钟数」。行情时间 >= 它才算
#: 「当日已收完」。**故意用整数而非 datetime.time**：本模块 `import time`
#: （标准库）给 `_retry` 用，若 `from datetime import time` 会把它覆盖掉，
#: `time.sleep` 随即 AttributeError，整条管道直接死。
_SINA_SESSION_END_MIN = 15 * 60
_SINA_TO_EM = {"code": "f12", "name": "f14", "open": "f17", "high": "f15",
               "low": "f16", "trade": "f2", "settlement": "f18",
               "volume": "f5", "amount": "f6", "turnoverratio": "f8"}


def _get_json_list(url: str, params: dict) -> list:
    """GET+JSON 解析，收数组响应（新浪 Market_Center 返回 list，EM 的 _get_json 只收 dict）。"""
    try:
        resp = requests.get(url, params=params, timeout=20, headers=_SINA_HEADERS)
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


def _positive(v) -> bool:
    """数值可转且 > 0。新浪 JSON 里数字是字符串（"1685.000"）。"""
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def _reject_zero_prices(rows: list[dict]) -> None:
    """拒绝「全零行情」入库——盘前快照的最后一道防线。

    2026-09-24 实测：北京 09:05（未开盘）新浪 hs_a 返回 100/100 行
    trade=0 open=0 volume=0，仅 settlement（昨收）有值。这类快照若入库，
    daily_bar 的 open/high/low/close 会静默全变 0——**不报错，只是数字错**。
    故过半无有效收盘价即判异常，按协议层错误上抛（重试无意义，直接暴露）。

    这里只兜底、不承担正常分支：正常盘后快照 0 行无价。
    """
    if not rows:
        return
    priced = sum(1 for r in rows if _positive(r.get("f2")))
    if priced * 2 < len(rows):
        raise DataError(
            f"新浪全市场快照价格异常：{len(rows) - priced}/{len(rows)} 行收盘价为空或 0"
            "（疑似盘前或接口异常），拒绝入库")


def _sina_clist(fields: list[str], page: int = 100,
                max_pages: int = 80) -> pd.DataFrame:
    """新浪全市场行情分页拉取 → DataFrame（列名为 EM f 码，供 _em_clist 回退）。

    口径对齐（2026-09-24 实测）：新浪 node=hs_a 共 5567 条，含北交所 346 条；
    剔除 bj* 后 5221 条，与 EM _EM_FS_ALL_A（深主板+创业板+沪主板+科创板，
    本就不含北交所）的 total 完全一致——故此处过滤 bj*，不改变原股票池口径。

    单位对齐（这是本回退最容易踩的坑）：新浪 volume 单位是「股」，EM f5 是
    「手」——必须 /100，否则全市场成交量静默放大 100 倍，不报错、只是数字错。
    三方实测 600519.SH 2026-09-23：新浪 3098122 股 = 腾讯 30981 手 =
    Wind 3098122（Wind 元数据 unit=股）。新浪 amount 单位是「元」，与 EM f6
    一致，不换算。
    """
    want = [f for f in fields if f in _SINA_TO_EM.values()]
    src = {v: k for k, v in _SINA_TO_EM.items()}
    out: list[dict] = []
    pn = 1
    while True:
        params = {"page": pn, "num": page, "sort": "symbol", "asc": 1, "node": "hs_a"}
        rows = _retry(_get_json_list, _SINA_CLIST, params)
        if not rows:
            break
        for r in rows:
            if str(r.get("symbol") or "").startswith("bj"):
                continue                      # 北交所不在原股票池口径内
            item = {f: r.get(src[f]) for f in want}
            if item.get("f5") is not None:
                try:
                    item["f5"] = float(item["f5"]) / 100.0     # 股 → 手
                except (TypeError, ValueError):
                    item["f5"] = None
            out.append(item)
        if len(rows) < page or pn >= max_pages:
            if pn >= max_pages:
                log.warning("新浪 clist 分页达上限 %d 页：取 %d 条", max_pages, len(out))
            break
        pn += 1
    _reject_zero_prices(out)
    return pd.DataFrame(out)


def _em_clist_paged(fs: str, fields: list[str], page: int = 100,
                    max_pages: int = 80) -> pd.DataFrame:
    """EM clist 分页拉取（服务端单页上限100）→ DataFrame（字段名为 f 码）。

    V1（二轮审计）：分页上限防 total 异常时的死循环。
    V1-fix（09-02 实跑教训）：上限 20 页=2000 条 < 全市场 5556 只——
    防死循环护栏截断了正常快照（当日 daily_bar 1912/5200 行，缺数
    被报告「数据质量」段如实报警）。护栏应远高于正常需求：A股全市场
    约 5600 只需 56 页，上限 80 页（8000 条）留增长余量，防 total
    返回异常大值（如 9999999）的死循环。
    """
    out: list[dict] = []
    pn, total = 1, None
    while True:
        params = {"pn": pn, "pz": page, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                  "fid": "f12", "fs": fs, "fields": ",".join(fields)}
        data = _retry(_get_json, _EM_CLIST, params).get("data") or {}
        diff = data.get("diff") or []
        out.extend(diff)
        total = data.get("total") or 0
        if not diff or len(out) >= total or pn >= max_pages:
            if pn >= max_pages and len(out) < total:
                log.warning("EM clist 分页达上限 %d 页：取 %d/%d 条",
                            max_pages, len(out), total)
            return pd.DataFrame(out)
        pn += 1


def _em_clist(fs: str, fields: list[str], page: int = 100,
              max_pages: int = 80) -> pd.DataFrame:
    """全市场行情列表：优先 EM，网络层失败（FetchError）回退新浪同口径源。

    V2（09-24）：EM clist 对本机 IP 高频 502（实测失败率 80%），此前守卫先崩、
    快照随后崩，整条 daily 链自 09-18 起静默停在 09-17。现按异常类型分流：
    FetchError（网络/5xx，可重试已耗尽）→ 回退新浪；DataError（协议变化，
    重试无意义）仍按原语义上抛，不掩盖真实的接口结构变更。
    """
    try:
        return _em_clist_paged(fs, fields, page, max_pages)
    except FetchError as exc:
        log.warning("EM clist 不可用（%s），回退新浪全市场源", exc)
        return _sina_clist(fields, page, max_pages)


def _em_data_date_em() -> date:
    """EM 行情最新数据日期（f124 时间戳→北京时区）。

    V1（二轮审计）：改为行数分布判断——此前 po=1+fid=f12 恒取"代码最大"
    那只票的时间戳，该票长期停牌则整条 daily 链天天静默 SKIP；现拉一页
    100 只取 f124 众数日期，单票停牌不再影响守卫。
    """
    params = {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
              "fid": "f12", "fs": _EM_FS_ALL_A, "fields": "f12,f124"}
    diff = (_retry(_get_json, _EM_CLIST, params).get("data")
            or {}).get("diff") or []
    if not diff:
        raise RuntimeError("EM clist 守卫请求返回空")
    dates = [datetime.fromtimestamp(int(d["f124"]),
                                    timezone(timedelta(hours=8))).date()
             for d in diff if d.get("f124")]
    if not dates:
        raise RuntimeError("EM clist 守卫请求 f124 全空")
    # 众数：抗单票停牌。P3（三轮审计）：max(set,...) 在并列时按 set 迭代
    # 序取值——结果不稳定。并列（多日期同票数）时取最新并告警。
    cnt = Counter(dates)
    top_n = max(cnt.values())
    winners = sorted(d for d, c in cnt.items() if c == top_n)
    if len(winners) > 1:
        log.warning("EM 日期计数并列 %s（数据不一致），取最新 %s",
                    winners, winners[-1])
    return winners[-1]


def _sina_quote_payload(text: str) -> list[str] | None:
    """取 hq.sinajs.cn 回报里引号内的字段数组。"""
    m = re.search(r'="([^"]*)"', text)
    return m.group(1).split(",") if m else None


def _sina_index_volume(text: str) -> float | None:
    """从 hq.sinajs.cn 指数回报里取成交量（下标 8，单位：手）。

    取不到返回 None（不据此判定盘前，避免误伤正常路径）。
    """
    parts = _sina_quote_payload(text)
    if not parts or len(parts) < 9:
        return None
    try:
        return float(parts[8] or 0)
    except ValueError:
        return None


def _sina_quote_minutes(text: str) -> int | None:
    """从 hq.sinajs.cn 指数回报里取行情时间（下标 31，如 "09:40:26"）→ 距零点分钟数。

    取不到返回 None。盘前该字段是**墙上时间**（实测 09:05 回报里是 09:05:21），
    收盘后停在最后一笔（15:00:0x），故可用于区分盘中与已收盘。
    """
    parts = _sina_quote_payload(text)
    if not parts or len(parts) < 32:
        return None
    try:
        hh, mm, *_ = parts[31].split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, IndexError):
        return None


def _sina_session_closed(vol: float | None, minutes: int | None) -> bool | None:
    """当日行情是否已收完 —— 区分「当前交易日」与「最近已完成交易日」。

    True  = 已收完（有成交量，且行情时间 >= 收盘 15:00）
    False = 未收完（盘前成交量 0；或盘中，时间 < 15:00）
    None  = 两个信号都取不到，判不了 —— 调用方保持原行为，**不引入 SKIP 风险**

    阈值取 15:00 而非留余量：收盘后行情时间就停在 15:00:0x，若取 15:05 会把
    「已收盘」误判成「未收盘」，从而把判定权交给日K接口——一旦该接口盘后
    更新滞后，守卫就会天天 SKIP（正是刚修好的静默冻结）。风险方向反了。
    """
    if vol is None and minutes is None:
        return None
    if vol == 0:
        return False                      # 当日尚无成交（盘前/集合竞价前）
    if minutes is not None and minutes > 0 and minutes < _SINA_SESSION_END_MIN:
        return False                      # 盘中：半截行情不能当已完成
    return True


def _sina_last_bar_date(symbol: str = "sh000001") -> date:
    """新浪指数日K最后一根的日期（= 最近一个已完成交易日）。

    与 hq.sinajs.cn 的日期字段不同：后者是「当前交易日」，盘前（约 09:00）
    就已翻到新交易日，而那时全市场 trade/open/volume 全为 0。本函数只认
    已经产生了 K 线的那一天。
    """
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": 2}
    rows = _retry(_get_json_list, _SINA_KLINE, params)
    days = [r.get("day") for r in rows if r.get("day")]
    if not days:
        raise RuntimeError("新浪日K守卫响应为空")
    return date.fromisoformat(str(days[-1])[:10])


def _sina_data_date() -> date:
    """新浪行情日期——EM 守卫不可用时的回退（上证指数回报里的日期字段）。

    语义对齐（2026-09-24 实测，这是本回退最隐蔽的坑）：EM `f124` 是
    「最近**已完成**交易日」，而 hq.sinajs.cn 的日期字段是「**当前**交易日」
    ——盘前约 09:00 就已翻到新交易日，此时全市场 trade/open/volume 全为 0。
    若直接返回该日期，daily.sh 守卫（`TODAY != D` 才 SKIP）会判定「数据已更新」
    而放行，把全零行情灌进库。可达路径：lkl-daily.timer 带 Persistent=true，
    17:20 关机、次日盘前开机 → systemd 立即补跑 → 放行。

    故先判「当日是否已收完」（成交量 + 行情时间，见 `_sina_session_closed`）：
    未收完（盘前零成交 / 盘中半截）→ 改取日K最后一根日期。收盘后成交量巨大、
    时间停在 15:00:0x，判为已收完，仍走原路径，因此不依赖日K接口的盘后更新
    时点（若依赖它，一旦更新滞后就会天天 SKIP，正是刚修好的静默冻结）。

    日期字段下标不固定（sh000001 与 sz399001 字段数不同），故用正则匹配
    yyyy-MM-dd，不按下标取。
    """
    try:
        resp = requests.get(_SINA_QUOTE + "sh000001", timeout=15,
                            headers=_SINA_HEADERS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError("新浪守卫请求失败", exc) from exc
    text = resp.content.decode("gbk", "replace")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if not m:
        raise RuntimeError("新浪守卫响应无日期字段")
    d = date.fromisoformat(m.group(1))
    vol, mins = _sina_index_volume(text), _sina_quote_minutes(text)
    if _sina_session_closed(vol, mins) is False:
        last = _sina_last_bar_date()
        log.warning("新浪指数当日行情未收完（成交量=%s 时间分钟=%s），"
                    "日期字段 %s 改用最近已完成交易日 %s", vol, mins, d, last)
        return last
    return d


def _downgrade_open_session(d: date) -> date:
    """EM 返回的日期若属「尚未收完」的当日，退回最近已完成交易日。

    EM 的 `f124` 与新浪日期字段同样是「**当前**交易日」：盘中就已翻到当天。
    实测 EM 仍有约 20% 概率应答（见模块内注释），所以只在新浪分支做收盘
    判定不够——EM 一应答，守卫照样在盘中放行，把当日半截行情入库。半截
    数据比盘前零值更隐蔽：数字看起来是合理的。

    收盘状态从新浪上证指数快照读（与用哪个行情源无关）。判不了（None）时
    沿用 d，不引入 SKIP 风险。
    """
    try:
        resp = requests.get(_SINA_QUOTE + "sh000001", timeout=15,
                            headers=_SINA_HEADERS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("收盘状态探测失败（%s），沿用行情源日期 %s", exc, d)
        return d
    text = resp.content.decode("gbk", "replace")
    closed = _sina_session_closed(_sina_index_volume(text),
                                  _sina_quote_minutes(text))
    if closed is not False:
        return d
    last = _sina_last_bar_date()
    log.warning("行情源日期 %s 属当日但未收完（盘前/盘中），改用最近已完成交易日 %s",
                d, last)
    return last


def em_data_date() -> date:
    """行情最新**已完成**交易日的日期——cron 交易日守卫（EM 优先，新浪回退）。

    V2（09-24）：EM clist 对本机 IP 高频 502（实测失败率 80%），守卫此前直接
    让 daily.sh exit 2，整条 daily 链自 09-18 起不再运行。现双源：EM 网络失败
    或返回空 → 回退新浪；两源都失败才抛错，守卫仍能如实报 FAIL（不静默放行）。

    两个源返回的都是「**当前**交易日」，故都必须过 `_downgrade_open_session`
    这道收盘判定——否则盘前/盘中补跑会把零值或半截行情灌进库。
    """
    try:
        d = _em_data_date_em()
    except (FetchError, RuntimeError) as exc:
        log.warning("EM 守卫不可用（%s），回退新浪交易日源", exc)
        return _sina_data_date()      # 内部已做收盘判定
    return _downgrade_open_session(d)


def _retry(fn, *args, **kw):
    """带重试调用：FETCH_RETRY 次，指数退避；末次异常上抛。"""
    last: Exception | None = None
    for i in range(_FETCH_RETRY):
        try:
            time.sleep(_TIME_SLEEP)
            return fn(*args, **kw)
        except DataError:
            raise                             # P3-4：协议变化，重试无意义
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            last = exc
            if "只能获取最近" in str(exc):  # EM 池历史硬限，重试无意义
                raise
            time.sleep(2 ** i)
    raise last  # type: ignore[misc]


class EastmoneyProvider:
    """将现有东财 clist 快照映射为 daily_bar 日线。"""

    name = "eastmoney"

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            if em_data_date() != end:
                return pd.DataFrame(columns=BAR_COLS)
            raw = _em_clist(_EM_FS_ALL_A, list(_SPOT_MAP))
            raw = raw.rename(columns=_SPOT_MAP)
            raw = raw[raw["code"].astype(str).str.zfill(6) == code]
            raw["date"] = end
            return normalize_frame(raw, code)
        except Exception as exc:
            raise ProviderError(f"eastmoney: {str(exc)[:100]}") from exc


PROVIDER: DailyBarProvider = EastmoneyProvider()
