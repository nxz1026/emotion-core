# 12 · LLM 层设计

- 日期：2026-09-26
- 前置：`v0.1-plan-frozen` · `docs/03`（R1/R2 需求）
- 目标：**让项目任何需要用 LLM 的地方可以填入 prompt，输出信息**；复用 Agnes
- 原则：**默认关闭**，主链不依赖 LLM

---

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| **抽象** | 定义 `LLMClient` 接口，任何 LLM 实现都实现这个接口 |
| **可插拔** | Agnes 是可插拔的实现之一，未来可换其他 LLM |
| **默认关闭** | 主链不依赖 LLM；LLM 层是可选的 |
| **prompt 模板化** | prompt 可填入、可版本化、可测试 |
| **可复用** | 直接复用 Agnes（lkl 的 `agnes-3.0-flash`） |

---

## 2. 架构

```
┌─────────────────────────────────────────────────┐
│  使用场景（调用方）                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │ 市场情绪  │ │ 推荐解释  │ │ 报告生成  │ ...     │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘         │
│       └─────────────┼─────────────┘               │
│                     ↓                             │
│  ┌─────────────────────────────────────┐         │
│  │  LLM 抽象层 (llm/base.py)           │         │
│  │  LLMClient.complete(prompt) -> str   │         │
│  └─────────────────┬───────────────────┘         │
│                    ↓                             │
│  ┌─────────────────────────────────────┐         │
│  │  Agnes 实现 (llm/agnes.py)          │         │
│  │  AgnesClient(LLMClient)             │         │
│  │  复用 agnes-3.0-flash               │         │
│  └─────────────────────────────────────┘         │
└─────────────────────────────────────────────────┘
```

---

## 3. 抽象层：`llm/base.py`

```python
"""LLM 抽象层：任何 LLM 实现都实现这个接口。

设计：填入 prompt，输出信息。不关心 LLM 是什么、怎么调、怎么计费。
默认关闭：主链不依赖 LLM；LLM 层是可选的。
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端接口。填入 prompt，输出信息。"""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """填入 prompt，输出信息。

        Args:
            prompt: 用户 prompt（可含模板变量）
            system: 系统提示（可选）
            temperature: 温度（0=确定性，1=随机）
            max_tokens: 最大输出 token 数
        Returns:
            LLM 输出的文本
        """
        ...


class LLMNotEnabledError(RuntimeError):
    """LLM 未启用时抛出。主链应捕获此错误并跳过 LLM 增强。"""


class NullClient:
    """空实现：LLM 未启用时使用。抛出 LLMNotEnabledError。"""

    def complete(self, prompt: str, **kwargs) -> str:
        raise LLMNotEnabledError("LLM 未启用；设置 LLM_PROFILE=agnes 启用")
```

**关键**：
- `LLMClient` 是 **Protocol**（结构化子类型），任何实现 `complete()` 的对象都是合法客户端
- `NullClient` 是默认实现，LLM 未启用时使用
- 主链捕获 `LLMNotEnabledError` 并跳过 LLM 增强

---

## 4. Agnes 实现：`llm/agnes.py`

**复用 lkl 的 Agnes**（`agnes-3.0-flash`）。

```python
"""Agnes LLM 实现：复用 lkl 的 agnes-3.0-flash。

lkl 的 strategy/runner.py 已用 Agnes 做 LLM 策略观察，
配置和调用方式直接复用，不重写。
"""
from __future__ import annotations
import os


class AgnesClient:
    """Agnes LLM 客户端（复用 lkl 的 agnes-3.0-flash）。"""

    def __init__(self, *, profile: str = "agnes", api_key: str | None = None):
        self.profile = profile
        self.api_key = api_key or os.environ.get("AGNES_API_KEY")
        self.model = "agnes-3.0-flash"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """填入 prompt，输出信息。复用 Agnes API。"""
        if not self.api_key:
            raise LLMNotEnabledError("未设置 AGNES_API_KEY")
        # 调用 Agnes API（复用 lkl 的调用方式）
        # ...
        return "LLM 输出"
```

**Agnes 的配置**（复用 lkl）：
- 模型：`agnes-3.0-flash`
- API Key：`AGNES_API_KEY` 环境变量
- 调用方式：复用 lkl 的 `llm.py` 的 `LLMClient` 抽象

---

## 5. Prompt 模板：`llm/prompts/`

**每个使用场景一个 prompt 模板**，模板可填入变量、可版本化、可测试。

### 5.1 市场情绪解读（`emotion.md`）

```markdown
你是 A 股市场情绪分析助手。请用通俗语言描述当前市场情绪。

## 输入
- 情绪阶段：{phase}
- 涨停家数：{limit_up_count}
- 炸板数：{bomb_count}
- 连板高度：{max_height}
- 生态评级：{ecosystem_rating}

## 要求
1. 用通俗语言，不用术语（如"发酵期"要翻译成"上升期"）
2. 3 句话以内
3. 不要给买卖建议
```

### 5.2 推荐解释（`recommendation.md`）

```markdown
你是 A 股推荐解释助手。请解释为什么推荐这只股票。

## 输入
- 代码：{code}
- 连板数：{cont_days}
- 是否换手板：{is_exchange}
- 满足条件：{conditions}
- 历史样本：N={sample_size}

## 要求
1. 用通俗语言解释"为什么是它"
2. 给出历史样本的胜率（带 N）
3. 明确标注"统计参考，非投资建议"
```

### 5.3 报告生成（`report.md`）

```markdown
你是 A 股日报生成助手。请根据以下数据生成日报。

## 输入
{report_data}

## 要求
1. 生成 13 段日报（⓪~⑫）
2. 每段用通俗语言
3. 数据准确，不要编造
```

---

## 6. 使用场景

| 场景 | prompt 模板 | 调用方 | 默认 |
|---|---|---|---|
| **市场情绪解读** | `emotion.md` | 直观层 | 关 |
| **推荐解释** | `recommendation.md` | 直观层 | 关 |
| **报告生成** | `report.md` | 直观层 | 关 |
| **个股诊断** | `stock.md` | `/stock/<code>` | 关 |
| **策略观察** | `strategy.md` | 逻辑层 | 关 |

**所有场景默认关闭**。启用方式：设置环境变量 `LLM_PROFILE=agnes`。

---

## 7. 与主链的关系

```
主链（默认）
  ↓
LLM 层（可选）
  ↓ 如果启用
Agnes
```

**主链不依赖 LLM**。LLM 层是**增强**：
- 默认关闭时，主链用模板/规则生成文本
- 启用 LLM 后，主链用 LLM 生成更自然的文本

**示例**（市场情绪解读）：
- 默认：`"今天市场在上升期，历史上这种状态下信号次日平均高开 3.1%"`
- LLM 启用：`"今天市场情绪偏暖，涨停 50 只但炸板 5 只，说明封板力度一般。历史上类似状态下，信号次日平均高开 3.1%，但样本只有 150 个，仅供参考。"`

---

## 8. 待确认

| # | 问题 | 我的建议 |
|---|---|---|
| **L1** | LLM 抽象层用 `LLMClient` Protocol + `NullClient` 默认？ | **确认** |
| **L2** | Agnes 复用 lkl 的 `agnes-3.0-flash` + `AGNES_API_KEY`？ | **确认** |
| **L3** | 5 个使用场景（情绪/推荐/报告/个股/策略）？ | **确认** |
| **L4** | 默认关闭，`LLM_PROFILE=agnes` 启用？ | **确认** |
| **L5** | LLM 层是**增强**（主链不依赖）？ | **确认** |
