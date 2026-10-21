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

### 9.1 技术约束

- 复用主线 `presentation/static/dashboard.css`（GitHub 风格 + 明暗主题），不新增 CSS 框架、不引 CDN、不加 JS 依赖；
- 服务用标准库 `http.server` + `jinja2`（与 `presentation/server.py` 同款，`requirements.txt` 零新增）；
- 独立端口 **8091**、独立 unit `emotion-lab-web.service`；主线 dash 坏了不影响它，反之亦然；
- 折线用**内联 SVG**（服务端算点），不用任何图表库。

### 9.2 页面结构（单页，五段，从上到下即决策顺序）

```
┌ Emotion Core · Lab      [数据 2026-10-20]  [主题切换]  [→ 主线面板]
├ 状态条（一行小字）
│  面板覆盖 675 交易日 · 变体 12（active 8 / retired 4）· 待批提案 1 · 上次训练 2026-10-19 02:11
├ ① 主线基线（数字卡 × 4，每张固定 3 行：值 / n / 口径）
│   [每信号期望 −1.26%]   [次日封板率 43.6%]   [观察名单 378 天]   [最差年度 2025 +0.96%]
│    n=375 口径B触及即成交   n=378              675 交易日中      n=64
├ ② 退化报警（有则显示，无则整块隐藏）
│   ⚠ 封板率 2025 56% → 2026 37%（跌破 40%）   ⚠ v07 连续 2 窗垫底 → 已退役
├ ③ 变体排行榜（表格，按「置信下界」降序）
│   变体｜状态｜与主线差｜n｜期望｜胜率｜置信下界｜最差子样本｜连续入围｜近 10 窗趋势
│   v12  active  +0.80pp  86  +0.42%  58%  −0.20%  −1.9%  2  ▁▂▃▅▆
│   v03  active  +0.11pp  41  −0.15%  52%  −1.80%  −4.2%  1  ▃▂▂▁▂
│   v09  retired −0.60pp  120 −1.86%  39%  −3.10%  −7.0%  0  ▆▅▄▂▁
├ ④ 待批提案（每条 3 行：结论 / 证据 / 动作 + [采纳][驳回]）
├ ⑤ 明细页 /lab/variant/<id>：参数 diff ｜ 分窗期望折线 ｜ 分相位与分年度切片 ｜ 最近 20 条信号
└ 页脚固定一行：本页只展示研究结论，不构成买卖建议；主线产品面不出买卖点（ADR 0002）
```

### 9.3 显示规则（写进实现，不是风格偏好）

| # | 规则 | 原因 |
|---|---|---|
| 1 | 每个数字必须带 **n** 与口径（哪个窗口、哪种成交假设）；n<30 整行置灰并写「样本不足」 | 小样本数字看着像结论，最容易被误读 |
| 2 | 缺失显示 **「—」**，永不显示 0 | 项目既有 F6 纪律 |
| 3 | 排序默认按 **置信下界**（`mean − 1.645σ/√n`），不按均值 | 抢眼的均值多是噪声 |
| 4 | 「与主线差」单独一列，**并排不混排** | 同 ADR 0001 D7「CPT 结论只并排、不参与判定」 |
| 5 | 红绿只表示**相对主线的改善/恶化**，不表示涨跌 | 避免看起来像行情 |
| 6 | 报警（事实）与提案（建议）**分两块**，不混排 | 事实不需要拍板，建议才需要 |
| 7 | 术语后括号写人话：`置信下界（小样本惩罚后的期望）`、`MIN_LEADER_DAYS（最小连板门槛）` | 用户要求「说人话」，本页也要遵守 |
| 8 | 状态只用四值：`active` / `draft` / `retired` / `pending`，中文标签配同一套 badge 颜色 | 与主线 badge 语义一致 |

### 9.4 与主线的边界

- 页面上唯一的写操作是提案的 `[采纳]/[驳回]`，**只写 `lab_promotion.status`**，不改任何 `public` 表、不改代码、不重启服务；
- 采纳后页面顶部提示：「已记录决定。改主线常量仍需人工修改 + 记 ADR + 跑回归。」——闭环最后一步永远是人。
- 主线 dash 不新增「实验」入口（避免耦合）；Lab 页面自带「→ 主线面板」链接。

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

