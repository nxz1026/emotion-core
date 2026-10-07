# emotion-core

A 股市场情绪周期 · 事实底座 · 决策系统

---

## 这是什么

`emotion-core` 是 `longkonglong`（下称 **lkl**）与 `a_share_emotion_leader`（下称 **asel**）
两个仓库的**合并目标仓库**。合并完成后，lkl 与 asel **都会停止并删除**，只保留本仓库。

- **lkl**：龙空龙策略盘后复盘器 —— 决策系统（情绪周期状态机 → 龙头淘汰 → 买入信号 → 交易桥）
- **asel**：A 股情绪龙头事实底座 —— 数据/制度/质量/只读展示（零决策能力）

### 两个仓库是两种不同的知识

| | lkl | asel |
|---|---|---|
| 知识类型 | **交易知识** | **工程知识** |
| 属于谁 | **奎爷（朋友，已故）** | **接手者（coder）** |
| 策略口径条数 | **22 处拍板**（lkl 侧 `lkl/docs/PLAN.md`，**不在本仓**） | **0 条** |

⇒ **合并 = asel 的骨架 + lkl 的大脑。**

---

## 决策规则（本项目宪法）

> ### 策略语义 → 一律照搬 lkl，逐字不改
> "什么算涨停 / 什么算龙头 / 什么算退潮 / 什么时候允许买 / 谁该被剔除"
>
> ### 工程实现 → 一律用 asel 的范式
> "怎么存 / 怎么分层 / 怎么调度 / 怎么展示 / 单位怎么统一 / 怎么保证质量"

**判据**：问自己"这一条，是**市场**的规则，还是**我的代码**的规则？"
- 市场的规则 → lkl
- 我的代码的规则 → asel

这条规则能解决全部 6 处口径冲突（C1~C6），**不需要懂股票**。

⚠️ **任何"我觉得这里应该改一下"的冲动，先问：这是奎爷的裁决，还是我的偏好？**
如果是前者 —— **不要改**，只加注释标注"存疑，待验证"。

---

## 当前阶段

**阶段 2b（Rust 移植）✅ · 阶段 3（PyO3 接线）✅ · 阶段 4~7（数据/编排/展示/LLM）✅ 已落地 · 阶段 8（上线）✅ 已完成**

Python 参考实现（阶段 2a）全部完成：11 个算法模块 + 1822 tests 全绿（2026-09-26 时点，历史口径）；当前全量实测 **生产 1927 passed + 2 skipped / CI 1483 passed + 2 skipped**（均 2026-10-07）。差额是依赖 `.so` 的 Rust 对账测试——`.so` 是 gitignore 的构建产物，CI 无 cargo 故整份跳过，详见 `core/__init__.py` 模块说明。命令见 `docs/15`「测试覆盖」。
Rust 移植（阶段 2b）按 `docs/07` 依赖拓扑逐模块推进，每个模块附 Python vs Rust 对账测试。

### Python ↔ Rust 模块对照

| Python（参考实现） | Rust（生产实现） | 对账测试 |
|---|---|---|
| `indicators.py` + `derive.py` | `indicators.rs`（`compute_derived`） | ✅ |
| `state.py` | `state.rs` | ✅ |
| `ladder.py` | `ladder.rs` | ✅ |
| `promotion.py` | `promotion.rs` | ✅ |
| `entry.py` | `entry.rs` | ✅ |
| `accelerate.py` | `accelerate.rs` | ✅ 33 tests |
| `exit.py` | `exit.rs` | ✅ 25 tests |
| `theme.py` | `theme.rs` | ✅ 143 tests |
| `dragon_env.py` | `ecosystem.rs` | ✅ |
| `evaluate.py` | `evaluate.rs` | ✅ 52 tests |
| `outcome.py` | `outcome.rs` | ✅ 41 tests |

> Python 文件保留作为参考实现和对账基准，不删除。每个文件头部已标注对应的 Rust 模块路径。

