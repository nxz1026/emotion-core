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
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "emotion_core"
PRICE_MODULE = SRC / "utils" / "price.py"
RUST_INDICATORS = SRC / "core" / "src" / "indicators.rs"

BANNED_FLOATS = {1.1, 1.2}
BANNED_INTS = {110, 120, 130, 1100, 1200}


def _iter_py_files():
    for p in sorted(SRC.rglob("*.py")):
        if p == PRICE_MODULE:
            continue
        if "__pycache__" in p.parts:
            continue
        yield p


def _iter_rust_files():
    """Rust 侧同样纳入「唯一实现」扫描范围。

    ★2026-10-07：此前只扫 `*.py`，于是 `core/src/*.rs` 完全在门禁视野之外——
    事实上的结果是 Rust 侧可以自由长出第二套板块比例，而没人拦得住。
    （`board_pct_milli` 漏掉北交所 `"92"` 号段就是这么活下来的。）
    """
    core = SRC / "core" / "src"
    if not core.is_dir():
        return
    yield from sorted(core.rglob("*.rs"))


_LINE_COMMENT = re.compile(r"//.*$", re.M)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RUST_STRING = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")


def _strip_rust_comments_and_strings(text: str) -> str:
    """去掉注释与字符串字面量——否则文档里的公式会被当成真代码。

    （Rust 没有 Python 的 `//` 与 `/* */` 之外的第三种注释；但宏里的 doc 注释
    `///` 会被 `//` 规则一并吃掉，正好。）
    """
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return _RUST_STRING.sub('""', text)


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


_RUST_MULT = re.compile(
    r"\*\s*(" + "|".join(str(v) for v in sorted(BANNED_INTS)) + r")\b")


def test_no_second_limit_price_formula_in_rust():
    """Rust 侧同样禁止硬编码板块比例（乘数形态）。

    Rust 的合法写法是 `pct` 由 board_pct_milli 给出、再进 `(x*(1000+pct)+500)/1000`，
    全文不该出现 110/120/130/1100/1200 这类被直接乘上去的字面量。
    """
    offenders: list[str] = []
    for path in _iter_rust_files():
        text = _strip_rust_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            if _RUST_MULT.search(line):
                offenders.append(f"{path}:{lineno} 整数板比乘数 {line.strip()}")
    assert not offenders, (
        "涨停价板块比例只能由 board_pct_milli 给出，以下位置出现硬编码乘数：\n"
        + "\n".join(offenders))


def _is_starts_with(func: ast.expr) -> bool:
    """识别 `str.startswith(...)`，容忍 snake/camel 两种写法。

    （Rust 侧叫 `starts_with`、Python 侧叫 `startswith`；早先按 `starts_with`
    过滤导致提取结果恒为空字典，下面的对等断言**空转通过**——门禁看起来在跑，
    实际什么都没比。）
    """
    return (isinstance(func, ast.Attribute)
            and func.attr.replace("_", "").lower() == "startswith")


def _python_board_prefixes() -> dict[int, list[str]]:
    """从 utils/price.board_pct_milli 的 AST 抽出 {返回值: [前缀...]}。

    故意**从 Python 侧推导**而不是在测试里再抄一份表：测试与实现共用同一份
    真相，就不存在「两份表一起过期」的可能。
    """
    tree = ast.parse(PRICE_MODULE.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "board_pct_milli")
    out: dict[int, list[str]] = {}
    for node in fn.body:
        if not isinstance(node, ast.If):
            continue
        ret = next((s for s in node.body if isinstance(s, ast.Return)), None)
        call = next((s for s in ast.walk(node.test)
                     if isinstance(s, ast.Call) and _is_starts_with(s.func)), None)
        if (ret is None or call is None
                or not isinstance(ret.value, ast.Constant)
                or not isinstance(ret.value.value, int)):
            continue
        prefix = call.args[0]
        items = prefix.elts if isinstance(prefix, ast.Tuple) else [prefix]
        out.setdefault(ret.value.value, []).extend(
            i.value for i in items
            if isinstance(i, ast.Constant) and isinstance(i.value, str))
    return out


def test_python_board_prefix_extraction_is_not_vacuous():
    """提取器自身不许空转。

    ★2026-10-07：`_python_board_prefixes` 的属性名过滤写成了 Rust 的 `starts_with`，
    而 Python 侧是 `startswith` ⇒ 恒返回 `{}` ⇒ 对等断言「没有任何遗漏」，
    **永远绿**。这类「断言了但什么都没断言」的测试比没有测试更危险，
    所以先把提取器的输出本身钉住。
    """
    prefixes = _python_board_prefixes()
    assert prefixes, "前缀提取器返回空——后续对等断言会空转通过"
    flat = {p for ps in prefixes.values() for p in ps}
    # 四个板块必须都在：主板之外，创业/科创 20%、北交所 30%（含 920 号段）
    for required in ("bj", "30", "68", "4", "8", "92"):
        assert required in flat, f"前缀提取漏掉 {required!r}：{prefixes}"
    assert 300 in prefixes, f"北交所 30% 分支没抽到：{prefixes}"
    assert 200 in prefixes, f"创业/科创 20% 分支没抽到：{prefixes}"


def _rust_board_pct_body() -> str:
    """截出 Rust `board_pct_milli` 的**函数体**（不含其上方的文档注释）。

    ⚠️ 必须限定在函数体内：早期版本直接对整个文件做子串搜索，结果被函数上方
    解释「这里原本漏了 92」的那段文档注释里的 `"92"` 命中——门禁看起来是绿的，
    实际把前缀删掉照样放行。注释里出现前缀名，正是本文件里最容易自我欺骗的地方。
    """
    src = RUST_INDICATORS.read_text(encoding="utf-8")
    start = src.find("fn board_pct_milli")
    assert start != -1, f"{RUST_INDICATORS} 里找不到 board_pct_milli"
    end = src.find("\n}", start)
    assert end != -1, "board_pct_milli 函数体没有正常闭合（缺行首 `}`）"
    body = src[start:end]
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", body))


def test_rust_board_pct_covers_every_python_prefix():
    """★2026-10-07 回归：Rust `board_pct_milli` 必须覆盖 Python 侧的前缀全集。

    事故：北交所 2023-04 起启用 **920xxx** 号段，Python 判据是 `("4","8","92")`，
    Rust 只抄了 `4`/`8` ⇒ 920xxx 落到 else 拿到主板 10%，涨停价直接算错三成。
    两份实现各自「看起来都对」，没有任何测试比对过。

    为什么在这里做**源码级**比对而不是跑 Rust 对账：`core_lib/emotion_core_rust.so`
    是 gitignore 的构建产物，生产机上**没有 cargo、无法就地重建**，等 .so 重编前
    这条门禁是唯一能立刻发现分歧的东西。.so 重建后另有 `tests/oracle` 兜底。
    """
    body = _rust_board_pct_body()
    missing: list[str] = []
    for pct, prefixes in _python_board_prefixes().items():
        for prefix in prefixes:
            if f'"{prefix}"' not in body and f"'{prefix}'" not in body:
                missing.append(f"{prefix!r} → {pct} 千分比")
    assert not missing, (
        "Rust indicators.rs 的 board_pct_milli 漏掉了 Python 侧已有的板块前缀：\n  "
        + "\n  ".join(missing)
        + "\n  （Rust 与 Python 必须逐前缀对等；920 = 北交所，漏了会按主板 10% 算涨停价）"
    )


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
