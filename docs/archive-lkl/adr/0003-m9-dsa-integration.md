# ADR-0003: 集 daily_stock_analysis 所长（M9）

- 状态：已采纳
- 日期：2026-09-12
- 决策者：队长（用户拍板范围：LLM 抽象 + 数据备源 + 策略移植 + dashboard 并入；否决：通知路由、GitHub Actions）

## 背景

daily_stock_analysis（DSA，MIT，fork nxz1026/daily_stock_analysis）是成熟的 AI 批量研报系统；
Stock_DB（龙空龙）是确定性纪律系统。功能对比后取三者之长移植，其余保持隔离。

## 决策

### 1. LLM 多后端回退链（lkl/services/llm_backend.py）
- `LKL_LLM_CHAIN` 逗号分隔 profile 链；调用 profile 自动置链首；未知名 warning 跳过。
- 密钥优先级：`LKL_LLM_API_KEY_<PROFILE>` > 共享 `LKL_LLM_API_KEY` > 密钥文件。
- 可回退：429/401/403/5xx/超时/连接错误（后端重试尽后换）；400/422 直抛不回退。
- 无密钥后端跳过并记 FAILOVER 审计行；全链尽抛 LLMChainExhausted（消息脱敏）。
- 单后端链 = 旧行为逐字节兼容（golden 锁）；chat 持实例锁（get_client 缓存共享实例）。
- 审计复用 llm_call_log（status="FAILOVER:<err>"[:200]），零 schema 变更。

### 2. 日线备源（lkl/providers/）
- DailyBarProvider 协议；eastmoney 薄封装主源（import 复用），pytdx/sina 备源。
- **单位口径锚定 daily_bar.volume=手**：TDX vol 原生手（不换算）；新浪股→/100；
  turnover_rate 新浪小数→×100；amount 三源皆元。fixture 用绝对数值锚点（禁循环论证）。
- `LKL_INGEST_FALLBACK_CHAIN` 默认空=完全关闭，主路径逐字节不变；备源只补主源缺口不覆盖；
  口径守卫（列全/价正/high>=low/日期升序）不合格即跳过。
- doctor 双源抽样体检（默认 5 票，>0.5% 偏差 warning，不阻断）。

### 3. DSA 策略子系统（lkl/strategy/）——观察性，非交易信号
- 15 个 YAML 全量移植（文件头 MIT 归属注释）；required_tools 删除、
  工具引用段改写为 lkl 数据上下文说明；无 yaml 依赖，loader 内置子集解析器。
- 新闻/舆情类指令整段移除（S3c：lkl 无新闻源，保留会诱导 LLM 编造新闻证据）。
- 单独入库 `strategy_signal`（UNIQUE(trade_date,code,strategy,prompt_hash)），
  **永不进 signal/position/trade export 主链路**；删 lkl/strategy/ 即整体回滚。
- 开关全默认关：STRATEGY_ENABLED / ACTIVE / PROFILE(fast) / MAX_LLM(50/日) /
  MAX_UNIVERSE(80) / HOT_N(50) / MAX_TOKENS(8192)。
- 输出契约严格 JSON（action∈BUY|WATCH|PASS + score + confidence + 中文reason + evidence），
  解析失败跳过不落脏；prompt_hash 幂等去重不重烧钱；
  单点 LLM 失败记 skipped 继续整轮（S3b）；整值浮点 score 容忍。
- **token 预算教训（S3d 冒烟确诊）**：deepseek-v4-flash 为推理模型，策略提示词下
  reasoning_tokens≈5200，profile 的 2048 预算被推理吃光 → content 空串、5 连败；
  独立 STRATEGY_MAX_TOKENS=8192 后 5/5 入库。
- 双写：PG 表（正式）+ reports/strategy_<date>.json（dashboard 只读文件层）。

### 4. Dashboard 并入（不启新端口）
- 复用 8098 进程与 nginx /dash/ 现成 basic auth；handler 路由表 +4 行。
- 新页面 /dash/strategy + 只读 API /api/strategy；展示层禁 DB（只读 reports 文件）。
- **前缀教训（S4b）**：/dash/ 反代剥前缀，页面内所有链接/fetch 必须相对路径；
  单测直调 handler 测不出，须以"无 href=\"/"字符串断言防回归。

## 否决项
- 合并 DSA 仓库：184M/628 文件摧毁 2.3M/86 文件的可审计性——只移植模块思想。
- DSA 的 LLM 生成信号方式进入主链路：无证据链不可证伪，与纪律系统哲学冲突。
- 通知路由 / GitHub Actions：用户自有服务器，本期不做。

## 后果
- 正面：LLM 限流自动换端点；东财单故障有日线备源；15 策略每日产出可观察、可积累
  胜率样本（跑 2-4 周后凭 strategy_signal 统计再议转正）。
- 负面/风险：LLM 成本（MAX_LLM 上限+幂等缓存兜底）；备源口径漂移（守卫+doctor 双源体检）；
  策略文本质量参差（观察层隔离，不触交易）。
