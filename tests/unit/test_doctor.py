"""doctor 七项自检（_check_* / CHECKS / run）：不连库。

语义来源 lkl/services/doctor.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 doctor.py 模块文档）：读库走 utils.db.query_df、now_sh 用
ZoneInfo 等价式、TRADE_DIR 未收录时取仓库根/trade、LLM/webhook 经 _cfg、
providers 经参数注入（无模块级 import）。

IO 全部桩掉（query_df / CONFIG / cwd / trade 目录探针 / halt 停机开关 /
providers 函数），真实库对账另跑（编排者阶段）。
"""
from __future__ import annotations

import logging
import sys
import types
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from emotion_core.algorithms import doctor

# lkl 原句（断言到 SQL 文本与参数元组一级，锁住谓词口径）
DB_SQL = ("SELECT count(*) n FROM information_schema.tables"
          " WHERE table_schema='public'")
CAL_SQL = ("SELECT min(date) a, max(date) b,"
           " count(distinct date) n FROM daily_bar")
SAMPLE_SQL = ("SELECT code, max(date) AS date FROM daily_bar GROUP BY code"
              " ORDER BY code LIMIT %s")


def stub_query(monkeypatch, fn) -> list[tuple[str, tuple]]:
    """桩掉 doctor.query_df；返回调用记录 [(sql, params)]。"""
    seen: list[tuple[str, tuple]] = []

    def fake(sql, params=(), conn=None):
        seen.append((sql, tuple(params)))
        return fn(sql, params)

    monkeypatch.setattr(doctor, "query_df", fake)
    return seen


def stub_config(monkeypatch, **kw) -> SimpleNamespace:
    """桩掉 doctor.CONFIG（_cfg / _trade_dir 都经它读）。"""
    cfg = SimpleNamespace(**kw)
    monkeypatch.setattr(doctor, "CONFIG", cfg)
    return cfg


# ────────────────────────── _check_db ──────────────────────────

class TestCheckDb:
    def test_ten_tables_ready(self, monkeypatch):
        seen = stub_query(monkeypatch, lambda sql, p: pd.DataFrame({"n": [10]}))
        assert doctor._check_db() == (True, "public 下 10 张表（≥10 为就绪）")
        assert seen == [(DB_SQL, ())]

    def test_more_than_ten_tables_ready(self, monkeypatch):
        stub_query(monkeypatch, lambda sql, p: pd.DataFrame({"n": [23]}))
        ok, note = doctor._check_db()
        assert ok is True
        assert note == "public 下 23 张表（≥10 为就绪）"

    def test_nine_tables_not_ready(self, monkeypatch):
        stub_query(monkeypatch, lambda sql, p: pd.DataFrame({"n": [9]}))
        ok, note = doctor._check_db()
        assert ok is False
        assert note == "public 下 9 张表（≥10 为就绪）"

    def test_connection_error(self, monkeypatch):
        stub_query(monkeypatch, lambda sql, p: (_ for _ in ()).throw(
            RuntimeError("could not connect")))
        ok, note = doctor._check_db()
        assert ok is False
        assert note.startswith("连接失败：")
        assert note == "连接失败：could not connect"


# ────────────────────────── _check_calendar ──────────────────────────

class TestCheckCalendar:
    def test_empty_daily_bar(self, monkeypatch):
        seen = stub_query(monkeypatch, lambda sql, p: pd.DataFrame(
            {"a": [None], "b": [None], "n": [0]}))
        ok, note = doctor._check_calendar()
        assert ok is False
        assert note == "daily_bar 空——先跑 lkl fetch backfill"    # 文案照搬（含 lkl）
        assert seen == [(CAL_SQL, ())]

    def test_normal_range(self, monkeypatch):
        stub_query(monkeypatch, lambda sql, p: pd.DataFrame(
            {"a": [date(2026, 1, 5)], "b": [date(2026, 9, 25)], "n": [180]}))
        assert doctor._check_calendar() == (
            True, "2026-01-05 ~ 2026-09-25 共 180 个交易日")

    def test_query_error(self, monkeypatch):
        stub_query(monkeypatch, lambda sql, p: (_ for _ in ()).throw(
            ValueError("relation \"daily_bar\" does not exist")))
        ok, note = doctor._check_calendar()
        assert ok is False
        assert note.startswith("查询失败：")


