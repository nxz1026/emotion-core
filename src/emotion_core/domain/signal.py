"""信号类型（signal 表）。5 条件 checklist + 1 警告。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SECONDARY = "SECONDARY"
    SELL = "SELL"
    # R58-4（2026-10-10）：新加次级推荐档。BUY 五条件全过未达时，c3 由
    # 「幸存集=={候选}」放宽为「候选 ∈ 幸存集」后的中间档——比 SECONDARY
    # 门槛高（仍要 c1/c2/c4/c5 + 最低板数），但比 BUY 宽松（不强求唯一幸存）。
    # 链路优先级：BUY → RECOMMEND → SECONDARY。RECOMMEND 仅作观察信号，不
    # 进入交易导出；语义上「准买入」而非「次级观察」。
    RECOMMEND = "RECOMMEND"


class SignalSource(str, Enum):
    """信号来源。replay=历史回填，live=实盘。"""
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True)
class Checklist:
    """5 条件 + 1 警告。None 表示 UNKNOWN（不可核验）。"""
    c1_uniqueness: bool | None      # 唯一换手高标
    c2_exchange: bool | None         # 换手板非一字
    c3_elimination: bool | None      # 淘汰赛身份（集合相等判定）
    c4_min_days: bool | None         # 最低板数门槛
    c5_strength_diverge: bool | None # 强度与分歧补偿（仅 ENHANCED）
    w1_crowding: bool | None         # 警告：同身位扎堆（永不否决）

    @property
    def passed(self) -> bool:
        """无否决条件即通过（filter None 后 all True）。

        ⚠️ 口径：**UNKNOWN(None) 不否决**，五项全 None 时返回 ``True``。
        这不是「空真 bug」，是显式契约——`algorithms/entry.passed_of` 的
        docstring 写着「过滤 None 后全 True 才算过（与回测一致）」，而回测侧
        一直是同一口径；Rust 侧 `entry::passed_of` 亦然。三边必须一致，
        `tests/oracle/test_exit_rust_vs_python.py:89` 断言的就是这个：
        卖出信号的 checklist 六项恒 UNKNOWN，`passed is True`。
        改动前请先看这条。

        「不可核验」这个第三态由**上层**表达，不由本属性表达：
        `services/diagnose_service.py:280` 在构造不出 checklist 时给出
        ``None``，前端据此渲染「不可核验」而非「全过」。
        """
        vals = [self.c1_uniqueness, self.c2_exchange, self.c3_elimination,
                self.c4_min_days, self.c5_strength_diverge]
        return all(v is True for v in vals if v is not None)


@dataclass(frozen=True)
class Signal:
    """信号。"""
    code: str
    date: date
    action: Action
    checklist: Checklist
    source: SignalSource
    status: str = "SUGGESTED"  # SUGGESTED / ADOPTED / EXPORTED
    # R58-4（2026-10-10）：以下两字段仅在内存中使用（BUY 即时推送需要展示
    # 股票名称与板数），**不入库**——`_persist` 只写 (date, code, action) +
    # window + checklist，不存 name/cont_days。frozen dataclass 默认值必须
    # 在末尾，旧字段次序保持不变。
    name: str | None = None
    cont_days: int | None = None
