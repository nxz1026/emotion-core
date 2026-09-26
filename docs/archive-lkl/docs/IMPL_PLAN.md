# 代码实施计划（IMPL_PLAN v1.1，依据 docs/PLAN.md v2.3 拆解）

> 本文件是编码施工图：任务分解 → 文件/函数签名 → 验收标准。
> 铁律：任何函数动笔预计 >50 行即停笔拆分（E1）；条件判断链一律"一条件一小函数"。

## 0. 任务依赖图

```
T0 基础设施 ─┬→ T1 数据拉取 → T2 衍生层+对账 ─┬→ T3 情绪 ─┐
             │                                ├→ T4 梯队 ─┼→ T5 买入 → T6 卖出 ─┐
             │                                │           │                     ├→ T8 复盘
             ├→ (schema 就绪后并行)             └───────────┴→ T7 持仓+执行占位 ──┤
             │                                   T9 评估(依赖T2~T6) ──────────────┘
             └→ T11 LLM层(仅依赖T0，独立并行；信号链路禁依赖它)
T10 CLI组装(贯穿,每任务合入时接线)
```

## 1. T0 基础设施

### 1.1 `lkl/config.py`（~90 行，纯常量）
```python
# DB —— 连接参数不含密码；密码只从 DB_CONFIG["password_file"] 指向的文件运行时读取
DB_CONFIG = {host, port, dbname="longkonglong", user="postgres",
             sslmode="verify-full", sslrootcert="~/global-bundle.pem",
             password_file="~/.dbconfig", password_key="$DB_PW"}
BOARD_PREFIXES = ("600","601","000","001","002")   # D1
LIMIT_RATIO, DOWN_RATIO = 0.10, 0.10               # 主板
NEW_STOCK_MIN_DAYS = 60                             # §1.1
MIN_LEADER_DAYS = 4                                 # R1 默认4
REQUIRE_YESTERDAY_COMPETITION = True                # R2
# 情绪阈值 §1.3：ICE_MAX_DAYS=3, ICE_COUNT_MAX=40, FERMENT_MIN_DAYS=4,
# FERMENT_ZT_PERF=1.5, FERMENT_STREAK=2, CLIMAX_COUNT=80, CLIMAX_BOMB_RATE=0.30,
# CLIMAX_AMPLITUDE=12.0, EBB_ZT_PERF=-2.0, EBB_LD_MULT=2.0, EBB_LD_MIN=15
DIVERGE_MIN_TURNOVER = 5.0                          # §1.5 条件5
FEE_COMMISSION, FEE_STAMP = 0.0002, 0.0005          # §1.7 基线用
DATA_START = date(2024,1,1)
# E4 LLM —— OpenAI 兼容，profile 化参数，密钥走环境变量/文件不进仓库
LLM_ENABLED = False                                  # 默认关闭，增强面显式开启
LLM_PROFILES = {   # 模型选型来自 2026-08-29 sensenova 端点实测（PLAN §E4.1）
  "default": {base_url="https://token.sensenova.cn/v1", model="glm-5.2",
              temperature=0.3, max_tokens=2048, top_p=1.0, timeout=120, max_retry=2},
  "fast":    {... model="deepseek-v4-flash", timeout=30 ...},   # 实测2.5s，新闻批处理用
  "smart":   {... model="sensenova-6.8-flash-lite", timeout=180 ...}, # 推理慢但深
}
LLM_KEY_ENV = "LKL_LLM_API_KEY"                      # 优先读环境变量
LLM_KEY_FILES = ["~/.llmkey", ".secrets/llmkey"]     # 兜底文件（后者已 gitignore）
```

### 1.2 `lkl/utils/db.py`（~80 行）
```python
def read_password() -> str                    # 解析 .dbconfig 的 $KEY=value
@contextmanager
def get_conn(): ...                           # psycopg 连接，autocommit=False
def execute(sql, params=()) -> int            # 影响行数
def query_df(sql, params=()) -> pd.DataFrame
def upsert_rows(table, columns, rows) -> int  # INSERT .. ON CONFLICT DO UPDATE，分批1000
def init_schema() -> None                     # 调 models.schema.create_all()
```

### 1.3 `lkl/models/schema.py`（~150 行）
```python
DDL: dict[str, str]  # 10张表，全部 CREATE TABLE IF NOT EXISTS + 主键 + date索引
def create_all(conn) -> None                  # 遍历 DDL 执行
```
表清单见 PLAN §2。关键约束：daily_bar PK(code,date)；derived_bar PK(code,date)；
signal PK(serial id) + UNIQUE(confirm_date, code, action)；ingest_progress PK(task,code) 断点表。