| 层 | 状态 | 现状（2026-09-28 实测） |
|---|---|---|
| 阶段 2a Python 参考实现 | ✅ 完成 | 11 个算法模块 |
| 阶段 2b Rust 移植 | ✅ 完成 | 11/11 模块 |
| 阶段 3 PyO3 绑定 + 外壳 | ✅ 接线完成 | `core/__init__.py` **惰性**加载 .so（首次访问符号时才加载；产物缺失时抛 `RustCoreUnavailable` 并给出编译指引，不再在导入期崩） |
| 阶段 4 数据层 | ✅ 已落地 | 30 张表 DDL；`daily_bar` 337.8 万行 / 663 交易日 / 5221 只；`derived_bar` 337.8 万行；`market_stat` 663 行；独立交易日历表 `trade_calendar`（8797 个开市日，1990-12-19~2026-12-31） |
| 阶段 5 编排层 | ✅ 已落地 | `orchestration/daily.py` 13 步（sync/coverage/hot/derive/market/ladder/signal/promotion/theme/ecosystem/strategy/outcome/health）；仓内 systemd：6 个 `.service` + 5 个 `.timer` 共 **11 个单元**（`emotion-core-{daily,dash,pool,report,strategy,watchdog}.service` + `{daily,pool,report,strategy,watchdog}.timer`）。**2026-10-07 起与 `/etc/systemd/system` 逐文件一致**，`close` 单元已撤销，详见下方「systemd 仓/生产漂移」小节 |
| 阶段 6 展示层 | ✅ 已落地 | 5 个页面 + 4 个 JSON 接口（另有 `/api/*` 兜底状态），端口 8098，nginx 前缀 `/emotion` |
| 阶段 7 LLM 层 | ✅ 已落地 | 4 个 profile（default/fast/smart/agnes）+ 回退链 + `llm_call_log` 审计；**信号链默认关**（`LLM_PROFILE=None`） |
| 阶段 8 上线 | ✅ 已完成 | 2026-10-07 实测：`emotion-core-dash` 常驻（:8098 HTTP 200，basic auth 经 `/emotion/`）；五个 timer 全部 `enabled + active`，`emotion-core-daily.timer` 于 2026-10-06 17:20 北京成功执行、`pipeline_state` 13 步全 `OK`（`for_date=2026-09-30`，因 10-01~10-07 国庆休市，交易日守卫正常跳过，下一交易日 10-08 自动接上）；`signal`/`signal_outcome` 各 162 行全 `source='replay'`；`theme_group` 仍 0 行 |

> 数据现状决定了展示口径：只有 `daily_bar` / `derived_bar` / `market_stat` / `stock_basic`
> 四张表有数据，所以个股诊断等功能的统计**全部从这四张表现算**（PIT 安全），
> 等上述信号表落数后按同签名加增强分支即可。

### systemd：仓与 `/etc/systemd/system` 已于 2026-10-07 对齐

两边现在都是 **11 个单元**（6 `.service` + 5 `.timer`），**仓内
`src/emotion_core/orchestration/systemd/` 是唯一真相源**，与生产逐文件一致。
三个曾经咬过人的坑，改之前先知道：

1. **时区只有一份**。`Asia/Shanghai` 直接写在每个 `.timer` 的 `OnCalendar` 上；
   历史上的 `.timer.d/10-timezone.conf` 已从 `/etc` 与仓库一并删除。两份真相源并存时，
   改了本体忘了 drop-in（或反过来）的表现是**排期静默偏 8 小时、零报错**。
2. **`close` 单元已撤销**：它跑的是完整 13 步 daily，与 17:20 的 daily **全量重复**，
   而「收盘对账」所需的 `orchestration/close.py` 从未存在。
3. **五个 timer 全部 `enabled + active`**，按北京时间 17:20/17:45/17:50/18:00/18:05+08:50 触发。

> 逐单元实测表、nginx 路由、数据现状、备份路径见 **`docs/15` §部署状态**（唯一权威）。
> 契约由 `tests/architecture/test_systemd_units.py` 钉住：禁 `python -c`、`-m` 目标
> 必须真有入口、`OnCalendar` 必须自带时区、单元引用不得指向不存在的单元。

设计文档（阶段 0~1 全部定稿）：

