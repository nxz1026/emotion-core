# 龙空龙 · A股龙头筛选器（longkonglong / lkl）

A 股超短线「龙空龙」策略的信号与盘后复盘软件。

## 策略哲学：把市场当成一个分布式系统选主

- **连板 = 心跳**：一只票连续涨停，说明资金持续为它投票；
- **换手 = 有效投票**：一字板是"网络垄断"（买不进，投票无效），不认；只有换手封板的涨停才是有效心跳（D7）；
- **每天淘汰同身位对手**：连板梯队逐日 PK，同高度只剩一只换手票时，**唯一龙头确立**，产出信号；
- **无龙头则建议空仓**：情绪周期不配合（冰点/退潮）或梯队混战时，系统明确给出"不动"的建议。

软件只出**信号与建议（盘后生成）**，不给买点、不做执行时机（D3）；执行层仅预留券商接口占位。

## 核心机制

### 1. 情绪周期状态机（全市场口径）

四相位，优先级 **退潮 > 高潮 > 发酵 > 冰点**，无命中延续昨日（S2：延续日报告显式标注「高潮（延续）」并注明当日数字仅参考，不冒充触发依据；`market_stat.phase_inherited` 可查）：

| 相位 | 触发条件（2026-09 校准后） | 买入窗口 |
|---|---|---|
| 冰点/启动 | 最高板≤3 且 涨停<40，且（昨涨停表现反转确认 **或** 当日无唯一候选） | NONE |
| 发酵/上升 | 最高板≥4 且 昨涨停表现连续2日>1.5 且 涨停家数连增2日 | STANDARD |
| 高潮/分歧 | 涨停>80 或 炸板率>**自适应阈值**（前30日中位数+σ） 或 最高板振幅>15% | ENHANCED；**高位分歧降级**：负反馈计数 nb≥3（断板/高度降/炸升/表现降/跌停升）→ NONE 禁买不清仓 |
| 退潮 | 最高板断板两日链 或 昨涨停表现<-2 或 跌停激增（>15且翻倍） | NONE + 强制清仓 |

### 2. 连板梯队淘汰（主板口径）

- 涨停价判定：`round(pre_close × 1.1, 2)`（分币级取整公式，杜绝浮点误差）；
- 梯队按 cont_days 分层，一字板不参与最高层 PK；
- **R2 严格淘汰**：昨日最高板组≥2只、今日恰好剩1只换手存活 → 淘汰完成；
- **R1 龙头门槛**：唯一换手最高板高度 ≥ MIN_LEADER_DAYS(4)。

### 3. 五条件买入清单（entry.checklist）

信号日候选须同时满足：窗口匹配、唯一最高板、断板确认、分歧换手≥5%、次日竞价预期等五条（详见 [PLAN §1.5](docs/PLAN.md)）。

**警告项（不否决）**：W1 同身位扎堆——绝对最高板≥2只时提示降仓/放弃
（P3a 证据：该组中位 -8.69%、胜率 8%，n=12 小样本，奎爷拍板"做警告
不否决"）。

### 3.1 次级推荐（次推荐，始终生成——含禁买日；决策导出默认关闭）

主推荐五条件 AND 全过才产 BUY 信号（最严格）。**次级推荐**在保持主推荐
逻辑完全不动的前提下，放宽硬性阈值后额外推荐：主信号未通过时始终尝试
次级，产出 `action='SECONDARY'` 记录，与主 BUY 同表同 UNIQUE 约束天然
隔离，不污染主链路（outcome 回填 / 交易导出 / dashboard 均只认 BUY）。

**禁买日（buy_window=NONE）次级仍生成**（2026-09-09 拍板）：次级是放宽
阈值的观察性推荐，其存在价值恰在主链路不给信号的日子里提供「假如明天
转暖，谁在身位上」的线索——NONE 日次级以观察记录落库（window 原样存
NONE，不伪装），报告⑥段带「禁买日观察记录」警示标注，且不导出到交易
端（`SECONDARY_EXPORT_ENABLED` 默认关）。主推荐 BUY 的 NONE 守卫不动：
禁买日绝不产可执行 BUY。

**次级推荐始终生成并落库**——dashboard / 复盘报告 / CLI 一直有次级数据；
**默认关闭的是「导出到交易端决策 json」**：`SECONDARY_EXPORT_ENABLED=False`
时 export 只导 BUY/SELL，次级是放宽阈值的观察性推荐，不自动变成交易端
执行指令；开启后才把当日 SECONDARY 记录一并写入 `decisions.json`。

放宽项（详见 [PLAN §1.5](docs/PLAN.md) 次级小节）：