### 1.4 `lkl/models/types.py`（~100 行）
```python
@dataclass Bar / DerivedBar / EmotionState / LadderRow / Signal / Position
# 字段与表一一对应；Signal.checklist: list[tuple[str,bool,str]]
```

### 1.5 `lkl/main.py`（骨架 ~60 行）
argparse 子命令注册表 `COMMANDS: dict[name, (module_fn, help)]`，main() 只做查表调用。
每个子命令 ≤5 行胶水。**验收**：`lkl --help` 列出全部命令（stub 报"待 T<n>"）。

**T0 验收**：`lkl init-db` 跑通，`\dt` 见 11 张表（含 ingest_progress）；pytest 收集通过。

## 2. T1 数据拉取 `lkl/services/ingest.py`（~200 行，拆 6 函数）

```python
def fetch_stock_basic() -> int
    # Baostock query_all_stock 逐日快照取最新 → stock_basic（code,name,list_date,market,is_st）
def backfill_daily_bars(start, end) -> int
    # 逐股 ak.stock_zh_a_hist(adjust="")，每股入库即写 ingest_progress 断点；
    # 内部拆: _hist_one_code(code) / _to_bar_rows(df) / _mark_done(code)
def snapshot_daily(date) -> int
    # ak.stock_zh_a_spot_em() 全市场单次快照 → daily_bar（每日增量主路径，1请求/日）
def fetch_limit_pool(date) -> int
    # ak.stock_zt_pool_em(date) → limit_pool_em 原样入库
def verify_pre_close(date) -> pd.DataFrame
    # 涨跌幅反算校验 pre_close，异常行返回（PLAN §6.2）
def sync_range(start, end) -> None
    # 编排: 逐交易日 snapshot_daily + fetch_limit_pool + verify_pre_close
```
限速：akshare 调用间隔 `TIME_SLEEP=0.5s`，失败重试 3 次退避。
**T1 验收**：2024-01-01 至今 daily_bar 行数 ≈ 股票数×交易日数；抽查 000001 连续 5 日 OHLC 与行情软件一致；断点重跑不产生重复行（upsert 幂等）。

## 3. T2 衍生层 `lkl/services/derive.py`（~180 行）

```python
def bar_flags(bar: Bar) -> DerivedBar
    # 单行判定，全部调 utils/price.py，无状态纯函数
def compute_derived(date) -> int
    # 全市场当日 bar_flags 向量化版（pandas apply 之外用列运算）→ derived_bar
def compute_cont_days(start, end) -> int
    # 按 code 分组扫描 is_limit_up 连续段 → 回填 derived_bar.cont_days
    # 拆: _streaks(flags: list[bool]) -> list[int]   # 纯函数，单测重点
def reconcile(date) -> pd.DataFrame
    # 自算 cont_days vs limit_pool_em.cont_days_em 对账，差异行返回并入库标记
def reconcile_range(start, end) -> pd.DataFrame
    # 汇总一致率报告
```
**T2 验收**：`lkl reconcile` 输出一致率 ≥99%；差异清单可解释（停牌/退市/除权日）；
`_streaks` 单测覆盖：全断/全连/中间停牌/首日是板。

## 4. T3 情绪 `lkl/services/emotion.py`（~200 行，规则全拆）

```python
def indicators(date) -> EmotionState
    # 五指标 SQL 聚合（全市场口径 R3），max_limit_days 取主板
def _rule_ice(t, y, b) -> bool        # 每个规则函数 ≤10 行
def _rule_ferment(t, y, b) -> bool
def _rule_climax(t, y, b) -> bool
def _rule_ebb(t, y, b, prev) -> bool  # 含"最高板断板次日未反包"→ 需读 prev.phase
def classify(series: list[EmotionState]) -> list[EmotionState]
    # 按优先级 退潮>高潮>发酵>冰点 逐日推进，无命中延续昨日（状态机持久化）
def window_of(phase) -> str            # STANDARD/ENHANCED/NONE + force_liquidate
def run_range(start, end) -> int       # 写 market_stat
```
**T3 验收**：黄金日断言（2024-2026 选 6 个典型日：冰点/发酵/高潮/退潮各≥1，人工核对阶段）；
`classify` 纯函数单测：构造指标序列喂入，断言阶段路径。

## 5. T4 梯队 `lkl/services/ladder.py`（~140 行）

