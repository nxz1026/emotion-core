# 《板学计划》vs 龙空龙 · 重合度核实报告

- 核实对象：`/tmp/板学计划(1).txt`（1358 行，30 节）
- 核实对象版本：2026-09-17 12:56 落盘
- 被核实项目：`/home/ubuntu/DSH/longkonglong` @ `98588aa`（工作树干净）
- 核实方法：逐节读计划 → 在 repo 内定位对应实现 → 查库验证数据可得性（非读文档推测）

---

## 0. 一句话结论

**你的判断方向对，但重复度被高估了。** 计划里"架构层"和"框架层"的东西——五层架构中的 4 层、情绪状态机、晋级矩阵、淘汰赛、题材聚合、评估对照组——**已经全部实现并跑了 658 个交易日**；真正新的是**"候选宇宙"（首板/一进二，现有系统只看最高板）和它上面挂的 7 个特征族**。也就是说：不是"功能重复"，而是**同一套引擎上换了一个研究对象**。

---

## 1. 判定总表（计划 30 节逐节）

| 计划节 | 内容 | 判定 | 现有对应物 |
|---|---|---|---|
| 一 | 14 条核心哲学 | ✅ 已内化 | `docs/REQUIREMENTS.md:16`「龙头不是提前选出来的」；`evaluate.replay(as_of)` 无未来函数；`calibrate` 只提案不改参 |
| 二 | 五层架构 | 🟡 4/5 层已有 | L1=`dragon_env`，L2=`emotion`，L3=`theme`，L5=`ladder.y_survivors`+`entry.c3`；**L4 宇宙不同** |
| 三 | 负反馈原始变量清单（34 项） | 🟡 9 项已入库 | `market_stat`（658 日）+ `promotion_day` |
| 四 | 负反馈一阶变化 ΔNF | 🟡 离散版已有 | `emotion._neg_feedback`（0~5 计数 vs 昨日） |
| 五 | 历史分位数代替固定阈值 | 🟡 已有先例 | `emotion._bomb_threshold`（前 30 日中位数+σ） |
| 六 | Allow_New_Cycle_Trial 布尔 | ✅ 已有 | `buy_window` NONE/STANDARD/ENHANCED + `force_liquidate`；`dragon_env.rating` |
| 七 | 一进二候选股特征（总纲） | ❌ 真新增 | 现有 `entry` 只扫最高板，从不扫首板 |
| 八 | A 类股性/市场记忆 | ❌ 真新增 | 无。但**数据够算**（见 §6） |
| 九 | B 类 1+1 / 小连板回踩 / N 字 | ❌ 真新增 | 无 |
| 十 | C 类技术结构（箱体/平台/均线/突破） | ❌ 真新增 | 无。数据够算 |
| 十一 | D 类首板质量 | 🟡 一半可算 | `derived_bar` 有一字/换手/炸板；封板时间仅 **28 日** |
| 十二 | E 类抗跌/反脆弱 | ❌ 真新增 | 无（可用全市场中位数当 market 基准） |
| 十三 | F 类相对强弱 RS | ❌ 真新增 | 无。**缺指数数据源**（硬约束） |
| 十四 | 板块/题材协同 | 🟡 骨架已有 | `theme_tag`/`theme_group`，但仅 18 日、且只含梯队股 |
| 十五 | 妖股→中军→趋势迁移（Stage 0-6） | ❌ 真新增 | 仅 `docs/theme-design.md` 提过"中军"概念，无实现 |
| 十六 | Label A 一进二 / Label B Future_Leader | 🟡 A 已有 | Label A=`promotion_day` `'1->2'` 层双口径（658 日）；Label B 无 |
| 十七 | 龙头淘汰赛 | ✅ **已实现** | `ladder.y_survivors` + `entry.c3_elimination` + `review.elimination_rows` + `/api/signal-detail` |
| 十八 | 仓位三阶段模拟 | ❌ 真新增 | `position` 是手工真仓；`execution.py` 16 行占位；P4 模拟盘卡在海外端口（`docs/PLAN.md:461`） |
| 十九 | 对照组 | ✅ **框架已有** | `hypothesis_test` / `window_compare` / `param_matrix` / `exit_compare`(A-E 族) / `dedup_episodes` |
| 二十 | 交互效应 + 独立 OOS | 🟡 部分 | `walk_forward` 已自曝"非真正样本外"；无 bootstrap CI / effect size / 多重检验 |
| 二十一 | 完整假说路径（A杀→…→Leader） | 🟡 零件齐、未串联 | 各环节都有代码；报告 `_CHECKS` 已把该路径写成人工验证点 |
| 二十二 | 社交媒体注意力 | ❌ **不可做** | 无数据源（见 §6 硬约束） |
| 二十三 | 行为金融解释 | — 无代码需求 | 现有 LLM 点评层可承载（默认关，信号链路禁 import llm） |
| 二十四 | 避免过拟合 | 🟡 见二十 | 时间切分已有，其余缺 |
| 二十五 | 不做 ML（三阶段递进） | ✅ 现状合规 | 全库零 ML。计划要的是**新增** logistic/SHAP/calibration |
| 二十六 | Dashboard 六面板 | 🟡 外壳已有 | `dashboard/`（纯 stdlib，nginx `/stock/`，basic auth）已有 6 个 API 面板 |
| 二十七 | 工程结构 `src/*.py` 20 模块 | 🟡 等价物已有 | 20 个模块名 ≈ 现有 `lkl/` 模块，见 §7 映射表 |
| 二十八 | MVP1-5 | 🟡 MVP1/3 半有 | MVP1 需补 ~20 列；MVP3 是纯查询；MVP2/4/5 新增 |
| 二十九 | 案例回放（逐日录像） | 🟡 引擎有、UI 无 | `evaluate.replay`（逐日+`as_of`）+ `emotion.replay`（全量可重复）+ 日期切换 |
| 三十 | 证伪优先 | ✅ 一致 | `README.md:470` 已知边界、`PLAN §6` 风险登记、`evaluate` 局限声明 |