# ────────────────────────── _check_tz ──────────────────────────

class TestCheckTz:
    def test_always_ok_note_shape(self):
        ok, note = doctor._check_tz()
        assert ok is True
        assert note.startswith("Asia/Shanghai 当前 ")
        assert "（与 UTC 日期差=" in note
        assert note.endswith("）")
        stamp = note[len("Asia/Shanghai 当前 "):note.index("（")]
        _, hhmm = stamp.split(" ")
        assert len(stamp[:10].split("-")) == 3 and len(hhmm) == 5  # %Y-%m-%d %H:%M
        assert note.rstrip("）").endswith(("True", "False"))       # utc_date_diff 为 bool


# ────────────────────────── _check_trade_dir ──────────────────────────

class TestCheckTradeDir:
    def test_creates_dir_and_leaves_no_probe(self, monkeypatch, tmp_path):
        target = tmp_path / "trade"
        stub_config(monkeypatch, TRADE_DIR=str(target))
        monkeypatch.setitem(sys.modules, "emotion_core.trade", None)   # halt 层未就位
        ok, note = doctor._check_trade_dir()
        assert ok is True
        assert note == f"{target} 可写"
        assert "HALTED" not in note
        assert target.is_dir()
        assert not (target / ".doctor_probe").exists()                 # 探针写删

    def test_unwritable_path(self, monkeypatch, tmp_path):
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "trade"                                     # 父路径是文件
        stub_config(monkeypatch, TRADE_DIR=str(target))
        ok, note = doctor._check_trade_dir()
        assert ok is False
        assert f"{target} 不可写：" in note
        assert note.startswith(f"{target} 不可写：")

    def test_halted_suffix(self, monkeypatch, tmp_path):
        target = tmp_path / "trade"
        stub_config(monkeypatch, TRADE_DIR=str(target))
        mod = types.ModuleType("emotion_core.trade")
        mod.halt = SimpleNamespace(halted=lambda: True)
        monkeypatch.setitem(sys.modules, "emotion_core.trade", mod)
        assert doctor._check_trade_dir() == (True, f"{target} 可写；⛔ HALTED 停机中")

    def test_halt_not_halted(self, monkeypatch, tmp_path):
        target = tmp_path / "trade"
        stub_config(monkeypatch, TRADE_DIR=str(target))
        mod = types.ModuleType("emotion_core.trade")
        mod.halt = SimpleNamespace(halted=lambda: False)
        monkeypatch.setitem(sys.modules, "emotion_core.trade", mod)
        ok, note = doctor._check_trade_dir()
        assert ok is True
        assert note == f"{target} 可写"
        assert "HALTED" not in note

    def test_default_trade_dir_is_repo_root_trade(self, monkeypatch):
        """CONFIG 未收录 TRADE_DIR → 仓库根/trade（与 lkl config.py 默认同义）。

        只断言路径求值，不跑检查（检查会真的 mkdir 仓库根/trade，测试不留痕）。
        """
        monkeypatch.setattr(doctor, "CONFIG", SimpleNamespace())
        assert doctor._trade_dir() == Path(doctor.__file__).resolve().parents[3] / "trade"
        assert doctor._trade_dir().parts[-2:] == ("emotion-core", "trade")


# ────────────────────────── _check_reports ──────────────────────────

class TestCheckReports:
    def test_missing_reports_dir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert doctor._check_reports() == (
            True, "reports/ 尚未创建（首次 review 时自动建）")

    def test_counts_md_only(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "2026-09-25.md").write_text("# a", encoding="utf-8")
        (reports / "2026-09-26.md").write_text("# b", encoding="utf-8")
        (reports / "notes.txt").write_text("x", encoding="utf-8")      # 不计
        assert doctor._check_reports() == (True, "reports/ 2 份 MD")


# ────────────────────────── _check_optional ──────────────────────────