```python
def build(date) -> list[LadderRow]
    # 主板 derived_bar 涨停股按 cont_days≥2 分组
def top_group(rows) -> list[LadderRow]
def sole_top(rows, min_days) -> LadderRow | None
    # §1.4：换手口径唯一最高板
def y_competition(date, code) -> tuple[int,int]
    # R2：昨日最高板组只数、今日幸存只数（供 entry 条件3）
def persist(date) -> int               # 写 ladder_day
```
**T4 验收**：2026-08-28 黄金用例——深中华A(7板,炸14回封)为唯一最高板；
海鸥住工(5板一字,换手1%)被换手口径剔除出最高层判定。

## 6. T5 买入信号 `lkl/services/entry.py`（~150 行）

```python
# 一条件一函数，签名统一: (date, cand, ctx) -> tuple[bool, str]  # str=核对说明
def c1_uniqueness(...)   def c2_exchange(...)     def c3_elimination(...)
def c4_min_days(...)     def c5_strength_diverge(...)
def checklist(date, cand, window) -> list[tuple[str,bool,str]]
def check_signal(date) -> Signal | None
    # 取 market_stat.buy_window + sole_top 候选 → 逐条件 → 全过则产 signal(SUGGESTED)
```
**T5 验收**：每条件独立单测（构造 LadderRow 喂入）；历史日全量跑通无异常；
checklist 完整落库 jsonb（复盘报告④直接渲染它）。

## 7. T6 卖出建议 `lkl/services/exit.py`（~80 行）

```python
def broken_board_check(pos: Position, day: DerivedBar) -> Signal | None   # a
def liquidate_check(stat: EmotionState) -> Signal | None                  # b
def suggestions(date) -> list[Signal]     # 遍历 open position，b 优先于 a
```
**T6 验收**：单测覆盖 持仓涨停/断板/一字/退潮 组合 4 用例。

## 8. T7 持仓 + 执行占位

### `lkl/services/position.py`（~90 行）
```python
def open_pos(code, date, price, shares, note="") -> int
def close_pos(pos_id, date, price) -> None
def current() -> list[Position]
```
### `lkl/services/execution.py`（~70 行，E2）
```python
class Executor(Protocol): submit(sig) / status(order_id)
class ManualExecutor:     # submit → signal.status=SUGGESTED（v1 唯一实现）
class BrokerExecutor:     # raise NotImplementedError("P4 接入 QMT/easytrader")
def feedback(date, code, action, price) -> None   # ADOPTED/DROPPED 人工回执
```
**T7 验收**：`lkl position open 600XXX --price --shares` 落库；feedback 状态流转正确。

## 9. T8 复盘报告 `lkl/services/review.py`（~220 行，渲染分函数）

```python
def collect(date) -> ReportData          # 聚合 market_stat/ladder/signal/position/对账质检
def _sec_emotion(d) -> str               # ① ② ③ ④ ⑤ 各一个渲染函数，每个 ≤30 行
def _sec_ladder(d) / _sec_elimination(d) / _sec_advice(d) / _sec_quality(d)
def render_markdown(date) -> str
def publish(date) -> None                # 终端打印 + reports/YYYY-MM-DD.md + review_report 入库
```
**T8 验收**：任意历史日出完整五段报告；无信号日④段明确"卡在哪个条件✗"；
持仓表头回显（PLAN §6.5）。

## 10. T9 信号评估 `lkl/services/evaluate.py`（~200 行）

```python
def replay(start, end, min_days) -> pd.DataFrame
    # 逐日驱动 T3/T4/T5/T6 纯逻辑（不写生产表，写 eval_* 临时表）
def forward_stats(sig) -> dict
    # T+1高开分布/T+1晋级率/T+3、T+5 最大涨幅回撤（§1.7，无买入假设）
def window_compare(df) -> str            # STANDARD vs ENHANCED
def param_matrix(df) -> str              # MIN_LEADER_DAYS 3 vs 4
def baseline_sim(df) -> str              # 参考基线净值（显式标注"仅口径演示"）
def report(start, end) -> None           # markdown 落 reports/eval-*.md + eval_result 入库
```
**T9 验收**：2024 至今全量评估报告生成；信号数、间隔分布、两窗口/两参数对比表齐全。

## 11. T11 LLM 层 `lkl/services/llm.py`（~180 行，E4）

