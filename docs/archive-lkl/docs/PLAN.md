# 实施计划 v2.4（2026-09-01 情绪周期研究引擎 v1 讨论后修订）

> v2.2 变更：① 新增函数 50 行红线规则 ② 移除 D3/D3-exec 买入时点需求——软件只出信号与建议，买点人工决定 ③ 执行层留接口占位 ④ 配套产出 docs/IMPL_PLAN.md 代码实施计划
> v2.3 变更：新增 E4 LLM 层（OpenAI 兼容、参数三级自定义、不进信号链路）
> v2.4 变更：吸收《龙空龙情绪周期研究引擎 v1》——新增 F 组决策（§0 四轮）、
> §1.10 晋级矩阵、§1.11 加速事件、§1.12 生态可用性评级；§1.3 情绪指标补中位数、
> 缺失值改显式 None（不再补 0）。**状态机维持 4 态骨架，加速定性为事件非状态。**

## 0. 决策总表

### 首轮（v1.0）
| 编号 | 决策项 | 结论 |
|------|--------|------|
| D1 | 交易范围 | 只做主板 10%：600/601/000/001/002；剔除 ST、次新 |
| ~~D3~~ | ~~买入时点~~ | **已移除（v2.2）**：软件不定义买入时点，只产出信号+建议，买点人工决定 |
| ~~D3-exec~~ | ~~次日执行细则~~ | **已移除（v2.2）**：随 D3 删除；高开幅度等仅作复盘参考信息展示，不构成规则 |
| D4 | 卖出规则 | 断板卖 + 退潮清仓——作为**建议**输出，非自动执行 |
| D6 | 数据深度 | 2024-01-01 至今 |
| D7 | 换手认定 | 当日最低价 < 涨停价 = 有效投票（含T字，全天一字自然排除） |

### 二轮（吸收 A/B/C/D 后修订）
| 编号 | 决策项 | 结论 |
|------|--------|------|
| A | 数据存储 | **PostgreSQL**（AWS RDS 18.3，独立库 `longkonglong` 已建，verify-full SSL） |
| B | 产品形态 | **不做实时**，只做盘后复盘 + 建议参考；分钟线仅用于事后还原对照 |
| R1 | 龙头最低板数 | 参数化 `MIN_LEADER_DAYS ∈ {3,4}`（默认 4），评估期两版对比后定 |
| R2 | 淘汰确认 | **严格版**：昨日最高板组 ≥2 只、今日只剩 1 只涨停才算胜出；卫冕晋级不算 |
| R3 | 情绪指标口径 | **双口径**：家数/炸板率/跌停数/昨涨停表现=全市场剔ST次新；连板高度/梯队/龙头=主板 |
| R4 | 持仓来源 | **手动录入实仓**（`lkl position`），复盘建议基于真实持仓 |
| Q1 | 情绪窗口 | 买入窗口 = 发酵期(标准) ∪ 高潮/分歧期(增强)；退潮/冰点禁买 |
| Q2 | "可试仓"含义 | 冰点/启动期仅观察不买入，"可试仓"只是报告文案 |
| Q3 | 报告形式 | 终端打印 + Markdown 落盘入库双输出 |

### 三轮（v2.2 新指令）
| 编号 | 决策项 | 结论 |
|------|--------|------|
| E1 | 函数行数 | **>50 行红线：动笔前发现函数将超 50 行，立即停笔拆子函数**；日常目标 ≤30 行 |
| E2 | 执行接口 | 定义 `Executor` 协议 + `ManualExecutor`（v1 唯一实现：只记录建议不委托）+ `BrokerExecutor` 占位（NotImplementedError，预留 QMT/easytrader） |
| E3 | 回测降级 | 无买入时点假设 → 全量回测改为**信号质量评估**（§1.7）；"次日开盘买入"仅作显式标注的参考基线 |
| E4 | LLM 层 | **OpenAI 兼容协议薄封装**（DeepSeek/通义/OpenAI/Ollama 通用）；参数三级自定义（config profile → 环境变量/密钥文件 → 调用 kwargs）；**仅限增强用途，永不进信号链路**（§1.9） |

