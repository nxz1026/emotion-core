#!/usr/bin/env python3
"""门禁 B：静态检查债务**只许减、不许增**（棘轮）。

## 为什么是棘轮而不是「清零」

2026-10-07 实测：本仓接入 ruff/mypy 的当下债务是

    ruff  check : 291 errors（102 F / 88 E / 47 I / 38 U / 15 B / 1 W）
    ruff format: 226 个文件待格式化
    mypy  strict: 未跑（无配置）

**直接把 ruff 挂进 CI 会让它第一秒就红** —— 而一个从第一天就红的门禁，
结局只有两个：被人注释掉，或者被人学会无视。**那比没有门禁更糟**，
因为它还占着「我们有 CI」的位子。

所以分两步：

1. **现在**：棘轮门禁。记录当前债务为基线，之后**只允许下降**。
   新增代码若引入新的 lint 错误 ⇒ 变红；存量错误保持不动。
   它永远不会因为「存量太脏」而误报，所以能一直活着。
2. **之后**：`ruff check --fix`（167 条可自动修）+ `ruff format`（纯格式化）
   把债务清到接近零，再把基线调到新值，最后把 ruff/mypy 真正挂进 CI。

## 债务不可只靠人自觉的原因

今天这一轮里，CPT 的 `ruff` 从没在 CI 跑过（其 CI 只跑静态门禁与 pytest），
于是 18 个 lint 错误长期存活、其中包括 `config.py` 漏 `import Final`
——它因 `from __future__ import annotations` 而**运行时��崩**，过了 12 道门禁。
**「没人在跑」和「跑出来是红的」是两种不同的失败，后者更容易被发现。**

## 退出码

0 = 债务未增长（持平或下降都算过）；1 = 新增了错误，或基线缺失/格式损坏。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "lint_debt_baseline.json"
TARGETS = ["src", "tests"]

#: 本机可能没有裸 `ruff`（例如只有 `uvx ruff@版本`）。**门禁不能因为
#: 「工具不在 PATH 上」就静默放过** —— 那正是「恒绿」的老套路。
#: 找不到就明确报出来，而不是当 0 个错误。
_UVX = Path.home() / ".local" / "bin" / "uvx.exe"
if sys.platform != "win32":
    _UVX = Path.home() / ".local" / "bin" / "uvx"


def _ruff_cmd() -> list[str] | None:
    import shutil

    plain = shutil.which("ruff")
    if plain:
        return [plain]
    if _UVX.exists():
        return [str(_UVX), "ruff@0.16.8"]
    return None


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        argv, cwd=str(ROOT), capture_output=True, text=True, timeout=600
    )
    return proc.returncode, proc.stdout + proc.stderr


def ruff_findings() -> tuple[int, dict[str, int]]:
    """返回 (总数, 按规则分布)。

    ⚠️ **工具没跑成 ≠ 零个错误。** 第一版没做这个区分，结果 `uvx` 一次联网
    失败 → 输出为空 → 债务记成 0 → 门禁转绿，而实际上 291 条债还在。
    这与「查不到共享表」被当成「一切正常」是同一个错误，跨项目重复出现。
    """
    base = _ruff_cmd()
    if base is None:
        raise FileNotFoundError("ruff")
    rc, out = _run([*base, "check", *TARGETS, "--output-format", "concise"])
    if "Found " in out:
        try:
            total = int(out.split("Found ")[1].split(" error")[0])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"解析 ruff 输出失败：{exc}") from exc
    elif "All checks passed" in out:
        total = 0
    else:
        raise RuntimeError(
            f"ruff 没能给出结论（rc={rc}）。**这不等于没有欠债** —— "
            f"多半是工具不可用或输出格式变了。输出片段：{out[:200]!r}"
        )
    by_rule: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split(" ")
        for p in parts:
            if p and len(p) > 1 and p[0].isalpha() and p[1:].isdigit() and p[1:].upper() == p[1:]:
                by_rule[p] = by_rule.get(p, 0) + 1
                break
    return total, by_rule


def format_findings() -> int:
    base = _ruff_cmd()
    if base is None:
        raise FileNotFoundError("ruff")
    rc, out = _run([*base, "format", "--check", *TARGETS])
    # ⚠️ 不能用 `split(" files")[0]` —— `ruff format --check` 在未格式化时会
    # 逐文件打 diff（"File would be reformatted\n --> src/..."），
    # 汇总行 "N files would be reformatted" 混在后面，第一版抓到了错的段。
    m = re.search(r"(\d+)\s+files?\s+would be reformat", out)
    if m:
        return int(m.group(1))
    if re.search(r"\d+\s+files?\s+already formatted|^\d+ files? already formatted", out, re.M) or "already formatted" in out:
        return 0
    raise RuntimeError(
        f"ruff format --check 没能给出结论（rc={rc}）。"
        f"**这不等于「都符合格式」**。输出片段：{out[:200]!r}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="把当前债务写成新基线（只在**有意**接受存量时用）",
    )
    a = ap.parse_args(argv)

    print("═" * 68)
    print("门禁 B：静态检查债务只许减、不许增")
    print("═" * 68)

    try:
        errors, by_rule = ruff_findings()
        unformatted = format_findings()
    except FileNotFoundError:
        print("  ⚠️ 找不到 ruff —— 本门禁需要它（uvx ruff 或 pip install ruff）")
        return 1
    except RuntimeError as exc:
        print(f"\n❌ {exc}")
        return 1

    print(f"  当前债务：ruff check {errors} 个错误，ruff format 待格式化 {unformatted} 个文件")
    if by_rule:
        top = sorted(by_rule.items(), key=lambda kv: -kv[1])[:6]
        print("    按规则：" + "  ".join(f"{k}={v}" for k, v in top))

    current = {
        "ruff_check_errors": errors,
        "ruff_format_files": unformatted,
        "by_rule": by_rule,
    }

    if a.update_baseline:
        BASELINE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n  ✅ 基线已更新 → {BASELINE.relative_to(ROOT)}")
        return 0

    if not BASELINE.exists():
        print(
            f"\n❌ 基线文件不存在：{BASELINE.relative_to(ROOT)}\n"
            f"   先跑一次 `python scripts/check_lint_debt.py --update-baseline` 记录存量。"
        )
        return 1

    try:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"\n❌ 基线文件读不出来：{exc}")
        return 1

    regressions: list[str] = []
    for key, label in (
        ("ruff_check_errors", "ruff check 错误数"),
        ("ruff_format_files", "ruff format 待格式化文件数"),
    ):
        before, now = int(base.get(key, 0)), int(current[key])
        if now > before:
            regressions.append(f"{label} 从 {before} 涨到 {now}（+{now - before}）")

    if regressions:
        print("\n❌ 静态检查债务**增长**了：")
        for r in regressions:
            print(f"  {r}")
        print(
            "\n  ⇒ 新写的代码带进了新问题。修完代码再跑；\n"
            "    如果确实是有意引入（例如为了绕开某个库的历史问题），\n"
            "    在 PR 里写明理由并同步调 `--update-baseline`，别默默放行。"
        )
        return 1

    dropped = int(base.get("ruff_check_errors", 0)) - errors
    tail = f"（较基线减少 {dropped}）" if dropped > 0 else "（与基线持平）"
    print(f"\n  ✅ 债务未增长 {tail} —— 允许存量，禁止新增")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())