# C 逻辑深度分析（2026-08-29）

## 一、与已锁定决策的冲突（3 处，需拍板）

### 冲突 1：龙头最低板数
- 已锁定 D2：≥3 板
- C §4.2：`max_days >= 4（至少4板以上才算龙头相，过滤低级杂毛）`
- 影响：3 板唯一最高板是否触发信号。回测 2024 至今，3板确认 vs 4板确认的信号数量差约 2~3 倍
- **建议：以 C 为准改 ≥4**（你的原文类比"3板还有10个"说明 3 板层竞争未充分，淘汰未结束）

### 冲突 2：回测成交价口径
- C §5.3 写"成交价记为当日涨停价（或次日开盘价）"
- C §8 坑点自己警告："切勿用到当日收盘才能确认的唯一最高板却在当日开盘买入"
- 当日涨停价成交 = 用收盘确认的信息回到当天成交，**未来函数，回测必虚高**
- **建议：锁定唯一口径 = 次日开盘价成交**（与 D3 一致），已确认的 D3-exec"高开≤5%才买、一字顺延一天"继续生效（C 未推翻）

### 冲突 3：淘汰确认的严格/宽松版
- C §5.3 要求："昨日最高板组 **≥2只**，今日只剩它一只"（严格：必须发生过同身位厮杀）
- C §4.2 后半段又接受："昨日3板组唯一活口今日晋级4板且今日无同身位，也满足唯一性"（宽松：允许卫冕晋级）
- 两版信号数量差异大：宽松版会出"一路一字/秒板卫冕"的弱投票信号
- **建议：做成 config 参数 `require_yesterday_competition`，回测两版对比后定**

## 二、模糊点量化定义（5 处，给出建议口径）

### 模糊 1：情绪指标统计范围 vs 交易范围
C §3.1 说情绪指标"全市场"，D1 说只做主板。这不矛盾——水温看全池子，捞鱼只捞主板的。
**建议双口径**：
- 涨停家数 / 炸板率 / 跌停家数 / 昨日涨停表现 → **全市场**（剔除ST/次新，含创业板20cm，情绪本就跨板块）
- 最高连板高度 max_limit_days / 梯队 / 龙头判定 → **主板**（空间板历来在主板）

### 模糊 2：炸板率分母
**建议**：`bomb_rate = 炸板数 / (收盘封住数 + 炸板数)`，其中炸板 = `high >= limit_up_price 且 close < limit_up_price`

### 模糊 3：状态机模糊词量化
| 原文 | 量化定义（全部进 config） |
|------|--------------------------|
| zt_performance 从负转正 | 昨日 < 0 且 今日 > 0 |
| 连续2日 > 1.5% | 今日、昨日均 > 1.5 |
| limit_up_count 逐日增加 | 今日 > 昨日 > 前日 |
| 最高板突然放量巨震 | 最高板股当日振幅 > 12% |
| 跌停家数骤升 | 今日跌停 > 昨日 × 2 且 > 15 家 |
| 断板次日无法反包 | 断板后次日 close 未涨停 |
**状态机规则**：判定优先级 退潮 > 高潮 > 发酵 > 冰点；无规则命中则延续昨日状态（状态需入库，见 market_stat 表）

### 模糊 4：6.1"盘中开板收盘未回封→收盘价卖出"
盘后复盘拿到收盘才知道没回封，收盘价已不可得。日线口径与"断板卖"是同一条件。
**建议统一**：持仓股当日 `close < limit_up_price` → 次日开盘卖出。分钟线版留 P4 复盘增强（事后用分钟数据还原"该卖在盘中哪个位置"做复盘对照，不做实盘）

### 模糊 5：次新未开板剔除
主板注册制新股上市前 5 日无涨跌幅限制，1.1 公式不适用。
**建议**：`上市日 < 60 自然日` 或 `处于上市首日至首个非涨停日之间` 任一命中即剔除；上市日期从 Baostock `query_stock_basic` 取（AKShare 涨停池无此字段）

## 三、数据库 Schema 设计（库已建：longkonglong）

```
stock_basic     股票基础：code, name, list_date, market, is_st          —— 维表
daily_bar       日线：code, date, open, high, low, close, pre_close,
                volume, amount, turnover_rate                          —— 原始层
limit_pool_em   东财涨停池快照（16列原样入库）                          —— 对账基准
market_stat     date, limit_up_count, bomb_rate, zt_performance,
                max_limit_days, limit_down_count, phase, can_trade     —— 模块一输出
ladder_day      date, code, cont_days, is_exchange, is_top,
                is_sole_top, yesterday_same_rank_count                 —— 模块二输出
signal          confirm_date, code, action(BUY/SELL), reason,
                exec_date, exec_price, status(PENDING/FILLED/EXPIRED)  —— 模块三/六输出
paper_position  模拟持仓（复盘建议跟踪）                                 —— 见问题Q4
bt_trade / bt_equity  回测成交与净值                                    —— P2
```

数据量估算：全市场 ~5400 股 × 650 交易日 ≈ 350 万行日线，RDS 无压力。
连接：psycopg3，参数集中 config.py，sslmode=verify-full + global-bundle.pem。

## 四、模块化架构（按 D 规范重写）

```
lkl/
├── config.py        # 全部阈值/常量/DB连接参数（消灭 magic number）
├── utils/
│   ├── db.py        # 连接池、upsert 助手
│   ├── price.py     # 涨停价/跌停价舍入（唯一实现，全项目复用）
│   └── dates.py     # 交易日历、日期序列
├── models/
│   ├── schema.py    # 建表 DDL
│   └── types.py     # dataclass：Bar/LadderRow/Signal/EmotionState
├── services/        # 每文件一职责，函数≤30行，只经函数签名通信
│   ├── ingest.py    # AKShare→DB 拉取入库（含对账）
│   ├── derive.py    # 衍生字段：连板数/一字/换手/触板
│   ├── emotion.py   # 模块一：指标计算 + 状态机 → can_trade
│   ├── ladder.py    # 模块二：梯队构建 + 唯一最高板判定
│   ├── entry.py     # 模块三：买入信号（AND条件逐项断言）
│   ├── exit.py      # 卖出：断板卖/退潮清仓
│   ├── backtest.py  # 回测编排：逐日驱动上述模块
│   └── review.py    # 盘后复盘报告生成（B 的核心交付）
├── main.py          # CLI 组装：lkl fetch/ladder/emotion/backtest/review，零业务逻辑
└── tests/           # 黄金用例：深中华A 7板、海鸥住工一字等实测样例
```

依赖方向（禁止反向 import）：`main → services → models/utils → config`

## 五、产品形态修订（响应 B）

- 砍掉：盘中轮询、分钟线实时触发、5.2 分钟线伪代码的实盘路径
- 保留：5.3 日频简化版为唯一执行主线
- 新增核心交付：**每日盘后 `lkl review` 复盘报告** = 情绪阶段 + can_trade + 梯队全景图 + 唯一最高板判定过程 + 明日操作建议（买入候选+放弃线 / 卖出指令 / 空仓）
- 回测定位从"策略验证"调整为"信号质量评估"：用历史复盘检验建议胜率