### 四轮（v2.4，2026-09-01 情绪周期研究引擎 v1 讨论）
| 编号 | 决策项 | 结论 |
|------|--------|------|
| F1 | 数据深度 | **接受数据不全**：不拉 Wind 五年历史，维持 D6（2024-01-01 至今）+ EM 增量。回测结论必须显式标注"周期覆盖不足一个完整长周期"，不得外推为策略长期有效性 |
| F2 | 加速定性 | **加速 = 事件（event），不是状态（phase）**。状态机维持 4 态（退潮/高潮/发酵/冰点）骨架不扩态；加速作为叠加标记，处理方式同现有 `diverge`（改窗口不改 phase）。理由：加速的实盘定义就是"一字板/每天开盘就涨停"这一可观测事件，扩成 8 态会导致判据组合爆炸、参数无法标定 |
| F3 | 晋级矩阵 | 新增 `promotion_day` 表：首板→二板 / 二板→三板 / 三板→四板 / 四板+ 分层晋级率，**名义口径与换手口径双列**（§1.10）。是 REPAIR 判据、加速背离判据、生态评级三者的共同前置 |
| F4 | 生态评级 | 新增 `dragon_environment` 三档 FAVORABLE/NEUTRAL/UNFAVORABLE（§1.12），**与 buy_window 并存不替换**：前者评"生态能不能干活"，后者是"今天给不给买"，语义不同不可合并。**B 方案落地（09-01 拍板）**：有利条件分**核心 G1/G4**（纯行情可算、历史全覆盖）与**增强 G2/G3**（依赖 theme_group/promotion_day 积累）；判定改为「核心全真 + 无不利成立 + 无有利被证伪 → FAVORABLE」，增强条件 UNKNOWN 由报告层显式标注"未验证"，不静默当作成立。动因：原"四条全真"实测致 FAVORABLE 恒 **0/645 天**（G2 在 644 天为 UNKNOWN，因 theme T13 于 08-31 才上线） |
| F5 | 中位数口径 | **已切 median（09-01 拍板）**：均值与中位数同时入库，判据列取 median（`EMOTION_PERF_BASIS` 保留回退）。实测差异（scripts/f5_diff.py，645 交易日）：60 天 phase/窗口翻转（9.3%），其中 **56 天收紧 NONE、仅 4 天放宽**，主翻转 高潮→退潮 26 天、发酵→退潮 13 天——median 系统性更保守。典型 2026-08-31：均值 +0.74% vs 中位数 −0.11% **方向相反**，过半昨涨停股当日亏损而均值被少数牛股拉正。历史已按新判据重跑，**回测数字须重新核对**（风险登记 9） |
| F6 | 缺失不补零 | 数据缺失一律返回 `None` 并在规则中显式处理，**禁止补 0/False 静默降级**（引擎 v1 §11）。已修：`_zt_perf` 缺前日→None、`_top_broke` 无 ≥2 板组→None（原实现 `NOT COALESCE(bool_or(...),false)` 在无组时得 True，把"没有高标"误判成"高标断板"）。实测影响：仅 2 天翻转（2024-01-03 退潮→冰点为回填边界首日；2026-08-31 见 F8） |
| F7 | 关系标签 | 引擎 v1 §4.5 六类关系标签（SAME_THEME/INDUSTRY_CHAIN/…）**暂缓**：theme_tag 仅 1 日样本（17 行），零统计力。待 P3 回测验证"梯队完整 vs 孤板"核心假设后再立项。现有黑名单 + FALSE_RELATION 负向防线维持 |
| F8 | 状态机 warm-up | **修复交付级 bug**：cron 每日 `lkl emotion <date>` 是单日 series，`y/b` 全为 None → `neg_feedback` 恒 0（高位分歧降级**从未在定时任务中生效**）、发酵规则与退潮 two_day/跌停分支静默失效。现 `run_range` 自动向前多取 `EMOTION_WARMUP=5` 日喂状态机，只回写目标区间。08-31 实证：修复前 nb=0/ENHANCED，修复后 nb=5/NONE |
| F9 | 加速阈值标定 | 影子模式实测（645 日）：A1 `gap>=2` 命中 **73 天（11.3%）**。背离度分布 gap=0 占 **75%**、gap≥2 占 15%、gap≥3 占 9.6%、gap≥4 占 6.8%——阈值确实卡在异常区而非误报泛滥。典型样本 2024-02-20 名义 8 板 / 可交易仅 3 板，高度全靠一字顶上去，龙空龙当日无标可选，判"加速"符合实盘。**决定：维持 `ACCEL_HEIGHT_GAP=2` 与 `ACCEL_ENFORCE=False`，影子跑约 2 周（至 09-15）核对命中率与实盘体感，再议是否提阈值或开干预** |
| A1 | 滚动基线读库 | 外部审计 P1-1/2（09-01 核实成立）：`_bomb_threshold` 与 `accelerate._baseline_ratio` 读 `market_stat`，而 run_range 先算后写 → 空库回填用固定阈值、二次重算读旧结果，不可重复；且 warm-up 5 日不保证「无命中延续昨日」继承链一致。**修复：双模式**——`replay`（从 DATA_START 内存全量重放，阈值/基线从内存序列滚动计算，不读库，可重复）+ `incremental`（读昨日已落库 phase 播种，cron 单日）；replay 后须与现库 diff=0 自验证。附带：回填期间 A3 基线恒 None → 加速判据 A3 从未生效，replay 落地后需重扫加速命中 |
| A2 | 时间穿越标注 | 外部审计 P1-3（09-01 核实成立，奎爷拍板**选 A：标注局限**）：`derive.py` 以当前 `stock_basic.is_st` 过滤全史、以当前 `float_shares` 反推全史换手率——ST 戴帽/摘帽与增发解禁会造成历史样本漂移与换手率失真。不拉历史数据（维持 F1），改为在 review._caliber、evaluate 报告头、PLAN §1.1 固定脚注「ST 与流通股本为**当前口径**，非 point-in-time」，相关回测结论只用于相对比较，不外推绝对收益 |
| A3 | G2 首板口径 | 外部审计 P1-4（09-01 核实成立）：theme.py 硬编码 `first_board_count=0`（P1 仅梯队口径，首板待 P1.5），而 G2 要求 ≥1 → 有利被证伪 → FAVORABLE 随 theme 数据积累归零。**修复：首板口径未实现前 g2 返回 UNKNOWN（不证伪不成立），note 标注"首板口径未实现(P1.5)"**；P1.5 落地后恢复判定 |
| A4 | 回测右删失 | 外部审计 P1-5（成立）：forward_stats 对前向数据不足 3/5 日的信号用不完整窗口极值入统计，近期信号系统性偏小。**修复：`len(w)==h` 才计算，不足置 None 并在报告标 pending 数**；三池对账同改（P1-8） |
| A5 | 信号闭环 | 外部审计 P1-6（成立）：daily.sh 无 `lkl signal`，信号样本自动积累的闭环未闭合。**修复：编排补 signal，位于 emotion 之后、promotion 之前（信号只需窗口+梯队）** |
| A6 | 题材事务化 | 外部审计 P1-7（成立）：theme 先 DELETE 再逐股拉 Wind，部分失败留残缺。**修复：先拉内存集合+完整性校验（覆盖数>0），通过才单事务 DELETE+INSERT；失败保留旧数据并标注 staleness** |
| A7 | 数据源硬校验 | 外部审计 P1-8（成立）：三池逐池 except-continue，全失败返回 0 照常出报告；reconcile 空池报"无差异 ✓"。**修复：全失败抛异常中断（set -e 拦截）；空池报"缺池无法对账"**。09-01 实跑再修正误报：池与自算**同为 0 是平静市况非缺失**（跌停 0 家日曾天天误报），改为交叉验证——池 0 行**且**自算口径有货才报缺 |
| A8 | 死代码清理 | 审计 P2/P3 速修：`dragon_env.run_range` 实测 TypeError（CLI 绕过未暴露）→ 修复并让 CLI 复用；`emotion.run_range` 空区间 days[0] 越界；review LLM 只收 head（重构笔误）；`types.top_broke` 默认 False 与 None 语义冲突；`entry` 换手缺失当 0 违反 F6 |
| S1 | 开板计数口径 | 外部解读报告（09-01 复盘讨论）核实：梯队段「炸N」直译东财 `zbc`（逐笔微观开板计数），易误读为 N 次完整炸板——分时可见开板阶段数与 zbc 语义不同。**修复（呈现层）**：渲染改「开板N(东财)」+ 首/末回封时间（`first_seal/last_seal` 已入库未用）；口径注写明 zbc 为供应商微观计数。**不造数据**：开板阶段数/累计时长/最大离板幅度需分钟级行情，无源，标为数据边界（F6 纪律）。判据不动：c5 `bomb_times>=1` 语义「开过板且封住」仍成立 |
| S2 | phase 继承显式化 | 同报告核实：无规则命中时继承昨日 phase，但报告呈现不区分「当日触发」与「历史延续」——09-01 三条高潮判据全未触发（涨停 80 不>80、炸率 0.17<阈 0.37、振幅未过 15%），纯继承，呈现却像当日触发。**修复（呈现层）**：classify 继承分支打 `·延续` tag（与 `·加速`/`·反转` 同机制）；报告①段区分「高潮（延续）」，延续日不摆面板数字当触发依据。**状态机不动**：无命中继承是设计，改判据会牵动 645 天历史。新增 `market_stat.phase_inherited` 落库；全史 replay：触发 408 / 延续 238 日。语义细节：phase 同值但规则命中≠延续（昨日冰点今日反转再确认是触发） |
| S3 | B4 换手口径 | 同报告核实：B4「最强题材」取 `highest_board DESC` 第一名（如海鸥住工 O2O 孤标置信 0.4）否决整个生态，与 G2 成员达标口径冲突。**奎爷拍板选 a**：B4 改判「**换手最高板组**各自题材的最大成员数」——龙空龙可参与标的是换手板，板块支持跟着可交易标的走；一字高标的孤立题材不再有否决权，但换手高标的题材孤立性被如实暴露。**全史验收（646 日重跑）**：仅 1 天翻转——2026-08-31 UNFAVORABLE→NEUTRAL（即解读报告指出的 O2O 误伤日）；09-01 实际 B4 不成立（万向德农反关税成员 2 板块有跟随），评级 UNFAVORABLE 由 B5（同身位跨 2 题材）独立正当支撑。踩坑：pandas NaN 归一（theme NULL→NaN truthy 绕过 UNKNOWN 分支，曾致 549 天误 UNFAVORABLE，`_th()` 修复） |
| S4 | 风格黑名单补漏 | 同报告核实：黑名单为精确匹配，「微盘股日频等权」≠「微盘股」漏网混入题材榜。**修复**：`THEME_STYLE_CONTAINS` 增「微盘」「等权」；先全扫 `SELECT DISTINCT theme` 一次性清族，不逐个打补丁 |
| W1 | seed 污染 + 增量阈值冻结 | 第二轮审计 P0-1/2（09-02 核实成立，均已实证复现）：① seed 被塞进 classify 后同走规则引擎，默认字段（limit_up=0/height=0/has_candidate=False）恰好命中冰点规则 → 昨日真实 phase 被就地改写成「冰点」，安静市况下凭空冰点+误压 NONE（09-01 侥幸正确只因 warm-up 窗口内 08-31 有规则命中冲掉污染）；② run_range 的 hist 是 days[0] 快照 dict 全区间冻结，与 replay 逐日滚动不等价——上轮「增量=replay」验收是 30 日中位数未被 warm-up 差异移动的数值巧合，结构不等价。**修复**：seed 不进规则引擎（classify 显式跳过 head）；run_range 改逐日滚动（循环内 hist 前插，与 replay 同构）；同区间两路径 diff=0 断言测试 |
| W2 | 次新统一剔除 | 第二轮审计 P0-3/7（成立，有实证）：次新股 8 条进过 top（好上好 89 天 3 板等）；`_NEW_OK` 只在 `_counts` 一处生效，ladder/promotion/accelerate/emotion 振幅断板候选函数全无次新过滤；refresh_first_bar 零调用（字段已手动填过 94%，但新票不再补）。**修复**：`_NEW_OK` 提升共享 SQL 片段统一引用；refresh_first_bar 挂 daily.sh；COALESCE 方向反转——first_bar_date 缺失倾向剔除（保守）。验收：修后统计次新进梯队历史天数与 phase 影响面 |
| W3 | 单事务化 | 第二轮审计 P0-8（成立）：db.get_conn 每调用独立连接独立事务，ladder/promotion/theme_group/theme_tag 四处 DELETE+INSERT 横跨两事务；theme.run 的 fetch_catalysts（几十次 Wind 网络调用）在 DELETE 之后——公告拉挂即半新半旧，set -e 拦不住。**修复**：db 层加 `transaction()` 共享连接上下文；四处 persist 改单事务 DELETE+INSERT；fetch_catalysts 移 DELETE 前（网络调用不进事务） |
| W4 | 回测诚实性 | 第二轮审计 P0-4/5/6（成立；4/5 维持上轮「标注局限」拍板不引历史数据）：① walk_forward「样本外」自证——判据阈值在全样本（含后 20%）标定，改名「分段稳定性对照」+ 声明头；② 历史信号 c5 换手腿超出 limit_pool_em 30 日窗口的回放标「换手口径不可核验」单独分组，不混入 passed 统计；③ 定量统计当前 ST/退市票历史进 top_group 天数，把 A2 文字披露升级为数字上限 |
| V1 | 数据接入健壮性 | 二轮审计 P1 接入层 8 条（先核实再修）：TDX 800 根固定拉取补覆盖校验（首根>ext_start→未完成可重试）；backfill 全军覆没对齐 A7 语义（预期非空区间 0 行=非零退出）；em_data_date 改行数分布判断（防单票停牌→天天 SKIP）；_backfill_one upsert 入 try；_upsert_pool 空数据丢弃+告警；fetch_float_shares 空守卫；snapshot/hot 守卫内移（EM 时间戳 vs 传入日期）；_em_clist 分页上限 20 页 |
| V2 | 口径统一+死代码 | 二轮审计 P1/P3 口径与死代码（先核实再修）：涨停价三套实现（utils/price 固定10%/_DERIVE_SQL 板块感知/flag_columns 固定10%）合一为单一板块感知实现交叉验证；换手最高板 H 三处定义（是否含首板）按 PLAN §1.5 统一；cont_days 跨 2024 边界腰斩——**奎爷拍板②a**：回填窗口前移 30 天专门喂 cont_days；死代码**③a 全删**：flag_columns/_cents/shift_trading_days/promotion.recent/utils/price 旧三函数/scripts 三个模拟脚本/f5_diff 差异A分支（git 历史可找回） |
| V3 | 判据修正 | 二轮审计 P1 情绪/梯队 10 条（动判据，全史 phase diff 先过目再落库）：y_competition 幸存者改换手口径（现名义含一字与 README 矛盾）；炸板率分母0→None（F6 禁补0，滚动基线跳过防假阈值传导）；derived_bar 当日整体缺失→显式「数据不可用」分支；_top_streak 停牌缺行归零；_top_amplitude 零涨停日守卫；promotion 今日侧缺失→None；首日种子渲染区分「无历史默认」与「·候选守卫命中」；_zt_perf 除零；唯一龙头标记落库。全史重放 diff 报告交奎爷验收 |
| V4 | 回测评估修正 | 二轮审计 P1 信号/回测 13 条：c3 淘汰性加身份校验（幸存者==候选本身）；c1/c2/c4 结构性恒真→报告显式标注有效筛子=c3/c5；c5 历史腿——**奎爷拍板④a**：不可核验日 c5 记 UNKNOWN 不参与 passed 判定；右删失防护（前向<5日→pending 不混入）；前向窗口仅1bar 不再退化当日买卖（T+1 自我约束）；回测出口补退潮清仓腿对齐实盘优先级；警告不否决结构化（warnings/blocking 分离聚合）；rule_ret_d 影子口径补汇总输出；样本去重；_dist inf 修正；param_matrix/forward_stats 空保护 |
| V5 | 报告与生态 | 二轮审计 P1 复盘/题材/生态 14 条：review 十段段级容错（全或无→降级）；MD/JSON 出口统一（checklist/空仓理由/人气榜先进 collect 再双渲染）；B3 lead(close) 前视——**奎爷拍板①a**：预览/复盘分离（当日运行 B3=None，历史回填=复盘态+报告标注）；B4/B5 置信度加权（conf<0.6 弱映射不参与否决）dragon_env 全史重算 diff；theme.run tag+group 两事务并一；LLM 超时上限+持仓脱敏；test_arch.py 正则修复（现匹配不到本仓写法，红线纯靠自觉）；Markdown 竖线转义；条件编号常量化；G2 文案方向；config 注释不等号方向修正。**收口核验（09-02）**：① config 三个阈值注释（G3 >-3.0 / B3 <-7.0 / G4 <=ref-1）与代码比较方向逐一核对全部一致——审计为提醒性条目，无实错；② 条件编号常量化落地：显示名从 fn.__name__[3:] 切片改为显式元组（插删条件不再错位）；③ 「警告不否决」结构化：split_checklist 分离条件/警告列，replay passed 只聚合条件（锁定测试 test_split_checklist_warnings_never_veto——警告返回 False 也不否决）；④ report 主口径去重：distribution/exit/hypothesis 消费 dedup_episodes 集（原始 vs 去重对比留 episode_summary 段，2026 样本 6→6 无重复） |
| V6 | 运维/schema/测试 | 二轮审计 P1/P2 运维防线 12 条：limit_pool_em 迁移补录（pool_type+主键）；daily.sh exit 语义分层（非交易日本次跳过=0 vs 异常/数据陈旧=非零）+ TZ 显式 + flock + set -euo pipefail；position 唯一键+基础 CHECK；conftest 连库标记；**⑤b 本地门槛**：CI 脚本（连库 skip、纯逻辑全绿才放行），暂不动仓库 Actions 配置；types/schema 漂移守卫测试（DDL vs _COLS vs dataclass information_schema 自动比对）；重写 8 个自证测试（复制逻辑/inspect.getsource 字符串匹配——删被测函数照样绿灯）；补 dates/calibrate 纯函数测试；CLI 日期解析友好报错；DB 连接复用。**实施驳回记录**：① DB 连接复用（进程级单例）实测被驳——多线程回填 workers=4 共享单连接串事务 + monkeypatch 测试关连接后单例悬挂（10 测试红），正确性优先，握手开销改由 reconcile_range 单条聚合 SQL 解决（645 天 1290 次查询→1 次）；②「CHECK 约束全表铺开」暂缓——枚举列由 Python 侧 dataclass + 漂移守卫覆盖，仅 position 铺唯一索引（防重复开仓已实证 UniqueViolation 拦截） |
| V7 | 收尾 | README/PLAN V 系列落档；全量测试+E1 红线；09-01 报告终验；分批 commit（V3/V5 判据批 diff 报告先交验收） |
| V8 | 实跑修复 | **09-02 收盘首次 systemd timer 全链实跑**：4分07秒 DAILY_DONE（15.7s CPU/100MB 峰值），V1 日期守卫/flock/exit 分层全部实弹生效；**抓到 V1 自伤 bug**——`_em_clist` 分页上限 20 页=2000 条 < 全市场 5556 只（需 56 页），snapshot 被砍到 1912/5200 行；缺数被报告「数据质量」段如实报警（V5 防线立功）。修复：上限 20→80 页（8000 条，护栏远高于正常需求，防 total 异常死循环保留），snapshot_daily 重拉 5209 行全量 + 全链重刷实证（涨停 25→50、名义/可交易最高板 4/3→5/5、缺数警示消除、评级链自洽）。B3 复盘态标注/B5 证据不足不计入/数据质量三道防线在实跑中全部正确呈现 |
| V9 | 产品迭代 | **三轮（产品经理 5 条建议，奎爷 3 项拍板）**：①报告头⓪速览段（链路健康+较昨日变化）；②⑫近 20 日 phase/buy_window/生态评级走势段；③持仓对账（ADOPTED 信号无对应 OPEN 持仓→报告警告行，覆盖退潮清仓静默盲区）；④webhook 推送通道（企微/钉钉 markdown，LKL_WEBHOOK_URL 默认空=关闭——**拍板：功能实现默认不使用**）；⑤LLM 点评移出 publish 同步路径（先落盘后异步，llm-comment 独立 CLI，LLM_ENABLED 仍默认关）；⑥evaluate --code/--group 筛选参数（验证单票/单组假设免手写 SQL）；⑦采纳率统计节（ADOPTED/DROPPED 反馈从未被读取——V9 补上）。推送内容**全部落报告文件**（拍板②），webhook 只做触达不另设格式 |
| V10 | LLM/筛选收口 | **V9 两增强面从「实现」到「用起来」**：① LLM_ENABLED 支持 LKL_LLM_ENABLED env 覆盖（默认仍 False——拍板③不变；daily.sh 异步自动化需要开关可注入）；② daily.sh 挂 llm-comment 异步步（报告落盘后 nohup 后台追加，不阻塞 DAILY_DONE，key 走 .secrets/llmkey 文件兜底）；③ 评估筛选实测并修 2 个真 bug——theme_tag 无 theme 列（`WHERE theme ILIKE` 一跑即崩 → primary_theme ILIKE + secondary_themes text[] ANY 匹配）、adopted 统计混入 08-31 演示种子 603099（feedback_date>=2026-09-01 过滤真实反馈期）；④ llm-comment 实测全链：09-02 报告追加 glm-5.2 点评（贴数据：退潮/炸板 27.5%/一字硬顶），幂等重跑跳过，脱敏无持仓信息；⑤ --code 600371 全史 0 样本如实报（600371 从未当过候选）、603038 有样本路径输出 5 候选→1 轮去重报告。测试 162→164 |
| V13 | 产品迭代批 P1/P2/P3（2026-09-05 外部代码审核 + 产品审计 7 项落地） | **审核报告逐条核对结论**：报告引述的 P0-1（alert-ack 链路断裂）/P1-1（涨停价口径分叉）/P1-2（upsert ±inf）/P2-1（transaction 重复）/P2-2（_trade 手写打点）/P2-3（字符串匹配）/P2-4（dbconfig 重读）/P3-1~P3-4（死代码/review 单体）在 `e381d98`（P1 批）及更早已修复；**新增 2 处审计缺口补强**（426178b）：① alert-ack CLI 纯 id 分发回归锁 3 条（此前仅 service 层测试，CLI 层无人锁）② 涨停价 SQL 整数式 vs Decimal 等价测试原只锁 10% 档——补 110/120 双档逐分等价（防科创/创业 20% 档静默分叉）。**P1 批（e381d98）**：数据可信度徽章 + 口径覆盖声明 + 空仓原因可见化；review.py 拆包（services/review/__init__.py + utils.py）；watchlist 表 DDL 语法修复（d3f3406）。**P2/P3 批（6a3ff40）**：① ⑥明日参考四层化——结论→数据事实（_data_facts_of 原始观测值）→关键判据→风险提示；② `lkl health-push` 独立健康服务 services/health.py（报告/数据链/信号回填三断档检测 + 去重入 alert 队列）+ lkl-health.timer 工作日 09:30 独立调度（主链中断次日开盘前即告警）；③ `lkl watch` CLI + 报告⑥附自选股状态段（有自选才渲染）；④ Dashboard 信号日历前端组件（修 `</html>` 后孤儿 JS + _origInit hack 从未渲染的遗留，移入主 script + 空态占位）。测试 197→231 |
| V14 | 次级推荐（2026-09-07 用户拍板，生成恒开 / 决策导出默认关） | **主推荐五条件 AND 逻辑完全不动，独立新增次级推荐**：放宽硬性阈值（降阈值非改判定结构）——c3 淘汰赛放宽为「昨日同身位(N-1板)竞争晋级」（主版要求昨日最高组唯一幸存，降档候选必败）、c4 板数 4→3、c5 换手 5.0%→3.5%；c1/c2 身份类不松。**语义（用户澄清）**：次级推荐**始终生成并落库**（dashboard/报告/CLI 一直有数据），**默认关闭的是「导出到交易端决策 json」**——`SECONDARY_EXPORT_ENABLED=False`（影子模式同 ACCEL_ENFORCE，env `LKL_SECONDARY_EXPORT_ENABLED` 可注入同 LKL_LLM_ENABLED），export 只导 BUY/SELL；开启后追加 `action='SECONDARY'` 动作到 decisions.json。次级是放宽阈值的观察性推荐，默认不自动变成交易端执行指令。隔离：`action='SECONDARY'` 同表同 UNIQUE，outcome/回填/dashboard 只认 BUY；次级回报在 import 端 continue 跳过（不落仓不留痕，无仓位语义）。主信号通过时不尝试次级（避免双 action）。四出口：落库（checklist 证据链）、报告⑥段「次级推荐」区块、JSON `secondary_signal` 字段（schema 仍 lkl/daily@2）、`lkl signal` 打印。全量回归 234 passed 主路径零回归；实测 2024-01-16 主失败→次级通过（2024-02-23 主通过→仅 BUY）。文档：README §3.1 + PLAN §1.5.1 |
| V14.1 | 禁买日次级观察记录（2026-09-09 用户拍板） | **起因**：2026-09-08（高潮·高位分歧降级 nb=4 → buy_window=NONE）无次级——`check_secondary_signal` 的 NONE 守卫把次级一并拦截，与「次级始终生成」的文档口径相悖。**改动**：① 删除次级函数内 NONE 短路（NONE 仅记 INFO「次级仍生成（观察记录，不导出）」）；② `check_signal` NONE 分支接线调用次级（主 BUY 守卫不动——禁买日绝不产可执行 BUY）；③ 报告⑥段 NONE 窗口次级加「禁买日观察记录」警示标注，`_no_signal_reason` 提示次级线索存在（「不构成买入指令」）；④ window 原样存 NONE 不伪装。隔离面不变：SECONDARY_EXPORT_ENABLED 默认关（不导出）、import 端 continue 跳过（无仓位语义）、outcome 回填按 action 天然覆盖（前瞻指标本就是观察价值）。新增测试 3 条锁定语义（NONE 生成 / 主 BUY 仍拦截 / 渲染标注），全量 240 passed 14 skipped。文档：README §3.1 + PLAN §1.5.1 同步。 |