```python
@dataclass(frozen=True)
class LLMParams:      # model, temperature, max_tokens, top_p, timeout, max_retry
@dataclass
class LLMReply:       # text, prompt_tokens, completion_tokens, latency_ms, model

def resolve_params(profile: str, overrides: dict) -> LLMParams
    # 三级合并: config.LLM_PROFILES[profile] ← 环境变量 LKL_LLM_* ← kwargs
def resolve_api_key() -> str          # LLM_KEY_ENV 优先，其次 LLM_KEY_FILE；无则抛 LLMNotConfigured
class LLMClient:
    def __init__(self, profile="default", **overrides)      # 内部只存已解析参数
    def chat(self, messages: list[dict]) -> LLMReply         # 主入口，≤25行
    def complete(self, prompt: str, system: str | None=None) -> str  # chat 便捷包装
    def _post(self, payload: dict) -> dict                   # httpx + 重试退避，≤30行
    def _log_call(self, reply, purpose: str) -> None         # 写 llm_call_log，失败静默降级
def get_client(profile="default", **overrides) -> LLMClient  # 工厂+按profile缓存
class LLMNotConfigured(RuntimeError): ...
```
依赖新增：`httpx`（显式写入 pyproject）。
**实测适配（PLAN §E4.1 的坑，写进 _post/chat）**：① `json_mode` 返回可能裹
```json 围栏 → `_strip_fence(text)` 工具函数处理 ② timeout 按 profile 120~180s
③ 重试仅针对超时/5xx，4xx 直接抛出 ④ 调用失败也写 llm_call_log（status=ERROR）。
**接入纪律（E4 红线）**：只有 review/evaluate 的"增强段"可 import llm，且必须
`if config.LLM_ENABLED:` 包裹 + try/except LLMNotConfigured 降级；emotion/ladder/entry/exit 出现 `import llm` 即测试失败（见 §13 test_arch.py）。
**T11 验收**：`lkl llm-test "ping"` 用 `.secrets/llmkey` 真实凭据走 sensenova 端点返回回复并落 llm_call_log 一行（端点连通性已于 2026-08-29 预验证 ✅）；未配置 key 时 `lkl review` 照常输出（增强段自动跳过）；单测 mock httpx 不发真请求。

## 12. T10 CLI 命令表（main.py 组装）

```
lkl init-db                    # T0
lkl fetch basic|backfill|snapshot|pool|sync   # T1
lkl derive [date|range] / lkl reconcile       # T2
lkl emotion [date|range] / lkl ladder <date>  # T3/T4
lkl signal <date>                             # T5/T6
lkl position open|close|list                  # T7
lkl feedback <date> <code> adopted|dropped <price>  # T7
lkl review [date]              # T8  ★ 日常主命令
lkl evaluate <start> <end>     # T9
lkl llm-test [prompt]          # T11 连通性/参数自检
```

## 13. 测试计划 `tests/`

```
test_price.py       涨停价舍入边界（10.00/9.99、低价股0.01步进）
test_streaks.py     连板纯函数：全断/全连/停牌/首板
test_emotion.py     classify 状态机：构造序列断言阶段路径 + 6黄金日
test_ladder.py      20260828 黄金梯队（深中华A唯一/海鸥一字剔除）
test_entry.py       5条件逐项 构造通过/不通过 → checklist 断言
test_exit.py        4组合用例
test_ingest.py      mock akshare 返回 → upsert 幂等、断点续传
test_llm.py         mock httpx：三级参数合并、重试退避、未配置key抛LLMNotConfigured
test_arch.py        架构守卫：AST扫描 emotion/ladder/entry/exit 源码，
                    出现 import lkl.services.llm 即 fail（E4 信号链路红线）
conftest.py         测试库用 longkonglong_test schema（不污染生产表）
```
黄金用例数据源：docs/DATA_SOURCES.md §1.1 实测样例，固化为 tests/fixtures/*.json。

## 14. 施工顺序与工作量

| 序 | 任务 | 预估 | 卡点风险 |
|----|------|------|----------|
| 1 | T0 基础设施 | 0.5d | 低 |
| 2 | T1 数据拉取 | 1d | 中：全量回填 ~40min 机器时间，先跑 |
| 3 | T2 衍生+对账 | 1d | 中：一致率<99% 需逐因排查 |
| 4 | T3 情绪 | 1d | 高：状态机细节多，黄金日人工核对 |
| 5 | T4 梯队 | 0.5d | 低 |
| 6 | T5/T6 信号 | 1d | 低（依赖 T4 接口清晰） |
| 7 | T7 持仓+执行 | 0.5d | 低 |
| 8 | T8 复盘报告 | 1d | 中：报告信息密度打磨靠用出来迭代 |
| 9 | T9 评估 | 1d | 中：全量 replay 性能（650日×5400股，需 SQL 下推） |
| 10 | T11 LLM层 | 0.5d | 低：独立并行，不阻塞主线；真实key连通性验证需奎爷提供 |
| 合计 | | ~9d | 每任务合入即：pytest 绿 + CLI 实跑 + git commit |

**合入门槛（每任务统一）**：① 函数全部 ≤50 行（E1 审查）② 单测覆盖核心纯函数
③ CLI 实跑输出贴 commit message ④ 不破坏已合入任务的测试。
