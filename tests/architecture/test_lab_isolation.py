"""架构守护：Lab 与主线的隔离（docs/20-自我进化闭环-Lab-设计与实施.md §2）。

三条硬边界，任意一条破了实验室就会污染生产：

1. **主线不得 import lab**：`src/emotion_core/` 里除 `lab/` 自身，任何模块都不许出现
   `emotion_core.lab` / `from emotion_core import lab`。
2. **lab 不得碰主线的编排/展示/服务层**：实验台只能靠 `utils`（DB 连接、价格换算）
   和 `algorithms` 里的纯规则对象，不能借 `services`/`orchestration`/`presentation` 的
   副作用（那些会写库、会推送）。
3. **lab 的写语句只能落在 `lab.` schema**：`INSERT/UPDATE/DELETE/CREATE` 的目标表
   必须带 `lab.` 前缀，且不含 `public.`。

为什么用 AST + SQL 字面量扫：lab 的 SQL 全是模块级字符串常量，正则扫不到
f-string 拼出来的表名才是真正的漏洞——因此另加一条「lab 里不许用 f-string 拼 SQL」
的显式检查（用 AST 找出 f-string 里出现 SQL 关键字的节点）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "emotion_core"
LAB = SRC / "lab"

_WRITE_PATTERNS = (
    re.compile(r"\binsert\s+into\s+([\w.]+)", re.I),
    re.compile(r"(?<!do )\bupdate\s+([\w.]+)\s+set\b", re.I),
    re.compile(r"\bdelete\s+from\s+([\w.]+)", re.I),
    re.compile(r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([\w.]+)", re.I),
)
_FORBIDDEN_LAYERS = ("services", "orchestration", "presentation", "data")


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                continue
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def test_mainline_never_imports_lab() -> None:
    offenders: list[str] = []
    for path in _py_files(SRC):
        if LAB in path.parents:
            continue
        hits = {m for m in _imports(path) if m == "emotion_core.lab" or m.startswith("emotion_core.lab.")}
        if hits:
            offenders.append(f"{path.relative_to(SRC)}: {sorted(hits)}")
    assert not offenders, "主线 import 了 lab（会造成实验污染生产）：\n" + "\n".join(offenders)


def test_lab_does_not_import_mainline_side_effect_layers() -> None:
    offenders: list[str] = []
    for path in _py_files(LAB):
        for mod in _imports(path):
            for layer in _FORBIDDEN_LAYERS:
                if mod == f"emotion_core.{layer}" or mod.startswith(f"emotion_core.{layer}."):
                    offenders.append(f"{path.relative_to(SRC)}: {mod}")
    assert not offenders, "lab 不许 import 主线的副作用层：\n" + "\n".join(offenders)


def test_lab_writes_only_into_lab_schema() -> None:
    offenders: list[str] = []
    for path in _py_files(LAB):
        text = path.read_text(encoding="utf-8")
        for pat in _WRITE_PATTERNS:
            for table in pat.findall(text):
                low = table.lower()
                if low.startswith("lab.") or low == "lab":
                    continue
                offenders.append(f"{path.relative_to(SRC)}: → {table}")
    assert not offenders, "lab 的写语句落在 lab schema 之外：\n" + "\n".join(offenders)


def test_lab_has_no_fstring_sql() -> None:
    """f-string 拼 SQL 会让上面那条扫描失效（表名是变量就扫不见）。"""
    bad: list[str] = []
    for path in _py_files(LAB):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(
                v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if re.search(r"\b(select|insert|update|delete)\b", literal, re.I):
                bad.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not bad, "lab 里出现 f-string 拼 SQL（写守卫会失效）：\n" + "\n".join(bad)


def test_lab_write_guard_rejects_public_tables() -> None:
    from emotion_core.lab.db import LabWriteDenied, check_write

    for sql in (
        "INSERT INTO public.signal (a) VALUES (1)",
        "UPDATE derived_bar SET a = 1",
        "DELETE FROM public.market_stat",
        "CREATE TABLE foo (a int)",
    ):
        try:
            check_write(sql)
        except LabWriteDenied:
            continue
        raise AssertionError(f"写守卫放过了 {sql!r}")

    for sql in (
        "INSERT INTO lab.lab_score (a) VALUES (1)",
        "UPDATE lab.lab_promotion SET status = 'approved'",
        "CREATE SCHEMA IF NOT EXISTS lab",
    ):
        check_write(sql)  # 不抛即通过


def test_lab_read_guard_rejects_unlisted_tables() -> None:
    from emotion_core.lab.db import LabWriteDenied, query_df

    for sql in ("SELECT * FROM cpt_track", "SELECT * FROM users LIMIT 1"):
        try:
            query_df(sql)
        except LabWriteDenied:
            continue
        raise AssertionError(f"读守卫放过了 {sql!r}")