| 条件 | 主推荐 | 次级推荐 |
|------|--------|----------|
| c1 唯一换手高标 | 唯一 4板+ | 唯一 3板+（随 c4 降档） |
| c2 换手板非一字 | 不松 | 不松（策略核心） |
| c3 淘汰赛身份 | 昨日最高组唯一幸存者=候选 | 昨日同身位(N-1板)换手连板≥2只竞争→今日晋级 |
| c4 最低板数 | ≥4 | ≥3（`SECONDARY_MIN_LEADER_DAYS`） |
| c5 分歧换手 | ≥5.0% | ≥3.5%（`SECONDARY_DIVERGE_MIN_TURNOVER`） |

阈值与导出开关常量全部进 `lkl/config.py`：`SECONDARY_EXPORT_ENABLED`
（支持 `LKL_SECONDARY_EXPORT_ENABLED` env 覆盖，同 `LKL_LLM_ENABLED` 哲学）、
`SECONDARY_MIN_LEADER_DAYS`、`SECONDARY_DIVERGE_MIN_TURNOVER`。

数据可追溯：次级信号以 `action='SECONDARY'` 落库 `signal` 表（含完整
checklist 证据链），复盘报告⑥段渲染「次级推荐」区块、JSON 快照含
`secondary_signal` 字段、`lkl signal <date>` 主无信号时打印次级推荐。
次级回报在交易端消费时只跳过不落仓不留痕（观察性，无仓位语义）。

### 4. 出口口径（E??A 正式 + 五族对照，2026-09-03 审计三轮）

正式口径 `rule_ret` = **E??A**（退潮清仓优先，与实盘 exit.suggestions 同一
优先级：退潮日按强制清仓日收盘结算，无退潮按 A 断板次日开盘卖）；
出口族 A/B/C/D/E 五列并列（`evaluate._exit_family`）：
A 断板开盘 / B 断板收盘 / C 两日收盘 / D 半仓（影子口径
signal_outcome.rule_ret_d，2-3 个月样本对照再议转正）/ E 退潮清仓。
08-31 出口对比（24 通过信号）：A 中位 -3.20/胜率 29% vs D 中位 +0.45/
胜率 54%——A 砍尾快但砸低开恐慌点；口径拍板历史见 [ADR-0001](docs/adr/0001-exit-family-calib.md)。

### 5. 晋级矩阵（v2.4 新增，[PLAN §1.10](docs/PLAN.md)）

按层统计 `1→2 / 2→3 / 3→4 / 4→5 / 5+→6+`，每层出**两个口径**：

- **名义晋级率**：连板延续即算（一字计入）；
- **换手晋级率**：上述且今日非一字——**可成交口径，才是真机会**；
- **背离度 = 名义 − 换手**：背离越大，说明高度是靠一字顶上去的，
  市场在涨而机会在消失。

分母 < 3 不出率（置 NULL），避免小样本 0% 污染趋势判断。

### 6. 加速事件（v2.4 新增，[PLAN §1.11](docs/PLAN.md)）

**定性为事件，不是状态**——命中不改 phase，只打标记（与 `diverge` 同构）。

实盘定义即"一字板，或者每天开盘就涨停"，三条判据：

| 判据 | 含义 |
|---|---|
| A1 高度背离 | 名义最高板 − 可交易最高板 ≥ 2（最高标根本买不到） |
| A2 连续一字 | 最高板组内单票连续一字 ≥ 2 日 |
| A3 一字泛滥 | 主板涨停一字占比 ≥ 0.35 且高于前 30 日中位数 |

命中规则 `A1 and (A2 or A3)`：A1 是本质，A2/A3 是表现；只有 A3 那是普涨不是加速。

**当前为影子模式**（`ACCEL_ENFORCE=False`）：只标注不压窗口，阈值未经样本标定，
先观察命中率与误报率再决定是否干预（PLAN 风险登记 11）。

### 7. 生态可用性评级（v2.4 新增，[PLAN §1.12](docs/PLAN.md)）

回答"当前生态允不允许龙空龙工作"，**不是买入信号**，与 `buy_window` 并存不替换：

- 有利 G1~G4：可交易高度扩张 / 主线梯队完整 / 断板负反馈温和 / 胜者上方有空间；
- 不利 B1~B5：加速事件 / 高度靠一字制造 / 胜出次日即核按钮 / **无板块支持（S3 换手口径：判换手最高板组各自题材是否全孤立——一字孤标的题材无生态否决权）** / 高标跨题材；
- 三档 `FAVORABLE / NEUTRAL / UNFAVORABLE`，任一不利成立即 UNFAVORABLE；
- 证据不足的条件记 `?`（UNKNOWN），既不计有利也不触发不利——**宁降档不猜**。

### 8. 数据可靠性与回测纪律（2026-09-01 外部审计修复，[PLAN 决策表 A1~A8](docs/PLAN.md)）

