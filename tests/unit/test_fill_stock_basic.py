"""stock_basic 填充（§9 第 11 条）：行业/总市值/流通股本入库，缺字段保留旧值。

`diagnose_service` 查 industry / market_cap，此前两列 DDL 里都没有、服务一调就抛
UndefinedColumn。现在列已补，数据源取 EM clist 的 f100（行业名）/ f20（总市值，元）；
`float_shares` 由 f21（流通市值，元）÷ f2（收盘价）本地反推，零额外请求。

新浪回退源：f100 确实没有（新浪只给证监会门类级行业，粒度太粗），故 industry
缺列时必须 NULL → 由 upsert 的 COALESCE 保留库内旧值，**不能用 None 覆写已有事实**；
f20/f21 自 2026-10-12 起已收录（provider 侧 `_SINA_UNIT_SCALE` 做万元→元），
所以本机 EM 持续 502 时回退源下市值/流通股本照样有值。

`is_st`（2026-10-13 接入）走**简称前缀** `ST`/`*ST`，先剥 `XD`/`XR`/`DR` 除权标记：
东财 clist 不给风险警示标志，而 `ref_security_status.populate_is_st_from_wind()`
要逐股烧 Wind 配额。该列进个股诊断 LLM prompt，此前全表 false（198 只漏标）。
"""
from __future__ import annotations

from typing import Self

import pandas as pd
import pytest

from emotion_core.data import fill_stock_basic as fsb


def test_rows_from_full_em_frame():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": "贵州茅台", "f2": 1288.0,
         "f100": "酿酒行业", "f20": 1.61e12, "f21": 1.61e12},
        {"f12": "000001", "f14": "平安银行", "f2": 11.73,
         "f100": "银行", "f20": 2.276e11, "f21": 1.8214e11},
    ])
    rows = fsb._rows_from_frame(raw)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("600519", "贵州茅台", "酿酒行业", 1.61e12),
        ("000001", "平安银行", "银行", 2.276e11),
    ]
    assert rows[0][4] == pytest.approx(1.61e12 / 1288.0)      # ≈12.50 亿股
    assert rows[1][4] == pytest.approx(1.8214e11 / 11.73)     # ≈155.3 亿股


def test_rows_from_sina_frame_without_industry_column():
    """新浪回退帧：f100 缺列 → industry=None（COALESCE 保旧值）；f20/f21 有值。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "贵州茅台", "f2": 1288.0,
                         "f20": 1.6101051e12, "f21": 1.6101051e12}])
    rows = fsb._rows_from_frame(raw)
    assert len(rows) == 1
    code, name, industry, cap, shares, is_st = rows[0]
    assert (code, name, industry) == ("600519", "贵州茅台", None)
    assert cap == pytest.approx(1.6101051e12)
    assert shares == pytest.approx(1.6101051e12 / 1288.0)     # ≈12.50 亿股
    assert is_st is False


def test_float_shares_absent_when_column_missing():
    """整列 f21 不存在（未request 该字段/更老的回退帧）→ None，不失行。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "甲", "f2": 100.0}])
    assert [r[4] for r in fsb._rows_from_frame(raw)] == [None]


def test_float_shares_needs_positive_price():
    """f21 空 / f2 为 0、负、NaN、不可解析 → 一律 None，不抛也不除零。"""
    raw = pd.DataFrame([
        {"f12": "600519", "f14": "甲", "f2": 100.0, "f21": None},
        {"f12": "600519", "f14": "甲", "f2": 0.0, "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": -1.0, "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": float("nan"), "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": "不是价", "f21": 1e11},
        {"f12": "600519", "f14": "甲", "f2": 100.0, "f21": "不是数"},
    ])
    assert [r[4] for r in fsb._rows_from_frame(raw)] == [None] * 6


def test_float_shares_uses_pre_close_when_suspended():
    """停牌股 f2=0 → 退到 f18 昨收；f2 有值时**必须**优先用 f2，不能误用 f18。"""
    raw = pd.DataFrame([
        {"f12": "000016", "f14": "*ST康佳A", "f2": 0.0, "f18": 2.42,
         "f21": 5.923546e9},
        {"f12": "000001", "f14": "平安银行", "f2": 11.73, "f18": 11.5,
         "f21": 1.8214e11},
    ])
    rows = fsb._rows_from_frame(raw)
    assert rows[0][4] == pytest.approx(5.923546e9 / 2.42)     # 用昨收
    assert rows[1][4] == pytest.approx(1.8214e11 / 11.73)     # 用收盘价，不是 f18


