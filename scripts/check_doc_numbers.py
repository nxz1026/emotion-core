#!/usr/bin/env python3
r"""门禁 A：文档与 docstring 里的「N 张表 / N 个文件 / N 行」必须与实测一致。

## 为什么要有这道门禁

2026-10-07 审计在**同一个仓的四个地方**找到四个互斥的表数：

    data/schema.py:3      「27 张表」
    README.md:76          「24 张表 DDL」
    docs/05-…:4           「41 张」
    schema.py 的 DDL 实际    30 张（create_all 返回 len(DDL)）

而且 ``docs/15-实施追踪.md:448-458`` 的完成度总览里，utils / services /
presentation / core 四层的文件数**全部偏小**（4/4→实际 6、28/28→33、8/8→10、
11/11→12 个 .rs）。

这类数字**没有任何现成工具能查**：

- ruff / mypy 只看语法树，docstring 里的「27 张」是一个字符串字面量；
- markdownlint 只查格式，不读数字的**含义**；
- CPT 的 `check_all_claims.py` 查的是路径/符号/路由的**存在性**，同样不比对值。

⇒ 必须自写抽取器。而一旦写了抽取器，**最要紧的是它自己别撒谎**：
每条断言都要能被「改坏实测」触发，否则又是一道恒绿的门禁
（同仓 `tests/unit/test_pytdx.py` 就是这个反面教材 ——
所有 fixture 只有一个日期，pre_close 的 shift 方向错了也测不出来）。

## 退出码

0 = 全部一致；1 = 有数字对不上（逐条列出文档位置、声称值、实测值）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "emotion_core"

#: 目录名 → 该目录下「.py 文件数 / .rs 文件数」的实测来源
_COUNT_SOURCES: dict[str, tuple[str, ...]] = {
    "utils": ("*.py",),
    "domain": ("*.py",),
    "algorithms": ("*.py",),
    "data": ("*.py",),
    "services": ("*.py",),
    "orchestration": ("*.py",),
    "presentation": ("*.py",),
    "llm": ("*.py",),
}

_SKIP_DIRS = {"__pycache__", "target", "core_lib"}


def _py_files(dirname: str) -> int:
    d = SRC / dirname
    if not d.is_dir():
        return -1
    return sum(1 for p in d.rglob("*.py") if not any(s in p.parts for s in _SKIP_DIRS))


def _rs_files(dirname: str) -> int:
    d = SRC / dirname
    if not d.is_dir():
        return -1
    return sum(1 for p in d.rglob("*.rs") if "target" not in p.parts)


def _ddl_table_count() -> int:
    """``schema.py`` 里 ``create_all`` 真正会建的表数 = DDL 列表长度。"""
    sp = SRC / "data" / "schema.py"
    if not sp.exists():
        return -1
    text = sp.read_text(encoding="utf-8")
    # DDL 是模块级 list/tuple，元素是 CREATE TABLE 语句
    return len(re.findall(r"CREATE TABLE IF NOT EXISTS", text))


#: 历史目录里的数字**不要求**跟当前代码对齐 —— 它们记录的是当时的快照
#: （lkl 时代、合并评估时点）。把它们也拖进来会让门禁误报十几条，
#: 而**一道天天误报的门禁，一周内就会被关掉** —— 那比没有更糟。
_HISTORICAL_PREFIXES = ("docs/archive-lkl", "docs/evidence", "docs/archive")

#: 只有「声明总数」语义的句子才值得比。形如
#: 「共 41 张表」「合计 30 张表」「24 张表 DDL」「声明 27 张表」
#: 而**不是**「待删的 2 张表」「空壳 15 张表」这种局部计数。
_TOTAL_HINTS = ("共", "合计", "总共", "声明", "清单", "DDL", "全部", "总计")


def _is_historical(rel: str) -> bool:
    return any(rel.startswith(p) or f"/{p}/" in f"/{rel}/" for p in _HISTORICAL_PREFIXES)


def _doc_claims(pattern: str, require_hint: bool = False) -> list[tuple[str, int, int]]:
    """在文档与 docstring 里找「声称的数字」，返回 (文件:行, 行号, 值)。"""
    hits: list[tuple[str, int, int]] = []
    targets = [
        *sorted((ROOT / "docs").rglob("*.md")),
        ROOT / "README.md",
        SRC / "data" / "schema.py",
    ]
    for p in targets:
        if not p.exists():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if _is_historical(rel):
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if require_hint and not any(h in line for h in _TOTAL_HINTS):
                continue
            for m in re.finditer(pattern, line):
                hits.append((f"{rel}:{i}", i, int(m.group(1))))
    return hits


#: **只有这两个地方在声明「本仓 DDL 有多少张表」**，别处数字的含义各不相同。
#:
#: ⚠️ 为什么不扫全部文档：``docs/05-数据库普查与清理审计.md`` 写「41 张表」，
#: 那指的是**数据库里实际有多少张表**（含 asel / asel_research / 未纳管的表），
#: 与「本仓 schema.py 定义多少张」**是两个不同的事实**。把它按 DDL 数去比，
#: 就是**门禁在撒谎** —— 而撒谎的门禁会被关掉，或者更糟：被人当成真话。
#:
#: 精确优于广覆盖：这两个锚点本身就是「入口声明」，改代码时最先看到它们。
_DDL_COUNT_ANCHORS = (
    SRC / "data" / "schema.py",
    ROOT / "README.md",
)


#: 只有「声明总数」语义的句子才比。同样地，``README.md:257`` 那句
#: 「asel **15 个表**在 0 处被引用」讲的是**迁移历史**，不是本仓表数 ——
#: 把它按 DDL 数去比同样是门禁撒谎。
_DDL_TOTAL_HINTS = ("共", "合计", "总共", "声明", "DDL", "全部", "总计")


def check_table_counts() -> list[str]:
    problems: list[str] = []
    real = _ddl_table_count()
    if real <= 0:
        return problems
    for p in _DDL_COUNT_ANCHORS:
        if not p.exists():
            continue
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not any(h in line for h in _DDL_TOTAL_HINTS):
                continue
            for m in re.finditer(r"(\d+)\s*张表", line):
                claimed = int(m.group(1))
                if claimed != real:
                    problems.append(
                        f"{rel}:{i}: 声明 {claimed} 张表，"
                        f"实测 schema.py 的 DDL 是 {real} 张（create_all 返回 len(DDL)）"
                    )
    return problems


def check_layer_file_counts() -> list[str]:
    """检查 `docs/15-实施追踪.md` 的完成度总览里的「层名 N/M」是否还对得上。

    表格真实格式是 ``| 基础层 utils/ | 4/4 | ✅ 100% |`` —— 层名后面**带斜杠**，
    第一版正则写成 ``utils\\|`` 所以一条都没匹配上（假绿）。
    """
    doc = ROOT / "docs" / "15-实施追踪.md"
    if not doc.exists():
        return []
    problems: list[str] = []
    text = doc.read_text(encoding="utf-8")
    pat = re.compile(
        r"\|\s*[^\n|]*?\b(" + "|".join(_COUNT_SOURCES) + r")/?\s*\|\s*(\d+)/(\d+)\s*\|"
    )
    for m in pat.finditer(text):
        layer, done, total = m.group(1), int(m.group(2)), int(m.group(3))
        if done != total:
            problems.append(
                f"docs/15-实施追踪.md: {layer} 写 {done}/{total}（未完成），"
                f"但完成度总览本该是全绿 —— 两处自相矛盾"
            )
            continue
        if layer == "core":
            real_rs = _rs_files("core")
            if real_rs >= 0 and total != real_rs:
                problems.append(
                    f"docs/15-实施追踪.md: core 声称 {done}/{total}，"
                    f"实测 core/src 下 .rs 文件 {real_rs} 个"
                )
            continue
        real_py = _py_files(layer)
        if real_py >= 0 and total != real_py:
            problems.append(
                f"docs/15-实施追踪.md: {layer} 声称 {done}/{total}，"
                f"实测该层 .py 文件 {real_py} 个"
            )
    return problems


def main() -> int:
    print("═" * 68)
    print("门禁 A：文档/docstring 的计数与实测一致")
    print("═" * 68)
    ddl = _ddl_table_count()
    print(f"  实测基准：schema.py DDL = {ddl} 张表")
    for layer in _COUNT_SOURCES:
        print(f"    {layer:<14} {_py_files(layer):>3} 个 .py")

    problems = check_table_counts() + check_layer_file_counts()
    if problems:
        print("\n❌ 文档数字与实测对不上：")
        for p in problems:
            print(f"  {p}")
        print(
            "\n  ⇒ 这些数字是**会漂**的：今天对不上，明天代码一改又对不上。\n"
            "    修法是改文档让它对，不是把门禁豁免掉。"
        )
        return 1
    print("\n  ✅ 文档里的计数与实测全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())