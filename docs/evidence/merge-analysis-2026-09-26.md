# longkonglong 与 asel 合并可行性分析（四层抽象）

- 日期：2026-09-26
- 对象：`/home/ubuntu/DSH/longkonglong`（下称 **lkl**，包 `lkl/`）· `/home/ubuntu/DSH/a_share_emotion_leader`（下称 **asel**，包 `asel/`）
- 方法：实际读码 + 连生产库实测 + 读线上 systemd/nginx 配置。每条结论标 `路径:行号` 或实测输出
- 判定口径：**✅真重复（可删一份）· 🔀可合并（原样搬）· ⚠️需重写 · ➡️仅一侧 · 🚨口径冲突（必须先裁决）**

---

## 0. 一句话结论

**这不是"两个功能重叠的项目要合并"，而是"一次已经做了一半的重写，要把剩下的另一半做完"。**

- **lkl 是决策系统**：龙空龙策略 —— 情绪周期状态机 → 龙头淘汰赛 → 买入信号 → 交易桥。数据只到 2023-11。
- **asel 是事实底座**：16 年数据 + 制度规则 + 质量语义 + 只读展示。**没有任何决策能力**。

四层重叠分布极不均匀：

| 层 | 重叠度 | 一句话 |
|---|---|---|
| 数据层 | **中**（源是交叉，不是包含） | 同库同凭据；但**单位口径不同（手/股、百分数/比值）**，合并即静默出错 |
| 算法层 | **低**（比你以为的低得多） | 两个"情绪"是不同抽象；lkl 11 项独有 / asel 9 项独有 |
| 编排层 | **低**（范式不同，非重复） | lkl 是 shell 17 步硬编码；asel 的声明式 DAG **在生产链路上没接线** |
| 展示层 | **中**（主干应择 asel） | 都是纯 stdlib HTTP；lkl 11 处直连 DB，asel 零 DB |

**真重复可删的代码不到 800 行**。剩下 2 万多行是**互补**的。合并的收益不在删代码，在：
① 单一权威数据源 ② 统一口径（消掉同一天 50/51/52 三套数字）③ 单套编排与展示。

**合并的真正障碍是 6 处口径冲突**（§6）。不先裁决，合并后会出现两套并存的"真相"。

---

## 1. 先决事实：合并的地基已经存在

你问"能不能合并"，答案是**物理上早就可以，而且已经合了一半**。

### 1.1 同一个数据库，不同 schema，零表名冲突（实测）

```
DB = longkonglong
tables by schema:  asel=12   asel_research=6   public=23
```

| schema | 归属 | 表数 | 证据 |
|---|---|---|---|
| `public` | lkl | **23** | `lkl/models/schema.py` 的 `DDL` 字典 18 张（`:5-248`）+ `_MIGRATIONS` 新建 5 张（`:303-320`）= 23，与库实测一致 |
| `asel` | asel 生产 | 12 | `migrations/0001-0006` |
| `asel_research` | asel 研究隔离 | 6 | `migrations/0005` |

| 维度 | lkl | asel | 相同？ |
|---|---|---|---|
| 库名 | `config.py:40` `"dbname": "longkonglong"` | `storage/dbconfig.py:20` `DEFAULT_DBNAME = "longkonglong"` | ✅ |
| 凭据 | `~/.dbconfig` 的 `$RDSHOST`/`$DB_PW`（`config.py:36-38`） | 同文件同键（`dbconfig.py:18,71`） | ✅ |
| 端口/用户/SSL | 5432 / postgres / `verify-full` + `~/global-bundle.pem` | 同（`dbconfig.py:19,21-22,90-91`） | ✅ |
| schema | `public`（裸表名，不设 `search_path`） | `asel`（`backfill_history.py:11-13`「铁律：只写 asel，禁止触碰 public」） | ⚠️ 隔离 |
| 连接实现 | `utils/db.py:26-57`（带 mtime 缓存） | `storage/dbconfig.py:37-92`（无缓存） | ❌ **两份同口径实现** |

> **含义**：数据层合并不是"迁库"，是"选一份权威表 + 统一单位"。**这是最省力的部分，也是唯一会导致静默错误的雷区（§2.4）。**

### 1.2 同一个 GitHub 远端，两个不相交的分支

```
lkl  remote "upstream" = https://github.com/nxz1026/Stock_Anlysis.git   (branch main)
asel remote "origin"   = https://github.com/nxz1026/Stock_Anlysis.git   (branch p0)

lkl  根 commit 7ffe9417…   HEAD eb53a46
asel 根 commit 42240b96…   HEAD fde92ac    （asel main == 根 commit）
```

asel 根 commit 是 `42240b9 chore: asel 项目基线（回填脚本 + 报告）` —— **全新历史，与 lkl 不共享任何对象**
（`git cat-file -t eb53a46` 在 asel 仓库报 `Not a valid object name`）。

### 1.3 已经存在的隐性跨仓耦合（4 处）

| # | 耦合 | 证据 | 性质 |
|---|---|---|---|
| 1 | asel **回填**与**看板**用 lkl 的 venv | `asel-backfill-daily.env:8`、`supervise_backfill.sh:12`、`watch_backfill.sh:9`、`asel-dash.service:9` → `/home/ubuntu/DSH/longkonglong/.venv/bin/python` | 部署层硬耦合 |
| 2 | asel 脚本**真 import 了 lkl** | `supervise_backfill.sh:72-73`、`watch_backfill.sh:29-33`：`sys.path.insert(0,'/home/ubuntu/DSH/longkonglong')` + `from lkl.utils import db` | **代码层耦合，违反 asel 自己的红线** |
| 3 | 两站**已双向互链** | lkl `dashboard/index.html:58` → `/stock/`；asel `presentation/render.py:88` → `https://140.83.62.161/stock-legacy/`（"旧版股票复盘 →"） | 产品层已认亲 |
| 4 | nginx 已把 asel 当"当前" | `dsh-web:54-55`「当前 ASEL 版本：/stock/；旧版 longkonglong：/stock-legacy/」 | **换代已完成** |

> **修正（我原稿写错过）**：asel **生产装配**（`asel-production-daily.env:6`）用的是
> `/home/ubuntu/.venvs/league/bin/python`，**不是** lkl 的 venv；该 venv 里没有 asel 包，
> 靠 `PYTHONPATH`（`:9`）挂载。所以 asel 是"**两个 python、三套 venv 语义**"：
> 回填用 lkl venv（要 akshare）、生产用 league venv（靠 PYTHONPATH）、看板用 lkl venv。

### 1.4 运行态：两个系统都在跑，asel 在下游

```
lkl-health.timer        enabled  active(waiting)  09:30
lkl-trade.timer         DISABLED inactive          12:01   ← 注意
lkl-close.timer         DISABLED inactive          15:05   ← 注意
asel-backfill-daily.timer (user) enabled  16:00 + 17:00 → asel.daily_bar_raw
lkl-daily.timer         enabled  active(waiting)  17:20   → public.*
lkl-strategy.timer      enabled  active(waiting)  17:50
asel-production-daily.timer (user) enabled  18:10         → snapshots/*.json
lkl-dash.service        active running            127.0.0.1:8098
asel-dash.service       active running            127.0.0.1:8899  --base-path /stock
```

每日真实时序：

```
15:00 收盘
16:00 asel-backfill-daily  ──→ 写 asel.daily_bar_raw（近端增量，注释自述约 1h）
17:00 asel-backfill-daily  ──→ 失败重试窗口（--skip-fresh 续传）
17:20 lkl-daily            ──→ 17 步 → 写 public.*（含 trade_poll 30min 尾巴）
17:50 lkl-strategy
18:10 asel-production-daily ──→ 读 asel.daily_bar_raw → 8 份 JSON 快照（约 90s）
```

**踩坑 #1（文档与实况不符）**：`asel/scripts/systemd/asel-production-daily.timer:6-8` 写着
`15:05 lkl-close —— 收盘对账、写入 asel.daily_bar_raw`，但：
- **`lkl-close.timer` 是 disabled 的**（`systemctl is-enabled` 实测）；
- lkl 全仓 `grep -rn "asel"` 在 `scripts/` 下 **0 命中** —— lkl 从不写 `asel.daily_bar_raw`。

真正写该表的是 asel 自己的 `backfill_history.py`。**这条注释是过期的设计意图，不是现状。**

### 1.5 数据存量（实测）

| 表 | 行数 | 覆盖区间 | 交易日 |
|---|---|---|---|
| `asel.daily_bar_raw` | **14,402,582** | 2010-01-04 ~ 2026-09-24 | **4064** |
| `public.daily_bar` | 3,510,495 | 2023-11-21 ~ 2026-09-24 | 691 |
| `public.derived_bar` | 3,339,347 | 2023-12-04 ~ 2026-09-24 | 682 |
| `public.market_stat` | 661 | 2024-01-02 ~ | 661 |
| `public.ladder_day` | 8,656 | 2024-01-02 ~ | 661 |
| `public.promotion_day` | 3,305 | 2024-01-02 ~ | 661 |
| `public.limit_pool_em` | 3,015 | 2026-08-11 ~ | 31 |
| `public.hot_rank` | 436,968 | 2025-08-31 ~ | 382 |
| `public.theme_tag` / `theme_group` | 180 / 825 | 2026-08-31 ~ | 17 |
| `asel.security_master` | 5,930 | — | — |
| `asel.ref_adjust_factor` | 56,930 | — | — |
| `asel.ref_trading_calendar` / `ref_limit_rule` / `ref_security_status` / `ref_dividend` | **0 / 0 / 0 / 0** | — | — |
| `asel.ops_raw_manifest` / `ops_pipeline_state` / `ops_quota_ledger` / `ops_data_revision` | **0 / 0 / 0 / 0** | — | — |
| `asel_research.*`（6 张） | **全 0** | — | — |

**踩坑 #2（最重要）**：asel 的 P0 制度层 / 溯源层 / 研究层**建好了表，一行数据都没写**。
更严重的是——**代码也没接线**：

| asel 模块 | 生产 import 者 | 实测 |
|---|---|---|
| `asel/sources/free_daily.py` / `manifest.py` / `st_events.py` | **0**（全仓 grep 只命中 `tests/`） | 写了但没人用 |
| `asel/storage/differential.py` | **0**（只被 `tests/test_differential.py` 调用） | 写了但没人用 |
| `asel/orchestration/steps.py`（536 行声明式 DAG） | **仅 `pipeline.py:39`**，而 `pipeline.py` 自述是 **P0 离线 demo**（`:1-4`「不连 DB、不联网、不调 Wind」） | 写了但没接生产 |
| `asel/semantics/seed.py` 的 ST 规则 | `production_daily.py:224` 把 variant **硬编码 `"normal"`** | 规则空转 |

> **`asel/sources/` 三个模块零生产 import、7 张表无写入者、DAG 引擎闲置** ——
> 这 15 张表 + 3 个模块是 asel 相对 lkl 的**核心增量设计**，**目前全是空壳**。
> 合并时不能把它们算作"已有能力"，要算作"待落地能力"。

### 1.6 数据对账：OHLC 逐行等价（实测）

```sql
select count(*) total,
       count(*) filter (where a.close is distinct from b.close) close_diff
from asel.daily_bar_raw a join public.daily_bar b using (code, date)
where a.date >= '2023-11-21';
--> total = 3,510,445    close_diff = 0
```

