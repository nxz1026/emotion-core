# 更新日志

所有 notable 变更均以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

_空占位：上线之后的变更追加在这里。_

## 2026-10-07 · （第二段：systemd 对齐 + close 撤销 + doctor 可失败 + 文档以实测为准）

### systemd：仓 ↔ `/etc/systemd/system` 双向对齐（两边现在都是 11 个单元）

| 单元 | 修之前 | 修之后 |
|---|---|---|
| `dash.service` | 只在生产 | **按生产原样收进仓库** |
| `strategy.{service,timer}` | 只在生产 | **按生产原样收进仓库** |
| `close.{service,timer}` | 只在仓库，**从未装到生产** | 🚫 **已撤销** |
| 5 个 `.timer` 的 `Asia/Shanghai` | 由 `.timer.d/10-timezone.conf` 提供 | **直接写在 `.timer` 本体**；`/etc` 与仓内的 `.timer.d/` 均已删除 |

- **备份**：`~/ec-systemd-backup-20261007-065933.tar.gz`（11 个单元 + 4 个 drop-in 目录，删改前打）。
- **时区真相源合一**是这次的重点。两份都存在时，改了本体忘了 drop-in（或反过来），
  漂移的表现是**排期静默偏 8 小时、没有任何报错**。同步后实测 `systemctl list-timers`
  的 NEXT 时刻与同步前一致（±20 秒内，来自新加的 `RandomizedDelaySec` 重掷），
  **没有出现 8 小时偏移**；`emotion-core-dash` 与 `cpt-dashboard` 全程 active、:8098 HTTP 200。
- `daily.timer` 新增 `RandomizedDelaySec=120`（pool 60 / report 60 / watchdog 120 **原本就有**；
  `strategy.timer` 没有，是刻意不加——17:50 紧跟 daily，不需要抖动）。

### 撤销 close 重复调度

`emotion-core-close.{service,timer}`（15:05）执行的是**完整 13 步 daily**，与 17:20 的
`emotion-core-daily.timer` **全量重复**；而 `docs/08` §4.2 / `docs/13` 所说的「收盘对账」
模块 `orchestration/close.py` **从未存在**。已 `git rm`（历史可追回），并在
`docs/08`、`docs/10`、`docs/13`、`docs/15`、README 五处标注撤销原因。

### doctor「日线双源」从永不失败改为可失败

`_check_provider_consistency` 原先四个出口全 `True`，偏差只 `log.warning`
⇒ `doctor.run()` 里这一行**永远显示通过**。这是照搬 lkl 时一起搬过来的**失效检查**，
不是本仓的设计。现在：查出偏差 → `ok=False`，note 点名前 5 只并保留总数；
「没得查」（链关闭 / 未注入 / 无样本 / 依赖不可用）仍返回 `True`。
已标为**唯一一处有意偏离 lkl**，并改掉模块头「未新增别的分支」的表述。3 条新测试 + 变异验证。

### 文档矛盾一律以实测为准

- `docs/15` 两处同名「部署状态」直接打架（`/cpt/` 一处 ✅ 一处 ❌ 已移除；
  `/dashboard/` 上游 8099 vs 8098）→ **合并为一份**并按 2026-10-07 实测重写，
  另附 nginx / systemd / 数据现状三张实测表。
- `schema.py` 「27 张表」→ **30**（`len(DDL)`）；原表清单只列了 23 个，已补全。
- `docs/05` §1.3 标题「空壳（15 张）」但表内 13 行、正文结论也写 13 → 统一按 13，
  并把「其余 13 张」更正为「其余 11 张（5 落地 + 6 暂缓）」。
- **澄清一个不是 bug 的矛盾**：`docs/05` 通篇的「41 张表」是 **`longkonglong` 库**
  （lkl 时代）口径，与 emotion-core 用的 `emotion_core` 库不是同一个库，已加互不校正说明。
  实测 `emotion_core.public` 32 张 = DDL 30 − 未建 6 + CPT 共用的 8 张 `cpt_*`。
- `README` 阶段 8 `⏳ 进行中` → `✅ 已完成`（按实测重写）。

### 更正我自己写下的一处错误陈述

README 曾写「仓内没有任何单元处于 enabled/active，emotion-core 的共享表没有东西会写」。
**实测为误**：五个 timer 全部 `enabled + active`，`emotion-core-daily.timer` 于
2026-10-06 17:20 北京成功执行、`pipeline_state` 13 步全 `OK`。业务表停在 2026-09-30
是因为 10-01~10-07 国庆休市、交易日守卫正常跳过，**不是没有调度器**。

