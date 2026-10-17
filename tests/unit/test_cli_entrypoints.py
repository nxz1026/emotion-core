"""A 类「建而未接线」模块的 CLI 入口测试：doctor / compare / ref_dividend /
ref_rs / ref_limit_rule。

审计 §3 把 5 个模块判为「死功能」（功能完成、测试活跃、零生产调用方）。本文件锁
接线后的契约：`python -m emotion_core.<mod>` 有可调用的 `main(argv)`、返回码语义
（0 成功 / 1 业务未就绪 / 2 参数错）、以及 `--dry` 之外的只读默认。

⚠️ 全部 IO 桩掉，不连库、不连 Wind、不发 HTTP；真实库对账在 oracle 编排阶段跑。
⚠️ 只测 `main()` 的分支与返回码，**不重复**各模块既有单测已覆盖的内部逻辑。
"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.algorithms import compare, doctor
from emotion_core.services import ref_dividend, ref_limit_rule, ref_rs


class _FakeCursor:
    def __init__(self, rows) -> None:
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql, params=()) -> None:
        self.sql = sql

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows) -> None:
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def cursor(self):
        return _FakeCursor(self._rows)


# ────────────────────── 模块入口存在性（A 类接线契约） ──────────────────────


class TestModulesDefineMain:
    """五个模块都必须有 `main` —— 这是「接了 CLI」的最小可验证契约。"""

    @pytest.mark.parametrize(
        "mod",
        [doctor, compare, ref_dividend, ref_rs, ref_limit_rule],
        ids=["doctor", "compare", "ref_dividend", "ref_rs", "ref_limit_rule"],
    )
    def test_main_is_callable(self, mod):
        assert callable(getattr(mod, "main", None)), f"{mod.__name__} 缺 main()"

    @pytest.mark.parametrize(
        "mod",
        [doctor, compare, ref_dividend, ref_rs, ref_limit_rule],
        ids=["doctor", "compare", "ref_dividend", "ref_rs", "ref_limit_rule"],
    )
    def test_main_accepts_argv_list(self, mod):
        """签名须接受 argv 列表（缺省 None）——否则测试与别的入口无法注入参数。"""
        import inspect

        params = list(inspect.signature(mod.main).parameters)
        assert params[:1] == ["argv"], f"{mod.__name__}.main 首参应为 argv，实为 {params}"


# ────────────────────────────── doctor ──────────────────────────────


class TestDoctorCli:
    def test_all_pass_returns_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(
            doctor, "run", lambda: [("数据库", True, "8 张表"), ("时区", True, "UTC+8")]
        )
        assert doctor.main([]) == 0
        out = capsys.readouterr().out
        assert "✅ 数据库：8 张表" in out
        assert "✅ 时区：UTC+8" in out
        assert "—— 2/2 项通过" in out

    def test_any_failure_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            doctor, "run", lambda: [("数据库", False, "连接失败：boom"), ("时区", True, "UTC+8")]
        )
        assert doctor.main([]) == 1
        out = capsys.readouterr().out
        assert "❌ 数据库：连接失败：boom" in out
        assert "—— 1/2 项通过" in out

    def test_quiet_suppresses_passing_lines(self, monkeypatch, capsys):
        monkeypatch.setattr(
            doctor, "run", lambda: [("数据库", True, "8 张表"), ("时区", False, "坏了")]
        )
        assert doctor.main(["--quiet"]) == 1
        out = capsys.readouterr().out
        assert "✅" not in out, "--quiet 下通过项不该刷屏"
        assert "❌ 时区：坏了" in out

    def test_unknown_option_exits_two(self, monkeypatch):
        monkeypatch.setattr(doctor, "run", lambda: [])
        with pytest.raises(SystemExit) as ei:
            doctor.main(["--nope"])
        assert ei.value.code == 2


# ────────────────────────────── compare ──────────────────────────────


def _rep(consistent: bool, drift: list[str] | None = None) -> dict:
    return {
        "date": "2026-09-25",
        "live": [{"code": "000001", "status": "BUY"}],
        "replay": [{"code": "000001", "passed": True}],
        "consistent": consistent,
        "drift": drift or [],
    }


class TestCompareCli:
    def test_consistent_returns_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(compare, "compare", lambda d: _rep(True))
        assert compare.main(["2026-09-25"]) == 0
        out = capsys.readouterr().out
        assert "## 回测—实盘一致性 2026-09-25" in out
        assert "✅ 两侧一致：无规则漂移" in out

    def test_drift_returns_one_and_reports_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(compare, "compare", lambda d: _rep(False, ["600825"]))
        assert compare.main(["2026-09-25"]) == 1
        err = capsys.readouterr().err
        assert "规则漂移" in err
        assert "600825" in err

    def test_json_flag_emits_parseable_json(self, monkeypatch, capsys):
        import json

        monkeypatch.setattr(compare, "compare", lambda d: _rep(True))
        assert compare.main(["2026-09-25", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["date"] == "2026-09-25"
        assert payload["consistent"] is True

    def test_date_is_forwarded_to_compare(self, monkeypatch):
        seen = []
        monkeypatch.setattr(compare, "compare", lambda d: (seen.append(d), _rep(True))[1])
        compare.main(["2026-10-16"])
        assert seen == [date(2026, 10, 16)]

    def test_bad_date_returns_two(self, capsys):
        assert compare.main(["2026-13-45"]) == 2
        assert "日期格式错误" in capsys.readouterr().err


# ────────────────────────────── ref_rs ──────────────────────────────


class TestRefRsCli:
    def test_default_lists_top_rs(self, monkeypatch, capsys):
        captured = {}

        def fake_top(**kw):
            captured.update(kw)
            return [{"code": "600519", "rs": 1.5, "rank": 1},
                    {"code": "000001", "rs": 1.2, "rank": None}]

        monkeypatch.setattr(ref_rs, "get_top_rs", fake_top)
        assert ref_rs.main(["--as-of", "2026-09-25"]) == 0
        out = capsys.readouterr().out
        assert captured["as_of_date"] == date(2026, 9, 25)
        assert captured["period"] == 20
        assert "600519" in out and "rs=1.50" in out
        assert "—" in out, "rank 为 NULL 要显示占位符而不是 'None'"

    def test_calculate_then_list(self, monkeypatch):
        seen = {}

        def fake_calc(as_of_date=None, benchmark=ref_rs.DEFAULT_BENCHMARK):
            seen["as_of"] = as_of_date
            seen["benchmark"] = benchmark
            return 7

        monkeypatch.setattr(ref_rs, "calculate_rs", fake_calc)
        monkeypatch.setattr(
            ref_rs, "get_top_rs", lambda **kw: [{"code": "600519", "rs": 2.0, "rank": 1}]
        )
        assert ref_rs.main(["--calculate", "--as-of", "2026-09-25", "--benchmark", "000905"]) == 0
        assert seen == {"as_of": date(2026, 9, 25), "benchmark": "000905"}

    def test_calculate_zero_rows_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            ref_rs, "calculate_rs", lambda as_of_date=None, benchmark="000300": 0
        )
        monkeypatch.setattr(ref_rs, "get_top_rs", lambda **kw: [])
        assert ref_rs.main(["--calculate"]) == 1
        assert "已写入 0 行" in capsys.readouterr().out

    def test_empty_table_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(ref_rs, "get_top_rs", lambda **kw: [])
        assert ref_rs.main(["--as-of", "2026-09-25"]) == 1
        assert "先跑 --calculate" in capsys.readouterr().out

    def test_period_is_forwarded(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            ref_rs, "get_top_rs",
            lambda **kw: (seen.update(kw), [{"code": "600519", "rs": 1.0, "rank": 1}])[1],
        )
        ref_rs.main(["--period", "60", "--top", "5"])
        assert seen["period"] == 60
        assert seen["limit"] == 5

    def test_bad_date_returns_two(self, capsys):
        assert ref_rs.main(["--as-of", "2026-13-45"]) == 2
        assert "日期格式错误" in capsys.readouterr().err


# ────────────────────────── ref_limit_rule ──────────────────────────


class TestRefLimitRuleCli:
    def test_seed_returns_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(ref_limit_rule, "seed_ref_limit_rule", lambda: 10)
        assert ref_limit_rule.main(["--seed"]) == 0
        assert "已写入 10 条" in capsys.readouterr().out

    def test_query_hit(self, monkeypatch, capsys):
        seen = {}

        def fake_pct(market, board, as_of):
            seen.update(market=market, board=board, as_of=as_of)
            return 10.0

        monkeypatch.setattr(ref_limit_rule, "get_limit_pct", fake_pct)
        assert ref_limit_rule.main(["--query", "SSE", "main", "--as-of", "2026-09-25"]) == 0
        assert seen == {"market": "SSE", "board": "main", "as_of": date(2026, 9, 25)}
        assert "±10%" in capsys.readouterr().out

    def test_query_miss_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(ref_limit_rule, "get_limit_pct", lambda m, b, d: None)
        assert ref_limit_rule.main(["--query", "NYSE", "main"]) == 1
        assert "未匹配规则" in capsys.readouterr().out

    def test_query_bad_date_returns_two(self, capsys):
        assert ref_limit_rule.main(["--query", "SSE", "main", "--as-of", "2026-13-45"]) == 2
        assert "日期格式错误" in capsys.readouterr().err

    def test_list_dumps_rules(self, monkeypatch, capsys):
        rows = [
            ("SSE", "main", 10.0, date(1996, 12, 16), None),
            ("SZSE", "chinext", 20.0, date(2020, 8, 24), None),
        ]
        monkeypatch.setattr(ref_limit_rule, "connect", lambda: _FakeConn(rows))
        assert ref_limit_rule.main([]) == 0
        out = capsys.readouterr().out
        assert "共 2 条" in out
        assert "SSE" in out and "±10%" in out
        assert "至今" in out, "effective_to 为 NULL 要显示「至今」"

    def test_empty_table_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(ref_limit_rule, "connect", lambda: _FakeConn([]))
        assert ref_limit_rule.main([]) == 1
        assert "跑 --seed" in capsys.readouterr().out


# ────────────────────────── ref_dividend ──────────────────────────


class TestRefDividendCli:
    def test_default_passes_none_codes(self, monkeypatch, capsys):
        seen = {}

        def fake(codes=None, sleep_sec=0.5):
            seen.update(codes=codes, sleep=sleep_sec)
            return (3, 1)

        monkeypatch.setattr(ref_dividend, "backfill_dividend", fake)
        assert ref_dividend.main([]) == 0
        assert seen == {"codes": None, "sleep": 0.5}
        assert "写入 3 条，跳过 1 条" in capsys.readouterr().out

    def test_codes_and_sleep_are_forwarded(self, monkeypatch):
        seen = {}

        def fake(codes=None, sleep_sec=0.5):
            seen.update(codes=codes, sleep=sleep_sec)
            return (1, 0)

        monkeypatch.setattr(ref_dividend, "backfill_dividend", fake)
        ref_dividend.main(["--codes", "600519", "000001", "--sleep", "0.1"])
        assert seen == {"codes": ["600519", "000001"], "sleep": 0.1}

    def test_zero_inserted_is_not_a_failure(self, monkeypatch, capsys):
        """Wind 不可用时 backfill 返 (0, 0) —— 是明确空转，不该报失败码。"""
        monkeypatch.setattr(
            ref_dividend, "backfill_dividend", lambda codes=None, sleep_sec=0.5: (0, 0)
        )
        assert ref_dividend.main([]) == 0
        assert "0 条写入" in capsys.readouterr().out

    def test_negative_sleep_returns_one(self, capsys):
        assert ref_dividend.main(["--sleep", "-1"]) == 1
        assert "--sleep 不能为负" in capsys.readouterr().err