| 日期 | asel 行数 | lkl 行数 |
|---|---|---|
| 2026-09-24 | 5,557 | 5,221 |
| 2026-09-23 | 5,556 | 5,221 |
| **2026-09-22** | **5,554** | **无（lkl 缺这天）** |
| 2026-09-21 | 5,553 | 5,210 |

**OHLC 数值零差异，asel 每日多约 336 只（含北交所）。**
**但 `close/high/low/open` 等价 ≠ 两表可互换** —— 见 §2.4，单位口径不同。

---

## 2. 数据层

### 2.1 两边的数据层地图

| 职责 | lkl | asel | 判定 |
|---|---|---|---|
| 行情适配器 | `lkl/providers/{base,eastmoney,sina,pytdx_provider}.py`（155 行） | `asel/sources/free_daily.py`（规范化纯函数，**零生产 import**） | 🔀 互补 |
| 抓取实现 | `lkl/services/ingest.py`（873 行，EM 分页/新浪回退/三池/人气榜/守卫） | `backfill_history.py`（34KB，**顶层脚本，不在包内**） | ⚠️ 两份重复实现 |
| 存储/仓储 | `lkl/utils/db.py`（157 行）+ **139 处内联 SQL** 散在 14 个 service | `asel/storage/repository.py`（全部 SQL 收拢，DB-API 注入，不暴露 cursor） | ⚠️ asel 范式更优 |
| 表结构 | `lkl/models/schema.py`（23 表，`public`） | `migrations/0001-0006`（`asel` 12 + `asel_research` 6） | ➡️ 互补 |
| 原始留存 | `lkl/services/revision.py`（64 行，只记 upsert 覆盖） | `asel/sources/manifest.py` + `ops_raw_manifest` | ➡️ asel 独有（**空表 + 无 import**） |
| 制度规则 | `lkl/config.py:54` 前缀硬编码 `{"68":120,"30":120}`，**无日期维度** | `asel/semantics/rules.py` + `seed.py`（**日期版本化**，含创业板 2020-08-24 改革） | ➡️ asel 独有（**空表 + ST 规则空转**） |
| 质量守卫 | EM 交易日守卫 + 新浪回退 + 全零快照拒绝 + 收盘判定 | `scripts/asel-coverage-gate.py`（**硬阻断** `exit 76`）+ `asel/quality/coverage.py` | 🔀 互补 |
| 快照产物 | `reports/*.json`（日报副产品） | `asel/snapshots/store.py`（content-hash 幂等原子写） | ⚠️ asel 更优 |

### 2.2 数据源覆盖对比：**交叉，不是包含也不是互补**

| 源 | lkl | asel | 证据 |
|---|---|---|---|
| 东方财富 clist | ✅ 主源 | ❌ | `ingest.py:251-264` |
| 东方财富 三池（ZT/ZB/DT） | ✅ | ❌ | `ingest.py:680-721` |
| 东方财富 人气榜 | ✅ `hot_rank`（437k 行 / 382 天） | ❌ | `ingest.py:801-873` |
| 新浪日线 | ✅ 备源 | ✅ **主源** | `providers/sina.py:20` vs `backfill_history.py:559`（**同一 akshare 函数 `stock_zh_a_daily`**） |
| 腾讯日线 | ❌ | ✅ 退市股兜底 | `backfill_history.py:568-572`（含两处 akshare 量纲 bug 实测修正 `:580-593`） |
| pytdx/通达信 | ✅ 长连接回填 | ❌ | `ingest.py:496-554` |
| Wind | ✅ 公告/概念 | ✅ ST 事件/上市日期（**staging，未接生产**） | `theme.py:84,163` vs `staging/wind_adapter/wind_sources.py` |
| 北交所 | ❌（`config.py:53` 自认"情绪计数天然缺北交所"） | ✅（每日多 ~336 只） | 实测 |

**真重叠只有一处：新浪日线。** 其余全是"仅 lkl"或"仅 asel"。

**lkl 独有且 asel 无法替代的源**：东财三池（封板时间/开板次数/一字/烂板）+ 人气榜。

**⚠️ `limit_pool_em`（东财外部池）与 `market_fact_pool_daily`（asel 自算池）不是同一业务事实，不可互相替代。**

### 2.3 存储层表级映射

| 业务事实 | lkl（`public`） | asel（`asel`/`asel_research`） | 判定 |
|---|---|---|---|
| 日线行情 | `daily_bar`（691 天） | `daily_bar_raw`（4064 天） | ⚠️ **同义但单位不同（§2.4）** |
| 证券主数据 | `stock_basic`（5554，`is_st` 当前布尔） | `security_master`（5930，`delist_date` + 溯源列） | ⚠️ asel 超集 |
| ST 状态历史 | ❌ 无历史 | `ref_security_status`（SCD2，**空表**） | ➡️ asel 设计独有，未落地 |
| 涨跌停制度 | 前缀 CASE（无日期） | `ref_limit_rule`（带 `valid_from/to`，**空表**） | ➡️ asel 设计独有，未落地 |
| 交易日历 | ❌ 各服务各写窗口函数 | `ref_trading_calendar`（**空表**） | ➡️ asel 设计独有，未落地 |
| 复权因子 | ❌ | `ref_adjust_factor`（56,930 行） | ➡️ **asel 独有且已落地** |
| 分红除权 | ❌ | `ref_dividend`（**空表**） | ➡️ asel 独有，未落地 |
| 派生行情（涨停/炸板/一字/换手/连板/振幅） | `derived_bar`（3.34M 行 / 682 天） | **无表**（只在 JSON 快照） | ➡️ **lkl 独有（历史唯一）** |
| 市场情绪事实 | `market_stat`（661 天 × 26 列） | **无表** | ➡️ **lkl 独有（历史唯一）** |
| 连板梯队 | `ladder_day`（8656） | **无表** | ➡️ lkl 独有 |
| 晋级矩阵 | `promotion_day`（3305） | **无表** | ➡️ lkl 独有 |
| 题材标签/分组 | `theme_tag`/`theme_group`（仅 17 天） | **无** | ➡️ lkl 独有 |
| 东财三池 / 人气榜 | `limit_pool_em`（31 天）/ `hot_rank`（382 天） | **无** | ➡️ lkl 独有 |
| 买入信号/结果/策略 | `signal`/`signal_outcome`/`strategy_signal` | **无** | ➡️ lkl 独有 |
| 持仓/交易/自选 | `position`/`trade_event`/`watchlist` | **无** | ➡️ lkl 独有 |
| 告警/报告/LLM 日志 | `alert`/`review_report`/`llm_call_log`/`eval_result` | **无** | ➡️ lkl 独有 |
| 原始响应留存 | `data_revision`（73 行） | `ops_raw_manifest`（**空表 + 无 import**） | ➡️ asel 设计独有，未落地 |
| 编排打点 | `pipeline_state`（10 行，`ON CONFLICT(step)` **覆盖式**，只记最新） | `ops_pipeline_state`（**空表**）+ `snapshots/pipeline_state.jsonl`（实况） | ⚠️ asel 打点落 JSONL 未落库 |
| 配额账本 | ❌ | `ops_quota_ledger`（**空表**） | ➡️ asel 独有，未落地 |
| 迁移账本 | ❌（`information_schema` 漂移守卫） | `schema_version`（5 行，**带 checksum**） | ➡️ **asel 独有且已落地** |
| 研究隔离 | ❌（`evaluate.py` 直接读写生产表） | `asel_research.*`（6 张，**全空**） | ➡️ asel 设计独有，未落地 |

**读法**：
- **lkl 的表是"事实的历史"**（661 天情绪事实、682 天派生行情）—— 删掉永久丢失，asel 侧无法重算（JSON 快照只有当日）。
- **asel 的表是"制度的骨架 + 溯源"**（ref_* / ops_*）—— 设计更对，但**大部分是空的**。

### 2.4 🚨 单位口径冲突：合并即静默出错（本次实测最严重的发现）

我原稿只对比了 OHLC 就说"数值零差异"，**这是不完整的**。逐字段对比后：

| 字段 | lkl | asel | 实测比值 |
|---|---|---|---|
| `volume` | `daily_bar.volume` 单位 = **手**（`ingest.py:189-193` 显式 ÷100） | `daily_bar_raw.volume_shares` 单位 = **股**（`migrations/0006:29` 注释） | **恰好 100.00**（5209/5209 行） |
| 换手率 | `turnover_rate` = **百分数**（`providers/sina.py:25` ×100） | `turnover_ratio` = **比值**（`0006:32`） | **0.01** |
| `pre_close` | **存列**（`schema.py:25`，EM 走源端 f18），且有 `verify_pre_close` 校验（`ingest.py:770-778`） | **不存**，读时 `LAG(close)` 重算（`production_daily.py:207`、`streak_pipeline.py:126`、`series_pipeline.py:126`） | 见下 |

**实测证据（2026-09-24 抽样）**：

```
code      asel_vol        lkl_vol      asel_tr   lkl_tr    vol比值
000001    104381872       1043818.72   0.005379  0.5379    100.00
000002    667265050       6672650.5    0.068683  6.8683    100.00
000006    27859946        278599.46    0.020637  2.0637    100.00
→ volume 比值 min/max/avg = 100.00 / 100.00 / 100.00（5209 行）
→ turnover 比值 min/max/avg = 0.0098 / 0.0100 / 0.0100
```

**`pre_close` 的 `LAG(close)` 在除权日必错（实测）**：

```sql
-- 2026-08-01 起全市场
select count(*) total, count(*) filter (where abs(lag_close - pre_close) > 0.005) mismatch
--> total = 192,593    mismatch = 339（0.176%）
```

偏差最大的实例：

| code | date | `LAG(close)` | 真实 `pre_close` | 偏差 |
|---|---|---|---|---|
| 301237 | 2026-09-01 | 54.660 | **39.300** | 15.360 |
| 001225 | 2026-09-23 | 50.920 | **36.370** | 14.550 |
| 603444 | 2026-09-17 | 373.490 | **363.530** | 9.960 |
| 300908 | 2026-09-14 | 24.810 | **17.110** | 7.700 |

**后果**：除权日 asel 用 `LAG(close)` 当 `pre_close`，会把「除权后真实涨停」判成「大跌 -20%~-30%」，
⇒ **漏判涨停、连板段被错误切断**。lkl 因为存了真实 `pre_close` 且有 `verify_pre_close` 校验，没有这个问题。

> **合并红线**：数据层收口时**必须先冻结单位口径**（手/股、百分数/比值），
> 并决定 `pre_close` 是"存列"还是"重算"。**不冻结就合并 = 数字错 100 倍且无任何报错。**

### 2.5 🚨 ST 口径冲突

| 侧 | 处理 | 证据 |
|---|---|---|
| lkl | **整体剔除 ST** | `derive.py:58` `WHERE NOT s.is_st` |
| asel | **为 ST 建了 5%/20% 规则，但生产把 variant 硬编码 `"normal"`** ⇒ 规则空转 | `semantics/seed.py:60-61` vs `production_daily.py:224` |

⇒ 合并后 **`limit_up_count` 两侧不可比**。

### 2.6 涨停/连板口径的双实现差分