### 顺带修的门禁自身缺陷

- `test_dependency_direction.py` 用 `str(rel)` 生成键，Windows 下得到 `algorithms\ladder.py`
  而 `_DEBT` 写的是正斜杠 ⇒ 同一条目**同时**被判成「新增违规」和「已偿还」，
  两个测试互斥失败而 Linux/CI 全绿。改 `as_posix()`。
- systemd 契约门禁两处过严：`-m` 目标只认 `def main(`（漏了 `presentation/server.py`
  的 `__main__` 守卫形态）；引用检查只认 `.service`（漏了 `strategy.timer` 的
  `After=emotion-core-daily.timer`）。**门禁比约定更严不是更安全，是没人愿意维护它。**

ruff format 债务 226 → 225，基线已下调。生产全量 **1912 passed + 2 skipped**。

---


### 同日另批：[Unreleased] — 2026-10-07（以层为单位审计的收口：零告警三成因 + 三道新门禁）

本节所有修复都经**变异验证**（把修复改回原样，确认对应测试真的会红）。
生产全量基线：**1901 passed / 2 skipped**（修前 1857 passed / 2 skipped）。

### 修复 · 监控自噬链（这三条叠加 = 2026-10-06~07 的零告警）

- **断档日历从被监控对象派生**：`algorithms/health.py::_recent_trading_days` 原来查
  `SELECT DISTINCT date FROM daily_bar` —— 日历是行情自己的投影，于是 `daily_bar`
  一停写，断档天数同步变小，**恰好在最该报警时沉默**。改用独立的 `trade_calendar`
  （生产实测覆盖 1990-12-19~2026-12-31，不退化成 `weekday()` 降级）；
  日历读不到时返回 `[]` 并记 error，而不是拿坏日历算出一个偏多的「应到未到」。
- **watchdog 退出码只认新入队**：`health.push()` 内部先按 `(source, 归一化 detail)`
  去重 ⇒ 同一个**没修复也没 ack** 的断档，第二天起新入队数恒为 0，watchdog 每次退 0，
  systemd 一路绿灯。改判「仍有未确认的 health 断档」，取数窗口与判重视窗同为 200。
- **`emotion-core-close.service` 丢弃返回码**：`python -c "…; run_daily()"` 把
  `run_daily` 的返回值丢在地上 ⇒ 失败码 1 / 覆盖率拦截 76 全被吞；且不经 `main()`
  ⇒ 无 `basicConfig`，journal 里没有进度。改走 `-m emotion_core.orchestration.daily`。

### 修复 · 行情口径

- **TDX `pre_close` 取到未来价**：`pytdx_provider._tdx_all_bars` 的 docstring 明写
  「TDX 返回降序」，而 `PytdxProvider.fetch_daily_bars` 与 `backfill_tdx.fetch_code`
  都在**降序帧**上直接 `shift(1)` ⇒ 每行拿到的是**次日**收盘价。pre_close 是涨停判定
  基准（caliber C2），取未来价会让涨跌幅/涨停整体反向。改为先 `sort_values("date")`
  再 shift，且 shift 放在区间过滤**之前**，用区间外那根给首行播种。
- **`normalize_frame` 自相矛盾**：`code` 被算进必需列，可函数下一行就是
  `out["code"] = code`。⇒ `PytdxProvider` / `TencentProvider` 恒定抛「缺少列: code」，
  **从未成功返回过一行**；之所以没人发现，是 `test_tencent.py` 把 `normalize_frame`
  整个 mock 掉了。
- **Rust `board_pct_milli` 漏北交所 `92` 号段**：北交所 2023-04 起对新上市公司启用
  920xxx，Python 判据一直是 `("4","8","92")`，Rust 只抄了 `4`/`8` ⇒ 920xxx 按主板 10%
  算涨停价。（生产影响为零：C7 已把北交所排除在判据层之外。）

### 修复 · 环境时区与可导入性

- `orchestration/strategy.py` / `pool.py` 的 `date.today()` → `today_sh()`。机器时区是
  Etc/UTC，北京时间 00:00~08:00 之间会少一天（`entry.py:266` 的 P1-3 明文禁止隐式
  `date.today()`）。
- **`core/__init__.py` 改惰性加载**（PEP 562）。`.so` 是 `.gitignore` 的构建产物、仓里
  没有编译它的 CI 步骤 ⇒ 干净环境下 `import emotion_core.core` 在**模块导入期**就崩
  （`AttributeError: 'NoneType' object has no attribute 'loader'`），连带
  `tests/oracle/*_rust_vs_python.py`、`test_dragon_env.py`、`test_ecosystem_service.py`
  在收集阶段即报错 —— **CI 的 `pytest tests` 从未真正跑通过**。