## 13 拍板结果（2026-10-21）

1. 隔离：**同库新 schema `lab`** + 只读授权（已确认）。
2. 晋升：**提案制，人拍板**（已确认）。
3. 搜索空间：首版**只开 A 档**，相位阈值（B 档）只观测不采纳（已确认）。
4. KPI：P1/P2 只承诺「度量准确 + 退化报警 + 淘汰可用」，不承诺正期望（已接受）。
5. 页面：见 §9，按「基线 → 报警 → 排行榜 → 提案 → 明细」五段排布（已确认方向）。

## 14 P1 实施记录（2026-10-21，已上线）

### 14.1 落地物

| 位置 | 内容 |
|---|---|
| `src/emotion_core/lab/` | 新子包：`db`（连接 + 读写守卫）、`schema`（五张表 DDL）、`metrics`（口径与统计）、`panel`（面板物化）、`score`（打分）、`runner`（每日流水线）、`web`（:8091 只读页）、`templates/`、`static/lab.css` |
| `tests/unit/test_lab_metrics.py` | 17 例：成交看最高价、卖在 T+2 开盘、跌停封死顺延、右截断不猜、统计量小样本纪律 |
| `tests/architecture/test_lab_isolation.py` | 6 例：主线不得 import lab、lab 不得碰 services/orchestration/presentation/data、写语句只能落 `lab.`、禁 f-string 拼 SQL、读写守卫反例 |
| `deploy` → `src/emotion_core/orchestration/systemd/` | `emotion-lab.service`（oneshot，每日 17:45 Asia/Shanghai）、`emotion-lab.timer`、`emotion-lab-web.service`（:8091） |
| `var/lab/panel.pkl` | 面板缓存（8801 行 / 675 天 / 1988 票 / 2.0 MB），`.gitignore` 已加 `var/` |

### 14.2 基线首次跑通（口径B 触及即成交）

窗口 2024-01-01 ~ 2026-10-21，主线 `signal` 里 378 条 BUY 观察记录，可评估 375 条：

| 口径 | 值 |
|---|---|
| 成交率 | 63.7%（T+1 最高价够到 T 日收盘 × 板比 的涨停价） |
| 每信号期望（未成交算 0） | **−1.16%**，95% 单侧置信下界 −1.95% |
| 成交后 | −1.85%，中位 +0.12%，胜率 50.8% |
| 最差一笔 | −34.03% |

与 `_audit/probe_fill.py` 的 −1.26% / 成交后 −2.01% 同向同量级（残差来自 2 条右截断样本与
跌停顺延判据的收严：现在要求 T+2 **开盘与收盘都在跌停**才算卖不掉）。

页面首屏因此直接报警：「主线每信号期望为负 —— 这不是退化，是现行规则本身没有正期望」。

### 14.3 三个实现期踩到的坑（已写进代码注释与测试）

1. **涨停基数是 T 日收盘，不是 T−1**：先用 `pre_close` 算涨停价，成交率虚高到 97.7%、胜率 0.8%。
2. **买限价在现价之上，成交看 `high` 不是 `low`**：写成 `low <= limit` 会让"跌下来"也算成交 —— 与 A/B/C 三轮的事后视角错误同源，现在是 `test_fill_needs_high_not_low_pin` 钉住的接口级约束。
3. **涨跌停封死要开收同判**：只看开盘会把「开盘砸跌停又撬板」误判成卖不掉。

### 14.4 未做（P2 之前）

- `lab_runner` 数据库角色：当前用同一连接 + 代码级写守卫（`check_write` 只放行 `lab.`），角色级最小权限留到 P2（要改 `utils/db.py` 支持第二份配置）。
- 页面「提案采纳/驳回」的写路径已实现，但 P1 没有提案来源，实际不会被触发。
- 面板缓存与服务日志都在 `var/lab/`，未做清理策略（单文件 2 MB，可忽略）。

## 15 P2 实施记录（2026-10-21，已上线）

### 15.1 落地物