**计数**：✅ 已有等价实现 5 节 · 🟡 部分已有 15 节 · ❌ 真新增/不可做 9 节 · 无代码需求 1 节。

---

## 2. 已实现、**禁止重建**的部分（带证据）

### 2.1 情绪周期状态机（计划 §二 Layer2 / §五 / §六）
`lkl/services/emotion.py`
- `:253-254` 四态规则表：退潮 / 高潮 / 发酵 / 冰点
- `:257-263` `window_of()` → `buy_window` + `force_liquidate`（就是计划要的 `Allow_New_Cycle_Trial`，且多一层正交语义）
- `:128-149` `_bomb_threshold()` 自适应阈值 = 前 30 交易日炸板率中位数 + σ ← **计划 §五"历史分位数"已有一例**
- `:266-279` `_neg_feedback()` 0~5 计数（断板/高度降/炸板升/表现降/跌停升）← **就是计划 §四 的"负反馈一阶变化"离散版**
- `:319-322` 高潮分歧降级（nb≥3 → 窗口 NONE）
- `:457-483` `replay()` 全量重放零读库，同数据重跑结果恒一致

计划 §二 列的 7 个状态，现有已有 5 个：**上升≈发酵、高潮、分歧、退潮、冰点**。真正缺的只有 **冰点修复**（现有是 `冰点·反转` 标签，`emotion.py:314-318`）和 **新周期试错**（现有是 `发酵`+`STANDARD` 窗口）。

### 2.2 晋级矩阵 = 计划 §十六 Label A（一进二成功率）
`lkl/services/promotion.py` + `market_stat`/`promotion_day`
- `:81-102` 分层统计，`config.py:115-119` 定义 4 层（`1->2` … `5+->6+`）
- **名义/换手双口径**（`rate_nominal` / `rate_exchange`）+ 背离度 + 该层失败股平均涨幅
- 实测：`promotion_day` **3,290 行 / 658 个交易日**，2024-01-02 起

计划 §十六 要的"一进二/二进三/三进四成功率"，现成。且现有口径比计划更严（分母<3 置 None 不补 0，`PROMOTION_MIN_DENOM`）。

