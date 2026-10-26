"""A 类「建而未接线」模块的 CLI 入口测试：doctor / compare。

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
        [doctor, compare],
        ids=["doctor", "compare"],
    )
    def test_main_is_callable(self, mod):
        assert callable(getattr(mod, "main", None)), f"{mod.__name__} 缺 main()"

    @pytest.mark.parametrize(
        "mod",
        [doctor, compare],
        ids=["doctor", "compare"],
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