| 判据 | lkl（SQL 整数式 `derive.py:37-67`） | asel（纯函数 `algorithms/limits.py` + `semantics/rules.py`） | 等价性 |
|---|---|---|---|
| 涨停价 | `((round(pre*100))::bigint*pct+50)/100`，pct 前缀硬编码 | `(pre_cents*pct_num+50)//100` + Decimal HALF_UP，比例**参数化 0.05/0.10/0.20/0.30** | ✅ 整数式逐字一致 |
| 制度选择 | 前缀 CASE + `NOT is_st` | `LimitRule(board,variant,valid_from/to)`，重叠抛错，**无匹配拒绝回退** | ⚠️ **asel 超集** |
| 创业板 2020-08-24 改革 | ❌ 无日期维度（但 `DATA_START=2024-01-01`（`config.py:187`）**规避了**） | ✅ 日期版本化 | ⚠️ 合并后若回填 2020 前历史即错 |
| 新股无涨跌幅 | ❌ 无法表达 | ✅ `variant='new_listing'` + `no_limit` | ➡️ asel 独有 |
| 一字 | `c=up AND l=up` | `is_limit_up and low==up` | ✅ |
| 换手板 | `c=up AND l<up`（`is_exchange` 列） | **无该字段** | ➡️ lkl 独有（ladder/promotion/accelerate/dragon_env 全依赖它） |
| 振幅 | `(high-low)/pre_close*100` | **无** | ➡️ lkl 独有（高潮判据用） |
| **炸板** | ✅ `is_bomb` + `market_stat.bomb_rate`（**情绪状态机输入**） | ❌ `grep -i bomb` 在 `asel/` **0 命中** | ➡️ **lkl 独有** |
| 连板数 | SQL 窗口 `grp=此前非涨停计数` | 布尔运行计数（`streaks.py`，**lkl 参考实现逐字移植**） | ✅ 算法等价 |
| **停牌断档** | **❌ 不感知**（见 §7 踩坑 #3，实测 45 行虚增） | ✅ 日历合成 missing 记录并断开 | 🚨 **冲突** |
| 三态质量 | ❌ 全布尔（**"未知"与"未涨停"不可区分**） | ✅ `status`/`quality` | ⚠️ asel 更诚实 |
| 缺价格 | SQL `pre_close>0 AND close IS NOT NULL` **静默丢行** | 显式 `quality=missing/unknown` | ⚠️ asel 更诚实 |

### 2.7 第 4 处未申报的移植重复（asel 抄了 lkl HEAD，不是它声称的基线）

`backfill_history.py:198-234` 的 `_sina_payload` / `_sina_session_closed` 与
`lkl/services/ingest.py:297-350` **逐字同源**（常量 `15*60`、`parts[8]`、`parts[31]` 全一致）。

但 `docs/PORTED.md` 只申报了 **3 处**移植，且源 commit 写的是 `98588aa`。
实测：`git show 98588aa:lkl/services/ingest.py` 中**不存在**该逻辑 ——
它是 lkl 的 `20ba9a5` / `724c6ed` / `eb53a46`（**2026-09-24~25**）才引入的，
**晚于 PORTED.md 记录的源 commit**（已用 `git merge-base --is-ancestor` 确认 98588aa 是 HEAD 祖先）。

> **含义**：asel 移植时抄的是 **lkl HEAD**，而非它声称的 `98588aa` 基线。
> 这是**未经申报的代码同步**，意味着 asel 的"不 import lkl、只移植 3 处"承诺在事实上不成立。
> 判定：**可直接删 asel 那份**（新浪守卫逻辑），改 import lkl 版。

### 2.8 数据层合并判定清单

| 模块 | 判定 | 理由 | 风险 |
|---|---|---|---|
| `public.daily_bar` | ✅ **删（真重复）** | asel 超集、OHLC 3,510,445 行零差异、多 5.9 倍历史、多北交所、lkl 还漏 2026-09-22 | **必须先冻结单位**（§2.4）；`pre_close` 决策；lkl 下游 `derived_bar` 等需切表 |
| `asel.daily_bar_raw` | **保留为权威行情表** | 见上 | 无 `pre_close` 列，需补或保留 lkl 的校验 |
| `lkl/providers/`（4 文件 155 行） | 🔀 **可合并** | 异常三分类（`FetchError`/`DataError`/`NoDataError`）+ 重试/限流骨架设计好 | asel 取数在顶层脚本里，需下沉进 `asel/sources/` |
| `lkl/services/ingest.py`（873 行） | ⚠️ **需拆分，不要重写** | 混合 EM 分页、新浪回退、三池、人气榜、交易日守卫、收盘判定 6 种关注点 | **东财三池 + 人气榜是 lkl 独有源，必须保留抓取逻辑**；`ingest` 是 7 轮审计换来的 |
| `lkl/utils/db.py` | ⚠️ **需重写** | 与 `asel/storage/repository.py` 同职责，asel 版范式更优 | `db.py` 的"消毒 upsert"与单事务 DELETE+INSERT 是踩坑换来的，须移植 |
| `asel/storage/repository.py` | **保留为存储层主干** | 全部 SQL 收拢，`DAILY_BAR_UPSERT_SQL` 是唯一实现 | lkl 的 **139 处内联 SQL** 需逐个迁入，工作量大 |
| `lkl/services/revision.py` | 🔀 **合并进 `ops_data_revision`** | 职责相同，asel 表带 `reason` 必填约束更严 | asel 表 0 行 |
| `asel/sources/manifest.py` + `ops_raw_manifest` | **保留（asel 独有）** | lkl 完全无原始留存；`parser_version` 是区分"源变了"与"我改了"的关键 | **模块零 import + 表 0 行，需先接线** |
| `asel/semantics/rules.py` + `seed.py` + `ref_limit_rule` | **保留（asel 独有）** | lkl 无法表达新股无涨跌幅/北交所/制度有效期 | **表 0 行 + ST variant 空转**；`source_url` 未核实不得入库 |
| `asel.ref_adjust_factor`（56,930 行） | **保留（asel 独有且已落地）** | lkl 无复权因子 | 无 |
| `backfill_history.py`（34KB） | **保留（asel 版更强）** | 双键续传 + `LEAST/GREATEST` 防污染（`:89-99`）+ 腾讯退市兜底 + 监督重启 | 与 lkl `fetch backfill` 二选一，否则**双份配额消耗** |
| `asel/storage/differential.py` | ⚠️ **待接线或删** | 只被测试调用 | 无 |
| `asel/storage/dbconfig.py` vs `lkl/config.py` 的 DB 段 | 🔀 **收敛为唯一实现** | 两份同口径解析（同一文件、同一键、同一库） | `config.py` 是 lkl 唯一常量出口，不能整文件替换 |

---

## 3. 算法层

### 3.1 两边的算法层地图

| 业务算法 | lkl | asel | 判定 |
|---|---|---|---|
| 情绪周期状态机 | `services/emotion.py:213-330`（4 相优先级） | **无** | ➡️ **lkl 独有** |
| 情绪状态分类 | — | `algorithms/emotion.py:219-231`（3 档阈值） | ➡️ **asel 独有** |
| 自适应炸板阈值 | `emotion.py:128-149`（30 日中位数+σ） | 无 | ➡️ lkl 独有 |
| 负反馈计数/分歧降级 | `emotion.py:266-279,319-322` | 无 | ➡️ lkl 独有 |
| 质量缺口上下界 | 无 | `algorithms/emotion.py:254-343` | ➡️ **asel 独有** |
| 趋势读数 | 无 | `algorithms/emotion.py:234-251,632-698` | ➡️ **asel 独有** |
| 涨停/炸板/一字判定 | `derive.py:48-73`（SQL） | `algorithms/limits.py:55-187` | ✅ 同义（asel 缺炸板/换手/振幅） |
| 连板计数 | `derive.py:24-34`（参考）+ `:84-95`（SQL） | `algorithms/streaks.py:12-18` | ✅ **真重复** |
| 连板缺口语义 | 无 | `algorithms/streak_facts.py:118-400` | ➡️ asel 独有 |
| 分层晋级矩阵（5 层 × 双口径 + divergence + fail_perf） | `services/promotion.py:63-102` | 无 | ➡️ **lkl 独有** |
| 逐票晋级事实 + 缺口区间 + undetermined | 无 | `algorithms/promotion.py:172-345` | ➡️ **asel 独有** |
| 梯队/龙头身份/淘汰赛 | `services/ladder.py:49-87` | **无**（全库 grep 0 命中） | ➡️ **lkl 独有** |
| 生态可用性评级 G1-G4/B1-B5 | `services/dragon_env.py:39-270` | 无 | ➡️ **lkl 独有** |
| 加速事件 A1/A2/A3 | `services/accelerate.py` | 无 | ➡️ lkl 独有 |
| 题材/概念聚合 | `services/theme.py` | 无（asel 的 "theme" 是 CSS 主题） | ➡️ **lkl 独有** |
| 交易板块聚合（main/star/gem） | 无（前缀内联在 SQL） | `algorithms/boards.py:123-147` | ➡️ asel 独有 |
| 买入信号五条件 | `services/entry.py:22-110` | 无 | ➡️ **lkl 独有** |
| 卖出建议 | `services/exit.py:45-54` | 无 | ➡️ lkl 独有 |
| LLM 策略观察 | `strategy/{context,loader,runner,universe}.py` | 无 | ➡️ lkl 独有 |
| 研究统计栈（replay/forward/dist/hypothesis/exit_compare/dedup/walk_forward/param_matrix/baseline/adoption） | `services/evaluate.py`（17 函数） | 无等价 | ➡️ **lkl 独有** |
| 结果回填/实盘回放一致性/校准提案 | `outcome.py`/`compare.py`/`calibrate.py` | 无 | ➡️ lkl 独有 |
| 通用事实行回测 | 无（面向信号） | `research/backtest.py` | ➡️ asel 独有 |
| 筛选/标注/JSON-CSV 导出 | 无 | `research/{filtering,export}.py` | ➡️ asel 独有 |
| 红线 AST 机检 / 研究契约 / 快照白名单 | 无 | `research/{boundary,contracts,snapshot_reader}.py` | ➡️ **asel 独有** |
| VWAP 质量审计 | 无 | `quality/vwap.py` | ➡️ asel 独有 |
| 分币级价格取整 | `utils/price.py:9-19`（硬编码 0.10） | `algorithms/price.py` + `semantics/price.py`（参数化） | ⚠️ asel 超集 |

**纯函数 vs 焊 SQL 的指控成立（实测）**：

| 侧 | SQL 出现次数 |
|---|---|
| asel `algorithms/` + `semantics/` + `research/` + `quality/` | **0 处** |
| lkl `emotion.py` | 13 |
| lkl `dragon_env.py` | 15 |
| lkl `derive.py` | 11 |
| lkl `evaluate.py` | 11 |
| lkl `theme.py` | 9 |
| lkl `ladder.py` | 7 |
| lkl `accelerate.py` | 6 |
| lkl `promotion.py` | 5 |
| lkl `entry.py` | 4 |
| lkl `exit.py` | 2 |

**注意**：lkl 的**状态机核心是纯的**（`emotion.py:213-330` 无 SQL），SQL 只在指标层 `:43-210`。

