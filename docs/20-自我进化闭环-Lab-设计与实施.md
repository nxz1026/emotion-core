# 20 自我进化闭环（Lab）：设计与实施

- 状态：**方案待拍板**
- 日期：2026-10-21
- 前置：ADR 0002（B 轮：盘后可执行买点被证伪）、ADR 0003（C 轮盘中采集，已拍板「要建、暂缓」）
- 一句话：把「规则参数 → 事实结果」变成可自动迭代的闭环，但**人始终在晋升环节**，
  且**永远不碰主线业务表**。

## 1 与「神经网络训练」的对应关系（先说清楚能学什么）

| 神经网络的部件 | 本系统的对应物 | 差异（必须承认） |
|---|---|---|
| 权重 | 判据参数（相位阈值、板数门槛、c3 档位权重、封板质量过滤） | 参数是离散、少量（~20 个），不是百万权重 |
| 前向传播 | 用某组参数在历史上重放（shadow replay）→ 假设信号 | 非可微、无梯度 |
| 损失函数 | 每信号的费后期望、胜率、稳定性 | 噪声极大，单日样本不可信 |
| 梯度下降 | 进化式随机搜索（变异 + 选择 + 淘汰） | 市场非平稳，不能用 i.i.d. 划分 |
| 训练/验证集 | **walk-forward 滚动窗**（前 500 交易日选参 → 后 60 交易日验证） | 时间序列，必须按时间切 |
| 过拟合 | 变体数越多越容易钓出假阳性 | 用多重比较校正 + hold-out + 邻域稳定性 |

**诚实结论**：675 个交易日 + 20 个离散参数，这里能学到的是「**哪个参数组合在样本外仍成立、
哪个已经退化**」，学不到「盘后凭日线给出正期望买点」（ADR 0002 已证否）。
所以本闭环第一版的 KPI 是**度量准确 + 退化报警**，不是「找到买点」。

## 2 隔离架构（保证不影响主线业务）

| 层 | 主线（现状） | Lab（新增） | 隔离手段 |
|---|---|---|---|
| 数据库 | schema `public` | schema **`lab`** | 角色 `lab_runner` 只有 `lab` 写权限 + 指定 public 表 `SELECT` |
| 代码 | `emotion_core.{algorithms,services,presentation,orchestration}` | `emotion_core.lab`（新子包） | 架构测试：主线**不得** import `emotion_core.lab` |
| 进程 | `emotion-core-dash.service`、daily 定时 | `emotion-lab.service`（周度训练）+ `emotion-lab-web.service`（页面 8091） | 独立 unit、独立端口 |
| 报告/推送 | `review.publish`、`notify.push` | 仅写 `lab` 表 + 独立的飞书提案消息 | 不复用主线推送函数 |

```sql
CREATE SCHEMA IF NOT EXISTS lab;
CREATE ROLE lab_runner LOGIN PASSWORD '…';           -- 口令进 ~/.dbconfig，不进代码
GRANT USAGE ON SCHEMA lab, public TO lab_runner;
GRANT ALL ON ALL TABLES IN SCHEMA lab TO lab_runner; -- 只对 lab 有写权
GRANT SELECT ON public.daily_bar, public.derived_bar, public.market_stat,
      public.ladder_day, public.limit_pool_em, public.signal, public.signal_outcome,
      public.trade_calendar, public.stock_basic TO lab_runner;
-- 关键：不给 INSERT/UPDATE/DELETE on public.*
```

`utils/db.py` 增 `connect_lab()`：同 `~/.dbconfig` 的连接参数 + `user=lab_runner` +
`options='-c search_path=lab,public'`。**主线代码一行不改**。

## 3 闭环五件套

| 组件 | 职责 | 产出 | 频率 |
|---|---|---|---|
| Labeler（贴标） | 把 `lab_signal` 接真实结果：T+1开盘买/T+2开盘卖、次日封板、破板 | `lab_outcome` | 每交易日收盘后 |
| Replayer（回放） | 用变体参数在历史上重放，产出假设信号 | `lab_signal` | 每周 |
| Scorer（打分） | 按验证窗算 n / 费后期望 / 胜率 / 稳定性 / 置信下界 | `lab_score` | 每周 |
| Selector（选择淘汰） | 排名、淘汰垫底、写晋升提案 | `lab_promotion` | 每周 |
| **人（拍板）** | 审提案 → 手动改主线常量 + 记 ADR | ADR + CHANGELOG | 按需 |

