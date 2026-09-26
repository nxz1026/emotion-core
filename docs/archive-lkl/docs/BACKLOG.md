# BACKLOG（待办）

> 审计代码问题已全部修完（P0 5/5、P1 14/14、P2 12/13、P3 7/7、P4 4/4，提交 ec94033→422f693）。
> 本文件只收**不修/缓修**项：产品功能增强、审计遗留不修项、挂起项。做完一项划掉一项（`✅` + 提交号）。

## A. 产品功能 backlog（源自 2026-09-03 外部审计 §5）

> 判定基线：`422f693` 七轮修复后已核对。语义层（UNKNOWN 三态、契约 v2、对账诚实性、脱敏、测试隔离）已完成，缺口集中在**呈现层**。
> **2026-09-04 拍板**：A2-1/A2-2/A2-3 **不做**；A1 全部 + A2-4…A2-10 **全部开工并交付**。

### 不做（拍板 2026-09-04，重开条件附后）
- **A2-1 题材关系图谱**（产品 P1-4）：数据全有（③题材段映射+confidence、事实/LLM 分离），缺图。**不做**：可视化投入大，MD 报告信息已够用。重开条件：Dashboard 需要交互式题材探索时再议。
- **A2-2 参数实验台**（产品 P2-1）：`lkl evaluate` 已支持区间/单票/单组，缺参数网格对比。**不做**：依赖策略参数结构化改造，收益边际递减。重开条件：参数需要系统性寻优时再议。
- **A2-3 字节级重放回归**（审计 §7-4）：`replay(as_of)` 能力已有，缺固定快照对比测试。**不做**：数据快照管理本身是新工程。重开条件：需要第三方可复现性认证时再议。

### A1/A2. 已拍板开工（2026-09-04 全部交付 ✅，详见 README §11 / REQUIREMENTS §5）
- ✅ **A1-1** 数据可信度总判（速览段 OK/PARTIAL/UNKNOWN 三态行，缺数不判绿）+ `/api/trade-board`
- ✅ **A1-2** `lkl doctor` 一键自检（DB/日历/时区/trade/报告/可选六项清单）
- ✅ **A1-3** signal 表 `strategy_version` 版本戳（=git describe）
- ✅ **A1-4** 契约测试补撤单/拒绝/残缺/错误账户四样例（+trade_event 留痕）
- ✅ **A2-4** `/api/signal-detail` 证据卡（五条件 PASS/FAIL/UNKNOWN+版本戳+竞争组/幸存者）
- ✅ **A2-5** `lkl compare <date>` 一致性对比+规则漂移→alert WARN 报警
- ✅ **A2-6** `lkl pipeline` 打点（RUNNING/OK/FAILED+耗时）+失败重跑指引
- ✅ **A2-7** alert 表+`lkl alerts`/`alert-ack`+`/api/alerts`（pipeline 失败/PARTIAL/漂移自动入队）
- ✅ **A2-8** `/api/evidence?date=&code=` 原始行情钻取（daily_bar/derived_bar）
- ✅ **A2-9** data_revision 留痕+`lkl revisions`（upsert 覆盖历史自动记录）
- ✅ **A2-10** `lkl trade-halt on|off|status` 停机总闸（文件开关）+export/import/sync 三入口守卫

## B. 审计明确不修项（需拍板才动）
- **B1 DB CHECK/外键领域约束**（审计 P0-5）：正股 2 亿小盘+单机部署，约束收益小。**需拍板口径**：若要加，先定义每列合法枚举（status/reason/账户名）再动 schema。
- **B2 脚本硬编码路径**（审计 P3）：scripts/*.sh 内项目绝对路径——单机部署有意约束，环境变量化收益低。
- **B3 XDXR 除权修正**（DATA_SOURCES.md 遗留）：除权日涨停判定漂移 ~0.1% 行，streak 断点。需引入 XDXR 事件表后才可修。

## C. 运维挂起项
- **C1 午间 12:01 timer**：已禁用（用户想清再开）。重开命令：`systemctl --user enable --now lkl-noon.timer`（名称以 `systemctl --user list-timers | grep lkl` 为准）。
- **C2 trade/ 根目录约 30 份交易端模拟测试残留 results**：状态枚举无 ok 字段、写错位置不在 user1/。用户明确「不管」。
- **C3 交易日历依赖**：`next_trading_day` 当前 daily_bar 真日历优先、否则跳周末——**春节等长假不适用**，届时需手工指定 for_date 或提前引入正式交易日历（与本文件 A2 无冲突，节前提醒）。
