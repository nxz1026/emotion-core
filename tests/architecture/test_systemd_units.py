"""架构守护：systemd 单元契约（部署层，仓内文件是唯一的排期真相源）。

背景
----
2026-10-07 审计 emotion-core 时实测发现生产 `/etc/systemd/system/` 与本仓
`src/emotion_core/orchestration/systemd/` 严重漂移，并且暴露出三条**已经造成过
真实损失**的不变量缺口。本文件把它们从「靠人记得」变成「CI 会红」。

1. **`python -c` 入口吞退出码**
   `emotion-core-close.service` 曾写
   `python -c "from ...daily import run_daily; run_daily()"`——
   `run_daily` 的返回值被丢在地上，`1`（步骤崩溃）与 `76`（覆盖率门槛拦截）
   全被吞掉，systemd 永远看到成功；`-c` 又绕过了 `main()`，没有
   `logging.basicConfig`，journal 里只剩 WARNING+。
   配合 watchdog 的退出码缺陷 ⇒ 主链死在第 N 步时**零告警**（2026-10-06~10-07 现场）。

2. **OnCalendar 必须自带显式时区**
   生产机器时区是 `Etc/UTC`。`OnCalendar=Mon-Fri 17:20` 在那里就是 UTC 17:20
   =北京时间凌晨 01:20，整体偏 8 小时。历史上靠 `.timer.d/10-timezone.conf`
   覆盖修正，但那个 drop-in **不在本仓**（daily 的那个只在生产有），于是
   「时区在哪一层」没有真相源，直到 2026-10-07 才把后缀直接写进 `.timer` 本体。

3. **drop-in 不得与本体矛盾**
   改完本体后仓库里仍留着 pool/report/watchdog 三个 `10-timezone.conf`。
   它们当前取值与本体**一致**（所以无害），但两份真相源迟早会漂——而漂移的
   表现是「排期静默偏 8 小时」，没有任何报错。本测试只要求**一致**，
   不强制某种结构：本体没写时区就靠 drop-in 兜底是完全合法的。

同时钉住两条小的：`-m` 指向的模块必须真有入口（`main()` 或 `__main__` 守卫，
否则 `python -m` 跑完什么都不做、退出 0）；timer 的 `Requires=` / `After=` /
`Wants=` 必须指向仓内真实存在的单元（防拼错单元名）。

⚠️ 2026-10-07 更新：仓内原先只有 10 个单元，而 `/etc/systemd/system` 有 11 个 ——
`emotion-core-dash.service` 与 `emotion-core-strategy.{service,timer}` **只存在于生产**，
而仓内的 `emotion-core-close.{service,timer}` **从未装到生产**。本轮已把前者按生产
原样收进仓库、后者撤销（close 实际跑的是完整 13 步 daily，与 daily.timer 全量重复，
详见 README「systemd 仓/生产漂移」小节）。现在两边都是 11 个单元。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UNIT_DIR = REPO / "src" / "emotion_core" / "orchestration" / "systemd"
SRC = REPO / "src"

_SERVICES = sorted(UNIT_DIR.glob("*.service"))
_TIMERS = sorted(UNIT_DIR.glob("*.timer"))
# `.timer.d/10-timezone.conf` 这类 drop-in：glob("*") 会命中目录，用 is_file 挡掉
_DROPINS = sorted(p for p in UNIT_DIR.glob("*/*.conf") if p.is_file())

_ONCALENDAR = re.compile(r"^OnCalendar=(.*)$")
_ENVIRON = re.compile(r"^(?:After|Requires|Wants|Before)=(.+)$")


def _directives(text: str) -> list[tuple[str, str]]:
    """解析 `Key=Value`，忽略注释与空行（不做续行/多行合并——本仓无此用法）。"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key, _, value = line.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def _exec_start(text: str) -> str:
    return next((v for k, v in _directives(text) if k == "ExecStart"), "")


def _effective_on_calendar(text: str) -> list[str]:
    """按 systemd 语义算生效值：空赋值 ``OnCalendar=`` 清空列表，后续追加。

    这是 drop-in 能改排期的机制本身——``OnCalendar=`` 不是「清空一次」，而是
    把累积列表清零，所以 drop-in 里的时刻会**完全替换**本体的时刻。
    """
    values: list[str] = []
    for key, value in _directives(text):
        if key == "OnCalendar":
            values = [] if value == "" else values + [value]
    return values


def test_unit_dir_is_not_empty():
    """守住路径常量本身：目录改名/搬走时，别让下面所有断言静默「全绿」。"""
    assert _SERVICES and _TIMERS, f"{UNIT_DIR} 下没有 service/timer 文件"