| # | 层 | 状态 | 文档 |
|---|---|---|---|
| 0 | **接手决策规则** | ✅ 已定稿 | `docs/02-算法说明与接手决策规则.md` |
| 1 | **功能层** | ✅ **已定稿**（F1~F7） | `docs/01-功能层讨论.md` · `docs/03-新增需求-具体推荐与个股分析.md` |
| 2 | **数据源层** | ✅ **已裁决**（D1~D8） | `docs/04-数据源层讨论.md` |
| — | 数据健康 | ✅ 已普查（只读） | `docs/05-数据库普查与清理审计.md` |
| — | 📘 上线后工作 | ⭐ 记忆锚点 | `docs/06-上线后工作手册.md` |
| 3 | **算法层** | ✅ **已裁决**（含性能实测） | `docs/07-算法层搬运方案.md` |
| 4 | **编排层** | ✅ **已裁决**（含清理） | `docs/08-编排层合并方案.md` |
| 5 | **展示层** | ✅ **已定稿** | `docs/09-展示层设计.md` |
| 6 | 实施步骤 | ✅ 9 阶段 | `docs/10-实施步骤规划.md` |
| 7 | 代码架构 | ✅ 分层/模块/功能 | `docs/11-代码架构设计.md` |
| 8 | LLM 层 | ✅ 抽象层 + Agnes | `docs/12-LLM层设计.md` |
| 9 | 架构评审与逐层框架 | ✅ 已定稿 | `docs/13-架构评审与逐层代码框架设计.md`（⚠️ 无「§9 缺口清单」——该引用是错的，`docs/13` 只有一~七节；交接文档已归档 `docs/archive/`） |
| 10 | 自主决策日志 | ⭐ 逐条拍板留痕 | `docs/14-自主决策日志.md` |
| 11 | 实施追踪 | ⭐ 缺口修复与运维记录 | `docs/15-实施追踪.md` |
| 12 | Trade API 协议 | 活路由 `/api/trade/*` 的唯一协议真身 | `docs/16-Trade-API-Protocol.md` |

### 已接受的架构决策

| ADR | 标题 |
|---|---|
| [0001](docs/decisions/0001-盘后约束与买点口径.md) | 盘后约束、买点口径（**打板价 = T 日收盘价 × 1.1**）、CPT 接入边界 |

**运行态**：lkl 与 asel 的服务已全部**停止并 disable**（单元文件保留未删）。
- 源仓库系统级：`lkl-daily` / `lkl-health` / `lkl-strategy` / `lkl-close` / `lkl-trade` / `lkl-dash` / `asel-dash`
- 源仓库用户级：`asel-backfill-daily` / `asel-production-daily`
- 本仓库在跑：`emotion-core-dash.service`（端口 8098）、`emotion-core-daily.{timer,service}`、
  `emotion-core-strategy.{timer,service}`

> 定时器时区：本机是 `Etc/UTC`，`OnCalendar` 里的时区**必须写进表达式**
> （`OnCalendar=Mon-Fri 17:20 Asia/Shanghai`），`[Timer] Timezone=` 在本机 systemd 259 上不生效。

---

## 现在能做什么（web 端）

入口 `https://<主机>/emotion/`：nginx 反代到本机 8098，basic auth 由 nginx 处理（服务自身不鉴权）。

| 页面 | 路径 | 内容 |
|---|---|---|
| 直观层 | `/emotion/` | 快照日期（默认最新交易日）+ 市场结论（阶段 / 买入窗口 / 涨跌停家数 / 最高板 / 生态评级）+ 当日推荐 + 当日最高板梯队（均可点进个股诊断） |
| 逻辑层 | `/emotion/logic` | 市场参数与梯队明细 |
| 算法层 | `/emotion/algorithm` | 公式与阈值（涨停价公式由 `utils/price` 现算，展示层不写死公式） |
| 策略观察台 | `/emotion/strategy` | LLM 策略信号 + 历史日期切换 |
| **个股诊断** | `/emotion/stock` · `/stock/<code>` | **输入代码/名称 → 该票的情绪周期体检 + 直观建议 + AI 解读；结果页三张卡（A 现在 / B 过去 / C 值不值）** |

### 快照日期（历史回看）

日更链只在**交易日**产快照，节假日/周末天然没有当日数据。所以页面的默认日**不是 `date.today()`**，
而是库里最后一个有快照的交易日（`market_stat` 的最大 `date`）；这也修掉了"非交易日打开页面显示未知/
今日无推荐"的假故障。

- 选择器：顶部快照条 + 展开式月历（直观层默认展开，其他层折叠），可点日历格、`‹ 上一交易日 / 下一交易日 ›`、
  月份切换，或直接用 `<input type="date">` 跳转；选中日期写进 URL 的 `?date=YYYY-MM-DD`
- 全局一致：`?date=` 在**五层之间透传**（导航、日历、推荐与梯队的个股诊断链接、跳转表单），
  逻辑层/算法层的市场参数、直观层的推荐与最高板梯队、个股诊断的 PIT 基准日全部跟选中的日期走