class TestCheckOptional:
    def test_defaults_off(self, monkeypatch):
        stub_config(monkeypatch, LLM_PROFILE=None, LKL_WEBHOOK_URL="")
        assert doctor._check_optional() == (
            True, "LLM=关（默认）；webhook=未配置（默认关）（均默认不参与主链）")

    def test_llm_profile_on(self, monkeypatch):
        stub_config(monkeypatch, LLM_PROFILE="agnes", LKL_WEBHOOK_URL="")
        assert doctor._check_optional() == (
            True, "LLM=开；webhook=未配置（默认关）（均默认不参与主链）")

    def test_webhook_configured(self, monkeypatch):
        stub_config(monkeypatch, LLM_PROFILE="agnes", LKL_WEBHOOK_URL="https://hook/x")
        assert doctor._check_optional() == (
            True, "LLM=开；webhook=已配置（均默认不参与主链）")

    def test_empty_webhook_env_value_is_off(self, monkeypatch):
        stub_config(monkeypatch, LLM_PROFILE=None, LKL_WEBHOOK_URL=None)
        assert doctor._check_optional() == (
            True, "LLM=关（默认）；webhook=未配置（默认关）（均默认不参与主链）")


# ────────────────────────── _check_provider_consistency ──────────────────────────

class ProviderError(Exception):
    """Provider 错误同形替身。"""


def make_providers(monkeypatch, *, main: dict | Exception, backup: dict | Exception):
    """构造 provider 函数并注入到 doctor 调用。

    fetch_daily_bars_fn(provider_name, code, start, end) — 4 参数
    provider.fetch_daily_bars(code, start, end) — 3 参数
    返回 (fetch_daily_bars_fn, get_provider_fn, provider_error_fn)。
    """
    def fetch(code, start, end):
        if isinstance(backup, Exception):
            raise backup
        return pd.DataFrame({"close": [backup[code]]})

    def fetch_main(provider_name, code, start, end):
        if isinstance(main, Exception):
            raise main
        return pd.DataFrame({"close": [main[code]]})

    def get_provider(name):
        return SimpleNamespace(fetch_daily_bars=fetch)

    def provider_error_fn():
        return ProviderError

    return fetch_main, get_provider, provider_error_fn