## 1. 策略规格书（最终口径，全部常量进 config.py）

### 1.1 标的池
```
主板前缀 {600,601,000,001,002}；剔除名称含ST；
次新剔除：上市<60自然日 或 处于上市首日至首个非涨停日之间（上市日期源：Baostock）
```

### 1.2 基础判定（utils/price.py 唯一实现）
```
limit_up_price = round(pre_close*1.1, 2)；跌停价 = round(pre_close*0.9, 2)
is_limit_up = close == limit_up_price
touched_limit = high >= limit_up_price
is_bomb     = touched_limit and not is_limit_up          # 炸板（日线近似）
is_one_word = touched_limit and low == limit_up_price    # 一字=网络垄断，不认
is_exchange = is_limit_up and low < limit_up_price       # 有效投票（D7）
cont_days   = 连续涨停天数（一字计入计数；断板归零）
```

### 1.3 模块一：情绪周期（services/emotion.py，全市场口径）
```
指标：limit_up_count / bomb_rate=炸板/(封住+炸板)
     / zt_performance_mean = 昨日涨停股今日涨跌幅均值      # 仅展示，不作判据
     / zt_performance      = 昨日涨停股今日涨跌幅中位数    # ★F5 状态机判据
     / max_limit_days(主板，名义口径含一字) / limit_down_count
状态机（优先级 退潮>高潮>发酵>冰点，无命中延续昨日状态，状态入库）：
  冰点/启动: max_limit_days<=3 且 limit_up_count<40
            且 (zt_perf 昨<0今>0 或 当日无唯一换手最高板候选)  # 2026-09 放宽
  发酵/上升: max_limit_days>=4 且 zt_perf 连续2日>1.5 且 涨停家数连增2日
  高潮/分歧: limit_up_count>80 或 bomb_rate>自适应阈值 或 最高板股振幅>15%
            # 自适应阈值=前30交易日炸板率中位数+σ（不含当日；预热期回退固定0.42）
  退潮:     最高板断板且次日未反包 或 zt_perf<-2 或 跌停>昨×2且>15
输出：buy_window = {STANDARD(发酵), ENHANCED(高潮/分歧), NONE(冰点/退潮)}
     退潮期额外置 force_liquidate = True
叠加标记（不改 phase，只改窗口；与 phase 正交）：
     diverge    高潮且负反馈 nb>=3 → 窗口降 STANDARD（§2026-09 校准）
     accelerate 加速事件命中 → 窗口降 NONE（§1.11，F2）
"无命中延续昨日"即引擎 v1 §5 要求的迟滞机制，防止一天一变；
  注意其副作用是启动方向也晚一天确认，故迟滞只用于收敛方向，不额外加严。
★F6 缺失值：prev 不存在或样本为空 → 返回 None（禁补 0.0/False）；
  规则遇 None 判据视为"该条不成立"并降低当日置信度，报告显式标注缺数据。
★名义 vs 可交易：phase 判据用 max_limit_days（名义），候选生成用 H（换手），
  两口径并存且必须在报告并列展示，禁止混用表述（引擎 v1 §2.2）。
```