class TestExecStart:
    """入口必须真有事可做：`-m` 模块或脚本，不能是 `python -c`。"""

    @pytest.mark.parametrize("path", _SERVICES, ids=lambda p: p.name)
    def test_no_inline_python_c(self, path: Path):
        """回归锁：``python -c "...; run()"`` 会把退出码丢在地上。"""
        cmd = _exec_start(path.read_text(encoding="utf-8"))
        assert cmd, f"{path.name} 没有 ExecStart"
        assert " -c " not in f" {cmd} ", (
            f"{path.name} 用 `python -c` 调起业务函数：返回值被丢弃 ⇒ "
            f"systemd 永远看到成功；且不经 main() ⇒ 无 basicConfig、journal 无进度。\n"
            f"  改为 `python -m <module>`，退出码与日志才有归宿。\n  当前：{cmd}"
        )

    @pytest.mark.parametrize("path", _SERVICES, ids=lambda p: p.name)
    def test_module_target_defines_main(self, path: Path):
        """`-m foo.bar` 必须真的有事可做。

        `python -m` 找到模块、而模块里既没有 `main()` 也没有 `__main__` 守卫时，
        是「跑完什么都不做、退出 0」——unit 假装成功，与吞退出码属同一类故障。

        两种合法形态都接受：`def main(`（走 main()）或 `if __name__ == "__main__":`
        （模块自带入口）。`presentation/server.py` 属后者（导出 `run_server()`）。
        首版只认 `def main(`，会把 dash 这类单元判红——**门禁比约定更严不是更安全**，
        是没人愿意维护它。
        """
        cmd = _exec_start(path.read_text(encoding="utf-8"))
        match = re.search(r"-m\s+([A-Za-z_][\w.]*)", cmd)
        assert match, f"{path.name} 的 ExecStart 不是 `-m` 模块形式：{cmd}"
        module = match.group(1)
        py = SRC / Path(*module.split(".")).with_suffix(".py")
        assert py.exists(), f"{path.name} 指向的模块不存在：{module}（{py}）"
        src = py.read_text(encoding="utf-8")
        has_main = re.search(r"^def main\(", src, re.M)
        has_guard = re.search(r'^if __name__ == ["\']__main__["\']:', src, re.M)
        assert has_main or has_guard, (
            f"{path.name} 指向 {module}，但该模块既没有 `def main(` 也没有 "
            f'`if __name__ == "__main__":` 守卫——`python -m` 会静默跑完就退出 0'
        )

    @pytest.mark.parametrize("path", _SERVICES, ids=lambda p: p.name)
    def test_env_lines_present(self, path: Path):
        """每个 oneshot 都要有 PYTHONPATH，否则 `python -m` 找不到包。"""
        envs = [v for k, v in _directives(path.read_text(encoding="utf-8")) if k == "Environment"]
        assert any(e.startswith("PYTHONPATH=") for e in envs), (
            f"{path.name} 缺 Environment=PYTHONPATH（当前 Environment: {envs}）"
        )


class TestTimerTimezone:
    """排期时刻必须自带时区，否则跟着机器时区（Etc/UTC）漂 8 小时。"""

    @pytest.mark.parametrize("path", _TIMERS, ids=lambda p: p.name)
    def test_every_oncalendar_carries_a_timezone(self, path: Path):
        text = path.read_text(encoding="utf-8")
        own = _effective_on_calendar(text)
        if not own:
            pytest.skip(f"{path.name} 没有 OnCalendar（纯单调 timer）")
        dropin_values = []
        for conf in _DROPINS:
            if conf.parent.name == path.name:
                dropin_values = _effective_on_calendar(conf.read_text(encoding="utf-8"))
        effective = dropin_values or own
        for spec in effective:
            assert "/" in spec, (
                f"{path.name} 的 OnCalendar={spec} 没带时区。\n"
                f"  机器时区是 Etc/UTC，不写就会整体偏 8 小时（17:20 → 北京时间次日 01:20）。\n"
                f"  写法：OnCalendar=Mon-Fri 17:20 Asia/Shanghai"
            )

    @pytest.mark.parametrize("path", _TIMERS, ids=lambda p: p.name)
    def test_dropin_does_not_contradict_parent(self, path: Path):
        """drop-in 与本体同时存在时，生效值必须与本体逐字一致。

        drop-in 用 ``OnCalendar=`` 清空再重填，是**替换**而非追加；两份真相源
        一旦漂移，表现是排期静默偏 8 小时且无任何报错。
        """
        text = path.read_text(encoding="utf-8")
        own = _effective_on_calendar(text)
        if not own:
            pytest.skip(f"{path.name} 没有 OnCalendar")
        for conf in _DROPINS:
            if conf.parent.name != path.name:
                continue
            override = _effective_on_calendar(conf.read_text(encoding="utf-8"))
            assert override == own, (
                f"{conf.parent.name}/{conf.name} 与 {path.name} 的排期不一致：\n"
                f"    本体   = {own}\n"
                f"    drop-in= {override}\n"
                f"  drop-in 靠 `OnCalendar=` 清空后重填，实际生效的是 drop-in。"
                f"请让两者一致，或删掉不再需要的那一份。"
            )


class TestUnitReferences:
    """单元互相引用不能拼错。"""

    @pytest.mark.parametrize("path", _TIMERS + _SERVICES, ids=lambda p: p.name)
    def test_references_point_to_existing_units(self, path: Path):
        """`Requires` / `Wants` / `After` / `Before` 的目标必须在仓内存在。

        注意**不能只认 `.service`**：`emotion-core-strategy.timer` 的
        `After=emotion-core-daily.timer` 指向的是另一个 timer。首版把已知集合
        取成只有 service，于是这条合法的排序依赖被判成「拼错单元名」。
        """
        known = {p.name for p in (*_SERVICES, *_TIMERS)}
        for key, value in _directives(path.read_text(encoding="utf-8")):
            if key not in ("Requires", "Wants", "After", "Before", "Unit"):
                continue
            for unit in value.split():
                if not unit.startswith("emotion-core-"):
                    continue  # network.target / postgresql.service 等系统单元
                assert unit in known, (
                    f"{path.name} 的 {key}= 指向仓内不存在的 {unit}\n  仓内单元：{sorted(known)}"
                )
