"""新手自检（产品 A1-2 / 审计 P2-3）：doctor 一键通过/未通过清单（第 7 层运维）。

语义逐字照搬 lkl/services/doctor.py（docs/07-算法层搬运方案.md §3.4，135 行），
判定零改动：7 个检查函数体语义与中文文案、CHECKS 列表 7 项中文名与顺序、
run() -> [(name, ok, note)] 形状、阈值（≥10 张表、双源偏差 0.005、抽样 5）、
SQL 文本与参数顺序全部照搬；只读检查，不修任何东西。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. 读库：lkl 在函数内 `from lkl.utils import db` + `db.query_df` → 本模块顶层
   `from emotion_core.utils.db import query_df`（与 outcome.py 同款）；SQL 逐字不变，
   含 `_check_calendar` 文案里的 'lkl fetch backfill'（照搬不改，仅指数据回填命令）。
2. `_check_tz`：emotion-core 的 utils/dates 无 now_sh（只有 today_sh，按 UTC+8 取日期、
   不带时刻），故在函数内用 `datetime.now(ZoneInfo("Asia/Shanghai"))` 取等价时刻——
   lkl 的 now_sh 即此式；note 文案逐字不变。
3. `_check_trade_dir`：emotion-core CONFIG 未收录 TRADE_DIR，自持 `_trade_dir()`：
   `getattr(CONFIG, "TRADE_DIR", None)` 为 None 时取仓库根/trade。lkl 写作
   `Path(config.__file__).resolve().parent.parent / "trade"`；emotion-core 是无
   `__init__.py` 的 namespace package（`emotion_core.__file__ is None`），故改用本模块
   `__file__` 上溯到仓库根，落到同一路径（<repo>/trade），语义与 lkl config.py 默认同义。
   mkdir(parents=True, exist_ok=True) + `.doctor_probe` 探针写删 + 成败文案逐字不变。
   停机开关 lkl.trade.halt 属第 9 层（emotion-core 无 trade/，本轮不建）→ 延迟
   `from emotion_core.trade import halt`；ImportError 时 extra=""（只影响提示后缀，
   ok/note 判定不变），不抛；import 成功时仍按 halt.halted() 拼 "；⛔ HALTED 停机中"。
4. `_check_reports`：零差异（相对 cwd 的 reports/）。
5. `_check_optional`：emotion-core 无 LLM_ENABLED / LKL_WEBHOOK_URL，经模块内
   `_cfg(name, default)`（= getattr(CONFIG, name, default)，与 outcome.py 同款）读取。
   LLM 开关语义映射为 `_cfg("LLM_PROFILE", None) is not None`（None=关，见
   utils/config.py:58）；webhook 取 `_cfg("LKL_WEBHOOK_URL", "")`。输出文案逐字不变。
6. `_check_provider_consistency`：emotion-core 无 providers（第 8 层未搬），保留 lkl 原
   函数体与它自身的 `except Exception` 降级分支（log.warning + (True, "日线双源不可用，
   静默跳过")）；providers 四处 import 写成函数内延迟 import（emotion_core.data.providers
   .base.ProviderError / .eastmoney / .pytdx_provider / .sina），使其落到同一条既有降级
   分支，**未新增别的分支**；fallback 链经 `_cfg("INGEST_FALLBACK_CHAIN", [])`（空 → 走
   既有 "日线备源链关闭" 分支），抽样数经 `_cfg("INGEST_DOCTOR_SAMPLE", 5)`。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.doctor")


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 项；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


def _trade_dir() -> Path:
    """trade 目录；CONFIG 未收录 TRADE_DIR 时取仓库根/trade（同 lkl config.py 默认）。"""
    configured = getattr(CONFIG, "TRADE_DIR", None)
    if configured is None:
        return Path(__file__).resolve().parents[3] / "trade"
    return Path(configured)


def _check_db() -> tuple[bool, str]:
    try:
        df = query_df("SELECT count(*) n FROM information_schema.tables"
                      " WHERE table_schema='public'")
        n = int(df["n"].iloc[0])
        return (n >= 10, f"public 下 {n} 张表（≥10 为就绪）")
    except Exception as exc:                     # noqa: BLE001
        return (False, f"连接失败：{exc}")


def _check_calendar() -> tuple[bool, str]:
    try:
        df = query_df("SELECT min(date) a, max(date) b,"
                      " count(distinct date) n FROM daily_bar")
        r = df.iloc[0]
        if r["n"] == 0:
            return (False, "daily_bar 空——先跑 lkl fetch backfill")
        return (True, f"{r['a']} ~ {r['b']} 共 {int(r['n'])} 个交易日")
    except Exception as exc:                     # noqa: BLE001
        return (False, f"查询失败：{exc}")


def _check_tz() -> tuple[bool, str]:
    import datetime as dt
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    utc_date_diff = now.date() != dt.datetime.now(dt.timezone.utc).date()
    note = f"Asia/Shanghai 当前 {now:%Y-%m-%d %H:%M}（与 UTC 日期差={utc_date_diff}）"
    return (True, note)


def _check_trade_dir() -> tuple[bool, str]:
    p = Path(_trade_dir()).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".doctor_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        try:
            from emotion_core.trade import halt
        except ImportError:
            extra = ""
        else:
            extra = "；⛔ HALTED 停机中" if halt.halted() else ""
        return (True, f"{p} 可写{extra}")
    except Exception as exc:                     # noqa: BLE001
        return (False, f"{p} 不可写：{exc}")


def _check_reports() -> tuple[bool, str]:
    p = Path("reports")
    if not p.is_dir():
        return (True, "reports/ 尚未创建（首次 review 时自动建）")
    n = len(list(p.glob("*.md")))
    return (True, f"reports/ {n} 份 MD")


def _check_optional() -> tuple[bool, str]:
    llm = "开" if _cfg("LLM_PROFILE", None) is not None else "关（默认）"
    hook = "已配置" if _cfg("LKL_WEBHOOK_URL", "") else "未配置（默认关）"
    return (True, f"LLM={llm}；webhook={hook}（均默认不参与主链）")


def _check_provider_consistency() -> tuple[bool, str]:
    """抽样比较 EM 与配置备源收盘价；不可用时仅 warning，不阻断 doctor。"""
    if not _cfg("INGEST_FALLBACK_CHAIN", []):
        return (True, "日线备源链关闭")
    try:
        from emotion_core.services.provider_service import (
            fetch_daily_bars,
            get_provider,
            get_provider_error,
        )
        ProviderError = get_provider_error()
        providers = {
            "pytdx": get_provider("pytdx"),
            "sina": get_provider("sina"),
        }
        sample = query_df("SELECT code, max(date) AS date FROM daily_bar GROUP BY code"
                          " ORDER BY code LIMIT %s",
                          (_cfg("INGEST_DOCTOR_SAMPLE", 5),))
        if sample.empty:
            return (True, "无日线样本，跳过备源体检")
        alerts, checked = [], 0
        for row in sample.itertuples(index=False):
            try:
                main = fetch_daily_bars("eastmoney", row.code, row.date, row.date)
                if main.empty:
                    continue
                for name in _cfg("INGEST_FALLBACK_CHAIN", []):
                    provider = providers.get(name)
                    if provider is None:
                        continue
                    try:
                        backup = provider.fetch_daily_bars(row.code, row.date, row.date)
                    except ProviderError:
                        continue
                    if backup.empty or not float(main.iloc[0].close):
                        continue
                    checked += 1
                    diff = abs(float(backup.iloc[0].close) / float(main.iloc[0].close) - 1)
                    if diff > 0.005:
                        alerts.append(f"{row.code} {name}偏差{diff:.2%}")
            except ProviderError:
                continue
        for detail in alerts:
            log.warning("日线双源偏差告警：%s", detail)
        return (True, f"抽样 {len(sample)} 只，比较 {checked} 组，告警 {len(alerts)} 组")
    except Exception as exc:  # noqa: BLE001
        log.warning("日线双源体检跳过：%s", exc)
        return (True, "日线双源不可用，静默跳过")


CHECKS = [
    ("数据库", _check_db),
    ("交易日历", _check_calendar),
    ("时区", _check_tz),
    ("trade 目录", _check_trade_dir),
    ("报告目录", _check_reports),
    ("可选功能", _check_optional),
    ("日线双源", _check_provider_consistency),
]


def run() -> list[tuple[str, bool, str]]:
    """逐项检查，返回 [(name, ok, note)]；DB 项失败后续项仍尝试。"""
    return [(name, *fn()) for name, fn in CHECKS]
