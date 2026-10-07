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

⚠️ 2026-10-07：本章原先内嵌 45 行 `LLMClient` / `NullClient` 源码。
**文档里抄一份代码必然过期**——下面的接口签名是契约，实现请直接读源文件。

```python
# src/emotion_core/llm/base.py
class LLMClient(Protocol):
    def complete(self, prompt: str, *, system: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 2048) -> str: ...

class LLMNotEnabledError(RuntimeError): ...      # 主链捕获后跳过 LLM 增强
class NullClient:                                  # 默认实现，未启用时抛上面那个错
    def complete(self, prompt: str, **kwargs) -> str: ...

def get_client(profile: str | None = None) -> LLMClient: ...
```

**关键**：
- `LLMClient` 是 **Protocol**（结构化子类型），任何实现 `complete()` 的对象都合法；
- `NullClient` 是默认实现，`get_client()` 在未启用时返回它；
- 主链捕获 `LLMNotEnabledError` 并跳过 LLM 增强——**主链不依赖 LLM**。

---

## 4. Agnes 实现：`llm/agnes.py`

⚠️ 2026-10-07：本章原内嵌 33 行 `AgnesClient` 草图，**已与代码不符**——
真实实现已是**多 profile 链**，不再是「一个 AgnesClient 读 AGNES_API_KEY」的单体。
实现请读 `src/emotion_core/llm/agnes.py`。

| 事实 | 值 | 出处 |
|---|---|---|
| profile 表 | `LLM_PROFILES`（default / agnes / …，每档带 model/base_url/timeout…） | `agnes.py` |
| 密钥来源（按优先级） | 环境变量 → `~/.llmkey` / `.secrets/llmkey` → `~/.env` | `resolve_key()` / `LLM_KEY_FILES` / `LLM_ENV_FILES` |
| 各 profile 的密钥环境变量名 | `LLM_PROFILE_KEY_ENV = {"agnes": "AGNES_API_KEY", ...}` | `agnes.py` |
| 通用密钥环境变量 | `LKL_LLM_API_KEY` | `LLM_KEY_ENV` |
| 参数装配 | `LLMParams` / `resolve_params(profile)` | `agnes.py` |
| 返回值清洗 | `strip_fence()` 去掉模型偶发的 ``` 围栏 | `agnes.py` |
| 未配置密钥 | 抛 `LLMNotConfigured` | `agnes.py` |

**真实的启用开关是 `LKL_LLM_ENABLED` 环境变量**（`services/llm.py` 读它，取值
`1`/`true`/`yes` 之一才算开）。`CONFIG.LLM_PROFILE` 只是**代码内**的默认值
（`None`=关、`"agnes"`=开），**不是环境变量** —— 本文档此前把它写成
「设 `LLM_PROFILE=agnes` 启用」是错的，已更正。

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

**所有场景默认关闭**。前 4 个场景的启用方式：设置环境变量 **`LKL_LLM_ENABLED=1`**
（⚠️ 2026-10-07 更正——此前写「`LLM_PROFILE=agnes`」，但 `LLM_PROFILE` 是 `CONFIG`
的代码内默认值、**不从环境变量读**；真正的总闸是 `services/llm.py` 读
`LKL_LLM_ENABLED`，取值 `1`/`true`/`yes`）。

**策略观察例外**：它多一道独立门 `EC_STRATEGY_ENABLED`（见 §6.1），
`LLM_PROFILE` 只决定用哪个后端，不决定开关。

### 6.1 策略观察子系统（DSA 移植）

移植自 `daily_stock_analysis`（MIT，见 `docs/archive-lkl/adr/0003-m9-dsa-integration.md`），
只搬模块思想、不合并仓库，因此**无运行时依赖**。

```
src/emotion_core/services/strategy/
├── context.py    67 行   组装个股上下文（行情/涨停/情绪）注入 prompt
├── loader.py    125 行   纯 stdlib YAML 子集加载器 → Skill 数据类
├── runner.py    250 行   主流程：门控 → 加载技能 → 建票池 → 调 LLM → 落库
└── universe.py   66 行   当日票池（ZT/ZB，排除跌停）

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
| 配额分配 | `for skill: for code:`，上限判在内层 → 首个策略独吞 `STRATEGY_MAX_LLM` 全部名额（实测 15 策略 × 68 只 = 1020 组合里只有 `bottom_volume` 跑过，落库 18 行全是它；该 68 只系票池尚未按 `pool_type` 收窄时的口径） | 交错遍历（代码优先、策略轮转），每个策略都能轮到 |
| 429 限流 | agnes 单后端时 `llm_backend._chat_locked` 传入的 `allow_status_retry=len(chain) > 1` 为假，客户端自带 `max_retry` 对 429 不生效；且失败照样扣配额（实测 50 次里 32 次 429） | 调用侧定速 + 429/5xx 指数退避；**失败不占配额**；连续失败超阈值熔断 |
| 去重 | 查询用 `sha256(prompt 文本)`、入库用 `sha256(strategy:code:date)`，两者永不相等 → `_existing()` 恒 False，每次重跑全量重打 LLM | 两侧同源，均取 prompt 文本摘要（`_signal_row` 直接用调用侧算好的值） |