### 1.4 模块二：连板梯队（services/ladder.py，主板口径）
```
每日按 cont_days 分组（≥2板入梯队）；
H = 当日换手板最高连板数；同身位 = cont_days==H 且 is_exchange 的股票集合
唯一最高板 = len(同身位)==1 且 H >= MIN_LEADER_DAYS(默认4)
```

### 1.5 模块三：买入信号（services/entry.py，AND 全满足）
```
前提 buy_window != NONE，对候选 S：
1 唯一性: S 为当日唯一最高板（§1.4）
2 换手性: S 今日 is_exchange == True
3 淘汰性(严格版R2): 昨日最高板组 ≥2只，今日仅 S 涨停，其余全部断板/炸板未回封
4 板数门槛: S.cont_days >= MIN_LEADER_DAYS
5 [仅分歧期] 强度补偿: S 今日炸板≥1次回封，或换手率 ≥ DIVERGE_MIN_TURNOVER(默认5%)
→ 产出 signal(BUY, confirm_date=T, window=STANDARD/ENHANCED, 条件核对清单)
※ v2.2：到此为止。何时买、买不买由奎爷决定；报告附参考锚点
  （昨收价、近5日同身位晋级率统计），不设执行规则。
```

#### 1.5.1 次级推荐（次推荐，V14 新增，生成恒开——含禁买日 / 决策导出默认关）