- 如实提示：快照条显示 `快照日期 + 周几`，非最新时打 **历史回看** 徽标并给出 `最新快照 YYYY-MM-DD（落后 N 天）`
  + `回到最新`；非法日期或无快照的日期一律回落到最新日并在页面说明
- 空数据不冒充有数据：当日无 BUY 信号时，页面直接给出 `signal` 表当日/全表行数，并说明"库里快照本身只到 X"
- 数据来源 `presentation/snapshot.py`（`available_dates` / `resolve_date` / `calendar_ctx` / `banner_ctx`，
  60s 缓存），API `GET /emotion/api/dates`
- 注意：nginx 用 `proxy_pass .../` 剥掉 `/emotion` 前缀，服务端只看到 `/`、`/logic`；页面自链接统一由
  `server._request_path()` 补回前缀（否则日历会跳到门户根路径）

### 个股诊断

- 数据 `data/stock_query.py`（SQL 唯一住所，全部 PIT：只用目标日及之前的样本）
- 判定 `algorithms/stock.py`（纯计算：零 SQL、零 LLM）→ 档位 `BUY` 可参与 / `LOW` 只做低吸回踩 /
  `WATCH` 观察 / `AVOID` 不建议参与；市场 `buy_window=NONE`、`force_liquidate`、ST、跌停为硬约束
- 服务 `services/stock_service.py`（组装 + 可选 LLM；**整链路只读**，不写任何库表）
- 三段式诊断 `services/diagnose_service.py`（**A 现在 / B 过去 / C 值不值**）：已接入 `/api/stock`，
  payload 新增 `diagnose` 键（既有键名全部不变），昂贵统计由 `stock_service` TTL 缓存注入
- 展示 `presentation/templates/stock.html` + `static/stock.js`（三张诊断卡 `#diagnose-a-card` /
  `#diagnose-b-card` / `#diagnose-c-card`，`renderDiagnose()`，无数据显示「暂无」）
- API：`GET /emotion/api/stock?code=601811`（规则层，首次 ~2s 全表聚合、缓存后 ~0.15s；
  响应新增 `diagnose` 段：A/B/C 三段式）；
  `GET /emotion/api/stock/llm?code=601811`（AI 解读，4~6s，前端异步取，失败不影响规则层结论）
- 页面内容：结论卡（档位 + 逐条依据 + 风险标签）、量价与连板结构（近 30 根 K 线逐日标注涨停/一字/
  换手/炸板/量比）、市场情绪环境与最高板梯队、**同层级历史晋级率/换手口径/次日收益分布**、AI 解读
- 统计口径与算法层**同源**：层标签、主板池、剔次新、次日 = 日历下一交易日、名义/换手双口径，
  全部对齐 `algorithms/promotion.py`；真库校准逐层 4 位小数相等
  （`EC_LIVE_DB=1 pytest tests/e2e/test_stock_live.py`）
- LLM 开关：`CONFIG.STOCK_LLM_PROFILE`（默认 `smart`，设 `off` 关闭）。
  **信号链的 `LLM_PROFILE` 仍为 `None`**（信号链不 import LLM 的约束不变）

### LLM 密钥来源（`services/llm_backend.py`）

优先级：`LKL_LLM_API_KEY_<PROFILE>` → `LKL_LLM_API_KEY` → **平台变量名**
（`default` / `fast` / `smart` → `SENSEN_API_KEY`；`agnes` → `AGNES_API_KEY`；先 `os.environ`
后 `~/.env`）→ `api_key=` 密钥文件。密钥文件与 `~/.env` **都不进 git**。
未配置密钥或调用失败 → 相关功能降级为规则层结论并如实显示原因，绝不 500。

---

## 测试

```bash
cd emotion-core
# 主基线（DB-free，约 2s）
PYTHONPATH=src .venv/bin/pytest tests/unit tests/oracle tests/caliber -q      # 1850 passed

# 真库校准（默认自动 skip；晋级率单日口径 vs 算法层逐层相等 + 整链路只读）
EC_LIVE_DB=1 PYTHONPATH=src .venv/bin/pytest tests/e2e/test_stock_live.py -q

# 历史信号回填（运维入口；--dry-run 只预览区间与交易日数、不写库）
PYTHONPATH=src .venv/bin/python -m emotion_core.services.replay_service --start 2024-01-01 --end 2026-09-30
```

---

## 目录

