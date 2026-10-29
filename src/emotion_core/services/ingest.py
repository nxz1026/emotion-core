"""T1 数据拉取：TDX pytdx(回填) + EM clist(列表/快照) + AKShare EM 三池 → DB。

语义逐字照搬 lkl/services/ingest.py（873 行，本文件分 3 段搬运：第 1 段=源 1-300 行，
第 2 段=源 300-560 行，第 3 段=源 560-873 行；第 3 段已在位）。差异仅 IO 适配：

- `from lkl import config` + `config.X` → 模块级 `_cfg("X", default)`（= getattr(CONFIG,
  name, default)，CONFIG 尚未收录的键取 lkl config.py 原值：INGEST_FALLBACK_CHAIN=[]、
  FETCH_RETRY=3、TIME_SLEEP=0.5、POOL_RECENT_DAYS=30、UPSERT_BATCH=1000）。
- `from lkl.utils import db` + `db.query_df/execute` → `emotion_core.utils.db`。
- providers 四处 import 保持函数内延迟 import，落到 `emotion_core.data.providers.*`
  （第 8 层未搬，与 doctor.py 同款：仅调用时 ImportError，不影响 import 期）。
  例外：EM clist + 新浪回退源的常量与取数函数（`_EM_CLIST` / `_SPOT_MAP` /
  `_em_clist` 等）2026-10-13 去重后为模块级 import——它们原先在本模块整段复刻了
  一份，`retry_fetch` 漏传 `fetch_retry/time_sleep` 造成静默漂移。
- `_retry` 源在 453-467 行（属第 2 段），但本段 `_sina_clist`/`_em_clist_paged`/
  `_em_data_date_em` 与第 3 段 `fetch_limit_pool`/`fetch_hot_snapshot` 都依赖它，
  故提前收进本段——这是唯一定义处，第 2 段不得重复定义。
- 源文件顶层 `import akshare as ak` 保留：EM clist 走自建封装（源实测 akshare spot
  分页被 reset），ak 仅服务 EM 三池。

源实测记录（本机海外 IP，2026-08-31 / 2026-09-24）逐字保留在注释里，勿改。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

import akshare as ak
import pandas as pd
import requests

from emotion_core.data.providers.eastmoney import (
    _EM_CLIST,
    _EM_FS_ALL_A,
    _SPOT_MAP,
    _em_clist,
)
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import execute, query_df, transaction
from emotion_core.utils.fetch import fetch_json, retry_fetch

log = logging.getLogger("emotion_core.ingest")


def _cfg(name: str, default):
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值默认。"""
    return getattr(CONFIG, name, default)


_INGEST_FALLBACK_CHAIN: list[str] = _cfg("INGEST_FALLBACK_CHAIN", [])
_POOL_RECENT_DAYS: int = _cfg("POOL_RECENT_DAYS", 30)
_UPSERT_BATCH: int = _cfg("UPSERT_BATCH", 1000)
_FETCH_RETRY: int = _cfg("FETCH_RETRY", 3)  # 单次拉取失败重试次数
_TIME_SLEEP: float = _cfg("TIME_SLEEP", 0.5)  # 抓取调用间隔（秒）


BAR_COLS = [
    "code",
    "date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
]


def _provider_registry() -> dict:
    """惰性构建日线 Provider 注册表，避免默认路径新增导入副作用。"""
    from emotion_core.data.providers.eastmoney import PROVIDER as eastmoney
    from emotion_core.data.providers.pytdx_provider import PROVIDER as pytdx
    from emotion_core.data.providers.sina import PROVIDER as sina

    return {p.name: p for p in (eastmoney, pytdx, sina)}


def _fallback_rows(codes: list[str], start: date, end: date, primary: set[str]) -> list[tuple]:
    """按配置链补齐主源缺口；失败或不合格 Provider 只记录 warning。"""
    from emotion_core.data.providers.base import ProviderError, valid_frame

    rows: list[tuple] = []
    for name in _INGEST_FALLBACK_CHAIN:
        provider = _provider_registry().get(name)
        if provider is None:
            log.warning("未知日线备源 %s，跳过", name)
            continue
        for code in codes:
            if code in primary:
                continue
            try:
                frame = provider.fetch_daily_bars(code, start, end)
            except ProviderError as exc:
                log.warning("日线备源 %s %s 失败：%s", name, code, exc)
                continue
            if not valid_frame(frame):
                log.warning("日线备源 %s %s 口径不合格，跳过", name, code)
                continue
            rows.extend(tuple(r) for r in frame.itertuples(index=False, name=None))
            primary.add(code)
    return rows