**没有自动晋升。** 这与项目现有纪律一致（`services/calibrate.py` 的 L2 周校准历来「只提案不落库」）。

## 4 学什么：搜索空间分三档

| 档 | 参数 | 说明 |
|---|---|---|
| A 安全档（首版只做这些） | `MIN_LEADER_DAYS`(3–5)、`SECONDARY_MIN_DAYS`(2–4)、`DIVERGE_MIN_TURNOVER`(3–6)、c3 档位权重（严格/延续/插队）、封板质量过滤（首封时刻分档、炸板次数上限）、板块前缀白名单 | 只影响信号产出，不改相位语义 |
| B 需 ADR 档 | 相位阈值 `CLIMAX_ZT`、`CLIMAX_AMPLITUDE`、`FERMENT_ZT_PERF`、`EBB_ZT_PERF`、`EBB_LD_MIN/MULT`、`ICE_ZT_MAX/MAX_DAYS` | 改的是「市场状态」定义，会改写 675 天历史相位 → 必须单独立 ADR |
| C 不做档 | 全自动调参落主线、任何形式的线上自动改代码、把 lab 结果写进 `public` | — |

B 档在首个版本里**只观测不采纳**：把变体的相位改动也回放出来，仅供对比看相位漂移多敏感。

## 5 反过拟合硬门禁（本方案的核心）

| 门禁 | 规则 | 理由 |
|---|---|---|
| 最小样本 | 验证窗 n < 30 不给分，只标「样本不足」 | 沿用项目既有纪律（docs/03 §2.5） |
| Hold-out | 最近 **120 个交易日永不参与选参**，只做最终确认 | 防止「越调越准」的时间泄漏 |
| Walk-forward | 每窗：用前 500 天选参 → 后 60 天验证；窗间滚动 | 不做 i.i.d. 随机切分 |
| 排序用下界 | 排名按 `期望 × Wilson下界胜率`，不按原始均值 | 惩罚小样本 |
| 多重比较 | 记录累计变体试验数 T；晋升要求 `t ≥ 3` 或 `α/T` 校正后仍显著 | 试 200 个组合必然出「神配置」 |
| 邻域稳定 | 参数 ±1 步长内结果不得塌（期望跌幅 > 60% 视为不稳） | 排除刀尖上的参数 |
| 连续窗口 | 需连续 2 个窗口进前 20% 才进 shortlist | 防单窗幸运 |

## 6 表设计（全部在 schema `lab`）

```sql
lab_variant   (variant_id text PK, params jsonb NOT NULL, status text NOT NULL,  -- draft/active/retired
               parent variant_id, created_at timestamptz, note text)
lab_signal    (variant_id, confirm_date, code, action, phase, tier, checklist jsonb,
               PRIMARY KEY (variant_id, confirm_date, code, action))
lab_outcome   (variant_id, confirm_date, code, entry_px, exit_px, ret_pct numeric(8,3),
               sealed boolean, broke boolean, PRIMARY KEY (variant_id, confirm_date, code))
lab_score     (variant_id, window_start date, window_end date, n integer,
               expectancy numeric(8,3), win_rate numeric(5,2), seal_rate numeric(5,2),
               lcb numeric(8,3), sub_worst numeric(8,3),   -- 最差子样本（年度/相位）
               trials integer, updated_at timestamptz,
               PRIMARY KEY (variant_id, window_start, window_end))
lab_promotion (id bigserial PK, variant_id, proposal text NOT NULL, evidence jsonb,
               status text NOT NULL DEFAULT 'pending',   -- pending/approved/rejected
               decided_by text, decided_at timestamptz, created_at timestamptz)
```

`public.eval_result` 已是同样的 `(name, params jsonb, result jsonb)` 形状，本方案**只借形状不借表**。

## 7 参数化重构（唯一动主线的部分，且零行为变化）

现状：`algorithms/state.py` 的阈值是模块级常量、`classify_series` 直接引用；
`algorithms/entry.py` 的 `rows/checklist` 已接受 `min_days`、`diverge_min`。

改法（**纯函数化，默认值即现值**）：