### 3.2 关键结论：两个"情绪"不是同一个算法

这是本次分析**最反直觉的发现**。你说两个 repo 都"分析市场情绪"，但：

| 维度 | lkl | asel |
|---|---|---|
| 状态集合 | `冰点/发酵/高潮/退潮`（`emotion.py:253-254`） | `hot/neutral/cold/unknown`（`emotion.py:91-95`） |
| 判定结构 | **优先级规则链**：退潮 > 高潮 > 发酵 > 冰点，首个命中定相；无命中**延续昨日** | **单变量阈值分档**：只看 `limit_up_count` |
| 输出 | `buy_window`（NONE/STANDARD/ENHANCED）+ `force_liquidate` —— **交易决策** | 7 项指标 + `state` —— **事实摘要** |
| 阈值 | `config.py:83-111`（无版本号） | `THRESHOLDS_VERSION="emotion-thresholds-v1"` + 规则原文（**可审计**） |
| 阈值对照 | `CLIMAX_COUNT=80` / `ICE_COUNT_MAX=40` | `hot_min=80`（巧合相同）/ `cold_max=20`（**不同**） |
| 缺数据 | `data_missing` 只继承不判规则 | `unknown`，绝不猜 |
| 质量语义 | 无 | `numerator/denominator/lower/upper` 缺口上下界 |

**asel 全库 grep `退潮|高潮|发酵|冰点|buy_window|force_liquidate|neg_feedback|phase` = 0 命中。**

> **asel 的情绪层是 lkl 的高潮腿与冰点腿的一维近似，不是同一个东西。**
> lkl 的情绪是**决策**（能不能买、要不要清仓）；asel 的情绪是**事实**（今天几档、趋势如何、数据缺多少）。
> 二者**不能互相替代**，且**不能同时对外输出"市场状态"**。

### 3.3 只有一边有的能力

**只有 lkl 有（11 项）**：周期状态机 · 自适应炸板阈值 · 负反馈/分歧降级 · 加速事件 · 生态评级 G1-G4/B1-B5 ·
题材聚合 · 龙头淘汰赛 · 分层晋级矩阵（双口径+divergence+fail_perf） · 买入 checklist · 卖出建议 · 完整研究统计栈 + LLM 观察

**只有 asel 有（9 项）**：制度规则表（board×variant×有效期，未匹配拒绝回退） · 逐指标质量缺口上下界 ·
趋势读数（断点丢弃/持平带/首值 0 不伪造百分比） · 连板缺口语义 · 执行范围 scope + excluded 计数 ·
交易板块聚合 · 研究隔离基础设施（AST 红线/契约/快照白名单） · 固定字段导出 · VWAP 质量审计

### 3.4 算法层合并判定清单

| 模块 | 判定 | 理由 | 风险 |
|---|---|---|---|
| `asel/algorithms/streaks.py` ↔ `lkl/services/derive.py:24-34` | ✅ **删 lkl 副本** | 逐字同一算法，`PORTED.md` 已声明来源；lkl 侧注释自称"仅供测试绑定，禁止业务依赖" | lkl 生产走 SQL 窗口函数，删副本后须保留"纯函数 ↔ SQL"等价测试 |
| `asel/algorithms/price.py` + `semantics/price.py` | 🔀 **原样合并** | asel 是 lkl 的参数化超集，整数式逐字一致 | lkl 的 SQL 整数公式仍是第二份实现，需等价测试锁定 |
| `asel/semantics/rules.py` + `seed.py` | 🔀 **原样合并** | lkl 无对应物 | 历史 `valid_from` 需回填；**ST variant 空转需修** |
| `asel/algorithms/limits.py` | 🔀 **合并 + 补派生** | 判据与 lkl 等价，质量语义更诚实 | **缺 `is_exchange`/`amplitude`/`is_bomb`**，而 lkl 的 ladder/promotion/accelerate/dragon_env 全依赖它们 |
| `asel/algorithms/streak_facts.py` | 🚨 **合并 + 口径裁决** | 缺口语义是 lkl 没有的 | **高**：同票两套连板数，切换会改 lkl 全部历史 `cont_days` 与下游 |
| `asel/algorithms/{market,pools,boards}.py` | 🔀 **原样合并** | scope/coverage/quality/excluded 是 lkl 缺的 | **命名冲突**：asel `boards` = 交易板块，lkl `theme_group` = 概念题材，必须消歧 |
| `asel/algorithms/emotion.py`（`classify_state`） | 🚨 **择一保留 → 建议降级改名** | 与 lkl 4 相状态机冲突，阈值 cold 20 vs 40 | **高**：`state` 是 asel 快照与页面的消费契约，改名破页面 |
| `asel/algorithms/emotion.py`（质量/趋势/解释） | 🔀 **原样合并** | lkl 完全缺 | 需统一 `quality` 取值域 |
| `lkl/services/emotion.py:213-330`（纯核心） | 🔀 **原样合并** | `_rule_*`/`window_of`/`_neg_feedback`/`classify` 已无 SQL | 无 |
| `lkl/services/emotion.py:43-210`（指标层） | ⚠️ **需重写** | 6 个函数全焊 SQL，`_bomb_threshold`/`_seed_and_hist` 回读 `market_stat` | 迁移须保留 A1 的内存滚动可重复性 |
| `asel/algorithms/promotion.py` ↔ `lkl/services/promotion.py` | 🚨 **需重写（合并版）** | 同功能两份实现，口径不同 | **高**：`divergence`/`fail_perf` 是 `dragon_env` G3/B2 的输入 |
| `lkl/services/ladder.py` | ⚠️ **需重写（IO 部分）** | `top_group`/`sole_top` 已是纯函数可直接搬；`build`/`y_survivors` 是 SQL | 无 asel 对应物 |
| `lkl/services/accelerate.py` | ⚠️ **需重写** | `_hit` 纯；其余全 SQL | `_top_streak` 的停牌断档可复用 asel 的 calendar 设计 |
| `lkl/services/dragon_env.py` | ⚠️ **需重写 / 暂缓** | 9 条件中 8 个是 SQL 聚合 | **最高**：依赖 `theme_group`、`promotion_day.divergence/fail_perf`、`ladder_day.is_sole_top` 三张 lkl 专有产物 —— **不先迁题材与分层晋级，本模块无法迁移** |
| `lkl/services/theme.py` | ⚠️ **需重写（IO）+ 部分原样** | `extract_event`/`group_stats`/`false_relation` 纯可直接搬 | 强依赖 Wind CLI 与 node 路径；asel 无题材概念，需**新建领域模块** |
| `lkl/services/{entry,exit}.py` | **保留 lkl + 重写 IO** | c1-c5 判据纯，ctx 构造全 SQL | `checklist_secondary` **临时改写全局** `config.DIVERGE_MIN_TURNOVER` —— 全局可变状态，纯函数化时须改为显式参数 |
| `lkl/services/evaluate.py` + `{outcome,compare,calibrate}.py` | **保留 lkl（需重写数据访问）** | asel `research/` 功能集合约为其 1/8，定位不同 | 17 处 SQL，依赖多张 lkl 专有表 |
| `lkl/strategy/*` | **保留 lkl** | asel 无 LLM 策略观察 | asel 红线禁止生产路径 import LLM，迁入需重新划层 |
| `asel/research/*` + `quality/vwap.py` | 🔀 **原样合并** | 全纯函数、无 lkl 对应物 | 合并前修 §7 踩坑 #4 |

---

## 4. 编排层

### 4.1 两边的编排层地图

| 职责 | lkl | asel | 判定 |
|---|---|---|---|
| 真编排器 | **shell**：`scripts/daily.sh`（74 行，17 步）+ `main.py` 680 行 CLI 门面 + systemd | **`production_daily.py:410-432` 的裸 `for key in keys:` 循环** | ⚠️ 见 §4.2 |
| 声明式 DAG 引擎 | ❌ 无 | ✅ `steps.py`（536 行）——**但生产链路未接线** | ⚠️ **闲置能力** |
| 步骤数 | 17（`daily.sh:41-58`） | 8（`SNAPSHOT_KEYS`，`production_daily.py:62-64`） | ➡️ 见映射 |
| 打点 | `services/pipeline.py`（`pipeline_state` 表，`ON CONFLICT(step)` **覆盖式**，无 run_id/无历史） | `orchestration/state.py`（JSONL append-only，**保留全历史**） | ⚠️ asel 语义更优 |
| 依赖感知 | ❌ 无（顺序写死在 shell + 注释） | ✅ `steps.py:507-514` 有实现，**生产未使用** | ➡️ asel 独有但闲置 |
| 部分重跑 | ⚠️ `RERUN` 字典给人肉命令 | ⚠️ `--only` + 字节相同跳过（**重算式幂等**，非 DAG 依赖跳过） | ⚠️ 两套不同哲学 |
| 幂等 | 靠 SQL upsert | ✅ content-hash（实测 8 份快照全 unchanged 时不重写） | ➡️ asel 独有 |
| 覆盖率门槛 | ❌ 无（只出警示 `review/__init__.py:125-130`） | ✅ **硬阻断** `exit 76`（`asel-coverage-gate.py:44-60`） | ➡️ **asel 独有** |
| 回填 | ❌ 无独立脚本（内嵌 CLI 子命令） | ✅ `backfill_history.py` + `verify_backfill.py` + `supervise_backfill.sh` + `watch_backfill.sh` | ➡️ **asel 独有且更强** |
| CI 门 | ✅ `scripts/ci_local.sh`（103 行，**5 道门**） | ❌ **无 CI 脚本** | ➡️ lkl 独有 |
| 配置 | `lkl/config.py`（264 行）+ `utils/env.py`（**有 env 文件加载器**） | `storage/dbconfig.py` + systemd `EnvironmentFile`（**零加载器**） | ❌ 两套机制 |

### 4.2 ⚠️ 修正：asel 的声明式 DAG 在生产链路上没接线

我原稿写"asel 是声明式 DAG 8 步"，**这是不准确的**。实测：

- `StepRegistry` 全仓只有 **`pipeline.py:39` 一处 import**，而 `pipeline.py:1-4` 自述是
  **P0 离线 demo**：「不连 DB、不联网、不调 Wind」，5 步全是合成 bar（`DEMO_BARS:84-92`）。
- 真生产入口 `production_daily.py:410-432` 是 `for key in keys:` 裸循环，
  `SNAPSHOT_KEYS`（`:62-64`）与 `BUILDERS`（`:327-336`）都是写死的元组/dict
  —— **没有任何 `Step`/`requires`/`produces`/guard/retry 参与**。
- asel 的「部分重跑」= `--only`（`:564-569`）+ 字节相同跳过（`:418-425`），
  安全性来自**每个装配器自带全量重算**（`emotion_pipeline.py:337-351` 自己再调
  `load_streak_records` + `load_series_records`），**靠自包含而非靠 DAG 顺序**。
- `state.py:40-44` 的 `read_records` **只被测试调用**，生产代码零消费者。

> **所以 asel 的依赖感知（`steps.py:507-514`）、retry（`:405-406`）、产物契约（`:517-527`）
> 是闲置能力。** 这反而**降低了**合并难度：接线到生产是"新增"而不是"替换"。

