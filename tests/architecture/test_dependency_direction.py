"""架构守护：依赖方向（docs/11-代码架构设计.md §1 §5）。

裁决依据
--------
§1「依赖方向自上而下，禁止反向依赖：core 不依赖 Python，algorithms 不直连 DB，
   presentation 不写库」；§5 层级表（低 → 高）：

    utils < core < domain < algorithms < data < services < orchestration < presentation

  外加 §4 的旁支：`llm/`「LLM 层（默认关，信号链路禁止 import llm）」。

本文件的判据只有一条：**任何模块 import 了比自己高的层，即违规**（同级/向下随便用）。
`domain` 是纯数据结构层（bar/ladder/signal/market/position），全员可 import，不视为违规。
`llm` 是与 algorithms 同级的旁支，只允许 import utils/core/domain/algorithms/llm。

历史欠账登记在 `_DEBT`（显式、带文件、带原因），使存量不红、新增即红；
`test_debt_is_not_stale` 保证偿还后必须从清单里划掉，欠账只减不增。

为什么用 AST 而不是 grep：字符串/注释里的模块名（SQL、文档、`importlib` 动态路径）
不算依赖；动态 import 另由 `test_notify_seam_keeps_two_paths` 单独钉住。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "emotion_core"

# 层级（低 → 高）。domain 全员可 import，不参与升降级判定。
_LEVEL = {
    "utils": 0,
    "core": 1,
    "core_lib": 1,        # Rust 编译产物目录（只有 .so），与 core 同级
    "algorithms": 3,
    "data": 4,
    "services": 5,
    "orchestration": 6,
    "presentation": 7,
    "llm": 3,             # 旁支：与 algorithms 同级
}
_SHARED = {"domain"}      # 全员可 import，不视为违规

# 信号链路禁止 import llm（§4）：这些文件惰性 import llm 是已知例外。
_LAZY_LLM_ALLOWLIST = {
    "services/stock_service.py",      # use_llm=True 时才惰性 import llm.render
}

# ── 历史欠账：登记的是「现状违规」的精确位置，只许减少。────────────────────
# 格式：(src_layer, dst_layer, src_file) -> 原因
_DEBT: dict[tuple[str, str, str], str] = {
    # A 类 algorithms → data（已偿还：改为走 services 胶水层，见 services/ladder_service.py / ecosystem_service.py）
    # ("algorithms", "data", "algorithms/dragon_env.py"): 已修
    # ("algorithms", "data", "algorithms/ladder.py"): 已修
    ("algorithms", "data", "algorithms/doctor.py"): (
        "自检脚本直接 import data.providers.* 做离线诊断；生产路径走 services"
    ),
    # 转移债务：A 类改走 services 后，变为 C 类（2026-09-29 偿还 A 类）
    ("algorithms", "services", "algorithms/dragon_env.py"): (
        "经 services.ecosystem_service 取数；最终方案应由 orchestration 注入"
    ),
    ("algorithms", "services", "algorithms/ladder.py"): (
        "经 services.ladder_service 取数；最终方案应由 orchestration 注入"
    ),
    # C 类 algorithms → services：review 包静态引用 services（2026-09-29 验收，待偿还）
    ("algorithms", "services", "algorithms/review/__init__.py"): (
        "review 子包静态 import services.* 做报告生成；应改走 orchestration 注入"
    ),
    # 旁支 llm → services：agnes 客户端反向引用 services 的 llm_backend（2026-09-29 发现）
    ("llm", "services", "llm/agnes.py"): (
        "agnes 客户端 import services/llm_backend 复用后端配置；应将共享逻辑下沉到 llm/base 或 domain"
    ),
}


def _layer_of(module_parts: list[str]) -> str | None:
    """emotion_core.<layer>.<...> -> layer name, or None if root/unknown."""
    if len(module_parts) < 2:
        return None  # `import emotion_core` — root, skip
    return module_parts[1]


def _scan_violations() -> set[tuple[str, str, str]]:
    """Return {(src_layer, dst_layer, src_file)} for every up-gradient import.

    使用 ast.walk 而非 iter_child_nodes：嵌套在函数/类里的惰性 import 同样是
    「直连 DB」，架构规则不区分顶层/嵌套。这与 docs §1「algorithms 不直连 DB」
    的严格语义一致。
    """
    violations: set[tuple[str, str, str]] = set()
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(SRC)
        parts = rel.parts
        if not parts:
            continue
        src_layer = parts[0]
        if src_layer not in _LEVEL:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(a.name for a in node.names)
            for module in modules:
                if not module.startswith("emotion_core"):
                    continue
                mp = module.split(".")
                dst_layer = _layer_of(mp)
                if dst_layer is None or dst_layer == src_layer:
                    continue
                if dst_layer in _SHARED:
                    continue  # domain 全员可 import
                if dst_layer not in _LEVEL:
                    continue  # 未知层（如 emotion_core.trade 占位）→ 跳过，不误报
                if _LEVEL[dst_layer] > _LEVEL[src_layer]:
                    violations.add((src_layer, dst_layer, str(rel)))
    return violations


# ── 1. 主护栏：新增反向依赖即红 ──────────────────────────────────────────────
def test_no_new_reverse_dependency():
    violations = _scan_violations()
    unknown = violations - set(_DEBT.keys())
    if unknown:
        msg = "发现未登记的反向依赖（新增即红，请先修复或登记到 _DEBT）：\n"
        for src, dst, f in sorted(unknown):
            msg += f"  {src} -> {dst}  ({f})\n"
        msg += "\n修复：把 import 改走 services 注入；\n登记：在 _DEBT 里加一条并写明原因（只许减少）。"
        pytest.fail(msg)


# ── 2. 欠账只减不增：已偿还的必须从 _DEBT 划掉 ───────────────────────────────
def test_debt_is_not_stale():
    violations = _scan_violations()
    stale = set(_DEBT.keys()) - violations
    if stale:
        msg = "以下 _DEBT 已偿还（不再违规），请从 _DEBT 划掉以保持清单新鲜：\n"
        for key in sorted(stale):
            msg += f"  {key}\n"
        pytest.fail(msg)


# ── 3. 信号链路禁止 import llm（§4）：模块级 import 一律禁止 ────────────────
def test_signal_chain_does_not_import_llm():
    """非 llm 模块在模块级 import llm → 违规；惰性（函数内）import 另由下一测试管。"""
    bad: list[tuple[str, int]] = []
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(SRC)
        parts = rel.parts
        if not parts or parts[0] == "llm":
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError:
            continue
        # 只检查顶层语句（模块级 import），嵌套的由 test_lazy_llm_imports_allowlisted 管
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("emotion_core.llm"):
                bad.append((str(rel), node.lineno))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("emotion_core.llm"):
                        bad.append((str(rel), node.lineno))
    if bad:
        msg = "信号链路禁止模块级 import llm（应惰性 import 或走 services 注入）：\n"
        for f, ln in bad:
            msg += f"  {f}:{ln}\n"
        pytest.fail(msg)


# ── 4. 惰性 import llm 只许在允许列表里 ──────────────────────────────────────
def test_lazy_llm_imports_allowlisted():
    """函数内惰性 import llm 只许在 _LAZY_LLM_ALLOWLIST 中的文件。"""
    # 检测：import 节点是否嵌套在 FunctionDef/AsyncFunctionDef 内。
    import ast as _ast

    class _Visitor(_ast.NodeVisitor):
        def __init__(self):
            self.nested_imports: list[tuple[str, int]] = []
            self._depth = 0

        def visit_FunctionDef(self, node):
            self._depth += 1
            self.generic_visit(node)
            self._depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ImportFrom(self, node):
            if node.module and node.module.startswith("emotion_core.llm") and self._depth > 0:
                self.nested_imports.append((node.module, node.lineno))

        def visit_Import(self, node):
            for a in node.names:
                if a.name.startswith("emotion_core.llm") and self._depth > 0:
                    self.nested_imports.append((a.name, node.lineno))

    bad: list[tuple[str, int]] = []
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(SRC)
        parts = rel.parts
        if not parts or parts[0] == "llm":
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError:
            continue
        v = _Visitor()
        v.visit(tree)
        if str(rel) not in _LAZY_LLM_ALLOWLIST and v.nested_imports:
            bad.extend([(str(rel), ln) for _, ln in v.nested_imports])
    if bad:
        msg = "惰性 import llm 只许在 _LAZY_LLM_ALLOWLIST 中的文件：\n"
        for f, ln in bad:
            msg += f"  {f}:{ln}\n"
        pytest.fail(msg)


# ── 5. notify 动态接缝：alerts.py 必须保留两条 import 路径 ────────────────────
def test_notify_seam_keeps_two_paths():
    """9515f45 事故：alerts.py 曾因单一路径 import 错误导致 4 天 webhook 静默。
    本测试钉住：必须同时存在 services.notify 与 algorithms.notify 两条路径，
    且通过 importlib.import_module 动态选择。
    """
    alerts = SRC / "algorithms" / "alerts.py"
    src = alerts.read_text(encoding="utf-8")
    assert "emotion_core.services.notify" in src, "alerts.py 必须保留 services.notify 路径"
    assert "emotion_core.algorithms.notify" in src, "alerts.py 必须保留 algorithms.notify 路径"
    assert "importlib.import_module" in src, "alerts.py 必须通过 importlib.import_module 动态选择"


# ── 6. Rust core 不依赖 Python ───────────────────────────────────────────────
def test_rust_core_does_not_depend_on_python():
    """core/src/ 下的纯计算 .rs 文件不应在代码（非注释）中引用 emotion_core。

    lib.rs 是 pyo3 binding 层（#[pymodule] 函数名必含 emotion_core），允许跳过。
    纯计算模块（indicators/state/ladder/...）是 Rust 核心，不应知道 Python 模块路径。
    """
    core_dir = SRC / "core" / "src"
    if not core_dir.exists():
        pytest.skip("core/src 不存在（Rust 核心未编译）")
    bad: list[tuple[str, int]] = []
    for rs in core_dir.rglob("*.rs"):
        if rs.name == "lib.rs":
            continue  # pyo3 binding 层，函数名 emotion_core_rust 是必须的
        for i, line in enumerate(rs.read_text(encoding="utf-8").splitlines(), 1):
            code = re.split(r"//|/\*", line, 1)[0]  # 去掉行注释
            if "emotion_core" in code:
                bad.append((str(rs.relative_to(SRC)), i))
    if bad:
        msg = "Rust core 纯计算层不应在代码中引用 emotion_core（pyo3 binding 层除外）：\n"
        for f, ln in bad:
            msg += f"  {f}:{ln}\n"
        pytest.fail(msg)


# ── 7. presentation 不写库 ───────────────────────────────────────────────────
def test_presentation_is_read_only():
    """presentation 层只允许 query_df / connect_ro，禁止 execute / executemany / transaction。
    （docs §1：presentation 不写库）
    """
    pres = SRC / "presentation"
    if not pres.exists():
        pytest.skip("presentation 目录不存在")
    write_funcs = ("execute(", "executemany(", "transaction(")
    bad: list[tuple[str, int, str]] = []
    for py in pres.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if any(w in line for w in write_funcs):
                bad.append((str(py.relative_to(SRC)), i, line.strip()))
    if bad:
        msg = "presentation 层禁止写库（只允许 query_df / connect_ro）：\n"
        for f, ln, text in bad:
            msg += f"  {f}:{ln}  {text}\n"
        pytest.fail(msg)