- **新增 `tests/conftest.py`**（本仓首个 conftest）：产物缺失时把依赖 Rust 的测试整份
  skip 并在 stderr 说明原因。判据用 **AST 解析 import**（不是文本搜索，否则
  `test_dependency_direction.py` 里注释提到的 `dragon_env` 会被误伤）。

### 门禁

- **新增 `tests/architecture/test_systemd_units.py`（31 例）**：禁 `python -c` 入口、
  校验 `-m` 目标真有 `main()`、`OnCalendar` 必须自带时区、drop-in 的生效值不得与
  本体矛盾、`Requires=` 指向的 service 必须存在。
- **`tests/caliber/test_price_unique_impl.py` 扫描范围扩到 `.rs`**：此前只扫 `*.py`，
  于是 Rust 侧可以自由长出第二套板块比例而无人拦阻。新增「Rust↔Python 板块前缀
  对等」断言（**源码级**，不需要 cargo —— 生产机上没有 cargo，无法就地重建 `.so`，
  等 `.so` 重编前这是唯一能立刻发现分歧的东西）。
  该断言首版按 Rust 的 `starts_with` 过滤、Python 侧实为 `startswith`，导致断言恒绿；
  已修，并补 `test_python_board_prefix_extraction_is_not_vacuous` 钉住「提取器不许空转」。
- `scripts/check_doc_numbers.py` 此前 rc=1，抓到 9 处真实计数错误
  （`README.md` DDL 声明 24 实为 30；`docs/15-实施追踪.md` 完成度总览 8 个层的文件数
  全偏小）。**改文档让它对**，现已 rc=0。
- 静态债务棘轮基线下调：ruff check **291 → 279**，format 待格式化 226（持平）。

### CI：第一次真正跑通

`2026-10-07` 之前**这条 CI 从未绿过**。第一次去看日志才发现，连续三次 push 全红，
`Unit and integration tests` 是 20 failed / 1489 passed / 2 errors。根因全在
「干净 runner ≠ 生产机」，不是代码改动引入的：

| # | 症状 | 根因 | 处理 |
|---|---|---|---|
| 1 | 18 条 `RuntimeError: 读不到 ~/.dbconfig` | `db.py` 在 `psycopg.connect` **之前**先读凭据；这批测试只 mock 了 connect，生产机能过**纯靠那台机器恰好有 `~/.dbconfig`** | CI 里起 Postgres service 并写 `~/.dbconfig` 指向它；不在 workflow 里另抄一份 DDL |
| 2 | `test_health::TestRun` 真去连库 | CHECKS 的 pipeline 项调 `algorithms/pipeline.py::failures()`，那是**另一个模块自己的** `query_df`，原 fixture 只 patch 了 `health.query_df` | fixture 补 `_pipeline_mod.failures` 替身 |
| 3 | `test_wind_client::test_availability_key_missing` | 只覆盖 `config_path`，`cli_script` 落到机器相关的默认路径；而 `availability()` 先判 CLI 存在 ⇒ 这条测试能否过取决于**跑测试的机器装没装 wind** | 两个路径都显式给（与 `test_availability_ok` 同形） |
| 4 | 建表步骤绿灯但表不存在 | `connect()` 不开 autocommit，`create_all()` 也不自己提交 ⇒ 连接一关 DDL 被回滚 | 建表走 `transaction()` |

**当前两套口径（别把它们混为一谈）**：

- **CI（GitHub Actions）**：**1457 passed / 2 skipped**。起 Postgres 16、
  用仓内 `schema.create_all()` 建 30 张表。**依赖 Rust 的测试整份跳过**
  （oracle 对账 6 份 + `test_dragon_env` + `test_ecosystem_service` + `test_daily`）
  —— `.so` 是 gitignore 的构建产物，而 CI 里没有 cargo 去编它。
- **生产机 oracle**：**1901 passed / 2 skipped**（`.so` 在，Rust 对账全跑）。
- 两者差额 **444** 就是那批 Rust 依赖测试。**Rust↔Python 对账目前只在生产机跑**
  （`docs/06` §3.3：每次 `cargo build` 之后必须跑一遍）。