def _daily_rows_from_em(frame: pd.DataFrame, trade_date: date) -> list[tuple]:
    """将东财快照转为 daily_bar 行，保持既有字段和单位。"""
    out = frame.rename(columns=_SPOT_MAP).copy()
    for col in set(_SPOT_MAP.values()) - {"code"}:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close", "pre_close"])
    out["date"] = trade_date
    return [
        tuple(getattr(r, col) for col in BAR_COLS) for r in out[BAR_COLS].itertuples(index=False)
    ]


# EM 行情列表 / 新浪回退源 的常量与取数实现（`_EM_CLIST`、`_EM_FS_ALL_A`、
# `_SPOT_MAP`、`_SINA_CLIST`、`_SINA_TO_EM`、`_SINA_UNIT_SCALE`，以及
# `_sina_clist` / `_em_clist_paged` / `_em_clist` / `_reject_zero_prices`
# 四个函数）已由 `emotion_core.data.providers.eastmoney` 单点定义，本模块只 import
# 再导出。2026-10-13 去重：这里曾整段复刻一份，其中 `retry_fetch` 漏传
# `fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP`，与 providers 静默漂移
# （当前 CONFIG 未收录这两个键、默认值恰好相同 3/0.5，故未造成实际差异，
#  但一旦有人调 CONFIG 就会只在快照路径上失效）。

# FetchError / DataError 的唯一定义处已下移到 `data.providers.base`（2026-09-29
# 架构守护：services 允许依赖 data，反向不允许）。本模块原先在此 import 再导出，
# 2026-10-13 去重后 EM / 新浪 clist 的取数实现整体移入 providers，这里不再捕获
# 这两个异常；需要时直接从 `emotion_core.utils.errors` 取。
# ── 新浪全市场源：EM clist 高频 502 时的回退 ────────────────────────────
# 2026-09-24 实测：本机出口是日本大阪 Oracle Cloud，EM push2 / push2delay 的
# clist 路径**高频失败**——连打 20 次仅 4 次 200（16 次 502 + 1 次连接失败，
# 失败率 80%）。是间歇性抖动而非硬封锁（换域名/路径/补全浏览器头均无效，
# 502 由东财自身 nginx 返回），但配 FETCH_RETRY=3 后单次调用仍有约
# 0.8³≈51% 概率三次全挂。新浪 / 腾讯 HTTP 源同期 100% 正常，故守卫与快照
# 双线回退到新浪——回退的目的是**让守卫可靠**，不是"EM 永远不通"。
# 单位对齐（volume 股→手、mktcap/nmc 万元→元）见 providers/eastmoney.py 的
# `_SINA_UNIT_SCALE`——加 `_SINA_TO_EM` 映射必须同时加单位表。


def _em_data_date_em() -> date:
    """EM 行情最新数据日期（f124 时间戳→北京时区）。

    V1（二轮审计）：改为行数分布判断——此前 po=1+fid=f12 恒取"代码最大"
    那只票的时间戳，该票长期停牌则整条 daily 链天天静默 SKIP；现拉一页
    100 只取 f124 众数日期，单票停牌不再影响守卫。
    """
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f12",
        "fs": _EM_FS_ALL_A,
        "fields": "f12,f124",
    }
    diff = (retry_fetch(fetch_json, _EM_CLIST, params).get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError("EM clist 守卫请求返回空")
    dates = [
        datetime.fromtimestamp(int(d["f124"]), timezone(timedelta(hours=8))).date()
        for d in diff
        if d.get("f124")
    ]
    if not dates:
        raise RuntimeError("EM clist 守卫请求 f124 全空")
    # 众数：抗单票停牌。P3（三轮审计）：max(set,...) 在并列时按 set 迭代
    # 序取值——结果不稳定。并列（多日期同票数）时取最新并告警。
    from collections import Counter

    cnt = Counter(dates)
    top_n = max(cnt.values())
    winners = sorted(d for d, c in cnt.items() if c == top_n)
    if len(winners) > 1:
        log.warning("EM 日期计数并列 %s（数据不一致），取最新 %s", winners, winners[-1])
    return winners[-1]


_UPSERT_ALLOWED_TABLES = {"daily_bar", "limit_pool_em", "hot_rank"}


def _upsert_rows(
    table: str, columns: Sequence[str], rows: list[tuple], conflict_cols: Sequence[str], conn=None
) -> int:
    """通用幂等 upsert，返回写入行数（复刻 lkl utils/db.upsert_rows 写语义）。

    W3：可传入 transaction() 的共享连接，与 DELETE 同事务（防半新半旧）。
    """
    if table not in _UPSERT_ALLOWED_TABLES:
        raise ValueError(f"upsert 拒绝未知表: {table!r}")
    if not rows:
        return 0
    # 行宽校验防错位静默写脏数据
    if len(rows[0]) != len(columns):
        raise ValueError(f"upsert {table}：行宽 {len(rows[0])} != 列数 {len(columns)}")
    if table == "daily_bar":
        # 2026-10-17：日线多一道写库前自检（形状 / 顺延 / 整批自洽）。
        # 放在这里而不是各调用方，是因为本函数是 daily_bar 在 services 侧的
        # 唯一出口——快照、备源、逐股回填三条路都从这走。见 data/bar_guard.py。
        from emotion_core.data.bar_guard import check_bars

        check_bars(rows)
    updates = [c for c in columns if c not in conflict_cols]
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in updates)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict_cols)}) "
        + (f"DO UPDATE SET {set_clause}" if set_clause else "DO NOTHING")
    )
    # 统一消毒：NaN / ±inf → NULL（PG numeric 可存 NaN/Inf，会击穿下游 ::bigint 转换）
    clean = [
        tuple(None if isinstance(v, float) and not math.isfinite(v) else v for v in r) for r in rows
    ]

    def _write(c):
        with c.cursor() as cur:
            for i in range(0, len(clean), _UPSERT_BATCH):
                cur.executemany(sql, clean[i : i + _UPSERT_BATCH])

    if conn is not None:
        _write(conn)
    else:
        with transaction() as c:
            _write(c)
    return len(clean)