| 文件 | 作用 |
|---|---|
| `src/emotion_core/algorithms/state.py` | 新增 `StateRules`(frozen) + `DEFAULT_RULES`；四个 `_rule_*` 与 `classify_series(series, rules=None)` 全部读规则对象。不传规则 = 原行为（`tests/caliber/test_state_rules_default.py` 钉住每条字段与 `CONFIG` 的对应关系） |
| `src/emotion_core/lab/panel.py` | 面板扩列到「够重跑一遍判据」：`derived_bar` 的 `is_one_word/is_limit_up/is_bomb/touched_limit`、`limit_pool_em` 的 `bomb_times/turnover_rate`、`stock_basic.name`、`market_stat` 全部状态机输入 |
| `src/emotion_core/lab/rules.py` | `LabRules`(frozen) = 状态机 9 项 + 判据 5 项 + 档位倍率 3 项；`variants()` 只造**单点变体**（一次改一个旋钮，17 个，避免 8 个旋钮 5832 格组合爆炸） |
| `src/emotion_core/lab/replay.py` | 内存内重跑「状态机 → 梯队 → c1..c5 → 档位」整条判据，`signals()` 出观察记录；默认规则下与 `signal` 表 BUY 集合**逐条一致**（377 vs 377，`tests/e2e/test_lab_replay_live.py` 真库复核） |
| `src/emotion_core/lab/search.py` | 选参/验证切分 + 七道门禁 + ±1 邻域稳健性 + 淘汰标记 + 提案落库 |
| `src/emotion_core/lab/notify.py` | Lab 自己的飞书摘要（不复用主线 `services/notify.py`；正文明确写「没有变体过门禁是有效结论，不是故障」） |
| `src/emotion_core/lab/runner.py` | 面板 → 基线打分 → 变体搜索 → 提案 → 推送；新增 `--no-search/--no-push` |

### 15.2 首轮搜索结果（2024-01-01 ~ 2026-10-20）

- 单点变体 **17 个**（`min_leader_days` 3/5、`diverge_min_turnover` 3/4/5/6、`climax_zt` 70/90、`ebb_zt_perf` −3/−1、`ferment_zt_perf` 1/2、`ice_zt_max` 30/50、`tier_mult_strict=0`、`tier_mult_newcomer=0/0.5）。
- 切分：选参 2024-01-03 ~ 2026-04-21（317 条），验证 2026-04-22 ~ 2026-10-20（58 条）。
- 基线：选参 −1.161%，验证 **−1.178%**（LCB −2.699%，t=−1.27）。
- **过七道门禁：0 个**。选参区间最好的是 +0.29pp 的变体，验证区间退化到 −1.21% —— 教科书式的过拟合，被第四道门禁（验证区间必须优于基线）拦下。
- 淘汰：hold-out LCB 比基线低 1pp 以上者标 `retired`（本轮无），其余 `draft`；`lab.lab_promotion` 仍为 0 行。

结论：**现行参数邻域内没有可提升的组合**。这不是搜索失败，而是这次搜索应得的答案——
关掉门禁的话，同一批数据能立刻"找出"一个 +0.29pp 的变体。

### 15.3 顺手修掉的一个真缺陷

`_INSERT_SCORE` 的 `ON CONFLICT DO UPDATE` 漏了 `window_name = EXCLUDED.window_name`，
于是 upsert 时 sel/hold 两类行会被先前的 NULL→`full` 回填永久钉成 `full`，页面上
「一个变体出现两行」且窗口列全是同一段。已补，且 `web.py` 的排行榜改 `LEFT JOIN LATERAL
… ORDER BY (window_name='hold') DESC, window_end DESC LIMIT 1`，保证每个变体一行、取验证区间。

### 15.4 未做

- `lab_runner` 数据库角色（仍靠代码级写守卫）。
- 淘汰后的「连续 2 窗进前 20%」才可复活/晋升的历史序列；目前 `retired` 只按单轮 LCB 差判定。
- 飞书摘要的定时：`emotion-lab.timer` 每日 17:45 跑一遍（面板 + 基线 + 搜索 + 推送，实测全流程约 40s），
  hold-out 窗口随之前移，等价于每日前滚一天。