- **可重复重算**：`lkl emotion replay` 纯内存滚动基线，同数据任意环境重跑零漂移；
- **右删失防护**：前向窗口不满 3/5 日的信号不计极值，标 pending 不猜；
- **数据源硬校验**：三池全挂中断出报告（宁停不假），题材拉取失败保留旧数据不残缺；
- **假成功清零**：空池≠无差异（与自算口径交叉验证），全失败≠成功 0 行；
- **供应商口径显式化（S1）**：开板计数为东财 `zbc` 逐笔微观口径，报告标注并附首/末回封时间，不与分时可见的开板阶段数混淆；
- **风格噪声过滤（S4）**：市值/指数族标签（微盘/小盘/大盘/等权等）不入题材榜；
- **增量=全量等价（W1）**：cron 增量与 replay 同构逐日滚动基线 + seed 不进规则引擎（同区间两路径判据级 diff=0 有测试锁定），「增量=replay」不再靠数值巧合；
- **次新统一剔除（W2）**：上市<90 自然日不入梯队/晋级/高度/一字/涨停家数判据（`config.NEW_ISSUER_FILTER` 单一出处四处引用，first_bar_date 缺失倾向剔除；daily 链自动回填首bar）；
- **写库原子性（W3）**：ladder/promotion/theme 四处 DELETE+INSERT 同一事务（`db.transaction()`），中途异常整体回滚不再有半清空日；Wind 网络调用移出事务块；
- **回测诚实性（W4）**：walk_forward 改名「分段稳定性对照」并声明阈值全样本标定；EM 池窗口外信号 c5 换手/回封腿标不可核验；当前 ST 票历史 top_group 条数定量披露；
- **接入层守卫（V1）**：TDX 回填覆盖校验+断点可重试、全败非零退出（A7 语义对齐）、EM 交易日守卫众数判定（抗单票停牌）、snapshot/hot 日期守卫内移（杜绝陈旧行情盖历史日）；
- **判据口径修正（V3）**：淘汰赛幸存者=换手口径且须为候选本身（c3 身份校验）、炸板率分母 0→None（F6 禁假 0）、数据缺失日显式「缺数据」分支不误判冰点、最高板振幅判据零涨停日不触发、晋级率今日侧缺数不产出假 0%、一字连板统计停牌断档归零——全史重放 diff=0（判据加固不翻历史）；
- **回测出口修正（V4）**：右删失 pending 不入统计（窗外仍涨停不再「近似结算」）、单 bar 场景拒绝当日买卖（T+1 合法）、E_退潮清仓腿对齐实盘优先级、盈亏比 inf→文字标注、c1/c2/c4 结构恒真显式披露（有效筛子=c3/c5）；
- **生态评级前视修正（V5）**：B3 预览/复盘分离（当日运行不含次日数据，历史回填标复盘态）、B5 置信度加权（conf<0.6 弱映射不制造假跨题材）、报告段级容错（单段挂不再全或无）、LLM 持仓脱敏、E4 红线守卫真实生效（from-import 写法可拦）；
- **运维防线（V6）**：daily.sh exit 分层（守卫失败/锁冲突非零退出）+ TZ 固定 + flock 重入锁、position 防重复开仓唯一索引、market_stat/ladder_day 漂移守卫（information_schema 自动比对）、本地 CI 门槛 `scripts/ci_local.sh`（测试+E1+架构红线）。

### 9. 产品迭代（2026-09-02 三轮·产品经理建议，[PLAN 决策表 V9](docs/PLAN.md)）

- **报告速览（⓪ 段）**：链路健康度（最近成功报告距今 N 个交易日，断档显式报警）+ 较昨日变化摘要（涨停家数/双口径高度/阶段/窗口/生态评级，30 秒读完当日结论）；
- **近 20 日走势（⑫ 段）**：phase/buy_window/生态评级时间线表，判据翻转（如 F5）一眼可见，不用翻历史 MD；
- **持仓对账（⑥ 段附）**：ADOPTED 信号无对应 OPEN 持仓 → 报告警告「疑似漏录」（堵住退潮清仓建议对漏录仓位纯静默的盲区）；
- **webhook 触达（notify.py）**：企微/钉钉 markdown 按地址自适应，推送 ⓪ 速览摘要且剥离持仓明细；`LKL_WEBHOOK_URL` 默认空=关闭（拍板：功能实现默认不使用）；
- **LLM 点评异步化**：移出 publish 同步路径（报告先落盘不被第三方 API 阻塞），`lkl llm-comment [date]` 独立追加，幂等不重复调用；
- **评估筛选**：`lkl evaluate <start> <end> --code 600371 --theme 算力`（单票/单题材假设验证免手写 SQL，落盘文件名含筛选后缀防覆盖全量报告）；
- **采纳率统计（评估报告新节）**：ADOPTED/DROPPED/SUGGESTED 计数 + 回执价 vs 信号日收盘均值/中位——feedback 闭环数据此前只写不读，现在物尽其用。


### 10. 数据与状态可信性（2026-09-03 三轮审计修复）

- **回执契约 v2**：results 五字段（ok/price/shares/order_id/reason）对齐交易端；
  SELL 按成交量扣减（部分成交保 OPEN 余股），空 holdings=真清仓；
