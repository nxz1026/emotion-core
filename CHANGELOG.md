# 更新日志

所有 notable 变更均以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased] — 2026-10-01

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