> 想让 CI 也覆盖 Rust 对账，需在 workflow 里加 Rust 工具链 + `cargo build --release`
> + 拷贝 `.so`。本机没有 Rust 工具链，**未经验证**，故没有写进去 —— 不写没验证过的步骤。

### 未修（需 owner 裁决，不是「修不好」）

- **生产 `.so` 仍无 `92` 修复**：机器上没有 cargo，`src/core_lib/emotion_core_rust.so`
  无法就地重建（当前 md5 `44ebfc09…`、mtime 2026-09-30 01:47:57 UTC）。须在别处
  `cargo build --release` 后拷回，并按 `docs/06` §3.3 跑 `pytest tests/oracle` 对账。
- **`Checklist.passed` 全 None 返回 True** 核实为**有意契约**（UNKNOWN 不否决，
  Python/Rust/回测三边一致，`tests/oracle/test_exit_rust_vs_python.py:89` 断言），
  已补 docstring 说明，不改行为。
- `docs/evidence`、`docs/archive-lkl` 等历史目录仍保留旧数字，按门禁的
  「历史目录不要求对齐」原则豁免；若要一并更新需单独立项。

> 上一版此处列的 systemd 漂移、`close` 职责重复、doctor 双源永不失败、
> `docs/15` 两处部署状态冲突 —— **四项已在 2026-10-07 第二段全部处理完毕**，
> 见本文件上方对应条目。

### 同日另批：飞书 webhook 接线（上线前 360° 审计）

- **webhook 从未配置**，告警与日报只留在本机，等于上线当天无人值守。现接入
  与 CPT 同一个飞书群。
- **原实现根本发不出飞书**：`_guess_channel` 只认企业微信/钉钉，飞书落到
  `generic` 发 `{"text": ...}`，而飞书自定义机器人要
  `{"msg_type":"text","content":{"text":...}}`。更阴的是它**看起来成功**——
  飞书在报文非法时仍回 HTTP 200，成败只在 body 的 `code` 字段（0=成功），
  而原判断只看 `status_code`。已加 `feishu` 渠道 + 正确报文 +
  `_resp_ok()` 额外校验 `body.code`（不符时打 warning）。
- **drop-in 不在仓内，换机必然漏配**：watchdog / report 单元的注释都写着
  「webhook 可在 drop-in 里补」，但那文件既不在仓库也不在任何部署脚本里。
  改为在**仓内单元**写 `EnvironmentFile=-.../deploy/env/emotion-core.env`
  （前缀 `-` = 文件缺失不报错，未配时静默跳过推送，与 notify 语义一致）；
  daily 的 health 步同样会推，一并接上。
- **`.gitignore` 差点把 webhook 提交进去**：原规则只有 `.env` 与 `.env.*`，
  **匹配不到** `deploy/env/emotion-core.env`。已显式挡 `deploy/env/*` 并只放行
  `*.example`，同时补模板文件；用 `git check-ignore` 验证过。
- **env 不复用 CPT 那份**：`EnvironmentFile` 会把文件里每个键都注入进程环境，
  指向 CPT 的 `cpt-dashboard.env` 会连带把 `CPT_LLM_API_KEY` 注入本项目，凭据
  越界比没配更糟。故单独建只含 webhook 的文件（600），值从 CPT 复制。
- 顺带修一处**测试自欺**：`tests/unit/test_notify.py` 里三个测试类**各定义两次**
  （后者静默覆盖前者，约 120 行从未执行），另有一条测试**没有任何断言**。
  已合并为单份并补飞书覆盖：40 条（原实际执行 22 条）。

---

## 2026-10-01 · (Rust ecosystem 移植)

### 新增

- **src/emotion_core/core/src/ecosystem.rs**: Rust 实现 g1~g4 + b1~b5 + verdict + rate + ladder_health + promotion_strength（PyO3 绑定）
- **tests/oracle/test_ecosystem_rust_vs_python.py**: Rust vs Python 对账测试 47/47

### 重构

- **algorithms/dragon_env.py**: 纯判定逻辑下沉到 Rust（_python 文件保留 DB 查询层 + 薄包装）
  - g1/g4/g3/b3/b1/b2/_verdict → 调用 `_rust.*`（ecosystem.rs）
  - ladder_health/promotion_strength → 调用 `_rust.*` 返回 Rust 结构体
  - DB 查询（_series / _B3_SQL / query_df）保留在 Python 层
  - 函数 docstring 标注 ★ Rust: ecosystem.rs::*

### 构建

- **Cargo.toml**: 新增 pyo3 依赖（已存在）
- **.gitignore**: 移除 emotion_core_rust.so（不再跟踪编译产物）

