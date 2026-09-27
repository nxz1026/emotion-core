"""市场状态机胶水层（§9 第 5 条 C7）：必须转发给 algorithms/emotion，不得自持口径。

历史缺口：market_service 自持 `_load_day_metrics` + `state.classify_series`，
与 emotion._counts 口径分叉（无次新过滤、maxh 未限主板、bomb_rate 等恒 None），
而 daily 跑的正是这一份 —— 等于 661 天对账过的 emotion 在生产链上零调用。
现在它只允许做一件事：把单日增量交给 emotion.run_range。

断言只针对**代码**（AST 取出非 docstring 字符串字面量），模块文档里复述历史不算违规。
"""
from __future__ import annotations

import ast
import inspect
from datetime import date
from types import SimpleNamespace

from emotion_core.services import market_service

D = date(2026, 9, 25)


def _code_string_literals(src: str) -> list[str]:
    """模块里所有**非文档字符串**的字符串常量（= 真实 SQL/取值，不是说明文）。"""
    tree = ast.parse(src)
    docs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            ds = ast.get_docstring(node, clean=False)
            if ds is not None:
                docs.add(ds)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docs]


def test_run_delegates_single_day_to_emotion_run_range(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(market_service, "emotion", SimpleNamespace(
        run_range=lambda a, b: calls.append((a, b)) or 1))
    assert market_service.run(D) == 1
    assert calls == [(D, D)], "单日增量必须是 run_range(d, d)（自带 warm-up 前情）"


def test_module_holds_no_sql_and_no_second_state_machine():
    src = inspect.getsource(market_service)
    code = "\n".join(_code_string_literals(src))
    for banned in ("FROM ", "SELECT ", "INSERT INTO", "classify_series"):
        assert banned not in code, f"胶水层代码里不得出现 {banned!r}"
    assert not hasattr(market_service, "_load_day_metrics"), "第二套单日装载必须删净"
    assert "emotion.run_range" in inspect.getsource(market_service.run)


def test_glue_layer_is_thin():
    """除 run 外不暴露业务函数：口径只在 algorithms 层。"""
    fns = {n for n, v in vars(market_service).items()
           if inspect.isfunction(v) and v.__module__ == market_service.__name__}
    assert fns == {"run"}


def test_emotion_is_the_only_market_stat_source_in_services():
    """服务层不直接碰 market_stat（唯一写者是 algorithms/emotion）。"""
    from emotion_core.services import derive_service

    for mod in (derive_service, market_service):
        code = "\n".join(_code_string_literals(inspect.getsource(mod)))
        assert "market_stat" not in code, f"{mod.__name__} 不得自持 market_stat 读写"


def test_run_signature_is_one_date() -> None:
    sig = inspect.signature(market_service.run)
    assert list(sig.parameters) == ["trade_date"]
    assert "emotion.run_range" in (market_service.run.__doc__ or "")
