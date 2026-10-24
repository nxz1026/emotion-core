"""涨停价唯一实现 —— **已迁至 Emotion-CPT**（2026-10-24）。

**唯一定义处**现在是 ``emotion_cpt.structure.price``（整数分运算）。
本文件保留为**转发层**，让仓内 4 个调用点零改动：

- ``algorithms/indicators.py``（模块级 import）
- ``algorithms/intraday.py``（模块级 import）
- ``presentation/server.py``（模块级 import）
- ``services/stock_service.py``（函数内 import）

## 为什么搬

此前**两套实现并存**（emotion-core 的整数分 vs CPT 的 Decimal），
跨仓对账用例虽能发现分歧，但那是**事后**；统一后是**事前**只有一份。

## 为什么保留转发层而不是改调用点

四个调用点分布在 algorithms / presentation / services 三层，
改它们会让 diff 变大、review 变难，而**转发层是零行为变化**：
``emotion_cpt.structure.price`` 的整数分实现与本仓原实现**逐分相同**
（穷举 12 个代码 × 6 个名字 × 35 个分位，见 Emotion-CPT 的
``tests/test_price_equivalence.py``）。

⚠️ 仓内那条「涨停价只许有一份实现」的 AST 门禁
（``tests/caliber/test_price_unique_impl.py``）已同步更新：
它现在断言**本仓内不再有任何实现**，且 Python 侧的前缀全集取自
Emotion-CPT —— **Rust 侧的 ``board_pct_milli`` 仍必须覆盖它**（跨语言对等门禁不变）。
"""

from __future__ import annotations

from emotion_cpt.structure.price import (
    board_pct_milli,
    is_limit_down,
    is_limit_up,
    limit_down_price_cents,
    limit_up_price_cents,
)

__all__ = (
    "board_pct_milli",
    "is_limit_down",
    "is_limit_up",
    "limit_down_price_cents",
    "limit_up_price_cents",
)