---


### 同日另批：[Unreleased] — 2026-10-01 (零覆盖模块收尾)

### 新增

- **tests/unit/test_strategy_context.py**: strategy.context 测试 11/11
- **tests/unit/test_strategy_universe.py**: strategy.universe 测试 11/11
- **tests/unit/test_wind_manifest.py**: wind_manifest 测试 6/6
- **tests/unit/test_calibrate.py**: calibrate 测试 7/7
- **tests/unit/test_ladder_service.py**: ladder_service 测试 4/4
- **tests/unit/test_ref_security_status.py**: ref_security_status 测试 11/11
- **tests/unit/test_derive.py**: derive 测试 16/16
- **tests/unit/test_indicators.py**: indicators 测试 9/9
- **tests/unit/test_state.py**: state 测试 26/26
- **tests/unit/test_reconcile.py**: reconcile 测试 6/6
- **tests/unit/test_stock.py**: stock 测试 53/53
- **tests/unit/test_providers_base.py**: providers.base 测试 17/17
- **tests/unit/test_normalize.py**: normalize 测试 19/19
- **tests/unit/test_config.py**: config 测试 12/12
- **tests/unit/test_dates.py**: dates 测试 7/7
- **tests/unit/test_db.py**: db 测试 6/6
- **tests/unit/test_cleanup.py**: cleanup 测试 6/6
- **tests/unit/test_theme_source.py**: theme_source 测试 5/5
- **tests/unit/test_tencent.py**: tencent 测试 5/5
- **tests/unit/test_review_utils.py**: review.utils 测试 41/41
- **tests/unit/test_sync.py**: sync 测试 10/10
- **tests/unit/test_pool.py**: orchestration/pool 测试
- **tests/unit/test_report.py**: orchestration/report 测试
- **tests/unit/test_strategy_orchestration.py**: orchestration/strategy 测试
- **tests/unit/test_watchdog.py**: orchestration/watchdog 测试
- **tests/unit/test_loaders.py**: presentation/loaders 测试 8/8
- **tests/unit/test_snapshot.py**: presentation/snapshot 测试 10/10
- **tests/unit/test_translate.py**: presentation/translate 测试 7/7
- **tests/unit/test_views.py**: presentation/views 测试 6/6
- **tests/unit/test_llm_agnes.py**: llm/agnes 测试 18/18
- **tests/unit/test_llm_render.py**: llm/render 测试 4/4

### 修复

- **pool.py:74**: dry-run 路径调已移除的 `ingest._retry` → 改用 `retry_fetch`
- **pytdx_provider.py:47**: `_TLS` → `_tls` 模块级变量名大小写不一致
- **test_exactly_half**: priced 计数错误修正
- **test_sina_index_volume**: 下标偏移修正（下标 8 = 第 9 个字段）

### 文档

- **docs/06**: T16 标记完成
- **docs/15**: 测试数量 1165→1765，98/98 模块全覆盖

---


### 同日另批：[Unreleased] — 2026-10-01 (审计报告修复)

### 修复

- **presentation/server.py**: `get_static` 目录穿越漏洞 — 加 `is_relative_to` 校验 (H1)
- **presentation/server.py**: HTML 回显异常 — 移除 `{e}` 拼接，改为通用 500 + server-side log (H2)
- **services/ingest.py**: `upsert()` f-string SQL 注入风险 — 加 `_UPSERT_ALLOWED_TABLES` 白名单 (M4)
- **Rust unwrap 修复**: 全部 `unwrap()` 加 `SAFETY` 注释 + `accelerate.rs` NaN 排序改 `unwrap_or` (M7)
- **utils/fetch.py**: `retry_fetch` 首次 sleep 移除 — 仅重试间隔退避 (L2)

---


### 同日另批：[Unreleased] — 2026-10-01 (产品审计修复)

### 修复

- **P0-2 买点参考**: `services/stock_service.py` 增加 `_buy_point_reference()`（打板价 + 同层级历史赔率），注入 `buy_point` 到 API payload；`stock.html` 增加买点卡片；`stock.js` 增加 `renderBuyPoint()`；`dashboard.css` 增加买点样式（88e9079）
- **P1-1 空态策略解释**: `server.py` 增加 `_load_negative_expectation()`（从 signal_outcome 聚合）+ `no_buy_reason`（策略解释）；`intuitive.html` 空态从 DB 心跳改为策略原因（59103a0）
- **P1-2 负期望披露**: `intuitive.html` 页头增加负期望警示框（均值/中位数/胜率/样本量）（59103a0）
- **P1-3 坦白机制**: `services/gaps.py` 10 项动态检查器；`algorithm.html` 缺口清单条件渲染（6605cb7）
- **P2-1 免责声明**: `stock.html` 顶部增加显式免责警示框（BUY/AVOID 动作配套强免责）（13de08b）
- **P2-2 日期交互**: `strategy.html` 移除独立 `<select>` 日期选择器，统一使用 base.html 日历组件（13de08b）

