"""动态缺口检查：扫描代码库，返回当前已知的算法层缺口列表。

取代 ``algorithm.html`` 里硬编码的 ``{% set gaps = [...] %}`` 块——
后者修一次代码就要同步改一次模板，永远追不上实际状态。
本模块让缺口清单从代码事实出发，修一个少一个。

每条缺口格式：
    {"item": str, "detail": str, "impl": str, "status": "open"|"wontfix"|"fixed"}

调用方：``presentation/server.py`` 的 ``_load_algorithm_data`` 注入 context，
``algorithm.html`` 的「已知缺口」区块改为从 context 渲染。
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

log = logging.getLogger("emotion_core.gaps")

SRC = Path(__file__).resolve().parent.parent  # emotion-core/src/emotion_core


# ---------------------------------------------------------------------------
# 单条检查器
# ---------------------------------------------------------------------------


def _check_coverage_gate() -> dict | None:
    """覆盖率门槛是否真正接线到编排层？"""
    daily = (SRC / "orchestration" / "daily.py").read_text()
    if "coverage" not in daily and "gate" not in daily:
        return {
            "item": "覆盖率门槛未接线",
            "detail": "MIN_COVERAGE=0.90 仅为常量；<code>orchestration/daily.py</code>"
                    " 未调用 <code>coverage.gate()</code>。",
            "impl": "services/coverage.py:59-65；orchestration/daily.py",
        }
    return None  # 已接线，无缺口


def _check_health_step() -> dict | None:
    """health 步骤是否实际调用 health 模块？"""
    daily = (SRC / "orchestration" / "daily.py").read_text()
    # 检查是否真的调用了 health 服务，而非只打印 TODO
    if "health" in daily and "TODO" in daily and "health.push" not in daily:
        return {
            "item": "health 步骤是空实现",
            "detail": "每日编排只打印 TODO，<code>services/health.py</code>"
                    " 的断档检查未被调用。",
            "impl": "orchestration/daily.py",
        }
    # 检查 health 模块是否存在且被调用（可能在 algorithms/ 或 services/）
    health_py = (SRC / "algorithms" / "health.py")
    if not health_py.exists():
        health_py = (SRC / "services" / "health.py")
    if not health_py.exists():
        return {
            "item": "health 服务不存在",
            "detail": "<code>services/health.py</code> 不存在，"
                    "断档检查（日报/数据链/信号结果陈旧）未实现。",
            "impl": "services/health.py（缺失）",
        }
    return None


def _check_caliber_tests() -> dict | None:
    """tests/caliber/ 是否有测试文件？"""
    caliber_dir = SRC.parent.parent / "tests" / "caliber"
    if not caliber_dir.exists() or not any( caliber_dir.glob("test_*.py")):
        return {
            "item": "tests/caliber/ 为空",
            "detail": "规划的口径守护测试未落地；C1-C7 无专测。",
            "impl": "tests/caliber/",
        }
    return None


def _check_ecosystem_rs() -> dict | None:
    """ecosystem.rs 是否有实际实现（非空壳）？"""
    rs = SRC / "core" / "src" / "ecosystem.rs"
    if not rs.exists():
        return {
            "item": "ecosystem.rs 不存在",
            "detail": "生态评级 Rust 实现缺失，仅有 Python 一份。",
            "impl": "core/src/ecosystem.rs（缺失）",
        }
    text = rs.read_text()
    lines = [l for l in text.splitlines()
             if l.strip() and not l.strip().startswith("//")]
    if len(lines) < 20:  # 空壳阈值
        return {
            "item": "ecosystem.rs 是空壳",
            "detail": f"全文仅 {len(lines)} 行有效代码，"
                    "dragon_env.py 注释声称的 Rust 实现未完成。",
            "impl": f"core/src/ecosystem.rs:{len(lines)}",
        }
    return None


def _check_config_hash_callers() -> dict | None:
    """config_hash() 是否有调用方（除定义处）？"""
    config_py = (SRC / "utils" / "config.py").read_text()
    tree = ast.parse(config_py)
    func_name = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "config_hash" in node.name:
                func_name = node.name
                break
    if func_name is None:
        return {
            "item": "config_hash() 未定义",
            "detail": "utils/config.py 中无 config_hash 函数定义。",
            "impl": "utils/config.py",
        }
    # 搜索整个 src 树的调用
    callers = 0
    for py in SRC.rglob("*.py"):
        if "utils/config" in str(py):
            continue
        try:
            src = py.read_text()
            if func_name in src:
                callers += 1
        except Exception:
            pass
    if callers == 0:
        return {
            "item": "config_hash() 无调用方",
            "detail": f"函数 <code>{func_name}()</code> 定义后无人调用，"
                    "pipeline_state / signal / eval_result 无指纹。",
            "impl": "utils/config.py",
        }
    return None


def _check_signal_source() -> dict | None:
    """signal 表是否有 source 列？"""
    import re
    schema = (SRC / "data" / "schema.py").read_text()
    # 提取 signal 表的整个 CREATE 块（从 signal ( 到对应的关闭 )）
    start = schema.find("signal (")
    if start == -1:
        start = schema.find("signal(")
    if start > 0:
        # 找关闭的 )""" 或 )\n end of block
        end = schema.find(')"""', start)
        if end == -1:
            end = schema.find(")\n", start)
        block = schema[start:end] if end > start else ""
    else:
        block = ""
    if "source" not in block.lower():
        return {
            "item": "signal 表无 source 列",
            "detail": "entry._persist 写入的 signal 行无 source 列，"
                    "live / replay 无法在库内区分。",
            "impl": "data/schema.py；algorithms/entry.py",
        }
    return None


