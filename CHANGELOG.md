# 更新日志

所有 notable 变更均以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased] — 2026-09-30 (个股诊断三段式接线 / R2)

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

- **口径统一（外审 2026-09-30 复核发现）**: 测试数改用实测口径（1839 collected / 1837 passed + 2 skipped，
  旧文里的 1822 / 926 标为历史）；`docs/15` 五张「行数」表原列实为**字节数**，全部用 `wc -l` 重算并在表头注明口径；
  `docs/15` 的「10 层依赖拓扑」标注为**设计层口径、非目录树**（`models/`、`review/`、`trade/` 在仓内不存在，
  真身分别是 `domain/`、`algorithms/review/`（入口 `orchestration/report.py`）、`presentation/trade_api.py`）
- **docs/05、docs/13**: `signal` 「只有 6 行」「172 个历史信号」标注为 2026-09-26 时点数字，
  并补上 2026-09-30 实测（162 行全 `source='replay'`、`live=0`）；「6 行」的真实出处是
  `tests/oracle/fixtures/signal_live.json` 这个只有 6 条的 fixtures 骨架
- **docs/06**: 新增「重建 Rust 产物后必须对账」小节（`pytest tests/oracle` + `.so` 指纹
  1,592,696 B / md5 `44ebfc09f0a813cead5c1c2b7c076469`）

---

## [Unreleased] — 2026-10-01 (Rust ecosystem 移植)

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

## [Unreleased] — 2026-10-01 (零覆盖模块收尾)

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

## [Unreleased] — 2026-10-01 (审计报告修复)

### 修复

- **presentation/server.py**: `get_static` 目录穿越漏洞 — 加 `is_relative_to` 校验 (H1)
- **presentation/server.py**: HTML 回显异常 — 移除 `{e}` 拼接，改为通用 500 + server-side log (H2)
- **services/ingest.py**: `upsert()` f-string SQL 注入风险 — 加 `_UPSERT_ALLOWED_TABLES` 白名单 (M4)
- **Rust unwrap 修复**: 全部 `unwrap()` 加 `SAFETY` 注释 + `accelerate.rs` NaN 排序改 `unwrap_or` (M7)
- **utils/fetch.py**: `retry_fetch` 首次 sleep 移除 — 仅重试间隔退避 (L2)

---

## [Unreleased] — 2026-10-01 (产品审计修复)

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

## [Unreleased] — 2026-10-01 (P2 批次)

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

## [Unreleased] — 2026-09-30

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

## [Unreleased] — 2026-09-29 (历史)

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