### 数据

- **schema.py**: 买点参考复用现有 promotion_matrix + layer_forward 统计

### 文档

- **docs/13**: 回写至当前阶段（1822 tests / 阶段 8）（24f2805）
- **docs/06**: P0/P1/P2 全部标记完成
- **README**: 测试计数 495→1822
- **CHANGELOG**: 本段

---


### 同日另批：[Unreleased] — 2026-10-01 (P2 批次)

### 新增

- **services/ref_dividend.py**: 分红除权表 `ref_dividend` + `backfill_dividend()` 服务，Wind 回填除权除息日（不复权体系下供分析参考）
- **services/ref_rs.py**: 相对强弱表 `ref_rs` + `calculate_rs()` 服务，计算个股相对基准指数（默认沪深300）的 20日/60日强度排名
- **tests/unit/test_ref_dividend.py**: ref_dividend 测试 13/13（mock WindClient + db）
- **tests/unit/test_ref_rs.py**: ref_rs 测试 8/13（period_return + get_prices + get_top_rs）

### 数据

- **schema.py**: 新增 2 张表 — `ref_dividend`（分红除权历史）, `ref_rs`（相对强弱排名）；索引 2 条；总表数 27→29

### 文档

- **docs/06**: P2 T13/T15 标记完成
- **docs/15**: 测试数量 1165→1372，总表数 27→29

---

## 2026-09-30 · (cron 看门狗修复 + 文档口径同步)

### 修复

- **cron 看门狗重启分支失效（服务器 oracle，2026-09-30 修）**：原 crontab 行
  `*/5 * * * * curl -sf -o /dev/null http://127.0.0.1:8098/ || systemctl restart emotion-core-dash`
  的重启分支在 cron（非交互）里必被 polkit 拒绝，原文
  `Access denied as the requested operation requires interactive authentication`，即看门狗实际从未生效。
  改为：
  `*/5 * * * * curl -sf --max-time 8 -o /dev/null http://127.0.0.1:8098/ || sudo -n systemctl restart emotion-core-dash >> /home/ubuntu/logs/ec-dash-watchdog.log 2>&1`
  （旧 crontab 备份 `/tmp/crontab.backup.20260930`）。
  **铁律：所有 systemd 重启一律 `sudo -n systemctl restart <unit>`**（裸 `systemctl restart` 在 SSH/cron 非交互场景必被 polkit 拒）。
  e2e 验证（模拟 cron 环境打 8099 死端口）：重启分支 rc=0、`MainPID 1673194 → 1674630`、服务 active、日志 0 字节。

### 测试

- 全量测试口径更新为 **1859 collected / 1857 passed + 2 skipped**（2026-09-30 实测，HEAD 43d3da9）；
  修复前基线（HEAD c1909cd）1839 collected / 1837 passed + 2 skipped。

### 文档

- **全量文档遍历同步**：`README` / `CHANGELOG` / `docs/11` / `docs/13-交接文档-2026-09-26` / `requirements.txt`
  统一测试口径并标注历史（1822 / 926 为 2026-09-26 时点，历史口径）；README 补历史信号回填运维 CLI
  与个股诊断三段式（A/B/C）接线说明；`docs/11` 标注 `diagnose_service.py` 已接入 `/api/stock`。

---


### 同日另批：[Unreleased] — 2026-09-30 (个股诊断三段式接线 / R2)

### 新增

- **services/diagnose_service.py**: 三段式个股诊断（docs/03 §4.2）
  - A 段：当日身份（连板/换手/梯队层级/唯一最高板）+ 五条件逐项 `PASS/FAIL/UNKNOWN` + 通俗原因 + W1 同身位扎堆警告
  - B 段：历史身份（首个 bar / 次新）+ 历史唯一最高板日期与其后 T+1/T+3/T+5 + 历史信号与结果（signal ⋈ signal_outcome）
  - C 段：晋级率双口径 + divergence + 题材完整性（theme_group.completeness）+ 生态评级（market_stat.dragon_env）+ 同状态赔率
