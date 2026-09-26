# 龙空龙量化策略需求文档

- 版本：v1.2（2026-09-03：+T13 交易桥需求；数据源实测结论取代选型）
- 目标读者：有编程能力但不熟悉超短交易的程序员
- 策略核心：**情绪周期追踪 + 连板梯队竞争淘汰 + 唯一龙头确认买入（非一字板）**

## 0. 策略直觉（Leader Election 类比）

把市场想象成分布式系统选主（Leader Election）：

- 几百只股票同时启动上涨 = 多个节点宣称自己想做 Master
- 连板 = 心跳信号，谁能连续涨停，谁还在候选圈里
- 一字板 = 网络垄断：通道被一人占满，其他人无法参与投票（成交）。**这种信号我们不认，必须有换手（投票交互过程）**
- 每天淘汰一批：3板剩10个 → 4板剩5个 → 5板剩2个 → 6板只剩一个活口。
  最后在充分换手中干掉所有同身位对手、第一个封死涨停的唯一最高板 = **龙头**
- 龙头不是提前选出来的，是市场用真金白银"卷"出来的。谁活到最后，谁就是龙头
- **龙空龙**：只在龙头胜出的那一刻打板买它；没有龙头就空仓；然后等下一个龙头。
  永远只做最强的那个节点，其余时间什么都不做

## 1. 术语速查表

| 术语 | 通俗解释 | 量化定义 |
|------|----------|----------|
| 涨停 | 价格涨到当日上限 | 收盘价 == round(昨日收盘价 * 1.1, 2)（A股10%情况） |
| 连板 | 连续涨停的天数 | 从第一个涨停开始，按日期连续计数，若某日未涨停则断板 |
| 一字板 | 开盘即涨停，日内无人能买到 | 开盘价 >= 涨停价 且 最低价 == 涨停价（全天无开板） |
| T字板 | 开盘涨停，盘中打开过缺口又回封 | 开盘价 >= 涨停价 且 最低价 < 涨停价 且 最终涨停 |
| 换手板 | 非一字的涨停，盘中给过买入机会 | 最终涨停且 最低价 < 涨停价（包含T字、实体涨停） |
| 最高板 / 空间板 | 全市场当前连续涨停天数最多的股票 | max(连板天数) |
| 同身位竞争 | 与最高板连板天数相同的其它候选股 | 所有连板天数等于当前最高连板数的股票 |
| 梯队 | 按连板天数分组的股票集合 | 1板一组，2板一组，3板一组…… |
| 情绪周期 | 市场整体的赚钱/亏钱效应阶段 | 通过涨停家数、炸板率、最高连板等指标划分 |
| 龙空龙 | 龙头-空仓-龙头循环 | 只在龙头唯一确立时买入，断板或退潮时空仓 |

## 2. 数据需求

回测/实盘环境需准备以下最低限度的日线数据，若能提供日内1分钟线更佳（用于打板点判断）。

### 2.1 必备字段（每只股票日频）

```python
# 日行情
- date: Date
- code: str           # 股票代码
- open: float
- high: float
- low: float
- close: float
- pre_close: float    # 前收盘价
- volume: float       # 成交量
- is_st: bool         # 是否ST（需排除）
- is_new: bool        # 是否次新未开板（涨停上市需排除）
```

### 2.2 衍生计算字段（需自算）

```python
limit_up_price    = round(pre_close * 1.1, 2)              # 涨停价（简单按10%四舍五入）
is_limit_up       = (close == limit_up_price)               # 是否涨停收盘
is_one_word       = (open >= limit_up_price) and (low == limit_up_price) and is_limit_up  # 全天一字板
is_exchange_board = is_limit_up and (low < limit_up_price)  # 换手板（含T字回封）
continuous_limit_days  # 连板天数：从首板开始累加，断板则归零
```

## 3. 数据接口选型结论（2026-09 实测定稿，踩坑详见 docs/DATA_SOURCES.md）

- 日线历史回填：**pytdx**（通达信协议，0.87s/代码，分页拉全历史）；
- 当日全市场快照：**东财 push2delay clist**（收盘后终态，自建单页封装，
  akshare 分页接口在海外 IP 被 reset）；
- 涨停/炸板/跌停池：**东财三池**（仅存近 30 日，用于对账；历史靠自算
  derived_bar 全量重建）；
- 情绪五指标：**全自算**（derived_bar，剔 ST 剔次新口径冻结）。

## 4. T13 交易桥需求（2026-09-03 多用户版）

分析端与交易端（LKL-Trade）通过 JSON 文件交换，不直连：

- 交换目录 `trade/`，多用户各一子目录 `trade/user1/`、`trade/user2/`；
- 三类文件：decisions（DB→交易端，两用户相同内容）、
  results/holdings（交易端→DB，各自目录）；文件名
  `{kind}_YYYYMMDD_HHMMSS.json`，时间戳一律 **Asia/Shanghai**；