def test_rows_drop_invalid_and_sanitise_nan():
    raw = pd.DataFrame([
        {"f12": "600519", "f14": " 贵州茅台 ", "f100": " ", "f20": float("nan")},
        {"f12": "not-a-code", "f14": "坏行", "f100": None, "f20": None},
        {"f12": "000001", "f14": "", "f100": None, "f20": None},
        {"f12": "000002", "f14": "万科A", "f100": None, "f20": "not-a-number"},
    ])
    rows = fsb._rows_from_frame(raw)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("600519", "贵州茅台", None, None),
        ("000002", "万科A", None, None),
    ]
    assert [r[4] for r in rows] == [None, None]           # 无 f21 列


class _Rec:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _Rec:
        return self

    def execute(self, sql) -> None:
        self.statements.append(sql)


def test_upsert_sql_keeps_old_values_on_missing_fields(monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(fsb, "transaction", lambda: rec)
    assert fsb.upsert_stock_basic([("600519", "贵州茅台", "酿酒行业", 1.9e12, 1.25e9, True),
                                   ("000001", "平安银行", None, None, None, False)]) == 2

    sql = rec.statements[0].as_string(None)          # 整条语句渲染出来读（铁律）
    assert ("INSERT INTO stock_basic (code, name, market, industry, market_cap,"
            " float_shares, is_st, in_market)") in sql
    assert "'酿酒行业'" in sql and "1900000000000" in sql and "1250000000" in sql
    assert "COALESCE(EXCLUDED.industry, stock_basic.industry)" in sql
    assert "COALESCE(EXCLUDED.market_cap, stock_basic.market_cap)" in sql
    assert "COALESCE(EXCLUDED.float_shares, stock_basic.float_shares)" in sql
    # is_st 由名称前缀推导、每次抓取都是权威值 → **不能**用 COALESCE 保旧值
    assert "is_st=EXCLUDED.is_st" in sql
    assert "in_market=true" in sql
    assert "updated_at=now()" in sql
    # 缺失字段渲染成 NULL（不是 'None'）；每行都带 in_market=true
    assert ", true)" in sql and sql.count(", true)") == 2
    # is_st 的布尔渲染成 true/false，不能漏出 Python 的 'True'/'False'
    assert "false" in sql and "True" not in sql and "False" not in sql
    assert "NULL" in sql


def test_upsert_empty_rows_is_noop(monkeypatch):
    monkeypatch.setattr(fsb, "transaction", lambda: pytest.fail("空行不该开事务"))
    assert fsb.upsert_stock_basic([]) == 0


def test_em_fields_requests_cap_and_float_market_cap():
    """f20/f21 必须在请求列表里 —— 漏了就是 COALESCE 保护下静默永远 NULL。"""
    assert "f20" in fsb.EM_FIELDS
    assert "f21" in fsb.EM_FIELDS
    assert "f2" in fsb.EM_FIELDS          # 反推流通股本要用
    assert "f18" in fsb.EM_FIELDS         # 停牌股的价格兜底


def test_market_prefix_rule():
    assert fsb._market("600519") == "sh"
    assert fsb._market("000001") == "sz"
    assert fsb._market("300750") == "sz"


def test_rows_drop_delisted_marker():
    """东财用 f100='-' 标记退市证券：必须丢弃，否则以 in_market=true 抬高覆盖率分母。

    2026-10-13 实证：`_EM_FS_ALL_A` 的板块过滤不排除退市股，一次抓取 5562 行里
    334 行是 f100='-'（PT金田A / 国华退 / 退市紫晶），写库后 coverage 上限
    5223/5562=0.939（门槛 0.90，安全垫 9.8%→3.9%）。
    """
    raw = pd.DataFrame([
        {"f12": "000001", "f14": "平安银行", "f2": 11.73, "f100": "银行"},
        {"f12": "000003", "f14": "PT金田A", "f2": 0.0, "f100": "-"},
        {"f12": "000004", "f14": "国华退", "f2": 0.0, "f100": "-"},
        {"f12": "600519", "f14": "贵州茅台", "f2": 1288.0, "f100": "酿酒行业"},
    ])
    assert [r[0] for r in fsb._rows_from_frame(raw)] == ["000001", "600519"]


def test_delisted_filter_only_matches_literal_dash():
    """判据只认字面量 '-'：新浪回退帧无 f100 列、新股 f100 为 NaN/空 都不能误伤。"""
    no_col = pd.DataFrame([{"f12": "600519", "f14": "贵州茅台", "f2": 1288.0}])
    assert len(fsb._rows_from_frame(no_col)) == 1
    nan_ind = pd.DataFrame([{"f12": "301660", "f14": "N粤芯-U", "f100": float("nan")}])
    assert len(fsb._rows_from_frame(nan_ind)) == 1
    blank = pd.DataFrame([{"f12": "600519", "f14": "贵州茅台", "f100": ""}])
    assert len(fsb._rows_from_frame(blank)) == 1


def test_nan_of_any_dtype_is_missing():
    """`isinstance(v, float)` 判 NaN 太窄：numpy float32/Decimal('NaN')/pd.NA 都要认。

    2026-10-13 修正前实测：`_as_float(np.float32('nan'))` 原样返回 nan（会作为 PG
    NaN 入库，COALESCE 拦不住），`_as_text(np.float32('nan'))` 返回字面量 'nan'。
    """
    from decimal import Decimal

    import numpy as np

    for v in (np.float32("nan"), np.float16("nan"), Decimal("NaN"), pd.NA, pd.NaT, None):
        assert fsb._as_float(v) is None, repr(v)
        assert fsb._as_text(v) is None, repr(v)
    assert fsb._as_float("12.5") == 12.5
    assert fsb._as_text(" 银行 ") == "银行"


def test_nan_name_and_blank_code_are_dropped_not_written():
    """f14=NaN 曾写成字面量 'nan'；f12='' 经 zfill 塌缩成 '000000' 并静默入库。"""
    raw = pd.DataFrame([
        {"f12": float("nan"), "f14": "庄股"},
        {"f12": "", "f14": "空代码"},
        {"f12": "  0", "f14": "短码"},
        {"f12": "600519", "f14": float("nan")},
        {"f12": "600519", "f14": "贵州茅台"},
    ])
    assert [(r[0], r[1]) for r in fsb._rows_from_frame(raw)] == [("600519", "贵州茅台")]


def test_float_shares_rejects_non_positive_cap():
    """f21=0 会写出 0.0，而 COALESCE 只拦 NULL 不拦 0 → 反把库内正确股数覆盖成 0。"""
    raw = pd.DataFrame([{"f12": "600519", "f14": "甲", "f2": 100.0, "f21": 0.0}])
    assert fsb._rows_from_frame(raw)[0][4] is None


@pytest.mark.parametrize(("name", "expected"), [
    ("贵州茅台", False), ("平安银行", False), ("N粤芯-U", False), ("C信诺维", False),
    ("九号公司-WD", False), ("ST海王", True), ("*ST美丽", True), ("XD*ST康佳A", True),
    ("DRST晨鸣", True), ("SST前锋", True), ("S*ST石化", True), ("＊ST全角", True),
    ("ST", True), ("", False), ("S佳通", False),
])
def test_is_st_from_name_prefix(name, expected):
    """is_st 走简称前缀（零 Wind 额度）；除权标记要先剥，否则 `XD*STxx` 当天漏判。"""
    assert fsb._is_st_name(name) is expected


def test_rows_carry_is_st_flag():
    raw = pd.DataFrame([
        {"f12": "000078", "f14": "ST海王", "f2": 3.0, "f100": "医药商业"},
        {"f12": "600519", "f14": "贵州茅台", "f2": 1288.0, "f100": "酿酒行业"},
        {"f12": "000016", "f14": "XD*ST康佳A", "f2": 2.5, "f100": "白色家电"},
    ])
    assert [r[5] for r in fsb._rows_from_frame(raw)] == [True, False, True]


def test_main_dry_run_never_writes(monkeypatch, capsys):
    monkeypatch.setattr(fsb, "fetch_clist",
                        lambda: [("600519", "贵州茅台", "酿酒行业", 1.0, 2.0, False)])
    monkeypatch.setattr(fsb, "upsert_stock_basic",
                        lambda rows: pytest.fail("--dry-run 不该写库"))
    assert fsb.main(["--dry-run"]) == 0
    assert "未写库" in capsys.readouterr().out


def test_main_writes_by_default(monkeypatch):
    seen: dict[str, int] = {}
    monkeypatch.setattr(fsb, "fetch_clist",
                        lambda: [("600519", "贵州茅台", "酿酒行业", 1.0, 2.0, False)])
    monkeypatch.setattr(fsb, "upsert_stock_basic",
                        lambda rows: seen.setdefault("n", len(rows)))
    assert fsb.main([]) == 1
    assert seen["n"] == 1


def test_main_help_is_not_a_backfill(monkeypatch):
    """回归：原模块不解析 argv，`--help` 会静默全量抓取 + 写库（2026-10-13 生产库事故）。"""
    monkeypatch.setattr(fsb, "backfill", lambda: pytest.fail("--help 不该触发回填"))
    with pytest.raises(SystemExit) as exc:
        fsb.main(["--help"])
    assert exc.value.code == 0