- **/api/stock**: 新增 `diagnose` 段（既有键名与层级不动）；昂贵统计由 stock_service 的 TTL 缓存注入，不重复全表聚合
- **presentation/templates/stock.html + static/stock.js**: 个股页新增 A/B/C 三张卡片（`renderDiagnose`），无数据显示「暂无」
- **services/replay_service.py**: 新增 `__main__` + argparse（`--start/--end/--dry-run`），历史信号回填终于有运维入口
  （`PYTHONPATH=src .venv/bin/python -m emotion_core.services.replay_service --start 2024-01-01 --end 2026-09-30`），
  `run()` 的签名与行为一字未改；`--dry-run` 只预览区间与交易日数，不写库
- **orchestration/daily.py**: `_run_step` 改为返回短 detail 并落 `pipeline_state.detail`——
  signal 步写 `signals=N code=… action=… buy_window=… source=live`，ladder 步写 `候选=N`，其余步留空

### 修复

- **R2 审计缺口②**：`services/diagnose_service.py` 此前是零调用方的并行死实现，现接入 `/api/stock`；
  五条件判定只调 `algorithms/entry.py`（本模块不重写规则），梯队只取 `algorithms/ladder.py` 落库的 ladder_day
- **降级纪律**：任一段缺表/缺列/异常只写 note（`applicable=false` + 「当日非候选」，不编造 PASS/FAIL），诊断段不可能让 `/api/stock` 500
- **P1-B 仓库侧（`signal` 无 live 行不可观测）**：`pipeline_state` 是 latest-state UPSERT，`mark_done` 的 detail
  默认空串 ⇒ 「跑了但没信号」与「根本没跑」在库内无法区分，`status=OK` 因此会被误读成「有信号」。
  daily 现在把每步的产物摘要写进 detail，`signal OK ''` 这类空证据不再出现
  （2026-09-29/30 实测重算为 `signals=0 buy_window=NONE`，即禁买日无候选，不是步骤没跑）
- **过期注释**：`orchestration/report.py`、`orchestration/pool.py`、`tests/unit/test_review_entrypoints.py`
  的「daily 12 步」→ 13 步；`algorithms/dragon_env.py` 的「1822 tests」→ 实测口径

### 测试

- **tests/unit/test_diagnose_service.py**: 5 → 16 个用例（A/B/C 各段、UNKNOWN 不否决、entry 行序变化退回 `entry.checklist`、DB 异常降级）
- **tests/unit/test_stock_service.py**: 新增 diagnose 段接线用例；保持单测 DB-free（新增 `diagnose_service.query_df` 空表桩）
- **tests/unit/test_daily.py**: 新增 8 个用例覆盖 detail 传递、返回 None 的桩、signal/ladder detail 落库

### 文档

- **口径统一（外审 2026-09-30 复核发现）**: 测试数改用实测口径（修复前基线 1839 collected / 1837 passed + 2 skipped；
  修复后 1859 collected / 1857 passed + 2 skipped，旧文里的 1822 / 926 标为 2026-09-26 时点历史口径）；`docs/15` 五张「行数」表原列实为**字节数**，全部用 `wc -l` 重算并在表头注明口径；
  `docs/15` 的「10 层依赖拓扑」标注为**设计层口径、非目录树**（`models/`、`review/`、`trade/` 在仓内不存在，
  真身分别是 `domain/`、`algorithms/review/`（入口 `orchestration/report.py`）、`presentation/trade_api.py`）
- **docs/05、docs/13**: `signal` 「只有 6 行」「172 个历史信号」标注为 2026-09-26 时点数字，
  并补上 2026-09-30 实测（162 行全 `source='replay'`、`live=0`）；「6 行」的真实出处是
  `tests/oracle/fixtures/signal_live.json` 这个只有 6 条的 fixtures 骨架
- **docs/06**: 新增「重建 Rust 产物后必须对账」小节（`pytest tests/oracle` + `.so` 指纹
  1,592,696 B / md5 `44ebfc09f0a813cead5c1c2b7c076469`）

---


### 同日另批：[Unreleased] — 2026-09-30

### 新增