def _check_c7_bj_filter() -> dict | None:
    """C7：market_service 是否过滤 bj 前缀？"""
    ms = (SRC / "services" / "market_service.py").read_text()
    if "bj" not in ms and "'8" not in ms and '"8' not in ms:
        return {
            "item": "C7 北交所未过滤",
            "detail": "<code>services/market_service.py</code> 的"
                    " <code>_load_day_metrics</code> 无 bj/8 前缀过滤，"
                    "与 <code>algorithms/emotion.py</code> 的 _counts 口径不一致。",
            "impl": "services/market_service.py",
        }
    return None


def _check_outcome_step() -> dict | None:
    """outcome 步骤是否正确调用 outcome.backfill()？"""
    daily = (SRC / "orchestration" / "daily.py").read_text()
    if "outcome" in daily:
        if "backfill" not in daily and "replay_service" in daily:
            return {
                "item": "outcome 步骤接错服务",
                "detail": "outcome 步骤接到 replay_service.run（回填 signal），"
                        "而非 algorithms.outcome.backfill（回填 signal_outcome）。",
                "impl": "orchestration/daily.py",
            }
    return None


def _check_stock_basic_columns() -> dict | None:
    """stock_basic 是否有 industry / market_cap 列？"""
    schema = (SRC / "data" / "schema.py").read_text()
    # 搜索 stock_basic 定义
    if "stock_basic" in schema:
        # 简单检查 industry 和 market_cap 是否在 schema 中
        has_industry = "industry" in schema.lower()
        has_market_cap = "market_cap" in schema.lower()
        missing = []
        if not has_industry:
            missing.append("industry")
        if not has_market_cap:
            missing.append("market_cap")
        if missing:
            return {
                "item": f"stock_basic 缺列 {', '.join(missing)}",
                "detail": f"<code>diagnose_service</code> 查询"
                        f" {', '.join(missing)} 列，DDL 中不存在。",
                "impl": "data/schema.py",
            }
    return None


def _check_config_duplicates() -> dict | None:
    """CONFIG 是否有重复字段？"""
    config = (SRC / "utils" / "config.py").read_text()
    tree = ast.parse(config)
    # 查找 class Config
    fields = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and "Config" in node.name:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if name.isupper():  # 只检查大写的类字段
                        if name in fields:
                            return {
                                "item": f"CONFIG 内有重复字段 {name}",
                                "detail": f"<code>{name}</code> 在 Config 中声明两次，"
                                        "dataclass 取后者；不影响运行但属冗余。",
                                "impl": f"utils/config.py",
                            }
                        fields.add(name)
    return None


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

_CHECKERS = [
    _check_coverage_gate,
    _check_health_step,
    _check_caliber_tests,
    _check_ecosystem_rs,
    _check_config_hash_callers,
    _check_signal_source,
    _check_c7_bj_filter,
    _check_outcome_step,
    _check_stock_basic_columns,
    _check_config_duplicates,
]


def scan_gaps() -> list[dict]:
    """扫描代码库，返回当前所有已知缺口。

    每条缺口格式：
        {"item": str, "detail": str, "impl": str}
    """
    gaps: list[dict] = []
    for checker in _CHECKERS:
        try:
            result = checker()
            if result is not None:
                gaps.append(result)
        except Exception as exc:
            log.warning("gaps: checker %s 异常 — %s", checker.__name__, exc)
    return gaps


def scan_gaps_summary() -> dict:
    """返回缺口摘要（供 API / 日志使用）。"""
    gaps = scan_gaps()
    return {
        "total": len(gaps),
        "gaps": gaps,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(scan_gaps(), ensure_ascii=False, indent=2))