def snapshot_daily(trade_date: date) -> int:
    """每日增量主路径：EM clist 全市场快照 → daily_bar（盘后运行）。

    V1（二轮审计）：守卫内移——此前只在 daily.sh 外部守卫，绕开脚本手动
    补数会把当前实时行情盖上任意传入日期（污染历史 + 派生交易日历）。
    2026-10-17 加第二道：守卫取数与正式取数是**两次请求**，中间可能漂移；
    故再按快照自带的 f124 复核一次（源自己的日期）。
    """
    from emotion_core.data.providers.eastmoney import em_data_date, snapshot_data_date

    em_date = em_data_date()
    if em_date != trade_date:
        raise ValueError(
            f"snapshot 守卫：EM 最新数据日期 {em_date} ≠ 传入 {trade_date}，"
            "拒绝把陈旧行情写进目标日期（daily.sh 外补数请核对日期）"
        )
    if not _INGEST_FALLBACK_CHAIN:
        raw = _em_clist(_EM_FS_ALL_A, [*_SPOT_MAP, "f124"])
        src_date = snapshot_data_date(raw)
        if src_date is not None and src_date != trade_date:
            raise ValueError(
                f"snapshot 守卫：快照自带日期 {src_date} ≠ 传入 {trade_date}，拒绝按请求日期盖章"
            )
        df = raw.rename(columns=_SPOT_MAP)
        for col in set(_SPOT_MAP.values()) - {"code"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "pre_close"])
        rows = [
            (
                r.code,
                trade_date,
                r.open,
                r.high,
                r.low,
                r.close,
                r.pre_close,
                r.volume,
                r.amount,
                r.turnover_rate,
            )
            for r in df.itertuples(index=False)
        ]
        n = _upsert_rows("daily_bar", BAR_COLS, rows, ["code", "date"])
        log.info("snapshot %s 入库 %d 行", trade_date, n)
        return n
    try:
        em = _em_clist(_EM_FS_ALL_A, [*_SPOT_MAP, "f124"])
        src_date = snapshot_data_date(em)
        if src_date is not None and src_date != trade_date:
            raise ValueError(
                f"snapshot 守卫：快照自带日期 {src_date} ≠ 传入 {trade_date}，拒绝按请求日期盖章"
            )
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("EM 日线批量失败：%s", exc)
        em = pd.DataFrame()
    primary_rows = _daily_rows_from_em(em, trade_date) if not em.empty else []
    primary_codes = {row[0] for row in primary_rows}
    codes = list(query_df("SELECT code FROM stock_basic")["code"])
    rows = primary_rows + _fallback_rows(codes, trade_date, trade_date, primary_codes)
    if not rows:
        raise RuntimeError("日线主源和备源均返回空数据")
    n = _upsert_rows("daily_bar", BAR_COLS, rows, ["code", "date"])
    log.info("snapshot %s 入库 %d 行", trade_date, n)
    return n