- **交易日历**：for_date = next_trading_day（daily_bar 真日历优先，至少跳周末），
  导出/状态页/轮询同口径，不再自然日+1 落休市日；
- **全量消费**：results/holdings 按升序消费全部文件（旧文件不再被最新一份遮蔽），
  一份一归档；池表同日同池 DELETE+INSERT 原子替换（源端移出的票不留幽灵行）；
- **TDX 分页全历史**：800 根单页 → 分页翻到上市日（覆盖校验不再依赖可能截断的
  first_bar_date 自证）；
- **测试隔离**：连库测试 `LKL_WITH_DB=1` 显式开启，默认全 skip 收集期零连接
  （`scripts/ci_local.sh --with-db`）；
- **LLM 白名单脱敏**：只送市场面段（速览/情绪/梯队/题材/淘汰赛/生态/反证/
  验证点/质检），持仓/信号/对账段整段跳过——按段名而非标题猜字；
- **必需段守卫**：情绪/梯队/质检段渲染失败 → 整份报告拒绝发布（不表面成功）；
  ladder/promotion 空 results 前查上游缺数（derived_bar 当日 0 行=采集挂，拒清空）；
- **PIT 漂移标注**：replay 输出 pit_drift 列（当前 ST 或上市晚于信号日=不可比样本），
  评估报告免责段带实时计数；
- **采集异常分层**：FetchError（网络可重试）/DataError（协议变化不重试）/
  NoDataError（明确无数据永久跳过）三类显式类型，不再字符串匹配误判。

### 11. 产品功能批（2026-09-04 产品审计落地，[BACKLOG](docs/BACKLOG.md) A 系）

- **可用于决策总判（A1-1a）**：⓪速览首行三态 `OK ✅ / PARTIAL ⚠ / UNKNOWN ⚠`
  （聚合⑩质检 diff+warns；缺数绝不判绿），JSON 同源；
- **交易七态面板（A1-1b）**：`/api/trade-board?date=`（建议/已报/成交/撤单/
  拒绝/OPEN 持仓/最近对账时间）；SELL 撤单/拒绝不再静默——trade_event 留痕；
- **一键自检（A1-2）**：`lkl doctor`（DB/交易日历/时区/trade 目录/报告目录/
  LLM-webhook 状态六项通过清单）；
- **策略版本戳（A1-3）**：signal 落库带 `strategy_version`（=git describe），
  历史重放可对版本；
- **契约测试补齐（A1-4）**：撤单 CANCELLED/拒绝 REJECTED 留痕、BUY ok 缺
  shares 抛 DataError 拒收、未注册用户目录回报不消费；
- **停机总闸（A2-10）**：`lkl trade-halt on|off|status`——HALTED 标志文件
  （不依赖 DB），export/import/sync 三写入口全拒绝，人工确认后恢复；
- **流水线状态（A2-6）**：主链命令自动打点 pipeline_state（RUNNING→OK/FAILED
  +耗时），`lkl pipeline` 总览+失败步骤重跑命令；
- **回测—实盘一致性（A2-5）**：`lkl compare <date>` 当日实盘信号 vs 今日重放
  并排，规则漂移即插 alert 报警（不静默）；
- **告警闭环（A2-7）**：alert 表 + `lkl alerts` / `lkl alert-ack <id>`；
  pipeline 失败/报告 PARTIAL/漂移自动入队，webhook 可推送未确认队列；
- **证据卡与钻取（A2-4/A2-8）**：`/api/signal-detail?date=`（五条件 PASS/FAIL/
  UNKNOWN+原始依据+版本戳+竞争组/幸存者）、`/api/evidence?date=&code=`
  （daily_bar/derived_bar 原始行）；
- **数据修订标识（A2-9）**：upsert 覆盖历史行自动留痕 data_revision，
  `lkl revisions` 查受影响日（重生成走 evaluate/compare 新文件不覆盖）。

### 12. 交易桥一致性批（2026-09-04 当日，[REQUIREMENTS §4](docs/REQUIREMENTS.md)）

- **decisions 契约 v2（schema:2）**：actions[] 新增 `exec` 执行语义字段
  （BUY=`OPEN_POS` / SELL=`CLOSE_ALL`），`window` 仅为买入口径标签——
  堵「window=NONE 退潮清仓被交易端 EXCLUDED」事故（09-04 实发）；
- **回报 status 语义（双端联调拍板）**：results 行自带显式 `status`，
  消费端优先取该值——status 在场时禁止 reason 含字启发式猜状态（仅
  schema1 旧格式回落）。三态口径：`REJECTED`=门禁/券商拒，**可重试
  非终态**（留档待开市/次日再试——仓位不动，T+1 由新 for_date
  decisions 承担）；`CANCELLED`=撤单（终态）；`EXCLUDED`=决策排除
  （历史/人工，契约 v2 起不再产生）。与交易端 orderstate 属性同口径
  （REJECTED: retryable/非终态，EXCLUDED: terminal）；window 自 v2 起
  仅为展示标签，执行唯一依据 `exec`；