**另一个 asel 内部问题**：`pool`/`board`/`promotion`/`emotion` 四个装配器
**全部委托 `load_streak_records`**（`pool_pipeline.py:186`、`board_pipeline.py:186`、
`promotion_pipeline.py:277`、`emotion_pipeline.py:337`），而它只读 `asel.daily_bar_raw`
（`streak_pipeline.py:96-124`）⇒ **同一份 streak records 一次 run 被重算 5 遍**。

### 4.3 每日步骤清单与映射

| # | lkl `daily.sh` 步骤 | asel 对应 | 判定 |
|---|---|---|---|
| 1 | 快照（日线）`:41` | 编排层**之外**：`backfill_history.py` + `asel-backfill-daily.sh` | ⚠️ 载体不同 |
| 2 | 三池 ZT/DT/ZB `:42` | 无（asel 的"池"是**从 raw 重算**，`pool_pipeline.py:168-205`） | 同概念、异数据源 |
| 3 | 人气榜 `:43` | **无** | ➡️ lkl 独有 |
| 4 | 次新代理字段 `:44` | 无（asel 用 `ref_security_status` SCD2，`streak_pipeline.py:121-124`） | ✅ asel 方式更对 |
| 5 | 衍生层落库 `:45` | 无独立步（内联进快照 `real_pipeline.py:393-448`） | ➡️ lkl 独有（asel 无落库衍生层） |
| 6 | 情绪周期 `:46` | `emotion_pipeline.py:317-365` | 🚨 **口径冲突**（§3.2） |
| 7 | 连板梯队 `:47` | `streak_pipeline.py:75-158` | 🚨 口径独立 |
| 8 | **买卖信号** `:48` | **无** | ➡️ **lkl 独有** |
| 9 | 晋级矩阵 `:49` | `promotion_pipeline.py:259-296` | 🚨 **口径冲突**（§6 C3） |
| 10 | 题材坐标 `:50` | **无** | ➡️ **lkl 独有** |
| 11 | 生态评级 `:51` | **无** | ➡️ **lkl 独有** |
| 12 | 复盘报告 `:52` | 无（asel 出 JSON 快照，不出 md 复盘） | ➡️ **lkl 独有** |
| 13 | 信号结果回填 `:53` | **无** | ➡️ **lkl 独有** |
| 14 | 交易桥导出 `:57` | **无** | ➡️ **lkl 独有** |
| 15 | 回报轮询 30min `:58` | **无** | ➡️ lkl 独有 |
| 16 | LLM 异步点评 `:63-66` | 无 | ➡️ lkl 独有 |
| 17 | 健康推送 `:72` | 无（asel 的 `no_new_data` 日志**不推送**） | ➡️ lkl 独有 |
| — | **无** | `daily` / `health` / `series` / `board` 四快照 | ➡️ asel 独有 |

**映射结论**：asel 只覆盖 lkl 17 步中的 **3 步**（derive/emotion/promotion），其中 2 步口径不同；
lkl 有 **11 步**在 asel 完全无对应。反过来 asel 有 **4 步** lkl 没有。

> **编排层不是"重复实现"，是"两个不同粒度的编排"。**
> 合并的正确做法：**用 asel 的 `StepRegistry` 当引擎，把 lkl 的 11 个独有步骤注册进去。**

### 4.4 调度配置对比与冲突检查

| unit | 触发（Asia/Shanghai） | 实际状态（实测） | 仓库内文件？ |
|---|---|---|---|
| `lkl-health.timer` | 09:30 | **enabled active** | ✅ |
| `lkl-trade.timer` | 12:01 | **disabled inactive** | ✅ |
| `lkl-close.timer` | 15:05 | **disabled inactive** | ✅ |
| `asel-backfill-daily.timer` | 16:00 + 17:00 | enabled（软链在） | ❌ **不在仓库** |
| `lkl-daily.timer` | 17:20 | **enabled active** | ✅ |
| `lkl-strategy.timer` | 17:50 | **enabled active** | ❌ **不在仓库** |
| `asel-production-daily.timer` | 18:10 | enabled（软链在） | ✅ |
| `asel-dash.service` | 常驻 8899 | running | ❌ **不在仓库** |

**「两个 daily timer 抢 15:05」——不存在。** 唯一 15:05 的 `lkl-close.timer` 是 disabled。
实际错峰 09:30 → 16:00/17:00 → 17:20 → 17:50 → 18:10，**无同分钟抢占**。

**但有 3 条真实时序风险**：

| # | 风险 | 证据 |
|---|---|---|
| A | **17:20 lkl-daily 的尾巴可能拖到 18:20** | `daily.sh:58` 的 `trade_poll.sh 30` 是 30 轮 × 60s（`trade_poll.sh:16-19`）；`lkl-daily.service:10` `TimeoutStartSec=3600` ⇒ 最坏 18:20，**越过 asel 18:10 装配点**。asel 的 buffer 只有 10 分钟（`asel-production-daily.timer:9-11` 自述） |
| B | **17:00 asel-backfill 重试可跑到 ~18:40** | `asel-backfill-daily.timer:15-17` 自述；此时 18:10 会读到**半截写入的** `asel.daily_bar_raw`。唯一护栏是覆盖率门槛（`exit 76`） |
| C | **无跨仓互斥** | lkl 锁 `/tmp/lkl_daily.lock`（`daily.sh:19`）；asel 锁 `snapshots/.production_daily.lock`（`asel-production-daily.sh:8`）⇒ 人工同时手跑不会被拦 |

**仓库与线上漂移（合并前必须对账）**：
- `lkl-strategy.{service,timer}` 只在 `/etc/systemd/system/`，仓库里没有，但 `scripts/strategy.sh:2` 却引用它；
- `asel-backfill-daily.{env,service,timer}` 与 `asel-dash.service` **完全不在 asel 仓库**；
- `lkl-daily.service` 与 `lkl-health.service` **缺 `[Install]` 段**（对比 `lkl-close.service:12-13` 有）。

### 4.5 状态与断点续跑对比

**结论：两边都没有"编排级断点续跑"。**

| 维度 | lkl | asel |
|---|---|---|
| 打点覆盖 | `_PIPELINE_STEPS`（`main.py:619-627`）**只映射 9 个步骤**；`signal`/`dragon-env`/`outcome`/`health-push` **不打点** | 8 快照全部打点（JSONL） |
| 打点失败处理 | **三处 `except Exception: pass` 静默**（`main.py:642-643, 650-651, 655-656`，本次实测确认） | 不吞（`steps.py` 先落 sink 再抛） |
| 历史保留 | ❌ `ON CONFLICT(step)` 覆盖，只记最新 | ✅ JSONL append-only |
| 断点续跑 | ❌ 靠人；`scripts/rerun.sh:4-5` 是**硬编码日期的临时脚本** | ❌ `steps.py:332-460` 从拓扑首项无条件开始，**不读历史 state** |
| 真续传在哪 | `ingest_progress(task, code)`（`ingest.py:471-478, 612-613`） | 双键续传 code/date（`backfill_history.py:174-182`）+ `--skip-fresh` + `LEAST/GREATEST` 防污染（`:89-99`） |

### 4.6 CLI / 入口对比

**lkl**：单一 CLI，**30 条子命令**，查表分发（`main.py:574-606` 的 `COMMANDS` dict），`pyproject.toml:20-21` 注册 `lkl = "lkl.main:main"`。

**asel**：**无 console_scripts**（`pyproject.toml` 全文 18 行，只有 `[tool.pytest.ini_options]`），三个独立 `python -m` 入口：

| 入口 | 载体 |
|---|---|
| `python -m asel.orchestration.pipeline` | `pipeline.py:434-456`（P0 demo） |
| `python -m asel.orchestration.production` | `production.py:134-151`（fixture 驱动） |
| `python -m asel.orchestration.production_daily` | `production_daily.py:587-617`（DB 驱动） |

**无同名重复**，但概念层面有 3 组重复：打点查看（lkl `lkl pipeline` vs asel 无消费者）、
dry-run 预览（asel 两个，语义不同）、**离线装配入口二选一**
（`production.py:81-116` 与 `production_daily.py:367-447` 都做"输入→快照落盘"，
`production.py:24` 直接复用 `real_pipeline` —— **同一装配逻辑的两个 CLI 外壳**）。

### 4.7 回填与 CI 门对比

| 能力 | lkl | asel |
|---|---|---|
| 独立回填脚本 | ❌ 无（内嵌 `fetch backfill`/`fetch hotback`/`ladder-backfill`） | ✅ `backfill_history.py`（34KB） |
| 续传 | 按 code（`ingest_progress`） | **双键** code/date + `--skip-fresh` |
| 防污染 | 未确认 | ✅ `LEAST/GREATEST` 只增不减 |
| 崩溃监督 | ❌ 未见 | ✅ `supervise_backfill.sh`（重启 ≤8 次）+ `watch_backfill.sh` |
| 验收/报表 | ❌ 无独立脚本 | ✅ `verify_backfill.py` + `coverage_report.py`（8 sections）+ `migrations/apply.py`（checksum 账本） |
| CI 脚本 | ✅ `ci_local.sh` **5 道门** | ❌ **无** |
| 门 1 测试 | ✅ | ✅（43 个测试文件） |
| 门 2 **E1 函数 ≤50 行** | ✅ `ci_local.sh:37-49` | ❌ **缺失** |
| 门 3 信号链禁 LLM | ✅ `test_arch.py` | 等价物：`test_orchestration.py:43-47`（禁 DB/网络 import） |
| 门 4 ruff E501/F401/F841 | ✅ `ci_local.sh:54-66`，工具缺失即 FAIL | ❌ **无 ruff 配置** |
| 门 5 **收集期零连接** | ✅ `ci_local.sh:68-101` | ❌ **缺失** |
| 数据质量门 | ⚠️ 只出警示，不阻断 | ✅ **硬阻断** `exit 76` |

**可合并性**：lkl 的 `ci_local.sh` 是**代码级**门，asel 的 `asel-coverage-gate.py` 是**数据级**门，
维度不重叠、可直接并存。只需把 `ci_local.sh:41` 的 `pathlib.Path("lkl").rglob("*.py")`
与 `:65` 的 `ruff check lkl/ tests/` 扩展到 `asel/`。

> **⚠️ 预期冲击**：asel 的 43 个测试文件**从未过 ruff/E501 与 E1 门**，
> 首次纳入很可能一次爆出大量超长行与超长函数
> （`production_daily.py:197-233` 的 `load_daily_rows`、`real_pipeline.py:393-448` 的 `run_snapshot` 目测都接近或超 50 行）。

### 4.8 编排层合并判定清单