**拍板**：主推荐逻辑完全不动，次级独立放宽硬性阈值（降阈值，非改判定结构）。
**次级推荐始终生成并落库**（dashboard/报告/CLI 一直有数据；**2026-09-09
补充拍板：buy_window=NONE 禁买日同样生成**——次级是观察性推荐，价值恰在
主链路静默的日子提供身位线索；window 原样存 NONE，报告标注「禁买日观察
记录」，主 BUY 的 NONE 守卫不动）；默认关闭的是
「导出到交易端决策 json」——`SECONDARY_EXPORT_ENABLED=False`（影子模式，同
ACCEL_ENFORCE 哲学），支持 `LKL_SECONDARY_EXPORT_ENABLED` env 覆盖（同
`LKL_LLM_ENABLED` 哲学）。

```
主推荐未通过时（check_signal 尾部始终尝试；buy_window=NONE 日主信号直接
短路，但次级仍生成——观察记录），对候选 S：
1 唯一性: S 为当日唯一换手高标（sole_top, min_days=SECONDARY_MIN_LEADER_DAYS=3）
2 换手性: S 今日 is_exchange == True
3 淘汰性(次级放宽): 昨日同身位(N-1板)换手连板 ≥2只，候选今日晋级
   —— 主版要求「昨日最高组唯一幸存者=候选」，降档候选（3板，昨日2板
      梯队）不在昨日最高组，主版必败；改为「昨日同身位竞争晋级」
4 板数门槛: S.cont_days >= SECONDARY_MIN_LEADER_DAYS(=3，主=4)
5 [仅分歧期] 强度补偿: 换手率 ≥ SECONDARY_DIVERGE_MIN_TURNOVER(=3.5%，主=5.0%)
→ 产出 signal(SECONDARY, confirm_date=T, window, 条件核对清单)
```

- **隔离**：`action='SECONDARY'` 与主 BUY 同表同 UNIQUE(confirm_date, code,
  action)；outcome 回填 / 交易导出 / dashboard / 报告均只认 BUY，次级不污染
  主链路。主信号通过时不尝试次级（避免双 action）。
- **决策导出默认关**：`SECONDARY_EXPORT_ENABLED=False` 时 `lkl trade export`
  只导 BUY/SELL 到 decisions.json——次级是放宽阈值的观察性推荐，默认不自动
  变成交易端执行指令；开启后才把当日 `action='SECONDARY'` 记录一并写入
  （`exec` 仍 OPEN_POS，由交易端决定是否执行）。次级回报在 import 端
  continue 跳过：不落仓、不留痕，无仓位语义。
- **数据可追溯**：checklist 证据链随行落库；复盘报告⑥段「次级推荐」区块
  （主信号后、卖出建议前）；JSON 快照 `secondary_signal` 字段（schema 仍
  lkl/daily@2 向后兼容）；`lkl signal <date>` 主无信号时打印次级推荐。
- **展示不受导出开关影响**：报告/CLI/dashboard 始终读历史已落库的 SECONDARY
  记录，开关仅控 trade export 是否把次级写进决策 json。阈值常量：
  SECONDARY_MIN_LEADER_DAYS / SECONDARY_DIVERGE_MIN_TURNOVER 全部进
  config.py（规则6 禁 magic number）。

### 1.6 卖出建议（services/exit.py，基于 R4 手动录入的真实持仓）
```
a 断板建议: 持仓股 close < limit_up_price → 建议卖出（报告输出，不代执行）
b 退潮清仓建议: force_liquidate=True → 建议无条件清仓（优先级最高）
c 高潮/分歧期: 持有不动，仅禁新增非龙头仓
```

### 1.7 信号质量评估（E3：替代原回测）
```
对历史每个 BUY 信号统计（不假设买入时点）：
  次日开盘高开幅度分布 / 次日涨停率(晋级率) / 次日收盘溢价 / T+3、T+5 最大涨幅与最大回撤
情绪阶段分层统计：STANDARD vs ENHANCED 窗口信号质量对比
参数矩阵：MIN_LEADER_DAYS 3 vs 4 对比
参考基线（显式标注"仅为口径演示"）：次日开盘价买入、断板/退潮次日开盘卖出、
  费率 佣金0.02%双边+印花税0.05%卖侧 —— 结果不作为策略有效性结论
```

### 1.8 执行接口占位（E2）
```python
# services/execution.py
class Executor(Protocol):
    def submit(self, sig: Signal) -> ExecResult: ...
    def status(self, order_id: str) -> ExecResult: ...
class ManualExecutor:      # v1 唯一实现：信号落库 status=SUGGESTED，等待人工反馈
class BrokerExecutor:      # 占位 raise NotImplementedError；预留 QMT/easytrader 接入点
```
人工反馈闭环：`lkl feedback <date> <code> adopted|dropped <成交价>` → signal.status=ADOPTED/DROPPED，供评估期统计"建议采纳率"。

### 1.9 LLM 层（E4，services/llm.py）