- **一致性消费 `lkl trade consume`**：单命令两阶段（全部 results 升序 →
  全部 holdings 升序）——消灭 import/sync 分裂进程的交错窗口
  （快照先吃/回报后吃的双重扣减与回滚）；毒丸整份拒收不中断，
  ERROR 告警 + 退出码 1；
- **旧快照仲裁**：holdings 快照时间戳早于本批最早回报 → 本轮跳过
  （防旧快照回滚已推进的账面）；
- **对账 diff 告警**：消费快照前后 OPEN 账面比较，偏差入 alert WARN
  （过程账与实盘的分歧可见化，不再静默纠正）；
- **15:05 收盘对账（lkl-close.timer）**：消费窗口设计三班——12:01 /
  15:05 / 17:20，盘中成交收盘即对齐不等盘后。
  ⚠️ **2026-09-18 实测更正**：`lkl-trade.timer`（12:01）与 `lkl-close.timer`
  （15:05）**当前为 disabled**（奎爷手动关闭，暂时不用），实际只有 17:20 的
  `daily.sh` 内含 consume + 30 分钟轮询在跑 → **日内对账延迟到盘后**，
  盘中交易七态面板看到的是旧账。要恢复：`sudo systemctl enable --now
  lkl-trade.timer lkl-close.timer`。
- **价格口径**：交换文件价格入库统一收缩 2 位小数（`apply._px`/
  `holdings._px`，浮点噪声 6.6999…→6.7）。

### 13. 产品迭代批 P2/P3（2026-09-05 产品审计 #1/#4/#5 + 自选清单落地）

P1 批（数据可信度徽章 / 口径覆盖 / 空仓原因可见化）见提交 `e381d98`。
P2/P3 四功能：

- **信号分层·数据事实层（产品 #1）**：⑥ 明日参考从三层扩为四层——
  结论 → **数据事实**（梯队最高板数/同身位只数、昨日最高板组→今日
  幸存、唯一换手高标换手率等**原始观测数值**）→ 关键判据（checklist
  逐项 ✓/✗/?）→ 风险提示（W* 警告）。数据缺时显式写「缺」，不编造
  （`review._data_facts_of`）；
- **断档主动推送（产品 #5）**：`lkl health-push` 升级为独立健康服务
  （`services/health.py`）——三断档检测（报告 gap / derived_bar 数据链
  gap / ≥6 交易日 BUY 信号无 complete outcome）+ **去重入 alert 队列**
  （同 source 未确认不重复轰炸）+ webhook 统一触达。配套
  `scripts/health-push.sh` + `lkl-health.timer`（工作日 09:30，
  **独立于 daily.sh 成败**——主链昨夜中断次日开盘前即告警，不再等
  用户发现）；
- **自选股 watchlist（产品 #3）**：`lkl watch add|rm|list|status` CLI +
  报告 ⑥附「自选股当日状态」段（有自选才渲染，空清单不占位）。
  状态口径：一字/换手 N 板、涨停首板、炸板、跌停、涨跌幅；
  停牌/缺数据显式「无行情」（`services/watchlist.py`）；
- **信号日历前端（产品 #4，数据层见 `e381d98`）**：Dashboard 底部
  📅 信号日历组件——按月度色块展示每信号日单数与命中率（绿 ≥50% /
  红 <50% / 橙待定），hover 见当日命中明细与最佳票。修复前次遗留：
  组件代码写在 `</html>` 后被浏览器忽略 + `_origInit` hack，从未渲染；
  现已移入主 `<script>` 并清空态占位。

### 13b. 代码审核复核（2026-09-05 外部审核逐条核对，提交 `426178b`）

审核报告 P0–P4 共 17 条，逐条对照后**多数引述 e381d98 之前的旧状态**：
P0-1（alert-ack）、P1-1（涨停价口径）、P1-2（±inf）、P2-1（transaction
别名）、P2-2（_trade 打点）、P2-3（字符串匹配）、P2-4（dbconfig 缓存）、
P3-1（_pending）、P3-3（惰性版本戳）、P3-4（review 拆包）均已在此前
提交修复。补强 2 处审计缺口：

- **alert-ack CLI 回归锁**：此前仅 service 层测试，`lkl alert-ack <id>`
  纯 id 参数分发无人锁定——补 3 条（纯 id / `ack` token 兼容 / 垃圾
  参数拒绝），防 handler 重构回退；
- **涨停价双档等价测试**：`test_cents_formula_matches_decimal` 只锁
  10% 档（`*11+5` 硬编码），SQL `{pct}` 参数化对科创/创业 20% 档
  （`LIMIT_PCT_BY_PREFIX=120`）无锁定——补 110/120 双档逐分等价，
  实测两式现等价，防任一改动静默分叉。

