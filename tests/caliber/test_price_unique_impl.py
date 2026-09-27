"""口径守护：涨停价全项目唯一实现（AST 静态扫描）。

裁决（docs/13 §S2）：涨停价是 is_limit_up / 连板 / 家数判定的地基，错一分钱全错。
历史事故是「三套实现并存」（奎爷 V2 决策合一），故用 AST 扫描禁止 `utils/price.py`
之外出现第二套公式：

* `x * 1.1` / `x * 1.2` 形态（浮点近似）；
* `x * 110` / `* 120` / `* 130` 形态（SQL 分整数式里的板块比例）；
* 以及定义 `limit_up_price*` / `limit_down_price*` 的第二个模块。

注意：**字符串/注释里的公式不算**（SQL 文本、文档说明），只扫语法树里的真实乘法；
展示层（`presentation/server.py`）的文案必须从 `utils/price.board_pct_milli` 现算，
不得写死数字——这条单独断言（审核文档 §9 第 4 条）。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "emotion_core"
PRICE_MODULE = SRC / "utils" / "price.py"

BANNED_FLOATS = {1.1, 1.2}
BANNED_INTS = {110, 120, 130, 1100, 1200}


def _iter_py_files():
    for p in sorted(SRC.rglob("*.py")):
        if p == PRICE_MODULE:
            continue
        if "__pycache__" in p.parts:
            continue
        yield p


def _numeric_mult_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
            continue
        for side in (node.left, node.right):
            if not isinstance(side, ast.Constant):
                continue
            v = side.value
            if isinstance(v, bool):
                continue
            if isinstance(v, float) and round(v, 6) in {round(x, 6) for x in BANNED_FLOATS}:
                bad.append(f"{path}:{node.lineno} 浮点板比乘数 {v}")
            if isinstance(v, int) and v in BANNED_INTS:
                bad.append(f"{path}:{node.lineno} 整数板比乘数 {v}")
    return bad


def test_no_second_limit_price_formula_in_ast():
    offenders: list[str] = []
    for path in _iter_py_files():
        offenders.extend(_numeric_mult_violations(path))
    assert not offenders, (
        "涨停价只能由 utils/price.py 实现，以下位置出现第二套公式：\n"
        + "\n".join(offenders))


def test_limit_price_functions_defined_once():
    defs: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and (
                    node.name.startswith("limit_up_price")
                    or node.name.startswith("limit_down_price")):
                defs.append(f"{path}:{node.lineno} {node.name}")
    assert not defs, f"涨停/跌停价函数只许定义在 utils/price.py：{defs}"
    src = PRICE_MODULE.read_text(encoding="utf-8")
    assert "def limit_up_price_cents(" in src
    assert "def limit_down_price_cents(" in src


def test_presentation_formula_is_derived_not_hardcoded():
    server = (SRC / "presentation" / "server.py").read_text(encoding="utf-8")
    assert "board_pct_milli" in server, (
        "展示层涨停价文案必须由 utils/price 现算，不得写死 110")
    assert "pre_close_cents * 110" not in server
    assert '"涨停价": "(pre_close_cents * 110 + 50) // 1000"' not in server
