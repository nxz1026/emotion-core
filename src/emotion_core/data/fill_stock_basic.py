"""填充 stock_basic：从东财/新浪全市场列表拉取 code/name/行业/市值/流通股本，幂等 upsert。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_stock_basic

数据源：emotion_core.data.providers.eastmoney._em_clist（EM 优先，502 回退新浪）。
口径与 daily_bar 一致：_EM_FS_ALL_A = 深主板+创业板+沪主板+科创板（不含北交所）。

注意：_em_clist 的 fields 需含 f2（收盘价），否则新浪回退分支的
_reject_zero_prices 守卫会因「无价格」误判盘前快照而拒绝入库。

审核文档 §9 第 11 条：`diagnose_service` 查询 `industry` / `market_cap`，而
stock_basic 既无列也无数据源。现补列并在入库时写入：

- ``industry`` ← f100 行业名。**新浪回退源确实没有**（新浪只给证监会门类级的
  `所属行业`，粒度太粗，故不映射）→ 一律「缺 → 保留库内旧值」。
- ``market_cap`` ← f20 总市值（元）。**注意 A+B 股公司**（如 000016 深康佳A）
  这里取到的是 A+B 合计总市值，比「深交所 A股总股本 × 价」大；`float_shares`
  不受影响，因为 f21 只含 A 股流通市值。
- ``float_shares`` ← f21 流通市值（元）÷ 价格，本地反推（零额外请求）。价格取
  f2 收盘价；停牌股 f2 = 0，退到 f18 昨收。
- ``is_st`` ← **名称前缀**（`ST` / `*ST`，先剥掉 `XD` / `XR` / `DR` 除权标记）。
  东财 clist 不直接给风险警示标志，而设计上的填充口
  `ref_security_status.populate_is_st_from_wind()` 是**逐股烧 Wind 配额**的路径；
  简称前缀是同一事实的免费来源。2026-10-13 前该列全表 false（198 只 `ST`/`*ST`
  股一个没标），而它会被喂进个股诊断的 LLM prompt（`llm/prompts/stock.md:11`

f20/f21 是 2026-10-12 才接入 `_SINA_TO_EM` 的（配 `_SINA_UNIT_SCALE` 做
万元→元换算）。此前它们在回退源下缺列，而 upsert 又是 COALESCE 不覆写，于是
「首次即 NULL 就永远 NULL」——本机 EM 持续 502 时 `market_cap` / `float_shares`
全表 5221 行皆为 NULL，正是此故。所有可缺列统一走
``COALESCE(EXCLUDED.x, stock_basic.x)``，不用 NULL 覆写已有事实。

`in_market`（覆盖率门槛的分母，见 `services/coverage.py`）在 upsert 时置 true——
本列表是「当前在市全市场」列表。**2026-10-13 订正**：原文写「退市股不在本列表
里」，与实测不符——东财 `_EM_FS_ALL_A` 的板块过滤并不排除已退市证券，EM 恢复
可用后一次抓取就夹带了 334 只（`f100='-'`，如 PT金田A / 国华退 / 退市紫晶）。
它们没有行情、不会进 `daily_bar`，却会被置成 `in_market=true`，把覆盖率硬门槛
（`CONFIG.MIN_COVERAGE=0.90`，`services/coverage.py`）的分母从 5223 抬到 5562，
覆盖率上限 1.0 → 0.939、安全垫 9.8% → 3.9%。故现在在 `_rows_from_frame` 里按
`f100='-'` 显式丢弃。**退市不反扫**依然成立：不会把库内已有的退市股改回 false，
待单独工单做退市扫描。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import Any

import pandas as pd

from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.data.fill_stock_basic")

# f12=代码 f14=名称 f2=收盘价（护栏 + 反推流通股本用，不入库）
# f18=昨收（停牌股 f2=0 时当价格用） f100=行业
# f20=总市值(元) f21=流通市值(元，仅用于反推 float_shares)
EM_FIELDS = ["f12", "f14", "f2", "f18", "f100", "f20", "f21"]

#: (code, name, industry, market_cap, float_shares, is_st)
Row = tuple[str, str, str | None, float | None, float | None, bool]


def _as_float(v: Any) -> float | None:
    """None / NaN / 不可解析 → None。

    pandas 会把整列的 None 变成 NaN（回退源缺列与空值都走这里）→ 必须显式识别，
    否则 NaN 会被当有效值写进库。判据用 ``pd.isna`` 而**不是** ``isinstance(v,
    float)``：源列的 dtype 可能是 float32/float16，也可能是 ``Decimal('NaN')``，
    它们都不是 ``float`` 实例却同样是缺失值（2026-10-13 实测 ``np.float32('nan')``
    会原样返回 nan 并作为 PG ``NaN`` 入库，``COALESCE`` 拦不住）。
    """
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):  # pd.isna(数组) 返回数组 → 真值判断抛 ValueError
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_text(v: Any) -> str | None:
    """None / NaN / 全空白 → None，否则 strip 后的字符串。

    判据同 `_as_float`。用 ``str(v or "")`` 是不行的：``np.float32('nan')`` 是真值，
    ``str()`` 会得到字面量 ``'nan'`` 并当作有效名称/代码写进库。
    """
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return str(v).strip() or None


#: 行情口径的除权除息标记，会**叠在** `ST` 前面（`XD*STxx`），判断前必须剥掉。
_ST_EX_PREFIXES = ("XD", "XR", "DR")
#: 风险警示标记。`SST` / `S*ST` 是「未股改 + ST」的老写法（已近乎绝迹，留着不亏）；
#: 全角 `＊` 一并认，免得哪天东财换了写法就静默漏判。
_ST_MARKERS = ("ST", "*ST", "SST", "S*ST", "＊ST")


def _is_st_name(name: str) -> bool:
    """简称是否带风险警示标记（零额度，不用 Wind）。

    实测 198/5228 命中，与 `stock_basic` 里的 `ST`/`*ST` 名单逐只吻合、零歧义。
    """
    s = name
    for _ in range(2):  # 最多两层除权标记
        for d in _ST_EX_PREFIXES:
            if s.startswith(d):
                s = s[len(d) :]
                break
        else:
            break
    return s.startswith(_ST_MARKERS)


def _float_shares_from(rec: dict, cols: set[str]) -> float | None:
    """流通股本 = f21 流通市值（元）÷ 价格；任一无效或价格非正则回 None。

    价格优先取 f2（当日收盘价）；**停牌股 f2 = 0**（2026-10-12 实测有 8 只，
    daily_bar 里 close/volume 也全为 0，所以没有别的兜底源），此时退到 f18（昨收）。
    停牌不改变股本，而 Sina/EM 的 f21 本身就是按停牌前最后收盘价算的，故相除仍得
    正确的股数。f2 有值时一律用 f2，正常股行为不变。

    f21 是快照值（最新交易日），故本列同样是**快照语义**：每次回填覆盖为当日股本，
    与「DDL 只有单列、无日期」一致，不做历史回溯。除权当日会有偏差，量级正确。
    """
    if "f21" not in cols:
        return None
    cap = _as_float(rec.get("f21"))
    if cap is None or cap <= 0:
        # cap<=0 与 None 同样作废：写 0 会被 upsert 的 COALESCE 当成有效值，反把
        # 库内已有的正确股数覆盖成 0.0（COALESCE 只拦 NULL，不拦 0）。
        return None
    price = _as_float(rec.get("f2")) if "f2" in cols else None
    if price is None or price <= 0:
        price = _as_float(rec.get("f18")) if "f18" in cols else None
    if price is None or price <= 0:
        return None
    return cap / price


#: 东财用它标记「已退市 / 无行业归属」的证券（f100 的字面值）。见 `_rows_from_frame`。
_DELISTED_INDUSTRY = "-"


def _rows_from_frame(raw: pd.DataFrame) -> list[Row]:
    """DataFrame（EM/Sina 同形，列名 f 码）→ 行元组；缺列取 None，不抛。

    回退源（新浪）只有 `_SINA_TO_EM` 收录的字段：f100（行业名）必然缺列 → None，
    由 upsert 的 COALESCE 保留旧值；f20/f21 现已收录，故总市值与流通股本在回退源
    下同样有值。一律用 `record.get` 而非属性访问，缺列即 None。

    **在市过滤（2026-10-13）**：东财 `_EM_FS_ALL_A` 的板块过滤不排除已退市证券，
    退市股会以 `f100='-'` 夹带进来（实测 5562 行里 334 行如此：PT金田A、国华退、
    退市紫晶…）。它们没有行情、不会进 `daily_bar`，却会被 upsert 置
    `in_market=true`；而 `in_market` 是 `services/coverage.py` 硬门槛的分母，
    会把覆盖率上限从 1.0 压到 5223/5562=0.939（门槛 0.90），安全垫 9.8%→3.9%。
    故 `f100` 明确为 `'-'` 的行直接丢弃，并记一行 info 便于对账。
    判据只认字面量 `'-'`：新浪回退源没有 f100 列，`record.get` 取到 None，不会
    误伤；新股尚未分类时 f100 是 NaN，也仍然保留（`.get` 走 `_as_text` → None）。
    """
    cols = set(raw.columns)
    rows: list[Row] = []
    dropped = 0
    for rec in raw.to_dict("records"):
        name = _as_text(rec.get("f14")) or ""
        code = (_as_text(rec.get("f12")) or "").zfill(6)
        # zfill 会把空串/无意义短码补成 '000000'，必须显式挡掉后再用 isdigit 校验，
        # 否则会静默写入假代码；同批出现两条同码时 INSERT ... ON CONFLICT 还会抛
        # PG「cannot affect row a second time」导致整批失败。
        if code == "000000" or len(code) != 6 or not code.isdigit() or not name:
            continue
        industry = _as_text(rec.get("f100")) if "f100" in cols else None
        if industry == _DELISTED_INDUSTRY:
            dropped += 1
            continue
        market_cap = _as_float(rec.get("f20")) if "f20" in cols else None
        rows.append(
            (code, name, industry, market_cap, _float_shares_from(rec, cols), _is_st_name(name))
        )
    if dropped:
        log.info("过滤已退市证券 %d 只（f100='-'），保留 %d 只在市", dropped, len(rows))
    return rows


def fetch_clist() -> list[Row]:
    """全市场 code/name/行业/总市值（东财优先，新浪回退）。"""
    from emotion_core.data.providers.eastmoney import _EM_FS_ALL_A, _em_clist

    return _rows_from_frame(_em_clist(_EM_FS_ALL_A, EM_FIELDS))


def _market(code: str) -> str:
    """6 开头 → sh，0/3 开头 → sz。"""
    return "sh" if code.startswith("6") else "sz"


def upsert_stock_basic(rows: list[Row]) -> int:
    """幂等 upsert stock_basic（code/name/market/industry/market_cap/float_shares/is_st）。"""
    if not rows:
        return 0
    from psycopg import sql

    def _lit(v: Any):
        return sql.Literal(v) if v is not None else sql.SQL("NULL")

    with transaction() as conn:
        with conn.cursor() as cur:
            values = sql.SQL(", ").join(
                sql.SQL("({}, {}, {}, {}, {}, {}, {}, true)").format(
                    _lit(code),
                    _lit(name),
                    _lit(_market(code)),
                    _lit(industry),
                    _lit(market_cap),
                    _lit(float_shares),
                    _lit(is_st),
                )
                for code, name, industry, market_cap, float_shares, is_st in rows
            )
            query = sql.SQL(
                "INSERT INTO stock_basic (code, name, market, industry,"
                " market_cap, float_shares, is_st, in_market) VALUES {} "
                "ON CONFLICT (code) DO UPDATE SET "
                "name=EXCLUDED.name, market=EXCLUDED.market,"
                " industry=COALESCE(EXCLUDED.industry, stock_basic.industry),"
                " market_cap=COALESCE(EXCLUDED.market_cap, stock_basic.market_cap),"
                " float_shares=COALESCE(EXCLUDED.float_shares, stock_basic.float_shares),"
                " is_st=EXCLUDED.is_st,"
                " in_market=true, updated_at=now()"
            ).format(values)
            cur.execute(query)
    return len(rows)


def backfill() -> int:
    """全市场快照刷新（抓 → 写），返回写入行数。

    供编排层（`orchestration/daily.py` 的 basic 步）与 `main()` 共用一份实现：
    此前只有 `main()`，而 CLI 不是可调用入口 ⇒ 这个模块在生产上**零调用方**，
    `industry` / `market_cap` / `float_shares` 一度全表 NULL 就是这么来的。
    """
    rows = fetch_clist()
    log.info("拉取全市场 %d 只", len(rows))
    n = upsert_stock_basic(rows)
    log.info("stock_basic upsert %d 行", n)
    return n


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：默认全量刷新；``--dry-run`` 只抓不写。

    2026-10-13 补 argparse：此前本模块不解析 argv，``--help`` 会被静默忽略并**真的
    执行全量抓取 + 写库**（一次审计拿它试 CLI，就把 339 只退市股写进了生产库）。
    `fill_list_date` / `fill_industry` 两个姊妹模块一直有 argparse + ``--dry-run``，
    这里补齐后运维入口才谈得上可试跑。
    """
    import argparse

    parser = argparse.ArgumentParser(description="刷新 stock_basic（全市场快照，幂等 upsert）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印解析行数与样例，不写库")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not args.dry_run:
        return backfill()
    rows = fetch_clist()
    print(f"dry-run：解析 {len(rows)} 行（未写库），前 3 行 = {rows[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