```
协议: OpenAI 兼容 /chat/completions，httpx 薄封装（不绑供应商 SDK）
      base_url/model 可指向 DeepSeek、DashScope、OpenAI、本地 Ollama 等
参数三级覆盖: ① config.LLM_PROFILES 默认档 ② 环境变量 LKL_LLM_* ③ 调用时 kwargs
      可调参数: model / temperature / max_tokens / top_p / timeout / max_retry
密钥: 环境变量 LKL_LLM_API_KEY 或 config.LLM_KEY_FILE 指向的文件（同 DB 密码模式，不进仓库）
Profile 预设: default(通用) / fast(便宜快速) / smart(深度分析)，各档参数独立
```
```python
class LLMClient:
    def chat(self, messages, profile="default", **overrides) -> LLMReply
    def complete(self, prompt, system=None, **overrides) -> str
    # 内部: _post(重试退避) / _log_call(入库 llm_call_log: tokens/延迟/成本)
def get_client(profile="default") -> LLMClient   # 工厂+缓存
```
**架构红线**：LLM 只允许出现在"增强面"——复盘报告点评、异动归因辅助、评估报告摘要（P4 起按需接入 review/evaluate）。**信号链路（emotion/ladder/entry/exit）禁止 import llm**，保证任何 signal 可离线复现；LLM 未配置时全系统照常运行（LLMNotConfigured 显式抛出，调用方自行降级）。每次调用写 `llm_call_log`（profile、tokens、耗时、成本估算），用量可审计。

#### E4.1 已验证供应商（2026-08-29 实测，凭据存 `.secrets/llmkey` 不进仓库）

```
端点: https://token.sensenova.cn/v1  OpenAI兼容 ✅ /v1/models 200
模型实测延迟（同 key）:
  sensenova-6.8-flash-lite  14~90s ⚠ 推理模型，波动大 → smart 档
  deepseek-v4-flash          2.5s  ✅ → fast 档
  glm-5.2                    6.1s  ✅ → default 档
  sensenova-u1-fast          列表有但未开通（model is not found）
能力: tools / json_mode / reasoning；context 262k~1M
客户端必须处理的坑:
  ① 响应 content 可能裹 ```json markdown 围栏 → 解析前 strip fence
  ② 首token慢 → timeout 默认 120s + retry 2；交互场景用 stream
  ③ /v1/models 列出 ≠ 可用 → 以实际调用为准，失败记 llm_call_log
```

#### E4.2 规划用途：新闻聚合与精选（P4 立项）

```
链路: 新闻源(akshare stock_news_em / 财联社电报等) → 入库 news_item
     → LLM 批处理: 去重聚类 → 相关性打分(关联个股/板块) → 重要性分级
     → 精选结果进复盘报告⑥段"明日新闻参考"
纪律: 新闻精选仅作展示参考，不进信号判定（E4 红线延伸）；
     批处理走 fast 档(deepseek-v4-flash)控延迟，重点新闻才升 smart 档
```

### 1.10 模块五：晋级矩阵（services/promotion.py，F3）
```
分层口径（主板，剔ST剔次新，与 ladder 同源）：
  层 n ∈ {1→2, 2→3, 3→4, 4→5, 5+→6+}
  分母 promote_from = 昨日 cont_days==n 的只数
  名义晋级 promote_nominal = 其中今日 cont_days>=n+1 的只数（一字计入）
  换手晋级 promote_exchange = 上述且今日 is_exchange==True 的只数
  名义晋级率 = promote_nominal / promote_from
  换手晋级率 = promote_exchange / promote_from     # ★可成交口径，真机会
  失败负反馈 fail_perf = 该层晋级失败股今日平均涨跌幅（None 若样本空）
核心派生量：
  背离度 divergence = 名义晋级率 - 换手晋级率      # 高=晋级靠一字推，机会在消失
  低位/高位背离 = min(1→2,2→3 换手晋级率) - max(4→5,5+ 换手晋级率)
                  # 正=低位先暖（修复早期特征，引擎 v1 §4.3）
数据前置：derived_bar 自连接即可，无需新数据源。
写入：promotion_day（§2），每日 5 行。
```

### 1.11 模块六：加速事件（services/accelerate.py，F2）
```
定性：事件标记，非状态。命中不改 phase，只压 buy_window。
实盘定义（奎爷 2026-09-01）：一字板，或者每天开盘就涨停。

判据（全部可由 derived_bar 现有字段计算）：
  A1 高度背离：名义最高板 max_limit_days - 换手最高板 H >= ACCEL_HEIGHT_GAP(默认2)
              # 最高那几只是一字顶上去的，换手口径够不着
  A2 连续一字：最高板标的中 is_one_word 连续天数 >= ACCEL_ONEWORD_DAYS(默认2)
              # 即"每天开盘就涨停"
  A3 一字泛滥：当日涨停股一字占比 >= ACCEL_ONEWORD_RATIO(默认0.35)
              且 高于前30交易日中位数
命中规则：accelerate = A1 and (A2 or A3)
  理由：A1 是本质（机会与高度背离），A2/A3 是表现；单有 A3 只是普涨不是加速。
效果：accelerate==True → buy_window = NONE（不清仓，非退潮；只是买不到、后手赔率差）
输出：market_stat.accelerate + accel_reason（列出 A1/A2/A3 实测值）
边界：一字板在 is_one_word 定义为 low==涨停价，T 字板（开板回封）算 is_exchange，
     符合"可成交"语义，不误判为加速。
```

### 1.12 模块七：生态可用性评级（services/dragon_env.py，F4）
```
语义：评"当前生态是否允许龙空龙工作"，不是买入信号，与 buy_window 并存不替换。
输出三档 + 理由清单（引擎 v1 §6）：
  FAVORABLE   有利条件成立、无不利触发
  NEUTRAL     有利与不利混合，或证据不足
  UNFAVORABLE 任一硬不利条件触发

有利条件（需同时满足多数）：
  G1 可交易高度 H 处于扩张（近3日 H 单调不降且至少一日上升）      ← promotion/ladder
  G2 存在梯队完整的主线题材（theme_group.completeness 达标且有首板）← theme_group
  G3 前龙断板负反馈温和（近5日断板股次日平均跌幅 > -3%）           ← signal_outcome
  G4 胜者上方有空间（H < 近20日最高板 - 1）                        ← ladder
不利条件（触发任一即 UNFAVORABLE）：
  B1 加速事件命中（§1.11）
  B2 名义高度主要由一字制造（A3 命中且 divergence >= 0.3）
  B3 高标当天胜出、次日即核按钮（近5日 sole_top 次日跌幅 < -7%）
  B4 无主线板块支持，只剩独立高标（theme_group 最高题材 member_count==1）
  B5 多高标跨题材（同身位组 primary_theme 唯一值数 > 1）→ 淘汰是板数巧合
输出：market_stat.dragon_env / dragon_env_reasons(jsonb) / dragon_env_risks(jsonb)
纪律：数据缺失的条件记 UNKNOWN 不计入有利，宁降档不猜（F6 同源原则）。
```

## 2. 数据库设计（A）

实例：`database-1.cpu0ak8aqr7w.ap-northeast-1.rds.amazonaws.com:5432`
库：`longkonglong`（独立，不碰 ai_butler）；SSL verify-full + global-bundle.pem；
密码运行时读 `/home/ubuntu/.dbconfig`（`$DB_PW` 键），**不写入代码/仓库**。

```
stock_basic     code, name, list_date, market, is_st, updated_at
daily_bar       code, date, open, high, low, close, pre_close, volume,
                amount, turnover_rate                        -- 原始层(全市场)
derived_bar     code, date, is_limit_up, touched_limit, is_bomb,
                is_one_word, is_exchange, cont_days, amplitude -- 衍生层
limit_pool_em   date, code, name, cont_days_em, bomb_times,
                first_seal, last_seal, turnover_rate          -- 东财快照(对账)