| 模块 | 判定 | 理由 | 风险 |
|---|---|---|---|
| `asel/orchestration/steps.py`（536 行） | **原样保留，并接线到生产** | 机制完备（依赖感知/retry/产物契约/拓扑校验），零 lkl 依赖 | 接线后 `production_daily.py` 的"自包含重算"模型要改；与 AST 红线冲突 |
| `asel/orchestration/state.py`（44 行） | **原样合并** | 零依赖 | 需新增 SQL sink 才能与 lkl `pipeline_state` 共存 |
| `asel/orchestration/pipeline.py`（459 行，demo） | ✅ **可删 / 降级为测试 fixture** | 自述不连 DB/不联网，5 步全是合成 bar，与生产零交集 | 若删需搬走 `provenance()`（`:334-339`），`production_daily.py:43` 依赖它 |
| `asel/orchestration/production.py`（154 行） | ⚠️ **择一（与 `production_daily.py` 二选一）** | 功能是后者的真子集（3 份 vs 8 份，fixture vs DB） | fixture 模式是离线验收路径，删前确认无测试依赖 |
| `asel/orchestration/real_pipeline.py` + 6 个 `*_pipeline.py`（2794 行） | **保留，但明确"这是业务层不是编排层"** | 全是 `assemble_*_from_db` 装配器 | 合并后同一概念有两套算法；5 个装配器重复调 `load_streak_records`，应收敛为一次 |
| `lkl/main.py`（680 行，30 命令） | **原样保留为唯一 CLI 门面** | 查表分发、零业务逻辑；asel 侧无 `[project.scripts]`，无冲突 | 需为 asel 的 `production_daily` 新增子命令并接入 `_PIPELINE_STEPS` |
| `lkl/services/pipeline.py` | ⚠️ **择一（建议采 asel 的 `state.py` 语义，重写 sink）** | lkl 按 `step` 覆盖、无 run_id、无历史；asel 有 run_id+kind+attempts+artifacts | 换语义要改 `schema.py:303` 表结构 + 可能的 dashboard 消费者 |
| `lkl/scripts/daily.sh` + `close.sh` + `trade.sh` + `trade_poll.sh` + `strategy.sh` + `health-push.sh` | **原样保留（lkl 独有链）** | asel 完全没有交易桥/信号/复盘/题材/生态/告警推送 | 需与 asel 统一日志/告警出口（lkl 有 webhook，asel 只有文件日志） |
| `asel/scripts/asel-production-daily.sh` | **保留，但应改为显式依赖 lkl-daily** | `:6` 默认 python 跨仓硬编码；`:26-41` 覆盖率门是唯一护栏 | 10 分钟 buffer 可能不足（§4.4 风险 A）。**建议 `After=lkl-daily.service` 而非靠时间差** |
| `asel/scripts/asel-backfill-daily.sh` | **保留，但与 lkl 的 `fetch snapshot` 二选一** | 工程成熟度高于 lkl 的 `daily.sh:41-44` | 两者**抓同一天同一批股票**，数据源都含新浪 ⇒ **双份配额消耗** |
| `asel/scripts/asel-coverage-gate.py` + `coverage_report.py` + `verify_backfill.py` + `migrations/apply.py` | **原样合并（净增资产）** | lkl 侧无任何等价物 | `apply.py:16-18` 的 checksum 账本与 lkl `schema.init_db()`（`main.py:568-570`）的 `IF NOT EXISTS` **语义冲突**，需统一 DDL 管理 |
| `lkl/scripts/ci_local.sh`（5 门） | **保留并扩展为两包共用** | 门 2/门 5 在 asel 侧完全缺失 | 首次纳入预期大量违规 |
| `lkl/config.py` 的 DB 段 vs `asel/storage/dbconfig.py` | 🔀 **收敛为唯一实现** | 两份同口径解析 | `config.py` 是 lkl 唯一常量出口，不能整文件替换 |
| 环境变量机制 | 🔀 **择一：保留 `env.load_env()`，把 `ASEL_*` 并入** | lkl 有文件加载器（真实 env 优先、幂等）；asel 零加载器 | 两套前缀需统一命名 |
| `asel/scripts/systemd/*` | ⚠️ **补齐仓库内缺失的 3 个 unit** | `asel-backfill-daily.*`/`asel-dash.service` 不在仓库 | 仓库与线上漂移，合并前须先对账 |

---

## 5. 展示层

### 5.1 两边的展示层地图

| 侧 | 组成 | 规模 |
|---|---|---|
| lkl | `dashboard/` 12 个 `.py`（738 行）+ 单文件 `index.html`（328 行）+ unit | **1066 行** |
| lkl | 报告/推送面 `services/review/`（771+284）+ `notify.py`（55）+ `alerts.py`（79） | 1189 行 |
| asel | `asel/presentation/` 21 个 `.py` | **5338 行** |
| asel | 前端资产 `static/{tokens.css,app.css,app.js}` | 432 行 |

**结构差异一句话**：lkl 是「后端 JSON API + 浏览器端 JS 渲染」；asel 是「服务端 Python 渲染 HTML + 只读快照」。
**两侧不存在可逐函数对齐的同层模块。**

### 5.2 Web 服务与路由对比

| 项 | lkl | asel |
|---|---|---|
| HTTP 栈 | 纯 stdlib `ThreadingHTTPServer` + `BaseHTTPRequestHandler` | 同 |
| 第三方 web 依赖 | **无** | **无** |
| 端口 | `127.0.0.1:8098` | `127.0.0.1:8899`（**不冲突**，实测同时 LISTEN） |
| 路由 | `do_GET` 里 if/elif 字符串比较，**16 条** | 纯函数 `route_get()` 返回 `(status,ctype,body)`，**11 条，可直测** |
| HTTP 方法 | 只 `do_GET`（POST → 501） | `GET/HEAD` 放行，`POST/PUT/DELETE` **显式 405 + Allow** |
| nginx 挂载 | `/stock-legacy/` → 8098 | `/stock/` → 8899（`--base-path /stock`） |
| 认证 | 应用层无；**线上 nginx 也无** | 应用层无；**线上 nginx 也无** |
| 静态资源 | 无独立目录（HTML/CSS/JS 全内联） | 白名单 `STATIC_FILES`，URL 不参与路径拼接 |

**路由清单对比（lkl 16 条 vs asel 11 条）**：

| 路由 | lkl | asel |
|---|---|---|
| `/` | 首页（整份 index.html） | 今日概览 |
| `/strategy` · `/api/strategy` | ✅ 策略观察台 | ❌ |
| `/api/dates` `/api/latest` `/api/report` `/api/download` | ✅ 日报读取/下载 | ❌ |
| `/api/trade-files` `/api/trade-download` `/api/trade-status` `/api/trade-board` | ✅ 交易交换桥 | ❌ |
| `/api/live` | ✅ 实时持仓/卖出建议 | ❌ |
| `/api/signal-detail` `/api/evidence` `/api/alerts` `/api/signal-calendar` | ✅ 信号证据/钻取/告警/日历 | ❌ |
| `/series` `/health` `/ladder` `/pool` `/board` `/promotion` `/emotion` `/research` `/stock/<code>` `/static/*` | ❌ | ✅ 10 个服务端渲染页 + 静态白名单 |

**踩坑 #9（lkl 侧死代码）**：lkl 的 `/api/trade-board`、`/api/signal-detail`、`/api/evidence`、`/api/alerts`
**四个端点前端零调用**；且 `evidence.theme_evidence`（`evidence.py:14`）、
`signaldetail.elimination`（`signaldetail.py:40`）两个函数**连路由都没有**。

### 5.3 面板级映射表

| 面板 | lkl | asel | 判定 |
|---|---|---|---|
| 顶部指标卡组 | 8 卡（`index.html:91-107`） | 3 可靠指标（明确把 max_height/promotion_rate 列为未实现） | ⚠️ 重复不等价 |
| 情绪面板 | ① 情绪面板（买点窗口+原因） | `/emotion` 情绪概览+指标卡+上游输入卡 | ⚠️ 重复（**asel 更完整**，但缺 buy_window） |
| 梯队全景 | ② 梯队全景 | `/ladder` 梯队概览+分层卡+质量异常区 | ⚠️ 重复（asel 更细） |
| 题材结构 | ③ 题材结构（概念题材） | `/board`（**交易板块口径，不是题材**） | ➡️ **题材实质仅 lkl** |
| 淘汰赛况 | ④ 淘汰赛况 | `/promotion` 晋级明细 | ✅ 重复 |
| 晋级矩阵 | ⑤ 晋级矩阵（层/双口径/divergence） | `/promotion` 晋级概览+区间卡+缺口 | ⚠️ 重复（asel 更完整，但缺分层与双口径） |
| 明日参考 | ⑥ 明日参考 | ❌ | ➡️ lkl 独有 |
| 龙空龙环境 | ⑦ 龙空龙环境（G/B 三态标签） | ❌ | ➡️ **lkl 独有** |
| 持仓/信号 | ✅ 实时 sells+positions | ❌ | ➡️ lkl 独有 |
| 交易交换文件 + 午间徽章 | ✅ | ❌ | ➡️ lkl 独有 |
| 信号日历 | ✅ 近 3 月命中率 | ❌ | ➡️ lkl 独有 |
| 策略观察台 | ✅ 整页 | ❌ | ➡️ lkl 独有 |
| 时间序列/趋势 | ❌（只有两点对比） | ✅ 内联 SVG 折线，**断点分段绘制，绝不连 0** | ➡️ **asel 独有** |
| 数据质量页 | ❌（只在 md 第⑩段） | ✅ `/health`（run_id/git_sha/nulls） | ➡️ asel 独有 |
| 逐票复盘页 | ❌ | ✅ `/stock/<code>` 7 卡 | ➡️ asel 独有 |
| 涨停池/触板池明细 | ❌ | ✅ `/pool` 两卡 | ➡️ asel 独有 |
| 研究页 | ❌ | ✅ `/research` | ➡️ asel 独有 |
| 主题切换按钮 | ❌（只有 `prefers-color-scheme`） | ✅ 手动切换 + 首帧引导 | ➡️ asel 独有 |

### 5.4 数据获取方式 —— 合并的关键冲突（实测）

| 侧 | 模式 | 证据 |
|---|---|---|
| lkl | **混合**：日报页读文件，其余 6 模块直连 DB / import 业务服务 | `dashboard/{evidence,live,signal_calendar,signaldetail,tradeboard,strategyview}.py` + `handler.py:67` = **11 处** `from lkl.utils import db` 或 `from lkl.services import …` |
| asel | **纯快照，零 DB** | `asel/presentation/` grep `psycopg\|asel.storage\|asel.algorithms\|asel.orchestration\|asel.semantics` = **0 命中**（实测） |

**这是合并唯一需要"架构裁决"的冲突点**：
- 统一到快照模式 → lkl 的 `/api/live`（实时持仓/卖出建议）、`/api/trade-status`、`/api/trade-files`、
  `/api/signal-calendar` 这 4 类**本质实时/文件态**能力无快照可读。
- 统一到直连模式 → 破坏 asel 的"只读、可离线复现、可 golden 对拍"纪律（asel 有 12 个 dashboard 测试文件依赖它）。

**建议**：保留 asel 的"页面只读快照"纪律，把 lkl 的 4 类实时能力**降级为独立的私有 API 路由**
（明确标注"实时态，不参与快照契约"），不强行塞进快照模型。

### 5.5 视图模型与序列化契约对比

| 维度 | lkl | asel |
|---|---|---|
| 独立 VM 层 | ❌ **无** | ✅ **10 个 `vm_*.py` 共 2591 行**（`vm_models` 389 / `vm_research` 845 / `vm_stock` 423 / …） |
| lkl 等价物 | 后端 `review.collect()` 取齐 + 前端 9 个 JS 渲染函数 | 全在 Python |
| JSON 契约 | 有但是**日报副产品**（`schema="lkl/daily@2"`，23 键） | **显式契约**：canonical JSON + sha256 + manifest（Decimal→str 不失真，null 是未知禁止转 0） |
| HTTP 序列化 | `httpbase.send_json` 用 `default=str` —— 与 store 规范化编码**不是同一套规则** | 无 JSON API |
| 跨层 payload 助手 | ❌ 无（各模块各写 `_plain`/`_n`） | ✅ `asel/_payload.py`（被展示层 import） |