**保留不修**：P4-2（_accel 内联推导）与 P4-4（alerts/alert-ack 共享
handler）为设计权衡——共享 handler 有意支持两种调用形态，非正确性问题。

## 架构

```
lkl/
├── config.py          # 全部阈值/口径参数（单一事实源）
├── main.py            # CLI 入口（lkl <command>）
├── models/            # schema(建表+迁移) / types(数据类)
├── services/
│   ├── ingest.py      # 数据拉取：TDX日线回填 / EM快照 / EM三池
│   ├── derive.py      # 衍生层：判板/炸板/一字/连板数（全市场自算）
│   ├── trade/         # 交易桥：export/import_results/holdings/consume/halt/archive/naming
│   ├── emotion.py     # 情绪周期状态机（指标+相位+自适应阈值+加速叠加）
│   ├── ladder.py      # 连板梯队与唯一龙头判定（名义/换手双口径高度）
│   ├── promotion.py   # 晋级矩阵：分层名义/换手双口径晋级率与背离度（v2.4）
│   ├── accelerate.py  # 加速事件：A1/A2/A3 判据，事件非状态（v2.4）
│   ├── dragon_env.py  # 生态可用性评级：G1~G4/B1~B5 三档（v2.4）
│   ├── entry.py       # 五条件买入清单
│   ├── exit.py        # 卖出建议（断板/清仓）
│   ├── theme.py       # 题材拓扑（theme_tag/theme_group，Wind 概念+公告催化）
│   ├── review.py      # 盘后复盘报告（十段式，日常主命令）
│   ├── evaluate.py    # 信号质量评估（回放+窗口对比+参数矩阵）
│   ├── outcome.py     # 信号结果回填（出口双口径 A/D）
│   ├── calibrate.py   # 周度阈值校准提案（只提案不改参）
│   ├── position.py    # 持仓与信号回执
│   ├── compare.py     # 回测—实盘一致性对比（漂移→alert）
│   ├── pipeline.py    # 流水线打点（RUNNING/OK/FAILED+耗时）
│   ├── alerts.py      # 告警闭环（record/pending/ack）
│   ├── revision.py    # 数据修订留痕（upsert 覆盖历史）
│   ├── doctor.py      # lkl doctor 一键自检
│   └── llm.py         # 可选LLM点评（永不进入信号路径，默认关闭）
└── utils/             # db(连接池/消毒upsert) / dates / price
```

数据层：PostgreSQL（RDS），17 张表（daily_bar 340万行、derived_bar 316万行、market_stat 645日、promotion_day…）。凭据读 `~/.dbconfig`，不入库不入仓。

## 数据源（海外 IP 实测结论，详见 docs/DATA_SOURCES.md）

| 用途 | 源 | 说明 |
|---|---|---|
| 日线历史回填 | 通达信 pytdx | 0.87s/代码，长连接无限流 |
| 当日全市场快照 | 东财 push2delay clist | 收盘后终态，5207行 |
| 涨停/炸板/跌停池 | 东财（仅存近30日） | 用于对账，历史靠自算 |
| 情绪五指标 | **全自算**（derived_bar） | 剔ST剔次新，换手率口径已校准 |

## 快速开始

```bash
# 1. 环境（Python ≥3.12）
python -m venv .venv && .venv/bin/pip install -e .

# 2. 建库（需 ~/.dbconfig 提供 RDS 主机/密码）
lkl init-db

# 3. 历史回填（首次，约数小时）+ 衍生层
lkl fetch backfill
lkl derive 2024-01-01 2026-08-31

# 4. 每日盘后流程（一条命令，含交易日守卫：非交易日自动跳过）
#    步骤：快照→三池→人气榜→衍生→情绪→梯队→信号→晋级矩阵→题材→生态评级→复盘→回填
scripts/daily.sh [date]

# 4.5 历史情绪重算（可重复性：审计 A1 双模式）
#    replay = 从 DATA_START 全量重放，滚动基线纯内存，不读库——任何环境重跑结果一致
#    日常增量（cron 路径）自动播种昨日 phase，无需手动
lkl emotion replay              # 全史重放（回填/口径修正后用）
lkl emotion replay 2026-09-01   # 从某日重放到今天

# 5. 自动化：systemd 系统级 timer（全部 Asia/Shanghai）
#    lkl-daily.timer  工作日 17:20 盘后全流程（fetch→…→review→export→轮询30分钟）✅ 已启用
#    lkl-trade.timer  工作日 12:01 午间交易桥（export + 轮询30分钟）⛔ 当前 disabled
#    lkl-close.timer  工作日 15:05 收盘对账（consume 两阶段消费）  ⛔ 当前 disabled
#    （12:01/15:05 两个窗口 2026-09-18 实测为 disabled = 手动关闭暂时不用，
#     日内对账因此延迟到 17:20；恢复见 §12 更正说明）
#    单元文件存档 scripts/systemd/，安装：
#    sudo cp scripts/systemd/{lkl-daily,lkl-trade,lkl-close}.{service,timer} /etc/systemd/system/
#    sudo systemctl daemon-reload && sudo systemctl enable --now lkl-daily.timer lkl-trade.timer lkl-close.timer
#    lkl-health.timer  工作日 09:30 链路健康推送（断档检测 + webhook）
#    lkl-strategy.timer 工作日 17:50 DSA 策略观察（M9；总开关 LKL_STRATEGY_ENABLED 默认关，
#    关时秒退零成本；单元文件同法安装，入口 scripts/strategy.sh）
#    日志：journalctl -u lkl-daily.service（其余同）

# 6. 评估与学习：全期评估 / 周度校准提案（只提案不改参）/ 信号结果回填
lkl evaluate 2024-01-01 <end>
lkl evaluate 2024-01-01 <end> --code 600371   # V9：单票筛选
lkl evaluate 2024-01-01 <end> --theme 算力     # V9：题材关键词筛选
lkl calibrate
lkl outcome          # daily.sh 已含此步

# 7. V9/V10 可选增强（默认全关）：
#    LLM 异步点评（V10 已接通实测）：
#      export LKL_LLM_ENABLED=1 后 daily.sh 自动在报告落盘后后台追加点评；
#      key 读 .secrets/llmkey（llm.py 文件兜底机制），手动单次：
#      lkl llm-comment [date]（幂等，已有点评不重复调用）
#    webhook 推送（V9）：export LKL_WEBHOOK_URL=<企微/钉钉机器人地址>
```