market_stat     date, limit_up_count, bomb_rate, zt_performance,
                max_limit_days, limit_down_count, phase,
                buy_window, force_liquidate,
                top_amplitude, top_broke, bomb_threshold, has_candidate,
                neg_feedback, diverge,                          -- 2026-09 已加
                zt_performance_median,                          -- ★F5 判据列
                accelerate, accel_reason,                       -- ★F3 §1.11
                dragon_env, dragon_env_reasons, dragon_env_risks -- ★F4 §1.12
promotion_day   date, layer('1->2'…'5+->6+'), promote_from,     -- ★F3 §1.10
                promote_nominal, promote_exchange,
                rate_nominal, rate_exchange, divergence, fail_perf
ladder_day      date, code, cont_days, is_exchange, is_top,
                is_sole_top, y_top_group_count, y_top_survivor_count
signal          confirm_date, code, action(BUY/SELL), reason, window,
                checklist(jsonb), status(SUGGESTED/ADOPTED/DROPPED/EXPIRED),
                feedback_price, feedback_date                 -- E2 人工反馈闭环
position        code, entry_date, entry_price, shares, status, note  -- R4 手动录入
review_report   date, markdown, created_at                     -- Q3 留档
eval_result     信号质量评估统计输出                            -- §1.7
llm_call_log    id, ts, profile, model, prompt_tokens, completion_tokens,
                latency_ms, cost_est, purpose                   -- E4 用量审计
```

数据量估算：全市场 ~5400 股 × 650 交易日 ≈ 350 万行日线，RDS 无压力。
连接：psycopg3，参数集中 config.py。

## 3. 模块化架构（D 规范 + E1 强制执行）

```
lkl/
├── config.py        # 全部阈值/常量/DB参数 —— 唯一常量出口，禁 magic number
├── utils/           # 纯工具无业务
│   ├── db.py        #   连接、execute、upsert、query_df
│   ├── price.py     #   涨/跌停价舍入（全项目唯一实现）
│   └── dates.py     #   交易日历、shift_n
├── models/
│   ├── schema.py    #   建表 DDL（幂等 CREATE TABLE IF NOT EXISTS）
│   └── types.py     #   dataclass: Bar/DerivedBar/LadderRow/EmotionState/Signal/Position
├── services/        # 一文件一职责；仅经函数签名通信；禁跨模块访问内部变量
│   ├── ingest.py    #   AKShare/Baostock → DB（断点续拉、限速）
│   ├── derive.py    #   衍生字段计算与写库（§1.2）
│   ├── emotion.py   #   模块一（§1.3）
│   ├── ladder.py    #   模块二（§1.4）
│   ├── entry.py     #   模块三买入信号（§1.5，条件逐项断言→checklist）
│   ├── exit.py      #   卖出建议（§1.6）
│   ├── promotion.py #   模块五晋级矩阵（§1.10，★F3）
│   ├── accelerate.py#   模块六加速事件（§1.11，★F2）
│   ├── dragon_env.py#   模块七生态可用性评级（§1.12，★F4）
│   ├── theme.py     #   题材拓扑 T13（theme_tag/theme_group，docs/theme-design.md）
│   ├── calibrate.py #   周度阈值校准提案（L2）
│   ├── outcome.py   #   信号结果回填（L3，出口双口径）
│   ├── position.py  #   实仓录入/查询（R4）
│   ├── execution.py #   Executor协议+Manual/Broker占位（E2）
│   ├── llm.py       #   E4 LLM层：OpenAI兼容客户端+profile参数+调用日志（禁入信号链路）
│   ├── evaluate.py  #   信号质量评估（§1.7）
│   └── review.py    #   盘后复盘报告（B 核心交付）
├── main.py          # CLI 组装调度，零业务逻辑（>10行业务即违规）
└── tests/           # 黄金用例：深中华A(7板炸14次回封)、海鸥住工(一字)、实测日梯队断言
```

**硬性代码规则**：
1. **E1 红线**：函数预计 >50 行 → 停笔拆子函数再写；日常 ≤30 行
2. 单一职责：一个函数只做一件事；条件判断链每条件独立小函数（`check_uniqueness` / `check_exchange` ...）
3. 文件拆分：独立功能独立文件，禁止塞车
4. 接口隔离：模块间只调公开函数签名，禁止 import 私有符号（`_`开头）
5. 复用优先：重复≥2次逻辑抽 utils
6. 禁 magic number：常量全进 config.py
7. 禁循环依赖：方向锁定 `main → services → models/utils → config`
8. 每文件头部一行 docstring 声明职责

## 4. 复盘报告设计（B 核心交付，`lkl review [date]`）

输出（Q3）：终端结构化文本 + Markdown 落盘 `reports/YYYY-MM-DD.md` 并入库。

```
① 情绪面板: 五指标现值+昨日值、当前阶段、buy_window、阶段切换原因
   ★v2.4 补：zt_performance 均值与中位数并列展示（标出哪个是判据）；
            名义最高板与可交易(换手)最高板**并列**，禁止只报一个（引擎 v1 §2.2）；
            加速事件命中时整段顶部横幅提示
② 梯队全景: 4板[S]、3板[A,B]、2板[...C只] —— 标注换手/一字/炸板次数
③ 题材结构: theme_group 聚合（T13，PLAN 旧版漏记此段）
④ 淘汰赛况: 昨日最高板组名单 → 今日各自结果(晋级/断板/炸板) → 唯一性判定过程
⑤ 晋级矩阵: ★v2.4 新增（§1.10）—— 各层名义/换手双口径晋级率 + 背离度 +
   低位高位差，附近 5 日趋势；边界日 None 显示为"—"不显示 0%
⑥ 明日参考（E2 后只给信息不给指令）:
   信号: 龙头确认候选 + 5条件核对清单(✓/✗) + 昨收价 + 同参数历史信号晋级率
   持仓: 断板/退潮 → 卖出建议 + 依据
   无信号: 空仓理由(哪个条件没过)
⑦ 生态评级: ★v2.4 新增（§1.12）—— FAVORABLE/NEUTRAL/UNFAVORABLE + 有利/不利
   条件逐条 ✓/✗/?；与 ⑥ 的 buy_window 语义分别标注，不得合并表述
⑧ 反证与不确定性: ★v2.4 新增（引擎 v1 §8.7）—— 列出反对当前 phase 判定的
   证据、样本不足的条件、数据缺失项；无内容也要显式写"无反证"而非省略
⑨ 次日验证点: ★v2.4 新增（引擎 v1 §8.8）—— 出现哪些现象确认延续/切换；
   措辞纪律：写"次日验证窗口/观察条件"，禁止写"明日买入某股"