**结论**：asel 的 vm 层**不是**对"lkl 直连 DB"的重构，而是对"lkl 日报 JSON + 浏览器 JS 渲染"这一组合的 Python 化重构。
**不存在可原样搬移的 vm 代码。**

### 5.6 报告与推送能力对比

| 能力 | lkl | asel |
|---|---|---|
| Markdown 日报 | ✅ ⓪+十段+⑫，段级容错 + 必需段失败拒绝发布 | ❌ |
| 日报 JSON 快照 | ✅ `lkl/daily@2` | ❌（但有全量事实快照） |
| webhook 推送 | ✅ 三通道识别（企业微信/钉钉/generic）+ **正则剥离持仓** | ❌ |
| 告警闭环 | ✅ 分级 + `(source,detail)` 幂等去重 + ack + 未确认推送 | ❌ |
| `reports/` 性质 | **是产物目录，且是展示层数据源** | **是文档产物，展示层零引用** |

**asel 展示层没有报告渲染与推送的任何等价物 → 整体属「仅 lkl」。**

### 5.7 部署与端口冲突检查

| 项 | lkl | asel | 判定 |
|---|---|---|---|
| systemd unit | 仓库内 `dashboard/lkl-dash.service`（与线上逐字一致） | 仓库**没有** dashboard unit，线上 `asel-dash.service` 是手工部署 | ⚠️ asel 缺 unit 版本管理 |
| 端口 | 8098 | 8899 | ❌ **不冲突** |
| nginx 路径 | `/stock-legacy/` → 8098 | `/stock/` → 8899 | ❌ 不冲突 |
| 认证 | 应用层无 + 线上无 | 应用层无 + 线上无 | ⚠️ **两侧均无认证**（安全债） |
| 写方法防护 | 应用层无（POST→501）；仓库片段有 `limit_except GET` 但**线上无** | 应用层 405 + Allow | ⚠️ lkl 需补 |
| 跨仓耦合 | 无（自带 .venv） | **线上 unit 用 lkl 的 venv** | 合并后应统一解释器 |
| 仓库内 nginx 片段 | `dashboard/nginx-dash.conf.snippet` 写 `/dash/` + auth —— **与线上 `/stock-legacy/` 不符，已过期** | 无片段 | ⚠️ 清理 |

### 5.8 展示层合并判定清单

| 模块 | 判定 | 理由 | 风险 |
|---|---|---|---|
| `asel/presentation/` 全套 | **保留为展示层主干** | 服务端渲染 + 只读快照 + base_path + 405 + 静态白名单 + 逃逸/空值纪律，被 12 个测试锁定 | 迁移期需维持 lkl 现有 URL 不断链 |
| `dashboard/httpbase.py` | ✅ **删（真重复）** | 与 `asel/presentation/server.py` 的 `_send` 同职责，且 `default=str` 序列化更弱 | 无 |
| `dashboard/loader.py` | 🔀 **合并** | 与 `view_loaders.py` 概念对应；lkl 版多一条 **ISO 正则防路径注入**（`loader.py:25-35`），可反向补进 asel | 低 |
| `dashboard/evidence.py` `theme_evidence` | ✅ **删（死代码）** | 全仓 grep 只有定义，无路由无前端 | 无 |
| `dashboard/signaldetail.py` `elimination` | ✅ **删（死代码）** | 同上；语义已被 asel `/promotion` 覆盖 | 无 |
| 4 个孤儿端点 | ⚠️ **待裁决** | 前端零调用 | 删前确认仓库外无消费者（**未确认**） |
| `dashboard/{tradeboard,tradestatus,tradefiles,live,signal_calendar,strategyview}.py` | **仅 lkl，保留（隔离为可选模块）** | 多用户交易桥/七态看板/实时持仓/信号日历/策略观察台；asel 零等价物 | 与 asel 只读纪律冲突 → 降级为私有 API；`tradefiles.py:37-45` 路径穿越防护须保留 |
| `dashboard/index.html` | ⚠️ **面板内容迁入 asel 视图，文件废弃** | 9 个渲染函数中 5 个与 asel 重叠；6 个面板为 lkl 独有 | 需逐面板确认归属，否则丢功能 |
| lkl 两份内联 CSS 调色板 | ✅ **删（真重复）** | `index.html:9-26` + `strategyview.py:177-194` 与 `static/tokens.css:19-35` 同值 | 需统一为 asel 的 `[data-theme]` 方案 |
| lkl 表格排序 JS | ✅ **删（真重复）** | `index.html:122-155` vs asel `app.js:57-` | 无 |
| `lkl/services/review/`（1055 行） | **仅 lkl，保留** | md+json+DB 四出口、段级容错、必需段拒绝发布 | 与展示层接口是 `reports/*.json` |
| `lkl/services/notify.py` | **仅 lkl，保留** | 三通道 webhook + 持仓脱敏 | 依赖 `requests` **未进 `pyproject.toml`**（函数内 import，隐藏依赖，踩坑 #10） |
| `lkl/services/alerts.py` | **仅 lkl，保留** | 告警分级/幂等/ack | `/api/alerts` 无前端 |
| `asel/snapshots/store.py` | **保留（写侧唯一入口）** | 原子写 + 规范化编码 + manifest sha256 | 无 |
| `asel/presentation/static/*` | **保留为唯一前端资产** | 主题切换 + 排序/过滤 + token 化配色 | 无 |

---

## 6. 合并必须先裁决的 6 处口径冲突

| # | 冲突 | lkl | asel | 影响面 | 建议 |
|---|---|---|---|---|---|
| **C1** | **连板口径** | `cont_days`：停牌断档**不打断**（`derive.py:84-95`） | `streak_facts`：缺口 → `None` 断开（`streak_facts.py:241-279`） | `derived_bar` 45 行虚增（0.103%）；`ladder_day`/`promotion_day`/`market_stat.max_limit_days`/`dragon_env` 全部下游 | **采 asel 口径**（正确），但需重算 lkl 历史并公告"最高板数字会变" |
| **C2** | **市场状态口径** | 4 相状态机 + `buy_window` + `force_liquidate` | 3 档阈值（`cold_max=20` vs lkl `ICE_COUNT_MAX=40`） | 两套"市场状态"会同时对外可见 | **lkl 状态机为决策权威，asel `state` 降级改名为"涨停热度档"** |
| **C3** | **晋级率口径** | 5 层 × 名义/换手双口径 + `divergence` + `fail_perf` | 逐票事实 + 单一总率 + 缺口上下界 + `undetermined` | `dragon_env` G3/B2 依赖 `divergence`/`fail_perf`；层标签 `1->2` 是报告 key | **合并为一份**：分层+双口径（lkl）+ 缺口上下界+undetermined（asel） |
| **C4** | **行情表单位与 `pre_close`** | `volume`=手、`turnover_rate`=百分数、**存 `pre_close`** | `volume_shares`=股、`turnover_ratio`=比值、**`LAG(close)` 重算** | 全库派生计算；除权日漏判涨停（实测 339 行） | **冻结单位 + 补 `pre_close` 列**（或保留 lkl 的校验），**否则合并即静默错 100 倍** |
| **C5** | **ST 处理** | **整体剔除**（`derive.py:58`） | 建了 5%/20% 规则但**生产硬编码 `normal`**（`production_daily.py:224`） | `limit_up_count` 两侧不可比 | 采 asel 的规则化 ST 处理，**并修掉 `variant="normal"` 硬编码** |
| **C6** | **展示层数据源范式** | 直连 DB / import 业务服务（11 处） | 只读快照（0 处 DB） | 4 类实时能力在快照模式下无解 | **页面走快照，实时能力降级为私有 API**（§5.4） |

### C3 实测证据（2026-09-24 同一天）

| 口径 | 分子 | 分母 | 总率 |
|---|---|---|---|
| lkl（5 层相加：`1->2` 9 + `2->3` 3 + `3->4` 1） | **13** | 35 + 7 + 2 = **44** | **29.55%** |
| asel（`promotion_count` / `previous_limit_up_count`） | **13** | **51** | **25.49%** |

**分子完全相同（13），分母差 7 只**（lkl 剔除次新 / 限主板 / 缺北交所日线源）。
两边的"晋级率"看起来接近（29.55% vs 25.49%），实则**在测不同总体**。

lkl 的分层明细（`public.promotion_day` 实测）：

| 层 | 分母 | 名义晋级 | 换手晋级 | rate_nominal | rate_exchange | divergence | fail_perf |
|---|---|---|---|---|---|---|---|
| `1->2` | 35 | 9 | 8 | 0.2571 | 0.2286 | 0.0285 | −2.1969 |
| `2->3` | 7 | 3 | 1 | 0.4286 | 0.1429 | **0.2857** | −8.5351 |
| `3->4` | 2 | 1 | 1 | `None` | `None` | `None` | `None` |
| `4->5` / `5+->6+` | 0 | 0 | 0 | `None` | `None` | `None` | `None` |

注意 `2->3` 的 divergence = **0.2857**（名义 42.86% vs 换手 14.29%，差 28.6 个百分点）——
这个背离度是 `dragon_env` B2 判据的输入，**asel 侧没有任何字段能产出它**。

---

## 7. 踩坑记录（本次实测发现）

1. **文档与实况不符**：`asel-production-daily.timer:6-8` 声称 `lkl-close 写入 asel.daily_bar_raw`，
   但 `lkl-close.timer` **disabled**，且 lkl 全仓 `scripts/` 下 grep `asel` **0 命中**。
2. **asel 的增量设计大半是空壳 + 未接线**：15 张表 0 行；`asel/sources/` 三个模块**零生产 import**；
   `storage/differential.py` 只被测试调用；`orchestration/steps.py`（536 行 DAG）**生产链路未接线**。
3. **lkl 连板数不感知停牌（已实测复现）**：`derive.py:_CONT_SQL` 的分段键只认"非涨停行"，
   停牌整行缺失时两段被拼成一段。实测 **45 / 43,860 行（0.103%）虚增**，集中在 2 连板档。
   已用独立源验证断档期确为真停牌：`600984`（2026-08-10→08-25）、`300333`（07-29→08-13）、`603137`（06-15→07-01）
   在 **asel.daily_bar_raw 与 lkl.public.daily_bar 中均为 0 行**。
   lkl 自己在 `accelerate.py:88-104` 的 V3 注释里承认过这个坑，但**只在 `_top_streak` 里修了，`cont_days` 没修**。
4. **asel 回测 `horizon` 参数从未参与计算（真 bug）**：`research/backtest.py:82,88-89,99` ——
   `horizon` 被校验、被写入输出 `"horizon": horizon`，但 `_run_rows`（`:95-98`）**只接收 setup/min_quality**，
   内部固定取 `row["next_close"]`。⇒ `horizon=5` 与 `horizon=1` 返回**相同结果**却报 `"horizon": 5`。
