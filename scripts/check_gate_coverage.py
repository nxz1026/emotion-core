#!/usr/bin/env python3
"""门禁 C：每道门禁都必须真的被 CI 跑，不许「写了却从不执行」。

## 为什么

CPT 项目有一条血泪教训：它自己做过一道同名门禁
（``check_gate_coverage.py``），起因是**新门禁接进 CI 却忘了配自检夹具**，
于是「它能不能红」从未被证明，而 CI 依然全绿 —— 因为它没红。

本仓此前**连 CI 都没有**（无 ``.github/``），所以这道门禁是连同 CI 一起新建的。
它防的就是下一步：有人加了 ``scripts/check_*.py`` 却忘了写进 ``ci.yml``。

判据（三方对齐）：

- ``ci.yml`` 里跑的 ``scripts/check_*.py``  ⊆  ``GATES`` 的键（不许逃逸）
- ``GATES`` 的键都必须指向**真实存在**的脚本（不许死登记）
- 磁盘上的 ``scripts/check_*.py`` ⊆ ``ci.yml`` 跑的（不许闲置）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WF_DIR = ROOT / ".github" / "workflows"

SELF = "check_gate_coverage.py"

#: 门禁登记处。每加一道门禁，这里加一行**并**写进 ci.yml —— 两边缺一即红。
GATES: tuple[str, ...] = (
    "check_doc_numbers.py",
    "check_lint_debt.py",
    SELF,
)

#: 名字像门禁、但不该进 CI 的脚本 → 理由（**每条必须写理由**）
NOT_A_GATE: dict[str, str] = {}


def _ci_gates() -> set[str]:
    found: set[str] = set()
    for p in sorted(WF_DIR.glob("*.y*ml")) if WF_DIR.exists() else []:
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"scripts/(check_[A-Za-z0-9_]+\.py)", text):
            found.add(m.group(1))
    return found


def _disk_gates() -> set[str]:
    return {p.name for p in SCRIPTS.glob("check_*.py")} if SCRIPTS.exists() else set()


def main() -> int:
    if not all(reason.strip() for reason in NOT_A_GATE.values()):
        print("❌ 豁免表里有空理由 —— 豁免必须逐条说明")
        return 1

    print("═" * 68)
    print("门禁 C：没有门禁能逃过体检")
    print("═" * 68)

    a, b, c = _ci_gates(), set(GATES), _disk_gates()
    problems: list[str] = []

    for name in sorted(a - b):
        problems.append(
            f"R1 逃逸：`{name}` 在 ci.yml 里跑，但不在 GATES 登记 ⇒ "
            f"它**能不能红**从未被证明"
        )
    for name in sorted(b):
        if not (SCRIPTS / name).exists():
            problems.append(
                f"R2 死登记：GATES 里的 `{name}` 在 scripts/ 下不存在"
            )
    for name in sorted(c - a - set(NOT_A_GATE)):
        problems.append(
            f"R3 闲置：`scripts/{name}` 存在，但 ci.yml 从不跑它 ⇒ 写了等于没写"
        )

    if problems:
        print("❌ 门禁清单与 CI 对不上：")
        for p in problems:
            print(f"  {p}")
        print(
            "\n  ⇒ 修法：新增门禁时**同一个 commit 里**改 scripts/check_gate_coverage.py "
            "的 GATES 和 .github/workflows/ci.yml。"
        )
        return 1

    print(
        f"  ✅ ci.yml 跑 {len(a)} 道门禁，登记 {len(b)} 条，"
        f"磁盘上 check_*.py 共 {len(c)} 个"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())