```python
@dataclass(frozen=True)
class StateRules:
    climax_zt: int = CONFIG.CLIMAX_ZT
    climax_amplitude: float = CONFIG.CLIMAX_AMPLITUDE
    ferment_zt_perf: float = CONFIG.FERMENT_ZT_PERF
    ebb_zt_perf: float = CONFIG.EBB_ZT_PERF
    ebb_ld_min: int = CONFIG.EBB_LD_MIN
    ebb_ld_mult: float = CONFIG.EBB_LD_MULT
    ice_zt_max: int = CONFIG.ICE_ZT_MAX
    ice_max_days: int = CONFIG.ICE_MAX_DAYS

def classify_series(days, rules: StateRules | None = None) -> list[EmotionState]:
    ...   # rules = rules or StateRules()
```

- 主线调用点一律不传 `rules` → 行为逐日不变；
- 守卫测试：`test_state_rules_default_equals_current`（对全史 675 天逐日比较 `rules=None` 与旧实现的相位）；
- 同理 `EntryRules` 包住 `min_days/diverge_min/require_competition`；
- **不进 `CONFIG`**（会改 `config_hash`），`StateRules` 住 `algorithms/state.py` 模块内。

## 8 性能：先物化面板，再内存回放

| 步骤 | 做法 | 目标 |
|---|---|---|
| 面板物化 | 一次性把 `daily_bar`+`derived_bar`+`ladder_day`+`market_stat` 落 parquet（`lab_panel/{start}_{end}.parquet`），按日增量更新 | 只读一次 DB |
| 日级指标 | 涨停数/炸板率/连板高度等与变体无关的量算一次复用 | — |
| 变体回放 | 纯内存 pandas + `StateRules`/`EntryRules` | 单变体 675 天 ≤ 10s |
| 周度训练 | 30 变体 × 10 窗 | ≤ 1 小时（夜间跑） |

B 轮的教训直接套用：**别用 `LEFT JOIN LATERAL` 逐行找前一日**（933s 未返回），
用 `WINDOW ... lag()` / `groupby.shift`（1 分钟）。

## 9 Web 独立页（不动现有 dash）

- `python -m emotion_core.lab.web --port 8091`，模板在 `src/emotion_core/lab/templates/`。
- 页面四块：①变体排行榜（n / 期望 / 胜率 / LCB / 状态）②单变体累计期望曲线 ③晋升提案
  （人可点 approve/reject，**只写 `lab_promotion`**）④数据新鲜度与样本告警。
- 与主线展示层的唯一共享物是 `wording.py` 的词表（只读 import）。

## 10 节奏

| 频率 | 动作 |
|---|---|
| 每日收盘后 | Labeler 贴标（补齐 T+1/T+2 结果） |
| 每周日 | Replayer + Scorer + Selector：变异 → 回放 → 打分 → 淘汰 → 写提案 → 飞书推一条 |
| 按需 | 人审提案；采纳则改主线常量 + 记 ADR（保持「改值必记 ADR」纪律） |

## 11 不做清单

- 不做梯度下降/神经网络（非可微、样本少、市场漂移）；
- 不让 lab 写 `public` 任何表，不 import 主线写路径；
- 不自动晋升、不自动改代码、不自动重启服务；
- 不在 C 采集器上线前承诺「找到正期望」；
- 不把 lab 页面嵌进现有 dash（独立端口、独立 unit，坏了不影响主线）。

## 12 分阶段与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1**（1–2 天） | schema `lab` + 角色 + 面板物化 + Labeler + Scorer + 独立只读页面（**只展示当前主线**这一条变体） | 页面上出现主线在 675 天上的期望/胜率/封住率，且与 `replay_service` 结果一致 |
| **P2**（2–3 天） | `StateRules`/`EntryRules` 重构 + Mutator + walk-forward + 淘汰 + 提案 + 飞书 | 连续两周产出提案；guard 测试全绿；主线零行为变化 |
| **P3**（等 C） | 接入盘中封单量维度（ADR 0003 D 段） | 门禁（样本 ≥60 / 封住率 ≥71%）达成后再谈是否采信 |

每阶段独立可回滚：lab 全是新增表 + 新进程，停掉 `emotion-lab.service` 即回到今天的状态。

## 13 待拍板

1. **隔离方式**：同库新 schema `lab` + 只读授权（推荐，能 join 主线数据）vs 独立数据库（更强隔离、无跨库 join）。
2. **晋升纪律**：提案制、人拍板（推荐）vs 允许自动落主线（不建议）。
3. **搜索空间**：首版是否只开 A 档（信号参数 + 封板质量过滤），相位阈值（B 档）只观测不采纳。
4. **KPI 认可**：P1/P2 只承诺「度量准确 + 退化报警 + 淘汰可用」，不承诺产出正期望（ADR 0002 已证否盘后买点）。