## T13 交易桥（JSON 文件交换）

分析端 ↔ 交易端（LKL-Trade，受限 SFTP 用户 `lkl-trade`）通过 `trade/` 目录交换：

- **decisions**（DB→交易端）：每日盘后导出 BUY 信号 + SELL 建议，
  `for_date` = 下一交易日，user1/ user2/ 各一份相同内容；
- **results**（交易端→DB）：成交回报 `{kind}_YYYYMMDD_HHMMSS.json`（Asia/Shanghai），
  契约 v2 五字段 `ok/price/shares/order_id/reason`；DB 升序全量消费，
  一份一归档至 `user{N}/consumed/<for_date>/`；
- **holdings**（交易端→DB）：全量持仓快照（权威对账 position 表）；
  空数组 = 真清仓（清空 OPEN）；同日多份按序消费以最后一份为准；
- 命令：`lkl trade export <date>` / **`lkl trade consume`（一致性消费主命令）**
  / `lkl trade import` / `lkl trade sync`；
  consume = 两阶段（全部 results 升序 → 全部 holdings 升序）+ 旧快照
  仲裁（快照早于本批回报则跳过防回滚）+ 对账 diff 告警（WARN）；
  毒丸整份拒收留原地告警、退出码 1；停机总闸 `lkl trade-halt on|off|status`
  （HALTED 时写入口全拒绝）；
  轮询 `scripts/trade_poll.sh <分钟>`（每分钟 consume，30 分钟全败 exit 1）。
  每日三消费窗口设计：12:01 午间 + 15:05 收盘对账（lkl-close.timer）+ 17:20 盘后；
  ⚠️ 2026-09-18 实测：**12:01/15:05 两个 timer 为 disabled**（手动关闭暂时不用），
  实际只有 17:20 盘后窗口在跑。
  多用户语义见 [ADR-0002](docs/adr/0002-multi-user-trade-bridge.md)。

## Dashboard

仪表盘（本地 nginx 反代，全中文化界面）：

- **URL**: `https://140.83.62.161/stock/`（本机 `NDORACLE` 的 nginx
  `location /stock/ → proxy_pass http://127.0.0.1:8098/`，server 级 basic auth）
- **凭证**: 见运维记录（`/etc/nginx/.htpasswd`，本 README **不再明文存放口令**——
  2026-09-18 巡检发现历史版本曾把 `user/pass` 明文写在此处并随提交推到远端，
  该凭据已失效，但明文入库本身是缺陷，故删除并改为指引）
- **功能**: 日期选择切换日报数据（指标卡 + 梯队/题材/淘汰/晋级表格 + 龙空龙环境）；下载按钮**始终指向最新日报 md**
- **全中文化**: 龙空龙评级（适宜/中性/不适宜）、买点窗口（无/标准/增强）、持仓状态（建议/持仓中）、明日参考等全部显示中文
- **API 端点**:
  - `GET /api/dates` → 可用日期列表 JSON
  - `GET /api/latest` → 最新日期（json+md 双存在校验）
  - `GET /api/report?date=YYYY-MM-DD` → 指定日期日报 JSON（ISO 日期严格校验）
  - `GET /api/trade-status` → 午间流程执行状态（T13）
  - `GET /api/trade-files` → 交易交换文件清单（user{N}/ 前缀）
  - `GET /api/trade-download?name=...` → 交换文件内容（限 trade/ 树内）
  - `GET /api/trade-board?date=` → 交易七态面板（建议/已报/成交/撤单/拒绝/持仓/对账）
  - `GET /api/signal-detail?date=` → 信号证据卡（五条件 PASS/FAIL/UNKNOWN+版本戳）
  - `GET /api/evidence?date=&code=` → 原始行情钻取（daily_bar/derived_bar）
  - `GET /api/alerts` → 未确认告警队列（A2-7）