_POOL_COLS = [
    "date",
    "code",
    "pool_type",
    "name",
    "cont_days_em",
    "bomb_times",
    "first_seal",
    "last_seal",
    "turnover_rate",
]
# 池函数 + 源列→目标列映射（各池字段名不同，缺列补 None）
_POOLS = {
    "ZT": (
        ak.stock_zt_pool_em,
        {
            "名称": "name",
            "连板数": "cont_days_em",
            "炸板次数": "bomb_times",
            "首次封板时间": "first_seal",
            "最后封板时间": "last_seal",
            "换手率": "turnover_rate",
        },
    ),
    "ZB": (
        ak.stock_zt_pool_zbgc_em,
        {
            "名称": "name",
            "炸板次数": "bomb_times",
            "首次封板时间": "first_seal",
            "换手率": "turnover_rate",
        },
    ),
    "DT": (
        ak.stock_zt_pool_dtgc_em,
        {
            "名称": "name",
            "连续跌停": "cont_days_em",
            "开板次数": "bomb_times",
            "最后封板时间": "last_seal",
            "换手率": "turnover_rate",
        },
    ),
}


def _src(mapping: dict, record: dict, target: str):
    src = next((s for s, t in mapping.items() if t == target), None)
    return record.get(src) if src else None


def fetch_limit_pool(trade_date: date) -> int:
    """三池原样入库 → limit_pool_em（ZT封住/ZB炸板/DT跌停，对账+情绪计数源）。

    A7（审计 P1-8 修复）：逐池失败仍落可用部分，但**三池全败即抛 RuntimeError**
    ——此前全挂返回 0，daily.sh 的 set -e 拦不住，会拿空池照常出报告，
    reconcile 还把空池当"无差异 ✓"。全败属数据源事故，宁可中断也不出假报告。

    ★2026-09-29 补第二层：原先只数 `fails`（抛异常的池），**三池都「返回空 DataFrame」
    不算失败**。而 EM 涨停池接口的实达历史窗口比 `_POOL_RECENT_DAYS` 记的 30 日更短
    （实测 2026-09-28 回拉最早只到 2026-09-04，09-01/02/03 三池全 0 行且不抛异常，
    而那三天各有 5203+ 根 bar、6~7 只一字板，确属交易日）。于是「空池」被静默当成
    同步成功——与 A7 要防的正是同一类假报告，只是从「拉取报错」换成了「拉回空表」。
    现将「返回 0 行」也计为该池失败。
    """
    ds = trade_date.strftime("%Y%m%d")
    total, fails, empties = 0, [], []
    for ptype, (fn, mapping) in _POOLS.items():
        try:
            df = retry_fetch(fn, date=ds, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP)
        except Exception as exc:  # noqa: BLE001
            log.warning("pool %s %s 拉取失败: %s", ptype, ds, exc)
            fails.append(ptype)
            continue
        n = _upsert_pool(df, trade_date, ptype, mapping)
        total += n
        if n == 0:
            empties.append(ptype)
            log.warning("pool %s %s 返回 0 行（日期超出东财池实达窗口？）", ptype, ds)
    dead = set(fails) | set(empties)
    if len(dead) == len(_POOLS):
        raise RuntimeError(
            f"东财三池全部无数据({ds})：失败={sorted(fails)} 空={sorted(empties)}"
            f"——放弃出报告，待重跑"
        )
    if dead:
        log.warning(
            "池 %s 无数据（失败=%s 空=%s），仅 %d 行入库（部分对账）",
            sorted(dead),
            sorted(fails),
            sorted(empties),
            total,
        )
    return total


def _upsert_pool(df, trade_date: date, ptype: str, mapping: dict) -> int:
    """单池 DataFrame → limit_pool_em upsert。

    V1（二轮审计）：非数字代码丢弃并告警——此前 "" zfill(6)="000000"
    会通过 isdigit 校验写进库（合法但虚假的持仓）。
    """
    if df is None or df.empty:
        return 0
    rows, bad = [], 0
    for d in df.to_dict("records"):
        code = str(d.get("代码", "")).zfill(6)
        if not code.isdigit() or code == "000000":
            bad += 1
            continue
        rows.append(
            (
                trade_date,
                code,
                ptype,
                str(_src(mapping, d, "name") or ""),
                _to_int(_src(mapping, d, "cont_days_em")),
                _to_int(_src(mapping, d, "bomb_times")),
                str(_src(mapping, d, "first_seal") or ""),
                str(_src(mapping, d, "last_seal") or ""),
                _to_float(_src(mapping, d, "turnover_rate")),
            )
        )
    if bad:
        log.warning("pool %s %s：%d 行代码无效被丢弃", trade_date, ptype, bad)
    # P1-9：同日同池先删后插（单事务原子替换）——旧版纯 upsert，
    # 源端减少的票（炸板回封后移出 ZB）会永久留库成幽灵行。
    with transaction() as conn:
        execute(
            "DELETE FROM limit_pool_em WHERE date=%s AND pool_type=%s",
            (trade_date, ptype),
            conn=conn,
        )
        n = _upsert_rows(
            "limit_pool_em", _POOL_COLS, rows, ["date", "code", "pool_type"], conn=conn
        )
    return n