### 2.3 龙头淘汰赛 = 计划 §十七
| 计划要素 | 现有实现 |
|---|---|
| 不提前预测唯一龙头 | `ladder.top_group()` 同身位组（`ladder.py:49-55`） |
| 每日更新候选集 | `ladder.build(date)` + `ladder_day` 表（8,611 行） |
| 是否晋级/断板/炸板 | `review.elimination_rows()`（`review/__init__.py:71-97`）输出 晋级N板/触板未封/断板/停牌 |
| 竞争与淘汰关系 | `ladder.y_survivors()`（`:65-81`）昨日最高组 × 今日换手幸存 |
| 身份校验 | `entry.c3_elimination()`（`entry.py:32-50`）集合等值：候选 ∈ 昨日组 ∧ 幸存集=={候选} |
| 可视化 | `dashboard/signaldetail.py:40-47` `/api/signal-detail` 的 elimination 段 |

**唯一缺口**：现有是 **1 日前视**（只看昨天→今天），计划要的是候选池**跨多日全生命周期**追踪 + 淘汰原因归因。这是增强，不是重建。

### 2.4 题材聚合 = 计划 §十四骨架
`lkl/services/theme.py` + `theme_tag`/`theme_group`
- `:213-236` `group_stats()`：最高板/中位/低位/成员数/完整度/状态（梯队完整/孤高/低位扩散）
- `:305-335` `run()`：Wind 概念标签 + 公告催化裁决（置信度 0.85/0.6/0.4）
- 缺：板块涨幅、成交额、RS、突破、中军、首板数（`first_board_count` 硬编码 0，`dragon_env.py:65-68` 自述"首板属 P1.5 未实现"）

### 2.5 生态评级 = 计划 §二 Layer1（大盘环境）
`lkl/services/dragon_env.py:254-270`：G1 可交易高度扩张 / G2 主线梯队完整 / G3 断板负反馈温和 / G4 胜者上方有空间；B1 加速事件 / B2 高度靠一字制造 / B3 胜出次日核按钮 / B4 无板块支持 / B5 高标跨题材 → `FAVORABLE/NEUTRAL/UNFAVORABLE`（`_verdict` `:237-251`）。

这就是计划 §二 Layer1「判断市场是否允许承担短线风险」，且每条判据都带 `ok/note` 原始证据（符合计划 §二十六"不要只给综合得分"）。