⑩ 数据质检: 自算连板 vs 东财字段对账差异行（有差异当日信号标黄）
```

## 5. 里程碑（按 B 重排优先级）

| 阶段 | 交付物 | 验收标准 |
|------|--------|----------|
| P0 数据层 | schema + ingest + derive | 2024至今全市场日线入库；自算连板 vs 东财对账一致率≥99%，差异清单入库 |
| P1 逻辑层 | emotion + ladder + entry + exit + execution(占位) + tests | 黄金用例全绿；任意历史日可单测复现判定过程 |
| P2 复盘器 | `lkl review` + `lkl position` + `lkl feedback` | 连续 5 个交易日盘后实跑，建议与人工复盘对账 |
| P3 评估 | `lkl evaluate`（§1.7，R1 两版对比） | 输出信号分布/晋级率/溢价统计报告；MIN_LEADER_DAYS 3vs4 对比 |
| P4 增强(可选) | 分钟线事后还原、Web 面板、BrokerExecutor 实接、**新闻聚合精选(LLM, PLAN §E4.2)** | 需求验证后立项 |
| **P5 市场评价层(v2.4)** | promotion + accelerate + dragon_env + F5/F6 口径修正 | ①晋级矩阵入库且与手工统计逐层一致 ②加速事件在已知一字垄断日命中、在换手健康日不误报 ③生态评级连续 5 交易日与人工复盘对账 ④F5 重跑后新旧 phase 序列差异清单可解释 |

## 6. 风险登记

1. AKShare 爬虫失效 → 三源对账 + DB 缓存，策略层只读库
2. 除权日 pre_close 错 → 涨跌幅反算校验，异常日剔除并记录
3. 状态机阈值（1.5%/80家/0.3/×2等）为 C 文档初值 → P3 敏感性分析调参
4. 严格版 R2 + 窗口双门槛，信号可能稀疏 → P3 给信号间隔分布，过稀再议放宽路径
5. 手动录入持仓若漏录，复盘建议失真 → 报告头部回显持仓表供核对
6. 分歧期强度补偿阈值为新增设计（非 C 原文）→ 参数化，P3 对比 STANDARD-only vs +ENHANCED
7. 无执行假设后，"策略赚钱能力"只能靠信号统计间接评估 → 采纳反馈闭环（§1.8）积累真实对照数据
8. LLM 非确定性污染信号的风险 → 架构红线：信号链路禁 import llm（§1.9）；lint 测试静态检查该约束；LLM 仅作增强面且默认关闭
9. **F5 中位数换判据是破坏性变更** → 历史 phase 序列会变，部分交易日窗口翻转。实施时先并跑出新旧两版差异清单（哪几天、为什么翻），人工过一遍再落库，禁止静默覆盖
10. **F1 周期覆盖不足**（2024-01 至今 ≈1.5 年，非引擎 v1 §9 要求的完整长周期）→ 所有回测/评估报告头部强制标注实际覆盖区间与"不足一个长周期"声明，结论禁止外推为策略长期有效性
11. **加速事件阈值 A1/A2/A3 为设计初值**，样本内无统计力标定 → 先以"只标注不干预"模式影子运行若干周，确认命中率与误报率后，再接 `buy_window=NONE` 降级
12. **promotion_day 边界日失真** → 回填窗口首日（2024-01-02）昨日无数据、分母不全；边界日各层显式置 None，不得输出 0% 晋级率污染趋势判断（F6 同源）
13. **dragon_env 与 buy_window 语义重叠风险** → 两者可能给出矛盾表述（如窗口 NONE 但生态 FAVORABLE）。报告必须分别标注语义（"生态可干"/"今日不给买"），禁止合并成单一结论

## 7. P4 模拟盘接入 · V11 卡点记录（2026-09-02）

- SDK 已就绪：`.venv-trade`（Python 3.10.21 + gmtrade 3.0.6，cp310 最高 ABI，主 venv 3.14 不可用）
- 凭据已落 `.secrets/gmkey`（id/token/endpoint，gitignored）
- **卡点**：`set_token → set_endpoint("api.myquant.cn:9000") → login` 报 1001「无法连接到终端服务」
- 网络判别（硬证据）：
  - api.myquant.cn=119.23.163.23（阿里云），ICMP 通（57ms），80/443 通（nginx 应答）
  - 9000/7001 端口 TCP RST（明确拒绝，非超时丢包）
  - 两个独立海外源（AWS 本机 + mimir 23.95.34.160）同样被拒 → 排除单 IP 封锁
  - 官方文档地址未变更（myquant/paper-trading-doc 至今 api.myquant.cn:9000）
- **结论：掘金仿真交易端口限制海外来源（大概率境内白名单）**
- **奎爷拍板（2026-09-02）：暂缓，回头部署到境内机器再接**——SDK/凭据/判别结论全部留存，届时从「解法」一步直接跳到「验证登录」，不重走调研
- 解法选项（届时境内部署时参考）：
  - A. 境内 VPS/跳板做 TCP 转发（阿里云/腾讯云轻量 ~¥30/月）
  - B. 奎爷本地电脑跑交易转发（家里宽带=境内出口，frp/ngrok 反向隧道到 AWS）
  - C. 放弃掘金仿真，换境内可达的模拟盘（如 QMT/东财仿真）
- 恢复路径备忘：`.venv-trade/bin/python` + `.secrets/gmkey`（id/token/endpoint 三键）；
  验证命令：`set_token → set_endpoint("api.myquant.cn:9000") → account(account_id=id) → login` →
  `get_cash()/get_position()` 出资金持仓即通。SDK 装在仓库 `.venv-trade/`（gitignored，
  境内机 clone 后需 `uv venv --python 3.10 .venv-trade && uv pip install --python .venv-trade/bin/python gmtrade` 重建）

## 8. V12 Dashboard（2026-09-03）

### 架构
- **纯标准库 `http.server`**（零依赖），监听 `127.0.0.1:8098`；前端原生 JS + fetch 渲染
- **nginx 反代**：`location /dash/` → proxy_pass 8098，复用 /dl/ 的 `.htpasswd-dl` 凭证（同 realm "Docs Download"，浏览器共享认证缓存）
- **systemd 常驻**：`lkl-dash.service`（Restart=always，开机自启）

### 数据源
- 直接读本地 `reports/` 的 `YYYY-MM-DD.json`（结构化数据），无需额外同步
- API：`/api/dates`（可用日期列表）/ `/api/latest`（json+md 双存在校验）/ `/api/report?date=`（指定日 JSON）

### 出发口径
| 项目 | 结论 |
|------|------|
| 技术栈 | 纯标准库 http.server，零依赖 |
| 日期选择 | 下拉框列出全部可用日期，默认最新 |
| 下载最新 | 按钮始终指向最新日期 md（`/dl/<latest>.md`），与日期选择器解耦；最新日期 = 目录扫描（json + md 双存在校验） |
| 全中文化 | 所有对外显示字段（龙空龙评级、买点窗口、持仓状态、明日参考等）一律中文化，映射函数 `cnEnv`/`cnWin`/`cnStatus`/`cnText` |
| 认证 | 复用 /dl/ 的 `.htpasswd-dl`（11233/QK6emisKyLwG），同 realm 浏览器缓存 |

## 9. T13 交易桥（DB 侧，2026-09-03）

### 职责分离：DB 侧（main）↔ 交易端（with-trade）
- **DB 侧（main）**：`lkl trade export` / `import` / `sync`
  - export：盘后导出次日执行决策 → `trade/decisions.json`；`for_date` = D+1；BUY 单查 `signal WHERE confirm_date<=D AND status IN ('SUGGESTED','T+1')`（含 T+1 重试），SELL 单调 `exit.suggestions(D)`（盘后当日数据，次日执行）；导出后 BUY 标 `EXPORTED` 防重复
  - import：消费交易端成交回报 `trade/results.json` → 落库，处理即归档（见下纪律）
  - sync：以交易端持仓快照 `trade/holdings.json` 为权威重建 position（幻影仓自动消失）
- **交易端（with-trade 分支）**：消费 decisions.json 实单，写 results.json 回报 + holdings.json 持仓快照

### DB 侧纪律（奎爷 2026-09-03，写进交易端提示词）
| # | 纪律 | 落点 |
|---|---|---|
| 1 | decisions 的 `for_date` 必须按 **Asia/Shanghai 交易日**算（+08:00），不能 `date.today()` 于 UTC 服务器 | `lkl/utils/dates.py today_sh()`；CLI export 缺省日期、daily.sh 均走它 |
| 2 | results.json 是「一张发票」：DB import 成功即改名归档 `results-<for_date>.json`（或打 `.consumed`），防次日覆盖丢账 | `import_results.consume()` 已实现 rename 归档 |
| 3 | holdings.json 建议盘前同步一次（交易日开盘前），DB 侧 import 以它为准重建 position | `holdings.reconcile()`（幂等：删全部 OPEN 重建） |

### 出发口径（奎爷确认 2026-09-03）
| 项 | 结论 |
|---|---|
| BUY 查询日 | confirm_date = 信号日 D（非 D+1），for_date=D+1 单独控制 |
| SELL 数据日 | exit.suggestions(D)，用当日盘后数据生成次日执行建议 |
| BUY fail 状态 | `T+1`——下次交易日再导出重试（非 REJECTED 永久放弃） |
| 消费幂等 | 处理即归档 results-<for_date>.json；position 靠 `position_open_code_uk` |
| 模拟盘 | 沿用 §7 拍板：掘金仿真海外被拒暂缓，桥接层先就位，境内机器再接通 |