- `GET /strategy` → 策略观察台页面（M9：LLM 观察性推荐，非交易信号，与主链路隔离）
- `GET /api/strategy?date=` → 策略快照 JSON（只读 reports/strategy_<date>.json，展示层零 DB）
- **本地服务**: `systemd` → `lkl-dash.service`（`sudo systemctl restart lkl-dash.service` 重启；`journalctl -u lkl-dash.service` 看日志）
- **代码**: `dashboard/`（app.py / loader.py / index.html），纯标准库零依赖
- **归属澄清**（2026-09-17 核实）: 本目录是**龙空龙复盘 UI**，线上 `127.0.0.1:8098`、nginx 路由 **`/stock/`**。
  它与 `https://140.83.62.161/dashboard/`（8099「统一门户」，仓库 `nxz1026/dashboard`）、
  `/dashboard/jc/`（8077 体彩竞彩看板，`league-v2`）是**三套互不相同的应用**，同名勿混。

## 测试

```bash
# 依赖（dev extras 含 pytest + ruff，两者缺一 ci_local.sh 即非零退出）
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/python -m pytest tests/ -q          # 274 离线用例（连库自动 skip）
LKL_WITH_DB=1 .venv/bin/python -m pytest tests/ -q   # +14 连库用例（需 ~/.dbconfig）
scripts/ci_local.sh                            # CI 五红线：测试/E1≤50行/架构禁LLM/ruff(lkl/+tests/)/收集期零连接
scripts/ci_local.sh --with-db                  # 连库版 CI
```

**测试约定（2026-09-18 巡检加固）**：

- **离线套件禁止连生产库**：`conftest.py::_no_prod_db`（autouse）在未设
  `LKL_WITH_DB=1` 时把 `db.get_conn`/`db.transaction` 换成抛错哨兵。
  背景：`test_strategy_runner` 的 parse-fail 用例曾每跑一次就往**生产**
  `llm_call_log` 插一行审计噪声（实测 max_id 234→235→236），因审计写入自带
  try/except 而永不暴露；
- **收集期零连接**：连库文件就绪探测一律放**用例 setup 期的 autouse fixture**，
  禁止模块级 `pytestmark = skipif(not _ready())`（import 期连库，
  conftest 的 skip 发生在 import 之后拦不住）。实测无库机器收集 288 用例
  由 90.88s 降到 <1s，`ci_local.sh` 第 5 步会硬拦回归。

**服务端 CI**（2026-09-18 补）：`.github/workflows/ci.yml`——push/PR 到 main 时
在 GitHub runner 上跑同一个 `scripts/ci_local.sh`（连库用例默认 skip，runner 无
`~/.dbconfig`）。此前仓库内**没有任何服务端校验**（无 `.github/`），漏跑本地
脚本无人知。

## 文档

- [需求文档](docs/REQUIREMENTS.md) — 术语与量化定义
- [策略规格书](docs/PLAN.md) — 决策记录 + 状态机规格（v2.3）
- [施工图](docs/IMPL_PLAN.md) — 任务分解 T0~T11
- [数据源实测](docs/DATA_SOURCES.md) — 源矩阵与踩坑
- [裁决记录](docs/ANALYSIS_C.md) — 需求冲突与模糊点
- [架构决策](docs/adr/) — ADR-0001 出口口径拍板 / ADR-0002 交易桥多用户
- [待办与挂起](docs/BACKLOG.md) — 不做项（题材图谱/参数实验台/字节级重放）+ 运维挂起
- 复盘报告：**运行时产物，不入库**（`.gitignore` 忽略 `reports/`），
  本机路径 `reports/<date>.md|json`（此前 README 链到 `reports/` 在纯净检出是死链）

## 已知边界

- 北交所无日线源，计数天然缺北交所；
- 除权日 pre_close 漂移约 0.1% 行（待 XDXR 修复）；
- **ST 与流通股本为当前口径（非 point-in-time，审计 A2，奎爷拍板标注局限）**：ST 戴帽/摘帽与增发解禁会使历史样本漂移、换手率失真——回测结论只用于相对比较，不外推绝对收益；
- 数据深度 2024-01 至今（F1），不足一个完整长周期；
- STANDARD 窗口样本量仍小（7 例），持续积累中；
- 所有"收益"数字为**假设次日开盘价成交的演示口径**，非实盘。

## 免责声明

本项目为研究/复盘工具，只输出信号与建议，不构成任何投资建议。股市有风险，入市自负盈亏。