调用侧旋钮（**模块级常量，刻意不进 `utils/config.py` 的 `Config`**）：

> ⚠️ `config_hash()` 哈希 `asdict(CONFIG)` 的**全字段**，往 `Config` 里加键会让
> `pipeline_state` / `signal` / `eval_result` 的策略指纹平白换代
> （同 `theme_service.py:31`、`notify.py:31` 的告警）。这些旋钮因此住在
> `services/strategy/*.py` 顶部，env 同名覆盖，不动指纹。

| 键 | 默认 | 声明处 | 含义 |
|---|---|---|---|
| `EC_STRATEGY_CALL_INTERVAL` | `1.5` | `runner.py` | 相邻两次 LLM 调用的最小间隔（秒） |
| `EC_STRATEGY_MAX_ATTEMPTS` | `3` | `runner.py` | 单个组合的最大尝试次数（含首次） |
| `EC_STRATEGY_RETRY_BASE_DELAY` | `2.0` | `runner.py` | 退避基数，第 n 次重试等待 `base × 2^(n-1)` 秒 |
| `EC_STRATEGY_MAX_CONSECUTIVE_FAILURES` | `8` | `runner.py` | 连续失败达此数即熔断，其余组合记 `连续调用失败熔断` |
| `EC_STRATEGY_POOL_TYPES` | `ZT,ZB` | `universe.py` | 收窄候选池 `pool_type`，逗号分隔 |

汇总日志形如 `策略观察 2026-09-29: 调用 50, 入库 18, 失败 3, 跳过 1002, 快照 ...`。
`skipped` 里可区分 `达到LLM调用上限` / `连续调用失败熔断` / `LLM调用失败:<原因>`。

**候选池口径**（`universe.py`，2026-09-30）：`limit_pool_em` 是东财**涨停池**表，
但同表混含三种 `pool_type`（实测 2026-09-29：`ZT` 57 / `DT` 10 / `ZB` 8）。查询现按
`pool_type = ANY(%s)` 收窄到 `OBSERVED_POOL_TYPES`（默认 `ZT,ZB`），跌停股不再占
LLM 配额 —— 该日候选池从 68 只降到 58 只，正好是 10 只 DT。

两条查询都强制 `ORDER BY`（`limit_pool_em` 用 `code`，`hot_rank` 用 `rank, code` 兜底）：
票池会被 `STRATEGY_MAX_UNIVERSE` 截断，**截断后的顺序就是 LLM 配额的归属顺序**，
缺 `ORDER BY` 时同一交易日重跑会得到不同票池，「跑过哪些组合」不可复现。
排序主键取 `code` 而非连板高度，是因为 `cont_days_em` 在跌停行上同样有值，按它排会把
跌停股排进前列。日后若要改「强势优先」，注意 DT 行的 `first_seal` 存的是**空串而非
NULL**，需写 `NULLIF(first_seal,'')`，否则空串比 `'092500'` 小、跌停股会被排到最前。

**调度**：`src/emotion_core/orchestration/systemd/emotion-core-strategy.{service,timer}`，工作日 17:50 `Asia/Shanghai`
（`Persistent=true`，在 `emotion-core-daily.timer` 之后）。手工补跑：

```bash
sudo -n systemctl start emotion-core-strategy.service   # systemd 重启/启动一律 sudo -n，见 docs/06 §3.4
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
| **L4** | 默认关闭，`LKL_LLM_ENABLED=1` 启用？ | **确认**（2026-10-07 更正：原文写的 `LLM_PROFILE=agnes` 不是环境变量） |
| **L5** | LLM 层是**增强**（主链不依赖）？ | **确认** |