5. **单位口径不同（静默错 100 倍）**：见 §2.4 —— volume 手/股（实测比值恰好 100.00，5209/5209 行）、
   turnover 百分数/比值（0.01）。**只看 OHLC 会误判"两表等价"。**
6. **`pre_close` 用 `LAG(close)` 在除权日必错**：实测 339/192,593 行（0.176%）不一致，最大偏差 15.36 元 ⇒ 漏判涨停。
7. **asel 用自己的红线测试逼出了 importlib 绕过**：`tests/test_orchestration.py:43-46` 用**正则**禁止
   编排层出现 `import psycopg` 字样；`production_daily.py:576-580` 于是改用
   `importlib.import_module("psycopg")` —— 字面合规、实质仍连库。
   **红线靠源码字符串匹配是可以绕过的**，建议改为 AST import 图检查。
8. **asel 违反了自己的跨仓红线**：asel 有 7 处测试断言"不得出现 lkl/longkonglong"
   （`test_semantics.py:296-297`、`test_algorithms.py:387-388`、`test_migrations.py:234`、
   `test_orchestration.py:688`、`test_pool_pipeline.py:461`、`test_production_daily.py:387`、`test_streak_pipeline.py:320`），
   但 `supervise_backfill.sh:72-73` 与 `watch_backfill.sh:29-33` 实际 `from lkl.utils import db`。**红线被真实违反，测试没覆盖 shell。**
9. **第 4 处未申报的移植**：asel 抄了 lkl HEAD（`20ba9a5`/`724c6ed`/`eb53a46`，2026-09-24~25），
   而非 `PORTED.md` 声称的 `98588aa`。见 §2.7。
10. **同一交易日三套数字**：2026-09-24 涨停家数 lkl `market_stat` = **50**，asel 快照 = **51**，
    我按板块比例朴素重算 = **52**。最高板 lkl = **4**（可交易 4），asel = **5**。
    根因（部分）：lkl 情绪计数剔除次新 + 缺北交所日线源（`config.py:53` 自认）+ 整体剔除 ST；asel 含北交所但 ST 规则空转。
    **完整根因需专项核实。**
11. **asel 一次 run 重算 streak records 5 遍**：`pool`/`board`/`promotion`/`emotion` 四个装配器
    全部委托 `load_streak_records`（`pool_pipeline.py:186`、`board_pipeline.py:186`、
    `promotion_pipeline.py:277`、`emotion_pipeline.py:337`）。
12. **lkl 展示层死代码**：4 个孤儿端点（前端零调用）+ 2 个无路由函数。
13. **lkl 打点失败被静默**：`main.py:642-643, 650-651, 655-656` 三处 `except Exception: pass`（实测确认）。
14. **lkl 隐藏依赖**：`services/notify.py:49` 函数内 `import requests`，但 `pyproject.toml` 未声明。
15. **asel 未声明依赖**：`pyproject.toml` **无任何 `dependencies`**，实际靠 lkl 的 venv 与 `PYTHONPATH`。
    asel 的 `asel/` 包本身是**纯 stdlib（零第三方 import，实测确认）** —— 设计干净，声明缺失。
16. **systemd 仓库与线上漂移**：`lkl-strategy.*` 只在 `/etc`；`asel-backfill-daily.*`/`asel-dash.service`
    **完全不在 asel 仓库**；`lkl-daily.service`/`lkl-health.service` 缺 `[Install]` 段。
17. **两侧展示层均无认证**：`/stock/`（asel）与 `/stock-legacy/`（lkl）在 nginx 上都**没有 `auth_basic`**。
18. **asel 无 CI 脚本**：lkl 的 `ci_local.sh` 门 2（E1 ≤50 行）与门 5（收集期零连接）在 asel 侧完全缺失，
    asel 的 43 个测试文件**从未过 ruff/E501**。

---

## 8. 合并方案建议

### 8.1 方向：以 asel 为主干，把 lkl 的决策层"注册"进来

**理由**：
- 数据层 asel 是严格超集（4064 天 vs 691 天，OHLC 逐行零差异）；
- 算法层 asel 是纯函数 + 质量语义（实测 0 处 SQL），可测性远好于 lkl 的焊 SQL（10 个模块共 83 处）；
- 编排层 asel 有 DAG 引擎 + 覆盖率硬阻断 + 回填工具链，lkl 是 shell 硬编码；
- 展示层 asel 零 DB 依赖 + 12 个测试锁定；
- **但 lkl 有 11 项决策能力 + 661 天历史事实表，asel 一项都没有** —— 这部分必须整体搬过来，不能重写。

### 8.2 分阶段（每阶段独立可验收）

| 阶段 | 目标 | 交付 | 风险 |
|---|---|---|---|
| **S1 冻结与对账** | C1~C5 写成 ADR 逐个拍板；拆解 2026-09-24 的 50/51/52 三套数字；**冻结单位口径**；systemd unit 仓库/线上对账 | 5 份 ADR + 口径差异根因报告 + unit 清单 | 无（纯文档） |
| **S2 数据层收口** | 补 `pre_close` 列；单位统一；`asel.daily_bar_raw` 定为权威；`public.daily_bar` 停写归档；`derived_bar` 加停牌断档修正后重算 | 迁移脚本 + 差异报告（明示哪些 `cont_days` 会变） | **高**：重算会改 lkl 历史数字 |
| **S3 算法层合并** | 删 `streaks.py` 重复实现；`limits.py` 补 `is_exchange`/`amplitude`/`is_bomb`；`promotion` 合成一份；`emotion` 状态机与 3 档分类消歧；**删第 4 处未申报移植** | 合并后的 `asel/algorithms/` + 差分测试 | **高**：C2/C3 会影响 `dragon_env` |
| **S4 编排层合并** | lkl 的 17 步改写为 `StepRegistry` 注册项（**首次接线**）；asel 的 18:10 改 `After=lkl-daily.service`；`load_streak_records` 收敛为一次；统一 systemd 层级 | 单套编排 + 单套 timer | **中**：`trade_poll` 长驻轮询不适合 DAG |
| **S5 展示层合并** | asel 展示层为主干；lkl 的 7 个独有面板迁入；实时能力降级为私有 API；统一静态资产 | 单套 dashboard + 旧 URL 301 | **中**：需逐面板确认归属 |
| **S6 清理与加固** | 删 4 孤儿端点 + 2 死函数 + 2 份重复 CSS + 重复排序 JS + `httpbase.py` + `pipeline.py`(demo)；补 nginx 认证；`ci_local.sh` 扩到 asel（**预期爆大量 E1/E501**）；修踩坑 #4/#7/#8/#11 | 清理 commit + CI 双包化 | **中**：E1 门首次纳入冲击大 |

### 8.3 需要你拍板的 7 个决策点

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **D1** | 合并后的仓库形态 | a) 以 asel 为主干，lkl 保留 `main` 分支作来源 b) 新建仓库 c) 在 lkl 开分支 | **(a)**。asel 已是 `/stock/` 当前版本，数据/编排/展示三层范式更优 |
| **D2** | C2 市场状态口径 | a) lkl 4 相为决策权威，asel 3 档改名 b) 反之 c) 并存 | **(a)**。asel 的 3 档没有 `buy_window`，不构成决策能力 |
| **D3** | C1 连板口径 | a) 采 asel（停牌断开，正确但改历史） b) 保留 lkl（不改历史，留 0.103% 错误） | **(a)**，但必须公告"最高板/连板数字会变" |
| **D4** | C4 单位与 `pre_close` | a) 统一到 asel 单位 + 补 `pre_close` 列 b) 统一到 lkl 单位 c) 各存各的、读取时转换 | **(a)**。`pre_close` 必须存列（`LAG` 在除权日错） |
| **D5** | asel 的 15 张空壳表 | a) 合并后优先落地前 3 张（制度规则/ST SCD2/交易日历） b) 全部暂缓 c) 全部落地 | **(a)**。这 3 张是 C1/C5 的前置；研究预注册/配额账本暂缓 |
| **D6** | 展示层实时能力（4 类） | a) 降级为私有 API b) 强行塞进快照 c) 废弃 | **(a)**。废弃会丢实时持仓/卖出建议 |
| **D7** | 是否保留 lkl 的 LLM 策略观察层 | a) 保留但隔离 b) 废弃 | **(a)**。asel 的红线是"LLM 不参与判定"，不是"不能有 LLM" |

### 8.4 工作量粗估

| 层 | 涉及代码量 | 判定分布 |
|---|---|---|
| 数据层 | lkl 1200 行 + asel 1600 行 | ✅ 删 1 份 · 🔀 3 项 · ⚠️ 5 项 |
| 算法层 | lkl 4900 行（83 处 SQL）+ asel 2900 行（0 处 SQL） | ✅ 删 1 份 · 🔀 6 项 · ⚠️ 7 项 · ➡️ 11+9 项独有 |
| 编排层 | lkl 1422 行 + asel 4667 行（编排骨架 1873 + 业务装配 2794） | ✅ 删 1 项 · 🔀 3 项 · ⚠️ 5 项 |
| 展示层 | lkl 1066+1189 行 + asel 5338 行 | ✅ 删 4 项 · 🔀 2 项 · ⚠️ 3 项 |
| **测试** | lkl 4180 行（32 文件）+ asel 14919 行（43 文件） | 两套都要保留（覆盖不同层） |

**真重复可删的代码不到 800 行**（`streaks.py`、`daily_bar`、`httpbase.py`、2 份 CSS、排序 JS、
4 个孤儿端点、2 个死函数、`pipeline.py` demo、第 4 处未申报移植）。
**剩下 2 万多行是互补的，不是重复的。**

---

## 9. 待核实项（本次未确认，不含编造）

1. **2026-09-24 涨停家数 50/51/52 的完整根因拆解** —— 已确认部分原因（次新剔除、北交所、ST），
   未逐票对账到"差的那 1~2 只具体是谁"。
2. **lkl 的 4 个孤儿端点是否有仓库外消费者** —— 未检查仓库外的脚本/外部调用方。
3. **lkl 的 `dashboard/` 是否读 `pipeline_state` 表** —— 未逐一读 `dashboard/handler.py`。
4. **asel 的 `asel-backfill-daily.timer` 实际下次触发时间** —— 本会话 `systemctl --user` 无 DBus，只确认软链存在。
5. **`asel.presentation` 的 `vm_*.py` 2591 行是否逐行通读过** —— 按模块 + 关键词核查，未逐行通读 21 个文件。
6. **lkl 的 `theme.py` 依赖的 Wind CLI / node 路径当前是否可用** —— 未实测调用。
7. **asel 的 `ref_limit_rule` seed 中 `source='pending_verification'` 的行** —— 未核实 `source_url` 是否已补。
8. **lkl `services/ingest.py` 是否有 `LEAST/GREATEST` 类防污染语义** —— 未通读全文。
9. **asel `staging/` 目录内容与 tests 的关系** —— 未展开。

---

## 附：证据文件

- 展示层完整取证：`/home/ubuntu/work/evidence/presentation-overlap-report.md`
- asel 架构方案（其自身的重写设计）：`asel/reports/architecture-proposal-2026-09-18.md`
- asel 移植溯源（**已发现与实际不符**）：`asel/docs/PORTED.md`
- lkl 与《板学计划》重合度核实（历史参考）：`lkl/reports/plan-overlap-review-2026-09-18.md`