class TestCheckProviderConsistency:
    def test_chain_off(self, monkeypatch):
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=[])
        seen = stub_query(monkeypatch, lambda sql, p: pd.DataFrame())
        assert doctor._check_provider_consistency() == (True, "日线备源链关闭")
        assert seen == []                                              # 关链不读库

    def test_chain_missing_key_defaults_off(self, monkeypatch):
        stub_config(monkeypatch)                                       # CONFIG 无该键
        assert doctor._check_provider_consistency() == (True, "日线备源链关闭")

    def test_providers_absent_skips(self, monkeypatch):
        """无注入 → 静默跳过（离线诊断不强制依赖 services）。"""
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["pytdx"], INGEST_DOCTOR_SAMPLE=5)
        assert doctor._check_provider_consistency() == (
            True, "日线双源未注入，跳过备源体检")

    def test_no_sample(self, monkeypatch):
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["pytdx"], INGEST_DOCTOR_SAMPLE=7)
        fetch_fn, get_prov_fn, err_fn = make_providers(
            monkeypatch, main={}, backup={})
        seen = stub_query(monkeypatch, lambda sql, p: pd.DataFrame(columns=["code", "date"]))
        assert doctor._check_provider_consistency(
            fetch_daily_bars_fn=fetch_fn,
            get_provider_fn=get_prov_fn,
            provider_error_fn=err_fn,
        ) == (True, "无日线样本，跳过备源体检")
        assert seen == [(SAMPLE_SQL, (7,))]

    def test_deviation_alert_threshold(self, monkeypatch, caplog):
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["pytdx"], INGEST_DOCTOR_SAMPLE=5)
        fetch_fn, get_prov_fn, err_fn = make_providers(
            monkeypatch,
            main={"000001": 10.0, "600000": 10.0, "000002": 10.0},
            backup={"000001": 10.0, "600000": 10.04, "000002": 10.06})
        sample = pd.DataFrame({"code": ["000001", "600000", "000002"],
                               "date": [date(2026, 9, 25)] * 3})
        seen = stub_query(monkeypatch, lambda sql, p: sample)
        with caplog.at_level(logging.WARNING, logger="emotion_core.doctor"):
            assert doctor._check_provider_consistency(
                fetch_daily_bars_fn=fetch_fn,
                get_provider_fn=get_prov_fn,
                provider_error_fn=err_fn,
            ) == (True, "抽样 3 只，比较 3 组，告警 1 组")
        assert seen == [(SAMPLE_SQL, (5,))]
        assert [r.getMessage() for r in caplog.records] == [
            "日线双源偏差告警：000002 pytdx偏差0.60%"]

    def test_backup_provider_error_skips_code(self, monkeypatch, caplog):
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["pytdx"], INGEST_DOCTOR_SAMPLE=5)
        fetch_fn, get_prov_fn, err_fn = make_providers(
            monkeypatch, main={"000001": 10.0}, backup=ProviderError("503"))
        sample = pd.DataFrame({"code": ["000001"], "date": [date(2026, 9, 25)]})
        stub_query(monkeypatch, lambda sql, p: sample)
        with caplog.at_level(logging.WARNING, logger="emotion_core.doctor"):
            assert doctor._check_provider_consistency(
                fetch_daily_bars_fn=fetch_fn,
                get_provider_fn=get_prov_fn,
                provider_error_fn=err_fn,
            ) == (True, "抽样 1 只，比较 0 组，告警 0 组")
        assert caplog.records == []

    def test_main_provider_error_skips_row(self, monkeypatch, caplog):
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["pytdx"], INGEST_DOCTOR_SAMPLE=5)
        fetch_fn, get_prov_fn, err_fn = make_providers(
            monkeypatch, main=ProviderError("boom"), backup={"000001": 10.0})
        sample = pd.DataFrame({"code": ["000001"], "date": [date(2026, 9, 25)]})
        stub_query(monkeypatch, lambda sql, p: sample)
        with caplog.at_level(logging.WARNING, logger="emotion_core.doctor"):
            assert doctor._check_provider_consistency(
                fetch_daily_bars_fn=fetch_fn,
                get_provider_fn=get_prov_fn,
                provider_error_fn=err_fn,
            ) == (True, "抽样 1 只，比较 0 组，告警 0 组")
        assert caplog.records == []

    def test_unknown_fallback_name_skipped(self, monkeypatch):
        """备源链含未注册名字 → providers.get 为 None 跳过（不报错）。"""
        stub_config(monkeypatch, INGEST_FALLBACK_CHAIN=["nosuch"], INGEST_DOCTOR_SAMPLE=5)
        fetch_fn, get_prov_fn, err_fn = make_providers(
            monkeypatch, main={"000001": 10.0}, backup={})
        sample = pd.DataFrame({"code": ["000001"], "date": [date(2026, 9, 25)]})
        stub_query(monkeypatch, lambda sql, p: sample)
        assert doctor._check_provider_consistency(
            fetch_daily_bars_fn=fetch_fn,
            get_provider_fn=get_prov_fn,
            provider_error_fn=err_fn,
        ) == (True, "抽样 1 只，比较 0 组，告警 0 组")


# ────────────────────────── CHECKS / run ──────────────────────────

class TestChecksAndRun:
    def test_run_shape(self, monkeypatch):
        monkeypatch.setattr(doctor, "CHECKS", [
            ("甲", lambda: (True, "a")),
            ("乙", lambda: (False, "b")),
        ])
        assert doctor.run() == [("甲", True, "a"), ("乙", False, "b")]

    def test_real_checks_seven_in_lkl_order(self):
        assert [name for name, _ in doctor.CHECKS] == [
            "数据库", "交易日历", "时区", "trade 目录", "报告目录", "可选功能", "日线双源"]
        assert [fn.__name__ for _, fn in doctor.CHECKS] == [
            "_check_db", "_check_calendar", "_check_tz", "_check_trade_dir",
            "_check_reports", "_check_optional", "_check_provider_consistency"]