def _to_int(v):
    try:
        return None if v is None or pd.isna(v) else int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def verify_pre_close(trade_date: date) -> pd.DataFrame:
    """校验 pre_close 与上一交易日 close 一致（除权日会命中，人工判读）。"""
    return query_df(
        "SELECT d.code, d.pre_close, p.close AS prev_close "
        "FROM daily_bar d JOIN LATERAL ("
        "  SELECT close FROM daily_bar x"
        "  WHERE x.code = d.code AND x.date < d.date"
        "  ORDER BY x.date DESC LIMIT 1) p ON true "
        "WHERE d.date = %s AND abs(d.pre_close - p.close) > 0.011",
        (trade_date,),
    )


_HOT_COLS = ["date", "code", "rank"]
# akshare 封装在拉完排名后还要合并行情字段（本 IP 下返回非 JSON 崩），自建单请求绕开。
_EM_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_HOT_PAYLOAD = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}


def _hot_current() -> list[dict]:
    r = requests.post(_EM_RANK_URL, json=_HOT_PAYLOAD, timeout=15)
    return r.json()["data"]


def fetch_hot_snapshot(trade_date: date) -> int:
    """东财人气榜 top100 → hot_rank（每日盘后一次；sc=市场前缀+代码）。

    V1（二轮审计）：守卫内移，同 snapshot_daily——当前榜只对应最新交易日，
    不允许盖任意历史日期。
    """
    from emotion_core.data.providers.eastmoney import em_data_date

    em_date = em_data_date()
    if em_date != trade_date:
        raise ValueError(
            f"hot 守卫：EM 最新数据日期 {em_date} ≠ 传入 {trade_date}，"
            "人气榜为实时榜单，拒绝写历史日期"
        )
    rows = [
        (trade_date, d["sc"][2:].zfill(6), int(d["rk"]))
        for d in retry_fetch(_hot_current, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP)
        if len(d.get("sc", "")) >= 8
    ]
    n = _upsert_rows("hot_rank", _HOT_COLS, rows, ["date", "code"])
    log.info("hot snapshot %s：%d 行", trade_date, n)
    return n


def _em_symbol(code: str) -> str:
    p = "SH" if code[0] in "69" else "BJ" if code[0] in "48" else "SZ"
    return p + code


def hot_universe(lookback_start: date) -> list[str]:
    """回填票池：lookback 起有连板史 ∪ 出过信号的票（影子验证样本域）。

    ⚠️ 2026-10-26 减法审计曾把它当「0 消费者」删除，但
    ``scripts/backfill_hot_rank_r21.py`` 仍在用（脚本不在 src/tests 扫描范围内，
    审计漏了）。2026-10-29 审计恢复。
    """
    df = query_df(
        "SELECT DISTINCT code FROM derived_bar"
        " WHERE cont_days >= 2 AND date >= %s"
        " UNION SELECT DISTINCT code FROM signal ORDER BY 1",
        (lookback_start,),
    )
    return list(df["code"])


def fetch_hot_history(code: str) -> int:
    """单票人气榜排名史（约366日）→ hot_rank（影子验证回填源）。

    注：akshare 1.18.97 的 ``stock_hot_rank_detail_em`` 列赋值与当前 API 返回
    格式不兼容（API 改字段名，akshare 仍按旧列数强赋值），直接绕过 akshare
    调原始接口，按新字段名 ``calcTime`` / ``rank`` 入库。
    """
    import requests as _requests

    url_rank = "https://emappdata.eastmoney.com/stockrank/getHisList"
    payload = {
        "appId": "appId01",
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "",
        "srcSecurityCode": _em_symbol(code),
        "yearType": "5",
    }
    r = _requests.post(url_rank, json=payload, timeout=15)
    r.raise_for_status()
    data_json = r.json()
    rows = []
    for item in data_json.get("data", []):
        calc_time = item.get("calcTime")
        rk = _to_int(item.get("rank"))
        if calc_time is not None and rk is not None:
            rows.append((pd.to_datetime(calc_time).date(), code, rk))
    return _upsert_rows("hot_rank", _HOT_COLS, rows, ["date", "code"])