- **for_date = 下一交易日**（next_trading_day：daily_bar 真日历优先，
  至少跳周末；禁止自然日+1）；
- **decisions 契约 v2（2026-09-04，schema:2）**：actions[] 每行
  `action / code / reason / window / exec / volume`；
  `exec` 为执行语义，唯一合法值 `OPEN_POS`（BUY）| `CLOSE_ALL`（SELL，
  清仓全部持仓）；**window 仅为买入口径标签**（STANDARD/ENHANCED/NONE，
  描述当日市场可否买），对 SELL 无执行含义——执行器不得以
  window=NONE 为由拒 SELL 单（历史事故：09-04 601988 退潮清仓被误判
  EXCLUDED）；volume 为建议股数（BUY=0 交易端自定，SELL=当前持仓）；
- 回执契约 v2（results.trades[] 每行）：
  `action / code / ok(已成交) / price(成交均价) / shares(成交量) /
  order_id / reason`；SELL ok=true 时 shares 必填实际成交量
  （DB 按股数扣减，部分成交余股保 OPEN）；ok=false 一律留痕不落仓
  （CANCELLED/REJECTED，reason 含「拒」判 REJECTED）；
- holdings 为全量快照（权威对账 position 表）；**空数组 = 真清仓**
  （清空全部 OPEN 持仓）；
- 消费语义：升序全量消费、一份一归档（`user{N}/consumed/<for_date>/`），
  旧文件不被最新一份遮蔽；交易端唯一主动删除的是已执行的 decisions；
- **一致性消费（2026-09-04，`lkl trade consume`）**：单命令两阶段——
  先全部 results 升序（毒丸整份拒收跳过不中断，事后 ERROR 告警），
  再全部 holdings 升序；holdings **仲裁**：快照文件时间戳早于本批最早
  回报 → 本轮跳过不消费（防旧快照回滚已推进的账面）；消费快照前后
  账面 diff → WARN 告警（过程账与实盘偏差可见化）；毒丸存在退出码 1；
  trade_poll / 15:05 收盘对账均走此命令；
- **每日时点**：12:01 午间（lkl-trade.timer）+ **15:05 收盘对账**
  （lkl-close.timer，新增）+ 17:20 盘后（lkl-daily.timer），
  三窗口消费回报与对账；
- 安全边界：交易端为受限 SFTP 用户（`lkl-trade`，chroot 至 trade/ 树）；
  分析端导入前不强信交易端数据——契约不匹配拒绝整份文件，幂等重试。

## 5. 产品功能需求（2026-09-04 落地版，裁决见 docs/BACKLOG.md）

- **可用于决策总判**：⓪速览首行三态 `OK/PARTIAL/UNKNOWN`（聚合⑩质检
  diff+warns）；**缺数绝不显示绿色结论**；JSON 快照同源携带；
- **交易七态安全台**：`/api/trade-board`（建议/已报/成交/撤单/拒绝/OPEN
  持仓/最近对账）；SELL 撤单/拒绝必须留痕（trade_event），禁止静默；
- **契约硬校验**：ok=true 必带 price+shares，缺失抛 DataError 拒收整条
  （不落半截仓）；未注册用户目录的回报不消费不归档；
- **停机总闸**：`lkl trade-halt on|off|status`（文件开关，不依赖 DB）；
  HALTED 时 export/import/sync 三写入口全拒绝，人工确认后恢复；
- **策略版本戳**：signal 表 `strategy_version`（=git describe），信号
  重放可对版本（可复现档案最小版）；
- **回测—实盘一致性**：`lkl compare <date>`——signal 表实盘记录 vs
  replay(as_of=当日) 并排；代码集差=规则漂移 → alert WARN 入队；
- **告警闭环**：alert 表 + `lkl alerts`（未确认队列，ERROR 在场 exit 1）
  + `lkl alert-ack <id>...`；pipeline 失败/报告 PARTIAL/漂移自动入队；
- **流水线打点**：主链命令 RUNNING→OK/FAILED+耗时入 pipeline_state；
  `lkl pipeline` 总览+失败步骤重跑命令；
- **证据卡/钻取**：`/api/signal-detail`（五条件 PASS/FAIL/UNKNOWN+原始
  依据+版本戳+竞争组/幸存者）、`/api/evidence`（原始行情行）、`/api/alerts`；
- **数据修订标识**：upsert 覆盖历史行自动留痕 data_revision；
  `lkl revisions` 查受影响日；重生成走 evaluate/compare 新文件不覆盖；
- **不做项（拍板）**：题材关系图谱、参数实验台、字节级重放回归——
  理由与重开条件见 docs/BACKLOG.md。