### 2.6 评估与对照组 = 计划 §十九
`lkl/services/evaluate.py`
- `:21-60` `replay()` 逐日重放，`as_of` 可复现基准日，禁隐式 `date.today()`
- `:87-117` `forward_stats()` T+1 高开/晋级/T+3·T+5 最大涨幅与回撤/规则化收益
- `:120-154` `distribution_stats()` 中位数/四分位/均值/**盈亏比/尾部 p10/胜率**（正是计划要的"不能只看均值"）
- `:189-214` `hypothesis_test()` 梯队完整 vs 孤板高标 / 一字垄断 / 同身位扎堆
- `:304-341` `exit_compare()` A/B/C/D/E 五族出口对照
- `:342-366` `dedup_episodes()` 同票 ≤5 交易日合并（"重复触发不是独立样本"）
- `:369-389` `walk_forward()` 时间 80/20
- `:403-416` `param_matrix()` MIN_LEADER_DAYS 3 vs 4

### 2.7 计划 §四/§六 的判据**已经写在每日报告里**（最重要的发现）
`lkl/services/review/utils.py:229-237` `_CHECKS` 按阶段给人工验证点：
- 退潮 →「跌停家数是否收敛（回落至 15 以下为负反馈减弱信号）」← 计划 §四
- 退潮 →「**首板与二板晋级率是否先于高位回暖（修复早期特征）**」← 计划 §四+§十六
- 冰点 →「昨日涨停表现中位数是否转正（负反馈实质收敛）」← 计划 §四"边际修复"
- 冰点 →「是否出现 ≥4 板换手高标（高度打开＝新周期启动候选）」← 计划 §六

**含义**：计划的"新思想"在现有项目里**已经被识别为待验证假设**，只是以"人工验证点"而非"计算状态"存在。计划要做的其实是**把这几条人工提示升级成可计算状态 + 事件研究**。

### 2.8 数据管线（计划 §二十七 未列，但重建成本最高）
`lkl/services/derive.py`（涨停价整数实现 `_DERIVE_SQL:48-73`、连板数窗口函数 `_CONT_SQL:84-95`）、`ingest.py`（589 行，三源兜底）、`providers/`（eastmoney/pytdx/sina）、`utils/price.py`（Decimal 逐分锁定）。**这 6 个模块是 7 轮审计 + 277 个测试换来的，绝对不要重写。**

### 2.9 首板其实已进过系统（但只喂给 LLM）
`lkl/strategy/universe.py:18-33` `build_universe()` = 东财涨停池全量 + 人气榜前 N —— 涨停池全量**包含首板**。实测 `strategy_signal` 196 行中 `cont_days=1` 有 **95 行**、`cont_days=0`（热榜非涨停）80 行。

所以准确表述是：**首板股已被 LLM 观察层扫到，但没有任何结构化首板特征**（LLM 只看 `strategy/context.py` 拼的日线文本）。

---

## 3. 计划 §三 变量清单逐项核实（34 项）

| # | 变量 | 状态 | 说明 |
|---|---|---|---|
| 1 | 每日涨停家数 | ✅ 入库 | `market_stat.limit_up_count` |
| 2 | 每日跌停家数 | ✅ 入库 | `market_stat.limit_down_count` |
| 3 | 炸板家数 | 🟡 可补算 | `_counts` 算了但只存了 `bomb_rate` |
| 4 | 炸板率 | ✅ 入库 | `market_stat.bomb_rate` |
| 5 | 首板家数 | 🟡 可补算 | `derived_bar` cont_days=1 |
| 6-8 | 二板/三板/四板+家数 | 🟡 可补算 | `ladder_day` 有 cont_days |
| 9 | 最高连板高度 | ✅ 入库 | `max_limit_days`（另有可交易口径 `tradable_max_days`） |
| 10 | 连板晋级率 | ✅ 入库 | `promotion_day` |
| 11 | 一进二成功率 | ✅ 入库 | `promotion_day` layer `'1->2'`，双口径 |
| 12 | 二进三成功率 | ✅ 入库 | 同上 |
| 13 | 三进四成功率 | ✅ 入库 | 同上 |
| 14 | 昨日涨停股今日平均收益 | ✅ 入库 | `zt_performance_mean` + `zt_performance_median` 双值 |
| 15 | 昨日连板股今日平均收益 | 🟡 可补算 | `promotion_day.fail_perf` 只是失败股 |
| 16 | 昨日高位股今日平均收益 | 🟡 可补算 | — |
| 17 | 昨日炸板股今日收益 | 🟡 可补算 | 需 `is_bomb` 前一日 |
| 18 | 高位股大面数量 | 🟡 需定义 | "大面"阈值待拍板 |
| 19 | 天地板数量 | 🟡 需定义 | 需明确"涨停开盘→跌停收盘"判定 |
| 20 | 跌停封死数量 | 🟡 可补算 | `is_limit_down` + low==跌停价 |
| 21 | 连续跌停数量 | 🟡 可补算 | 需反向 cont 段 |
| 22 | A 杀股票数量 | 🟡 需定义 | "A 杀"无客观定义，必须先拍板 |
| 23-25 | 上涨/下跌家数/占比 | 🟡 可补算 | `daily_bar` close vs pre_close |
| 26 | 涨跌停比 | 🟡 可补算 | 两个分量已入库 |
| 27 | 全市场成交额 | 🟡 可补算 | `daily_bar.amount` |
| 28 | 短线强势股成交额占比 | 🟡 可补算 | 需定义"强势股" |
| 29 | 开盘核按钮数量 | 🟡 需定义 | 需拍板（开盘低开幅度？） |
| 30 | 昨日强势股低开比例 | 🟡 可补算 | — |
| 31 | 昨日涨停股高开比例 | 🟡 可补算 | — |
| 32 | 涨停股次日溢价 | ⚠️ 仅零星 | `signal_outcome.t1_gap` 只有 6 条（且只覆盖信号股） |
| 33 | 连板股次日溢价 | 🟡 可补算 | — |
| 34 | 高标次日溢价 | 🟡 已算未存 | `dragon_env.b3_next_day_dump` 内部算了 sole_top 次日表现，未落库 |

**小结**：**9 项已入库、20 项可由现有表直接补算、4 项需先拍板定义**（大面/天地板/A杀/核按钮）、1 项（涨停股次日溢价）仅对已落库的 6 条信号有值。
**结论**：§三 不是"重复"，是**在同一张 `market_stat` 上补列**——`ALTER TABLE` + 重跑 `emotion.run_range` 即可，不需要新表新管线。

---

## 4. 真新增清单（去重后的净工作）

| # | 缺口 | 规模 | 依赖 |
|---|---|---|---|
| 1 | **首板/一进二候选宇宙生成器**（cont_days==1） | 小 | 现有 `derived_bar` 够 |
| 2 | **市场记忆特征族**（60/120/250/500 日涨停次数、历史最大连板、距上次涨停/二连板） | 中 | **需前推回填窗口**（见 §6） |
| 3 | **形态特征族**（1+1 各间隔 / 小连板回踩 / N 字三档宽松度） | 中 | 现有日线够 |
| 4 | **技术结构特征族**（箱体/平台/前高/年线/半年线突破、MA20/60/120/250 slope、距高点、突破前涨幅） | 中 | 现有日线够（MA250 需 250 交易日前置） |
| 5 | **首板质量特征族**（封板时间/开板次数/一字/T字/烂板/尾盘偷板/反包/带动） | 中 | **封板时间仅 28 日**（见 §6） |
| 6 | **抗跌/反脆弱特征族**（R_stock − R_market、龙头跌停时拒跌、分歧时回封、尾盘抢筹） | 中 | 可用全市场中位数当基准 |
| 7 | **相对强弱特征族**（RS 5/10/20/40/60 日、slope、新高、加速度） | 中 | **缺指数数据源**（见 §6） |
| 8 | **题材协同变量**（板块涨幅/成交额/增速/RS/突破/中军/首板数） | 中 | `theme_group` 需扩覆盖（现仅 18 日、仅梯队股） |
| 9 | **`market_stat` 补 ~20 列**负反馈原始变量 | 小 | 现有表 |
| 10 | **repair（冰点修复）显式状态** + 事件研究（极端负反馈后 1/3/5/10/20 日市场表现） | 小 | 现有 658 日 + `phase`/`reason` 标签 |
| 11 | **Future_Leader 标签层**（10/20/40/60 日 × 多定义 Top1/3/5%） | 中 | 现有日线够 |
| 12 | **Candidate Set 跨日追踪**（1 日前视 → 全生命周期 + 淘汰归因） | 中 | 现有 `ladder_day` 够 |
| 13 | **仓位三阶段模拟**（试错/确认/趋势，含涨停买不到/跌停卖不掉/跳空/滑点） | 大 | P4 模拟盘卡海外端口（`PLAN §7`） |
| 14 | **统计严谨层**（真 OOS 时间切分 + bootstrap CI + effect size + 多重检验 + logistic 回归） | 中 | 现有 `evaluate` 可扩展 |
| 15 | **Dashboard 六面板** | 中 | `market_stat`/`theme_group` 可直出 |
| 16 | **社交媒体注意力** | ❌ | **数据不可得，见 §6** |

---

## 5. 计划 §二十七 工程结构 vs 现有模块（映射表）

计划提的 20 个 `src/*.py`，**12 个在现有 repo 已有对应实现**：

| 计划模块 | 现有对应 |
|---|---|
| `data_loader.py` | `lkl/services/ingest.py` + `lkl/providers/*` |
| `limit_rules.py` | `lkl/utils/price.py` + `lkl/services/derive.py` |
| `emotion_features.py` | `lkl/services/emotion.py`（`indicators`） |
| `negative_feedback.py` | `lkl/services/emotion.py`（`_neg_feedback`） |
| `emotion_regime.py` | `lkl/services/emotion.py`（`classify`） |
| `repair_detector.py` | ❌ 缺（部分在 `reason` 的 `·反转` 标签） |
| `market_memory.py` | ❌ 缺 |
| `pattern_n.py` | ❌ 缺 |
| `pattern_1plus1.py` | ❌ 缺 |
| `breakout_features.py` | ❌ 缺 |
| `first_limit_quality.py` | ❌ 缺 |
| `resilience.py` | ❌ 缺 |
| `relative_strength.py` | ❌ 缺 |
| `theme_features.py` | `lkl/services/theme.py` |
| `candidate_generator.py` | `lkl/services/ladder.py`（**但宇宙是最高板，不是首板**） |
| `leader_labels.py` | ❌ 缺（Label A 在 `promotion_day`） |
| `leader_elimination.py` | `lkl/services/ladder.py`（`y_survivors`）+ `review.elimination_rows` |
| `statistics.py` | `lkl/services/evaluate.py` |
| `backtest.py` | `lkl/services/evaluate.py`（`replay`/`forward_stats`） |
| `dashboard.py` | `dashboard/`（6 个 API 模块 + `index.html`） |

**缺的 8 个（repair/market_memory/pattern_n/pattern_1plus1/breakout/first_limit_quality/resilience/relative_strength）正好就是 §4 的净新增 1-7 项。**

---

## 6. 数据可行性核实（实测，非推测）

### 6.1 现有数据存量（本机库实测）

| 表 | 行数 | 覆盖 |
|---|---|---|
| `daily_bar` | 3,489,633 | 5,224 只，**2023-11-21** ~ 2026-09-17 |
| `derived_bar` | 3,319,285 | **678 个交易日**，2023-12-04 起 |
| `market_stat` | 658 | 2024-01-02 ~ 2026-09-17 |
| `promotion_day` | 3,290 | 658 日 × 4 层双口径 |
| `ladder_day` | 8,611 | 2024-01-02 起 |
| `theme_tag` / `theme_group` | 139 / 645 | **仅 18 个交易日**（2026-08-31 起） |
| `limit_pool_em` | ZT 1,766 / ZB 627 / DT 314 | **仅 24~28 日**（2026-08-11 起） |
| `hot_rank` | 436,672 | 379 日（2025-08-31 起） |
| `signal` / `signal_outcome` | 6 / 6（complete 5） | 信号稀疏（设计如此） |
| `strategy_signal` | 196 | 5 日（LLM 观察层） |

### 6.2 三条硬约束（会直接决定计划能否落地）

**① 计划 §八「过去 250/500 日涨停次数」——现有数据不够**
`daily_bar` 最早 2023-11-21。对 2024 年上半年的样本，500 日窗口里只有约 30 天数据，**特征严重右截断**，早期样本的"市场记忆"会系统性偏小，直接污染统计。
→ 需把回填窗口前推（`pytdx` 支持分页拉全历史，见 `docs/DATA_SOURCES.md`）。**这是 §八 的前置条件，不做这一步就别做 §八。**

**② 计划 §十一「首次封板时间/开板次数/分时走势」——只有 28 天**
`limit_pool_em` 实测 `first_seal`/`last_seal`/`bomb_times` **1,766 行 100% 有值**，但只覆盖 2026-08-11 起 28 个交易日（东财池 30 日上限）。
→ 历史封板时间需换源：Tushare `limit_list_d`（`first_time`/`last_time`/`open_times`，**数据自 2020 年**，需积分）或自算分钟线（akshare `min_em` 可用但"历史深度有限"，Baostock 无 1 分钟）。

**③ 计划 §十三「RS = 股票/全A、/沪深300、/行业指数、/题材指数」——库里没有任何指数表**
23 张表里没有一张指数表，数据源矩阵里也没有指数采集。
→ 必须先新增指数数据采集任务。**没有指数就没有 RS，§十三 无法开工。**
（变通：RS 的市场基准可用全市场中位数收益率代替，但"行业指数/题材指数"没有替代品。）

### 6.3 计划 §二十二 社交媒体——**必须明确回答：不可得**

计划原文要求"如果历史社交媒体数据无法可靠获得，明确告诉我，不允许伪造"。**核实结论：不可得。**

- 雪球 / 东方财富股吧 / 淘股吧 / 同花顺：**没有公开的历史全量帖文 API**；爬取需登录态 + 反爬对抗，且**历史快照无法回溯**（今天爬不到 2024 年某只票当时的讨论热度）。现有项目也从未采集过。
- 计划特别强调"必须严格按照帖子真实发布时间"——这恰恰是爬取数据最不可信的部分（列表页时间戳可编辑、置顶帖时间失真）。
- **唯一近似物**：`hot_rank`（东财人气榜，379 日、43.7 万行）。但它是**人气排名**，不是"讨论数量/增速"，无法构造计划要的 `Attention_Level` / `Attention_Acceleration`。
- **建议**：§二十二 整节标记为"数据不可得，暂缓"；若要推进，只能从今天起**前向采集**（攒 1~2 年后才有统计力），且必须先解决时间戳可信性。**不要用 hot_rank 冒充讨论热度。**

---

## 7. 关于"新建独立项目还是 research 分支"

计划 §三十 第 3 条问的这个问题，核实后的答复：

**不要新建独立项目、不要新建独立包。** 理由：

1. **计划的 20 个模块里 12 个已有实现**，新建等于把 7 轮审计 + 277 个测试 + 五条 CI 红线（`scripts/ci_local.sh`）换来的东西重写一遍；
2. **数据管线重建成本最高**：`ingest`/`derive`/`price`/三源对账/涨停价整数实现/连板数窗口函数——这些是踩坑踩出来的（`docs/DATA_SOURCES.md` 整篇）；
3. **计划要的 658 日情绪事实库、晋级矩阵、淘汰赛、题材聚合、评估对照组，全部已在同一个 PG 库里**。新建项目要么连同一个库（那就不是一个独立项目），要么重新回填（数小时 + 重复踩坑）；
4. 计划 §二十七 提 `config.yaml` + "禁止硬编码"——现有 `lkl/config.py` 已满足（`docs/PLAN.md:84` 明文"全部常量进 config.py"），换 yaml 只是形式。

**建议方案**：在现有 repo 开 `research` 分支（或 `lkl/research/` 子包），新增：
- `lkl/research/features/`（§4 的 7 个特征族）
- `lkl/research/labels.py`（Future_Leader）
- `lkl/research/regime.py`（repair 状态 + 事件研究）
- `lkl/research/stats.py`（OOS/bootstrap/effect size/logistic）
- `market_stat` 补列 + `theme_group` 扩覆盖（都走现有 `_MIGRATIONS` 幂等 ALTER）

**红线不变**：不碰 `entry`/`signal` 生产链路，不启用 `lkl-trade.timer`/`lkl-close.timer`（你手动关的），LLM 不进信号链。

---

## 8. 陷阱记录（本次核实踩到的）

1. **只看表结构会误判"数据已有"**：`limit_pool_em` 有 `first_seal`/`last_seal`/`bomb_times`，看起来完美支持计划 §十一 首板质量——但只有 **28 天**。核实数据必须查 `min(date)`/`count(distinct date)`，不能看 `information_schema` 就下结论。
2. **`theme_group` 的 `first_board_count` 是硬编码 0**：计划 §十四 要的"同题材首板数量"，字段存在但恒为 0（`dragon_env.py:65-68` 自述 P1.5 未实现）。字段存在 ≠ 功能存在。
3. **"首板完全没被扫描"是错的**：`strategy/universe.py` 的涨停池全量已含首板，`strategy_signal` 实测 `cont_days=1` 有 95 行。核实"某类股票有没有进系统"必须查调用方，不能只看主信号链（`entry` 确实只扫最高板）。
4. **计划 §四/§六 的判据已在报告里**：`review/utils.py:229-237` 的 `_CHECKS` 已把"首板与二板晋级率先于高位回暖""跌停家数收敛""昨涨停表现中位数转正"写成人工验证点。**先查现有报告的建议段，再判断"想法是不是新的"**，否则会漏掉最重的重复。
5. **`promotion_day.fail_perf` 不是"昨日连板股今日平均收益"**：它只统计该层**晋级失败**股，别当成 §三 第 15 项直接用。
6. **`walk_forward` 不是样本外检验**：现有代码自己写明"判据阈值在全样本（含后段）标定，后段不构成泛化证据"（`evaluate.py:369-375`）。计划 §二十四 要的真 OOS 需要**重新标定**，不是套用现有函数。

---

## 9. 给你的一句话行动建议

先做**零新增数据**的三件事（1~2 天量级）：`market_stat` 补 21 列负反馈变量 → repair 状态显式化 + 事件研究（MVP2/3）→ 首板候选池 + 能从现有日线算出的 5 个特征族（记忆/形态/技术结构/抗跌/协同）。
**卡在外面的两件事**：§十三 RS 需先建指数采集；§八 的 250/500 日窗口需先把回填前推。
**放弃一件事**：§二十二 社交媒体，数据不可得。