- **services/wind_client.py**: Wind MCP CLI 适配器，窄接口（通用调用 + 配额记账 + 原始响应留存），复用 wind-mcp-skill CLI（8d659fe）
- **services/ref_limit_rule.py**: 制度规则表 `ref_limit_rule` + 10 条种子数据（主板±10%/创业板±20%/科创板±20%/北交所±30%/ST±5%），查询服务 `get_limit_pct(market, board, as_of)`（8d659fe）
- **services/ref_security_status.py**: ST 安全状态表 `ref_security_status` + `backfill_security_status()` 服务，对 is_st=true 股票逐只调 Wind `get_stock_events`（8d659fe）
- **services/wind_manifest.py**: `record_call()` 将每次 Wind 调用落库到 `ops_raw_manifest`（原始 JSON 留存）+ `ops_quota_ledger`（配额消耗账本）（8d659fe）
- **tests/unit/test_wind_client.py**: WindClient 单测 13/13，覆盖成功/配额/鉴权/参数/超时/空输出/count 递增（8d659fe）
- **tests/unit/test_ref_limit_rule.py**: 制度规则种子与查询测试 7/7（8d659fe）

### 数据

- **schema.py**: 新增 4 张表 — `ref_limit_rule`, `ref_security_status`, `ops_raw_manifest`, `ops_quota_ledger`；索引 4 条；总表数 23→27（8d659fe）

### 文档

- **docs/06**: P1 T6/T7/T9/T10 标记完成（8d659fe）
- **docs/15**: 测试数量 1147→1165，总表数 23→27（当前变更）
- **CHANGELOG**: P1 批次新增条目（当前变更）

---

## 2026-09-29 · (历史)

### 修复

- **alerts**: `notify.push` 异常不再外抛，加 `try/except Exception` 兜底（be19aa6）
- **daily**: psycopg3 无 `Connection.executemany()`，改用 `with conn.cursor() as cur: cur.executemany()`（e2d0bdf）
- **presentation/server.py**: `_load_algorithm_data` 静默吞错加 `log.warning`，返回空 dict 时留日志痕迹（当前变更）
- **pytdx_provider**: 硬编码 TDX 服务器 IP 改为环境变量 `TDX_HOSTS` 配置，格式 `host:port,host:port`（当前变更）
- **pool**: 两个新单元补 `TimeoutStartSec`，防止东财接口挂死不退（fc2ae24）
- **monitor**: 五层监控同时失效导致故障静默 3 天，补测试覆盖（9515f45）
- **report**: 接通复盘报告，修四个断点并建独立 CLI（c1c7bdb）
- **pool**: 「空池被当成功」静默错误（a980ab0）
- **data/loader.py**: `stat.bomb_rate`→`stat.bomb_count`、`stat.max_limit_days`→`stat.max_height`，修复字段名不匹配（P1-F）
- **revision.py**: 加 `_ALLOWED_TABLES` 白名单防 SQL 注入（P1-G）

### 新增

- **tests/architecture/**: 架构方向违规扫描，19 条反向依赖 allowlist 登记，新增即红（f1fb4a8）
- **tests/unit/test_ingest_fallback.py**: ingest 降级路径测试 17/17（57a5645）
- **tests/unit/test_alert_webhook.py**: alert webhook 推送通道测试 8/8（be19aa6）
- **tests/unit/test_daily.py**: orchestration/daily 核心路径测试 19/19（78f0657）
- **tests/unit/test_loader.py**: data/loader.py 测试 12/12（P1-F）
- **tests/unit/test_revision.py**: revision.py 校验测试 13/13（P1-G）
- **presentation**: 日历 B 方案 — 460px 左侧 + 右侧最近快照栏（b257321）
- **api**: 只读端点 `/api/alerts`，供门户今日速览（dc27f0f）

### 文档

- **docs/13**: 移除虚构符号 `MarketInputs`/`StateResult`/`_bridge`，改为实际类型 `DayMetrics`/`EmotionState`（当前变更）
- **docs/15**: 测试数量 754→1038，文件数量同步修正（当前变更）
- **docs/01**: 过时字段名 `CLIMAX_COUNT`→`CLIMAX_ZT`、`ICE_COUNT_MAX`→`ICE_ZT_MAX`（当前变更）
- **README**: 同步当前进度与 web 功能清单（49a222c）

---

## [0.1.0] — 2026-09-27

### 新增

- 整体架构：9 层（utils/domain/algorithms/data/services/orchestration/presentation/llm/核心）全量实现
- Rust 核心：`core/src/state.rs` + PyO3 绑定
- 交易流水线：日终采集 → 派生指标 → 状态机 → 阶梯 → 入场 → 复盘
- systemd 定时器：daily/pool/report/watchdog 四件套
- Web 展示层：三层 dashboard（直观/逻辑/算法）+ 暗色主题
- LLM 层：策略解读 prompt 模板
- 数据回填：TDX/Sina/EastMoney 三源，akshare 同源备源