```
emotion-core/
├── docs/
│   ├── 01-功能层讨论.md              两个仓库功能清单 + 三方对照 + 终态功能集提案
│   ├── 02-算法说明与接手决策规则.md    ★ 决策规则 + 逐个功能域通俗解释
│   ├── 03-新增需求-具体推荐与个股分析.md  ★ R1 具体推荐+买点 / R2 个股诊断
│   ├── 04-数据源层讨论.md              ★ 源清单 + 5 个裁决 + P4/P5 结论
│   ├── 05-数据库普查与清理审计.md      ★ 41 表普查 + 7 个问题 + 删除建议（现在不删）
│   ├── 06-上线后工作手册.md             ⭐ 记忆锚点：上线清单/日常运维/待办/回滚
│   ├── 07-算法层搬运方案.md             ★ 逐字搬运 + 10 层依赖顺序 + 风险点
│   ├── 08-编排层合并方案.md             ★ 线性Python入口 + 2timer+1service + 不复杂
│   ├── 09-展示层设计.md                 ★ 三层架构：直观/逻辑/算法 + 补充展示清单
│   ├── 10-实施步骤规划.md               ★ 9 阶段（自底向上）+ 每阶段验证
│   ├── 11-代码架构设计.md               ★ 分层/模块化/功能化 + 目录结构
│   ├── 12-LLM层设计.md                  ★ 抽象层 + Agnes 复用 + 默认关
│   ├── 13-架构评审与逐层代码框架设计.md   ★ 评审结论 + 逐层代码框架
│   ├── 14-自主决策日志.md               逐条拍板留痕
│   ├── 15-实施追踪.md                   缺口修复 / 运维记录 / 踩坑
│   ├── decisions/                    ADR：0001 盘后约束与买点口径 + 口径冲突清单
│   ├── evidence/                     取证材料（合并分析 + 展示层取证）
│   └── archive-lkl/                  ★ lkl 策略规格书归档（只读，46 文件）
├── src/emotion_core/
│   ├── core/          Rust 算法核心（.so）
│   ├── algorithms/    Python 算法外壳（PyO3）+ 个股判定
│   ├── data/          数据层（DB + 同步 + 清理 + 独立交易日历）
│   ├── orchestration/ 编排层（daily.py 13 步 + systemd）
│   ├── presentation/  展示层（直观/逻辑/算法/策略/个股 5 页 + 4 个 JSON 接口）
│   ├── services/      服务层（覆盖率门槛/校准/LLM/个股诊断/历史信号回填 CLI）
│   ├── llm/           LLM 层（4 profile + 回退链 + prompts/strategies）
│   ├── domain/        契约层（bar / ladder / market / position / signal / snapshot）
│   └── utils/         工具层（db/dates/price/config）
├── tests/            unit / oracle / caliber / architecture / e2e
└── scripts/
```

### `docs/archive-lkl/` 是什么

lkl 被删除前抢救出来的**文档与报告归档**：525 行策略规格书、术语表、数据源踩坑、
3 份 ADR、40+ 份实盘日报。**只读，禁止编辑**。

> **代码可以重写，奎爷的口径裁决重写不出来。**

---

## 关键约束（合并必须正面处理）

口径冲突 **C1~C6 的权威表在 `docs/decisions/README.md`**（合并分析 §6 批量裁决：
单位/`pre_close`、连板、市场状态、晋级率、ST、展示层数据源）。此处只留两条
不在那张表里的提醒：

- **不冻结单位就合并 = 静默错 100 倍**：lkl `volume`=手 / asel `volume_shares`=股
  （实测比值恰好 100）；换手率 百分数 / 比值（0.01）。
- **asel 的增量设计大半未落地**：15 张表 0 行、`asel/sources/` 零生产 import、
  声明式 DAG（`steps.py`）生产未接线。**算待落地能力，不算已有资产。**

---

## 源仓库（只读参照，不改动）

| 仓库 | 路径 | 分支 | HEAD | 状态 |
|---|---|---|---|---|
| lkl | `/home/ubuntu/DSH/longkonglong` | `main` | `eb53a46` | 服务已停，待删除 |
| asel | `/home/ubuntu/DSH/a_share_emotion_leader` | `p0` | `fde92ac` | 服务已停，待删除 |

两者 `git remote` 指向同一个 GitHub 仓库 `nxz1026/Stock_Anlysis.git`，但**历史不相交**。

**删除前置条件**：emotion-core 上线并验证通过。在此之前两个源仓库**保持原样**。
