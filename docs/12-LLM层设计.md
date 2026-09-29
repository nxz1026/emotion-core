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
| **策略观察** | `llm/strategies/*.yaml`（15 个） | 编排层 | 关 |

**所有场景默认关闭**。前 4 个场景的启用方式：设置环境变量 `LLM_PROFILE=agnes`。

**策略观察例外**：它多一道独立门 `EC_STRATEGY_ENABLED`（见 §6.1），
`LLM_PROFILE` 只决定用哪个后端，不决定开关。

### 6.1 策略观察子系统（DSA 移植）

移植自 `daily_stock_analysis`（MIT，见 `docs/archive-lkl/adr/0003-m9-dsa-integration.md`），
只搬模块思想、不合并仓库，因此**无运行时依赖**。

```
src/emotion_core/services/strategy/
├── context.py    67 行   组装个股上下文（行情/涨停/情绪）注入 prompt
├── loader.py    125 行   纯 stdlib YAML 子集加载器 → Skill 数据类
├── runner.py    237 行   主流程：门控 → 加载技能 → 建票池 → 调 LLM → 落库
└── universe.py   37 行   当日票池

src/emotion_core/llm/strategies/*.yaml    15 个策略
```

15 个策略：`bottom_volume` `box_oscillation` `bull_trend` `chan_theory` `dragon_head`
`emotion_cycle` `event_driven` `expectation_repricing` `growth_quality` `hot_theme`
`ma_golden_cross` `one_yang_three_yin` `shrink_pullback` `volume_breakout` `wave_theory`

> ⚠️ 每个 YAML 首行是 MIT 出处署名，**不得删除**（删掉即违反 DSA 的 MIT 许可）：
> `# Ported from daily_stock_analysis (MIT, Copyright (c) 2026 ZhuLinsen), adapted to lkl data context`

**两道门，缺一不可**（`runner.py:run_for_date`）：

| 次序 | 位置 | 条件 | 关闭时行为 |
|---|---|---|---|
| 1 | `runner.py:98` | `CONFIG.STRATEGY_ENABLED` ← `EC_STRATEGY_ENABLED` | 打一条 `策略观察已关闭` 后返回 0 |
| 2 | `runner.py:105` | `STRATEGY_DIR` 解析出的技能非空 **且** 票池非空 | 打一条 `skills=N codes=M, 跳过` 后返回 0 |

**`STRATEGY_DIR` 锚点契约**（`runner.py:22`）：

```python
STRATEGY_DIR = Path(__file__).resolve().parents[2] / "llm" / "strategies"
#                                        ^^^^^^^^^^ 必须是包根 emotion_core
```

`runner.py` 位于 `emotion_core/services/strategy/`，往上数三层（`strategy` → `services`
→ `emotion_core`）才是包根，故用 `parents[2]`。历史上误写成 `parents[3]`（旧 LKL 布局的层级），
解析到不存在的 `src/llm/strategies`；而 `load_strategies()` 对不存在的目录
`glob("*.yaml")` **返回空列表且不抛异常**，于是直接掉进第 2 道门的「跳过」分支 ——
15 个策略一个不跑、不落库、无告警，是典型的静默失败。
回归锁在 `tests/unit/test_strategy_runner.py`（21 项断言）。

**配额、限流与去重**（`runner.py`，2026-09-30 修复）：

| 事项 | 旧行为 | 现行为 |
|---|---|---|
| 配额分配 | `for skill: for code:`，上限判在内层 → 首个策略独吞 `STRATEGY_MAX_LLM` 全部名额（实测 15 策略 × 68 只 = 1020 组合里只有 `bottom_volume` 跑过，落库 18 行全是它） | 交错遍历（代码优先、策略轮转），每个策略都能轮到 |
| 429 限流 | agnes 单后端时 `llm_backend._chat_locked` 传入的 `allow_status_retry=len(chain) > 1` 为假，客户端自带 `max_retry` 对 429 不生效；且失败照样扣配额（实测 50 次里 32 次 429） | 调用侧定速 + 429/5xx 指数退避；**失败不占配额**；连续失败超阈值熔断 |
| 去重 | 查询用 `sha256(prompt 文本)`、入库用 `sha256(strategy:code:date)`，两者永不相等 → `_existing()` 恒 False，每次重跑全量重打 LLM | 两侧同源，均取 prompt 文本摘要（`_signal_row` 直接用调用侧算好的值） |

新增配置（`utils/config.py`）：

| 键 | 默认 | 含义 |
|---|---|---|
| `EC_STRATEGY_CALL_INTERVAL` | `1.5` | 相邻两次 LLM 调用的最小间隔（秒） |
| `EC_STRATEGY_MAX_ATTEMPTS` | `3` | 单个组合的最大尝试次数（含首次） |
| `EC_STRATEGY_RETRY_BASE_DELAY` | `2.0` | 退避基数，第 n 次重试等待 `base × 2^(n-1)` 秒 |
| `EC_STRATEGY_MAX_CONSECUTIVE_FAILURES` | `8` | 连续失败达此数即熔断，其余组合记 `连续调用失败熔断` |

汇总日志形如 `策略观察 2026-09-29: 调用 50, 入库 18, 失败 3, 跳过 1002, 快照 ...`。
`skipped` 里可区分 `达到LLM调用上限` / `连续调用失败熔断` / `LLM调用失败:<原因>`。

**调度**：`deploy/emotion-core-strategy.{service,timer}`，工作日 17:50 `Asia/Shanghai`
（`Persistent=true`，在 `emotion-core-daily.timer` 之后）。手工补跑：

```bash
sudo systemctl start emotion-core-strategy.service
# 或
PYTHONPATH=src .venv/bin/python -m emotion_core.orchestration.strategy --date 2026-09-29
```

**输出**：只写 `strategy_signal` 表，主链不读它 —— 策略观察是旁路观察，不参与信号生成。

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
