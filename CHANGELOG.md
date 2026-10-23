# 更新日志

所有 notable 变更均以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### 变更 · 2026-10-23 退潮判据收紧（N4）

「最高板连着两天炸板」不再是退潮的充分条件，还要求当日跌停 >15 家。677 天实测：
退潮 292→220 天、高潮↔退潮切换 180→149 次；被移出的 72 天当日涨停表现中位 +0.57%、
次日高度不降率 80.6%（高于仍判退潮的 75.3%），属被误标。全量重算后 BUY 记录 380→371，
事后对照期望 −1.251%→−1.351%（收益不是这次的目的，可读性才是）。对账测试改为
「旧判据必须重现 lkl 661 天 + 新判据的分歧只允许是退潮收紧」。详见 ADR 0002「N4」节。

### 功能 · 2026-10-23 观察记录的事后对照（口径B）+ 相位持续性

报告 ⑥ 段新增「事后对照（口径B）」：把最近 30 条观察记录按「当时真打板买入」重算一遍，
给成交率、每信号期望、成交后期望与分组表（全部/相位/身份档）。全史 379 条：成交率 62.8%、
每信号 −1.251%、成交后 −1.992%，与 Lab 基线同向同量级。

- 新增 `algorithms/caliber_b.py`（触及即成交、T+2 开盘卖、跌停封死顺延、费 0.15%、右截断单列）
- 新增 `algorithms/phase_persistence.py`：① 段末尾加「退潮已连续 4 天；历史上第 4 天的退潮，次日仍退潮 24/28 次（86%）」
- **口径修正**：`presentation/server.py::_load_negative_expectation()` 原读 `signal_outcome.rule_ret_a`（断板卖，已废止口径，≈ −4.28%）→ 改口径B（≈ −1.25%）
- 单测 22 例（`tests/unit/test_caliber_b.py`、`tests/unit/test_phase_persistence.py`）

### 修复 · 2026-10-23 补录深市主板 003 号段（含全量重算）

`CONFIG.BOARD_PREFIXES` 从 7 个前缀补到 8 个（`+ "003"`）。此前 003 开头的深市主板票
**整段没进候选池**：42 只票、28,411 根日线、664 根涨停 bar 从未参与过梯队与情绪判定。

- `algorithms/promotion.py` 里复制的一份 7 前缀硬编码改为 `tuple(CONFIG.BOARD_PREFIXES)`（单一来源）
- 全量重算：`ladder_day` 8810 → 8965 行（+155）、`signal` BUY 377 → **380**、`signal_outcome` complete 447
- 新增 3 条 BUY 全来自 003；2026-10-20 的结论从「今天没有符合条件的连板龙头」变成
  `003029 吉大正元 —— 硬条件全过`
- `config_hash` 换代 → `fdbd6541cbc4`（历史行保留旧指纹，属预期）

### 功能 · 2026-10-23 盘中台账 `trigger_log` + 收盘结算 `--settle`

ADR 0003 第二层的前半截（建表 + 结算），常驻采集器仍不建。

- `data/schema.py` 新增 `trigger_log`（十五列，PK `(date, code)`，索引 `idx_trigger_log_date`），库里已建（15 列 / 0 行）
- `data/loader.py`：`insert_triggers`（`ON CONFLICT DO NOTHING`，首次触发为准）、`load_triggers`、`save_settlements`
- `services/intraday_watch.py`：`--settle` 按口径 B 结算 —— 卖点 T+1 开盘、跌停封死顺延 T+2（`rolled`）、
  两天都封死不写 `exit_px`、`ret_pct` 扣双边费 0.15%
- 常驻单元 `emotion-intraday.service` 暂不创建：CPT 交付只有 5 列，缺 `price/prev_close/chg_pct/name`，
  本仓判据算不了（ADR 0003 §H.3 缺口①）

### 变更 · 2026-10-22 盘中 provider 改读 CPT 实际交付表（ADR 0003 §H.4）

- CPT 实际交付的不是 §G 的 13 列 `intraday_quote`，而是 5 列结论表
  `public.intraday_seal_snapshot`（`at` / `code` / `sealed` / `bid1_lots` / `limit_price`），
  存的是 CPT 侧 `seal_strength()` 的判定结果。
- `src/emotion_core/data/providers/cpt_intraday.py` 改读该表：`_SELECT_SQL` 用
  `WHERE at = (SELECT max(at) ...)` **取整轮**（§H.3 第 3 条：按 code 单点查最新会把
  「上一轮封着、这一轮开板」读成两只票）；新增 `fetch_round()` 返回 `(round_at, {code: record})`。
  保留原契约：表不存在 → `None`（不是故障），表在但无轮次 → `{}`。
- `bid1_lots` 单位是**手**，本仓 `SEAL_BID1_VOL_MIN` 是股，比较前须 ×100（写进模块 docstring 与单测）。
- `tests/unit/test_cpt_intraday.py` 按新表结构重写（7 例，含「SQL 必须是 `at = max(at)`」与
  「列清单与 §H.2 逐字对齐」两条防回归）。
- 当日 23:30 按 CPT 的 `docs/intraday-seal-contract.md` §3 定稿（该文件现为读表口径的权威来源）：
  新增 `sealed_state()` 三态判定（**没行 = 判不了**，`sealed=False` 且 `bid1_lots` 为 NULL
  也是判不了，都不许当开板证据）、`bid1_vol_shares()` + `SHARES_PER_LOT = 100`（手→股）、
  修 `round_at` 被 code 过滤抹成 `None` 的缺陷；单测扩到 17 例。
- `trigger_log` 仍未建（数据源已就位，等拍板后与 `--settle`、常驻单元一组动作落地）。

### 功能 · 2026-10-22 CPT 观察期可观测化（ADR 0003 §D10 条件②）

- 原口径「盘中定时任务连续 5 个交易日无失败」不可观测（只能人肉翻日志数 error），
  且太松 —— 采集器每轮 `skipped` 时同样「一条 error 都没有」但一天没采到东西
  （2026-10-22 实测 CPT 侧真踩了：第一版 `trading_day()` 读 `max(date) FROM daily_bar`，
  而日线盘后才写 ⇒ 盘中恒判休市；CPT 侧已修为读 `trade_calendar.is_open`）。
- 新增 `src/emotion_core/lab/cpt_watch.py`（只读）：按「交易日 × 轮次」判定，
  交易日看 `trade_calendar.is_open`；当日 `error`=0 且 `ok` 轮数 ≥10 才算干净；
  `empty` 不算失败；今天盘中未收盘不计入连续串。
  CLI `python -m emotion_core.lab.cpt_watch`（退出码 0 = 闸门已过）；Lab 页 :8091
  加观察期小节与逐日表；单测 `tests/unit/test_lab_cpt_watch.py`（16 例）。
- ADR 0003 §D10 就地改写「② 的可观测化」小节，头部状态同步。

### 修复 · 2026-10-22 盘中判据板比（跨仓对账查出真 bug）

- `algorithms/intraday.py::board_pct_limit` 判定顺序改为「创业板/科创 → 北交所 → 主板ST → 其余」：
  原先 ST 判定在最前，北交所 ST 落到 20%，会算出**偏低的涨停价** ⇒ 「已经封板」被误判成
  「还没到板」（方向比漏报更糟）。跨仓 probe 实测 `limit_price` 不一致 40 条、`is_at_limit`
  不一致 60 条，全部是 `920xxx` + ST 名组合。已加 `tests/oracle/test_intraday_rules_vs_cpt.py` 逐条钉住。

### 功能 · 2026-10-22 盘中 provider 第一层（CPT 接入）

- 新增 `data/providers/sina_quote.py`：纯 stdlib 批量五档（新浪），与 CPT
  `SinaQuoteClient.fetch_quotes` 逐字段对账（`tests/oracle/test_intraday_quote_vs_cpt.py`），
  作对照 + CPT 采集器上线前的生产路径。
- 新增 `data/providers/cpt_intraday.py`：读 CPT 落库表的预留口，**表不存在返回 `None` 不炸**
  （表还没建是正常状态）。
- ADR 0003 更新：§D10 的「CPT 稳定」改成可验证标准（连续 5 个交易日 `cpt-dashboard` 无 error
  + 盘中定时任务无失败）；新增 §F 记录第一层落地与「本层没做什么」。


### 功能 · 2026-10-21 Lab P2 上线（参数化 + 搜索 + 七道门禁）

- `algorithms/state.py` 新增 `StateRules`(frozen) / `DEFAULT_RULES`，四个规则函数与
  `classify_series(series, rules=None)` 支持外部传参；不传 = 原行为（`tests/caliber/test_state_rules_default.py` 逐字段钉住与 `CONFIG` 的映射）。
- 新增 `lab/rules.py`（`LabRules` 13 旋钮 + 单点 `variants()`）、`lab/replay.py`（内存重跑判据）、
  `lab/search.py`（选参/验证切分 + 七门禁 + ±1 邻域 + 淘汰 + 提案）、`lab/notify.py`（飞书摘要）。
- 等价性：默认规则回放与 `signal` 表 BUY 集合逐条相等（377 vs 377），相位与 `market_stat` 逐日相等
  （`tests/e2e/test_lab_replay_live.py`，真库复核）。
- 首轮 17 变体：选参 317 条 / 验证 58 条，基线验证 −1.178%（LCB −2.699%），**过门禁 0 个**；
  选参最优变体在验证区间退化 ⇒ 门禁拦下教科书式过拟合。
- 修复：`lab_score` upsert 漏更新 `window_name`（sel/hold 行被回填成 `full`），排行榜改 `LATERAL` 取验证区间一行。
- 修复：`algorithms/intraday.py` 自带涨停价实现违反「唯一实现」门禁，改为复用
  `utils/price.limit_up_price_cents(..., pct_milli=)`，函数更名 `limit_price`（`utils/price.py` 增加 `pct_milli` 覆盖，供 ST 5% 使用）。

### 功能 · 2026-10-21 Lab P1 上线（自我进化闭环的度量层）

- 新增 `src/emotion_core/lab/` 子包（`db`/`schema`/`metrics`/`panel`/`score`/`runner`/`web`）：
  同库新 schema `lab` 五张表、口径B 触及即成交的收益口径、面板物化（8801 行/675 天/2 MB）、
  主线基线打分、`:8091` 只读页面。
- 新增 `emotion-lab.service`（每日 17:45 Asia/Shanghai）+ `emotion-lab.timer` +
  `emotion-lab-web.service`，与主线 unit 无 `Requires` 依赖，停掉即完全回滚。
- 基线首次跑通：成交率 63.7%、每信号期望 **−1.16%**（LCB −1.95%）、成交后 −1.85%、
  胜率 50.8%，与 `_audit/probe_fill.py` 的 −1.26% 同向同量级；页面首屏即报「现行规则无正期望」。
- 新增 `tests/unit/test_lab_metrics.py`（17 例）、`tests/architecture/test_lab_isolation.py`（6 例）：
  钉住成交看最高价、卖在 T+2 开盘、跌停开收同判、小样本不给分位数，以及
  主线不得 import lab / lab 写语句只能落 `lab.` / 禁 f-string 拼 SQL。
- `docs/20-自我进化闭环-Lab-设计与实施.md` 增 §14 实施记录（含三个实现期坑位）。

### 文档 · 2026-10-21 自我进化闭环（Lab）方案

- `docs/20-自我进化闭环-Lab-设计与实施.md`（**新增，方案待拍板**）：与神经网络的逐项对应关系、
  schema `lab` + `lab_runner` 角色隔离（主线零改动）、闭环五件套（贴标/回放/打分/淘汰/人拍板）、
  搜索空间三档（安全档 / 需 ADR 档 / 不做档）、七条反过拟合门禁（hold-out 120 天、
  walk-forward、LCB 排序、多重比较、邻域稳定、连续窗口）、五张 lab 表、`StateRules` 参数化重构
  （默认值即现值、逐日守卫测试）、面板物化 + 内存回放性能方案、独立 8091 页面、P1/P2/P3 分阶段验收。

### 文档 · 2026-10-21 B 轮结论与 C 轮可行性（ADR 0003）

> B 轮在「成交假设必须能触发」硬约束下把追强族三种入场、妖股回调、炸板次日、龙头断板、
> 情绪择时全部重算：**域内没有正期望的可执行买点**（追强三口径 −0.71% ~ −1.78%，
> 妖股回调仅在 T−1 无涨停时 +0.50%）。D1 据此定档「不出买点」，产品面维持 A 止血状态。

- `docs/decisions/0002-策略口径修正-退潮解禁与c3定档.md`：新增「B 轮结论」整节
  （三族探针表 + 判决三条 + 教训「任何事后状态字段都不能当成交判据」）。
- `docs/decisions/0003-盘中触发-可行性与架构.md`（**新增；已拍板「要建」，暂缓实施**，含 D 段实施蓝本）：数据源实测表
  （新浪五档/涨幅榜可用、腾讯可回退、东财 push2 不可靠、CPT 无盘中）、三件套架构
  （涨幅榜扫描 → 30s 五档采集 → 触发+收盘回填）、定档门禁（样本 ≥60、跨 ≥3 个月、
  封住率 ≥71%、组合 >0）、风险与待拍板三问。
- `docs/15-实施追踪.md`：新增「B 轮 · 重新寻找可执行边缘」整节。
- 探针（只读，产物在 `_audit/`）：`probe_b_pullback.py`、`probe_b_alt.py`、
  `probe_b_reversal2.py`、`probe_b_market.py`、`probe_c_seal_quality.py`。
- `docs/decisions/0003` 追加 **D 段实施蓝本**（奎爷 2026-10-21 拍板：要建，但 CPT 在修 bug，
  先留文档后再动手）：文件清单与分层、两张表 DDL（`intraday_quote` / `trigger_log`）、
  7 个模块级常量与依据、主循环伪代码、触发/松动判据、收盘回填、systemd 单元、
  测试清单、DoD、暂缓原因与重启条件。
- 本轮无生产代码变更（产品面维持 A 止血态：只观察、不出买卖点）。

### 变更 · 2026-10-21 A 止血：买点/卖点从产品面移除（ADR 0002「复核」+「A 止血」）

> 起因：口径 B 的成交假设复核证伪——`_audit/probe_exec.py` 用「次日收盘仍封住涨停」
> 当「买到了」，是事后视角；真实打板在**触及涨停那一刻**成交，之后炸板撤不回。
> 按「触及即成交」重算同一批 375 个信号（同买入价、费后 0.15%）：**每信号 −1.26%**、
> 成交后 −2.01%、胜率 50%、最差 −34.03%，炸板桶 71 条均亏 15.04%。
> ⇒ 「盘后决策 + 仅日线」没有正期望的可执行买点。奎爷拍板「先 A 止血，再 B+C」。

- **删除买点产出**（`algorithms/entry.py`）：`buy_point()`、`plan_text()` 及其 `utils.price` 导入；
  `_INSERT_SQL` 的 `reason` 改 SQL 字面量 `NULL`，参数 9→8；`_persist(sig, window, rows)` 去掉 `reason`。
- **档位语义降级**：`PositionTier` 标签 满仓/半仓/观望 → **优先/次选/不参与**，`weight` 仅作过滤器
  （`weight <= 0` 不落记录），不再对用户表达仓位。
- **内存操作卡**：`domain/signal.py` 删除 `Signal.plan`。
- **报告**（`algorithms/review/__init__.py`）：⓪ 顶部卡改「市场 / 观察 / 次日验证点」三行并引用
  −1.26% 实测；⑥ 段「买入信号」→「观察名单」，结论改「只进观察名单；本系统不出买入建议」，
  删除「卖出建议」逐条渲染；`_sec_trend`/`_sec_overview` 的「能不能买」→「情绪档」；
  「买入窗口」全库改「情绪档」（`wording.WINDOW_MAP` → 活跃/一般/低迷）。
- **推送**（`services/notify.py`、`services/signal_service.py`）：`push_buy_signal` **默认关闭**
  （`EMOTION_PUSH_BUY=1` 才推）；标题「今日观察记录（不出买入建议）」，正文禁买点/卖点/仓位；
  `_HUMANIZE_BUY_PROMPT` 禁止写成「可以买入」；仅 `action=BUY` 触发推送。
- **持仓**（`services/holding_service.py`）：观察视角（无成本）恒 `HOLD`「只观察（不出建议）」，
  `levels=_NO_LEVELS`/`sizing=_NO_SIZING`/`ladder=()`，新增 `facts.advice=False`；
  持仓视角（有成本）的时间退出（T+2 开盘卖、T+3 强制了结）**保留**。
- **展示层**：`stock.html`「买点参考」→「打板价（口径B · 已证伪，仅供复盘对照）」、
  `intuitive.html`「当日推荐」→「当日观察名单」、「买入窗口」→「情绪档」；
  `stock_service`/`odds_service` 的 disclaimer 与 docstring 标注已证伪；
  `server._explain_no_buy`、`diagnose_service._WINDOW_TEXT` 同步。
- **数据**：`signal.reason` 377 行置空（备份 `_audit/pre_cleanup_signal_reason_20261021.csv`）。
- 测试：2824 passed / 2 skipped；新增守卫 `test_buy_point_and_plan_text_removed`、
  `test_disabled_by_default`、`test_sealed_board_is_watch_only`。

---

### 变更 · 2026-10-21 策略口径修正（ADR 0002）：退潮解禁 + c3 改身份档 + 买卖点改口径 B

> 起因：奎爷「效果不满意——要给明确的买卖点和时间」。落成可复现数字后发现三处判定与
> 真实可执行路径脱节，不是阈值松紧问题。实证底稿 `_audit/2026-10-21-修正方案-实测定档.md`，
> 裁决 `docs/decisions/0002-策略口径修正-退潮解禁与c3定档.md`。

**一、买点/卖点（`algorithms/entry.py`、`algorithms/exit.py`）**
- 新增 `entry.buy_point(close, code)` = T 日收盘 × 板比（走 `utils/price.limit_up_price_cents` 唯一实现），`entry.plan_text()` 输出「买点 X（T+1 涨停价，盘中封板才成交）；卖点 T+2 开盘无条件卖出…；仓位 …」，并首次写入 `signal.reason`（此前恒 NULL）。
- `exit.py` 整文件改为口径 B 时间退出：`held_days` / `SELL_AFTER_DAYS=1` / `FORCE_AFTER_DAYS=2`；开盘跌停封死（开盘=收盘=跌停）顺延，T+3 强制了结。**删除** `force_liquidate` 清仓与「断板减仓」两条卖因（实测持有到断板 −1.90%/胜率 38.1%）。
- 口径依据：T+1 封板价买 → T+2 开盘卖 = **+3.54%/胜率 68%**（n=183，费后 0.15%）；而「T+1 开盘买」是 **−0.71%** 负期望，「T 日收盘买」在盘后买不到（幻觉口径）。

**二、相位（`state.py`、`emotion.py`）**
- `window_of("退潮")` 由 `("NONE", True)` 改为 `("STANDARD", False)`；`force_liquidate` 恒 False；冰点是唯一禁买相位。
- 高潮 + 高位分歧**不再把窗口压成 NONE**，只打 `diverge=True` 交给仓位档降级。
- 依据：退潮日候选每信号期望 +2.31%、封板后胜率 77%（n=169），优于高潮 ENHANCED +1.48%/68%；退潮次日连板高度延续率 0.647 为四相位最高。

**三、c3（`entry.py`、`domain/signal.py`）**
- `_CONDITIONS` 的 c3 槽位改挂 `c3_identity`（恒 None = 不否决），新增 `c3_tier`（严格胜出/身位延续/新插队）、`position_tier`（仓位矩阵）、`tier_of`（报告与落库同源）。
- `Signal` 新增内存字段 `plan`（操作卡原文，不入库）。
- 依据：旧 c3 在 427 个唯一高标日里只有 27 天可能满足，可买日漏斗里 160/181 天被它单独否掉，而 passed=True/False 两组次日涨停率 40.0% vs 42.2%（无信息量）。
- **BUY 失败后不再进 RECOMMEND 档**（c3 不否决后它与 BUY 等价，实质不可达）；`check_recommend_signal` 保留供直接调用。

**四、报告与推送（`algorithms/review/`、`services/notify.py`）**
- ⓪ 段顶部新增**操作卡**：市场一行、买一行（代码+票名+绝对买点+卖点时间+仓位档）、卖一行。
- ①⑫ 段相位/窗口翻人话（新 `algorithms/review/wording.py`，`presentation/translate.py` 转为 re-export 以守住分层）；⑥ 段判据行改人话、取消 c1..c5 代号；`_no_signal_reason` 按相位给原因（冰点/分歧降级/退潮未重算各有各的说法）。
- BUY 即时推送改走 LLM 改写（`_HUMANIZE_BUY_PROMPT`）：保留代码/票名/价格/仓位档/板数，禁止收益承诺，禁止把「盘中封板才成交」改写成「一定买得到」。

**五、持仓决策两视角（`services/holding_service.py`）**
- 持仓视角（有成本）只按口径 B 时间退出；查不到建仓日 → 明确说「算不出持有交易日数」，不猜。
- 观察视角（无成本）买点 = 打板价，当日已封板才 `ADD`（`action_label` 写「打板买入（观察视角）」），分档压成单一档；`price_basis` 新值 `caliber_b_buy_point`。
- 依据：旧规则「退潮清仓→止损→断板减仓→目标减仓→到买点加仓」把「有没有持仓」混进了方向语义（同一只票填成本叫清仓、不填叫加仓）。

**六、测试与数据**
- 全量 **2829 passed / 2 skipped**（新增 `tests/caliber/test_c3_identity_semantics.py` 31 例；`test_exit.py` 重写 26 例；`test_holding_service.py` 57 例）。
- Rust 参考实现（`core/src/{entry,exit,state}.rs`）**有意冻结在旧口径**，由 oracle 测试显式钉住。
- 数据侧已执行：`emotion.run_range(2026-09-10, 2026-10-20)`（刷新 23 行 `buy_window`）、`replay_service.run(2026-09-25, 2026-10-20)`（3 个新口径信号）、`review.publish` 重出 09-30 与 10-20 报告。**历史全量重算未执行**（需重写 675 行 market_stat + 165 行旧口径 signal，等奎爷发话）。

### 修复 · 2026-10-19 REA 分析回填：Rust 孪生四处对齐 + `.so` 重建（消除 09-30 漂移）+ 五模块补对账

> 起因：用 REA（`rea-agents@6.5.0`）逆向分析本仓找缺陷，产出 `_audit/2026-10-19-REA分析报告.md`。REA 对 Python 主体无覆盖（无 Python 分析面），但给出 `.so` 身份（sha256 / mtime / 架构 / rustc commit），顺线挖出三处缺陷，用户拍板全部修复。**勘误：初版报告称「本机无 `cargo`/`rustc`」是错的——工具链在 `~/.cargo/bin`，只是不在 `PATH`。**

**一、`tests/unit/test_alert_webhook.py` 时间炸弹（HEAD 上就红）**
- `:133` 断言硬编码「第 1 天/第 2 天」，桩 `created_at` 写死 `2026-10-17`/`2026-10-16`，而 `alerts._days_active` 取墙上时钟 ⇒ 只在提交当天（10-17）绿；10-19 实测 `1 failed, 2581 passed`（跑出「第 3 天 / 第 4 天」）。
- 修：桩按 `datetime.now(UTC) - timedelta(days=N)` 构造，保留「第 1 天/第 2 天」语义断言；新增 `TestDaysActive` 6 例（今日=1 / 昨日=2 / 10 天前=11 / naive 视为 UTC / 未来钳到 1 / 非法与 None 归 1）。该文件 **15 passed**。

**二、Rust 孪生 `.so` 与已提交源码漂移**
- 「零成交顺延行不打断连板」修复（`indicators.rs`，`ca14705`，10-17）从未编进 `.so`（产物 mtime 2026-09-30 01:47）。同一 600825 场景实测：Rust `cont_days=[1,0,1]` vs Python `[1,0,2]`。
- 用 `~/.cargo/bin/cargo 1.98.1`（rustc `48a229cea`，正是 REA 从 `.so` 推断出的 commit）重建：`cd src/emotion_core/core && PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo build --release`，拷回 `src/core_lib/emotion_core_rust.so` 与 `src/emotion_core/core_lib/emotion_core_rust.so`（两份一致）。sha256 `25b8a6a4…`（1,592,696 B）→ **`6cb81c9d…`（1,595,816 B）**；漂移消除：两轨同为 `[1,2,0,3]`。

**三、补 5 个对账测试 + 写测试时挖出的 5 处 Rust 孪生分歧（一律以 Python 为权威）**
- 新增 `tests/oracle/test_{indicators,price,ladder,promotion,entry}_rust_vs_python.py`（**141 passed**）。此前 `tests/oracle/` 只有 6 个 rust-vs-python 文件，`lib.rs` 声明的 14 个导出函数绑定级引用为 0。
- 分歧 1 `ladder.rs` 缺 R67-2 停牌旁路：补 `LadderDay.is_suspended` 字段、`top_group` 过滤 `is_suspended`、`fold_candidates` 增 `suspended` 参数。
- 分歧 2 `entry.rs::_field("name")` 在 `ctx.name` 为空时回落 `cand.code`，Python 回落 default（空串）⇒ 消息文案不同 → 改 default。
- 分歧 3 `entry.rs` c5 用 `{}` 格式化 `diverge_min`（`5%` vs Python `5.0%`）→ 改 `{:?}`。
- 分歧 4 `promotion.rs` 取整为 Rust 五远离零（1/32→0.0313），Python `round()` 五取偶（0.0312）→ 新增手写 `round4`（五取偶）用于 `rate`/`div`/`fail_perf`。
- 分歧 5（语义缺陷）`entry.rs::passed_of` 把 W- 警告行一起聚合，警告 False 会静默否决——Python 侧 V4 审计早已用 `split_checklist` 结构分离，Rust 孪生残留 → 剔除 `W` 前缀行后再聚合。

**四、文档**
- `algorithms/indicators.py:5-6` 模块 docstring 判据描述与实现相反（写 `close >= limit_up` / `low == limit_up`，实为 `==` / `low >= limit_up`）→ 按实现改写。
- `core/src/indicators.rs:25` 注释指向的 `tests/oracle/test_price_rust_vs_python.py` 此前**不存在**（空气引用），本轮真实创建。
- `docs/15-实施追踪.md:165` 的「11 个模块全部 Rust↔Python 逐值一致」改写为与现状一致（本轮起 11 个模块确有对账文件；Rust 侧仍无 `entry._c3_secondary`/`c3_loose` 宽松路径，测试内注明为接口差异）。

**五、全量验证**
- `PYTHONPATH=src .venv/bin/python -m pytest tests -q` → **2808 passed, 2 skipped**（92.78s，退出码 0）。
- 门禁 B（`scripts/check_lint_debt.py`）：ruff check 245（基线 276）、ruff format 220（基线 225），未增长；5 个新文件 ruff clean。
- `.so` 已改为随仓库分发（见「六」）；`fold_candidates`/`layer_row`/`matrix` 三个死导出已被对账测试直接调用，生产接线仍待拍板。

**六、`.so` 随仓库分发 + `available()` 升级为「试加载」（用户 2026-10-19 拍板）**
- 背景：产物原本被 `*.so` 整类排除，干净 checkout 上 `import emotion_core.core` 必炸；且「文件存在」被当成可用，异构机器（CI 是 x86_64）会带着 arm64 产物在**收集阶段**报 `AttributeError: 'NoneType' object has no attribute 'loader'`。
- `.gitignore`：保留 `*.so`，追加 `!src/core_lib/emotion_core_rust.so` **精确放行**（只放行加载器真正读取的那一份；其余 `.so` 仍整类忽略，防误入库）。删除历史副本 `src/emotion_core/core_lib/`（全仓零引用）。
- `src/emotion_core/core/__init__.py`：`available()` 由「文件存在」升级为「存在 **且** 能在当前解释器里加载」，新增 `load_error()` 返回失败原因；`_load()` 的 `try` 扩到 `module_from_spec`——dlopen 发生在这一步，伪产物曾直接抛 `ImportError: … file too short` **穿透 `available()`**。
- `tests/conftest.py`：跳过提示同步打印产物路径 + 失败原因，并注明「这不是测过了，是根本没测」。
- 验证（伪产物 = `printf 'not-an-elf' > src/core_lib/emotion_core_rust.so`）：正常态 `available=True`；伪产物 `available=False` 且 `load_error()` 首行为 `ImportError: … file too short`；伪产物下按 CI 口径 `pytest tests/oracle`（目录遍历）→ 依赖 Rust 的模块被忽略、其余 10 passed、**0 error**；还原后全量 `pytest tests -q` → **2808 passed, 2 skipped**。
- 注意：`pytest_ignore_collect` 对**命令行直接点名的文件**不生效（pytest 9.1.1 实测：单独点某个 oracle 文件时仍会 import 并报收集错误）；CI 与日常 `pytest tests` 走目录遍历，不受影响。

### 加固 · 2026-10-17 `daily_bar` 写入护栏：源自带日期闸门 + 写库前自检

> 起因：下面那条 09-29 错价，根因是**没有任何一道守卫能拦住「源说自己是 10-13、调用方却要写 09-29」**——既有守卫比的是 `em_data_date()`（当天）与调用方参数，而污染发生在**历史日期**上，两边都在说「今天」，两边都觉得对。本条目给写入路径补三道。

**一、取数处日期闸门**
- `EastmoneyProvider.fetch_daily_bars`、`sync.fetch_snapshot`、`services.ingest.snapshot_daily`：请求字段加入 `f124`（**源自己说**的行情日期），与请求日期不等即拒绝（`DataError` / `ValueError`），绝不按调用方给的日期盖章。
- `sync.sync_daily` 的 `end` 由 `date.today()` 改为源数据日期 `em_data_date()` —— 原先周末/节假日补跑会把上一个交易日的快照盖上今天。

**二、写库前自检（新增 `data/bar_guard.py`）**
挂点：`data/sync.upsert_daily_bars`、`services/ingest._upsert_rows`（`daily_bar` 在 services 侧的唯一出口——快照 / 备源 / 逐股回填三条路都从这走）。三层判据：
1. **形状/顺延**：行宽、OHLC 自洽、`close>0`、`volume>=0`；顺延行（OHLC 全 0 且零成交）只许两种形状——`close=0`（无价占位）或 `close=pre_close`（带上一真实收盘）。
2. **链式偏离**：`pre_close` 与库内前收偏离 > 21% 记告警（除权除息日属正常；**成片**出现即整批写错日期）。
3. **整批自洽**：中位偏离 > 1% ⇒ 拒绝入库（≥20 行才统计）；`allow_gap=True` 可显式放行（历史修复场景）。

**三、拿污染前的真实快照彩排**
- `check_bars(2026-09-29 污染批 5224 行)` → **第 3 层命中**：`整批自洽不通过：pre_close 与库内前收的中位偏离 3.31%（上限 1.0%）`；第 1 层 0 处。
- 对照 `2026-10-16` 真实批 5225 行 → **通过**（只 2 行 >21% 的除权告警）。
- 新增测试：`tests/unit/test_bar_guard.py`（18 例）、`test_sync.py` +4 例、`test_ingest_fallback.py` +3 例。

### 修复 · 2026-10-17 `daily_bar` 的 **2026-09-29 全市场错价**（5218 行）+ 连板口径「零成交顺延行不得打断」

> 起因：10-16 报告可用性告警 `WARN review … PARTIAL：对账差异 1 行`。差异票 `600825` 新华传媒：东财池 `cont_days_em=11`，自算 `1`。两条线都出在 09-29 那一天。

**一、错价：09-29 一整天写的是「回补时刻的最新价」**
- 指纹：「`pre_close(09-29) == close(10-12)` 且 `!= close(09-28)`」命中 **5134** 只票；当天 5224 行里 **5152 行**与上一交易日 `close` 对不上。
- 实证 `600825`：库内 09-29 是 `open 0.000 / close 13.780 / pre_close 13.780 / volume 0`（13.78 正是 10-12 收盘），新浪真值 `open/high/low/close 9.41 / pre_close 8.55 / volume 156514.67 / turnover_rate 1.497909` —— 与东财池当日 `1.4979` 逐位吻合；`600519`（1279.23/1288.00 vs 1235.58/1243.88）、`000001`、`002242`（`pre_close` 16.64 = 新浪 10-12 收盘）、`600241`（12.94 同）同型。**不是除权除息。**
- 修复（逐票取新浪 09-25~10-01 窗口的 09-29 行回写）：**改写 5210 行**、补 **5 行**零成交顺延行（`300082/300527/301139/600363/603400`，当天真停牌、此前整天缺行）、修 **3 行** `pre_close`（`300211 7.58→6.98`、`601238 5.72→5.09`、`605303 23.86→23.68`）。修复前全量快照：`/home/ubuntu/logs/daily_bar_20260929.before.csv`。
- 验证：09-29 `pre_close` vs 上一「有成交」日 `close` = **0/5221 不等**；09-30 的影子错位 **5140 → 42**（日常除权区间 21~45）。
- 写入方未定位：形态是「回补时把最新价填进历史缺口」（`open=0`、`volume=0`、`close=最新收盘`），候选是 `data/backfill_sina.py` / `data/backfill_tdx.py`（都走 `data/sync.py::upsert_daily_bars`）。**待加护栏**：写 bar 前校验「源返回的 date 是否等于请求的 date」。

**二、口径：零成交顺延行不得打断连板（`_CONT_SQL` 之前把它当普通交易日）**
- 根因：`derived_bar` 里存在、但 `daily_bar.volume = 0` 的顺延行，被 `_CONT_SQL` 按普通交易日参与 `cont_days` 递推 ⇒ `is_limit_up=False` 把链打断。`600825` 因此把 10-14/10-15 算成断板，10-16 的第 11 板记成 1。
- 三点一致修：`algorithms/derive.py::_CONT_SQL`（`JOIN daily_bar … AND COALESCE(b.volume,0) > 0`）、对账口径的 `indicators.compute_derived`、Rust 孪生 `core/src/indicators.rs`（本工作树无 `cargo`/`.so`，未编译）。
- 口径守护：`tests/caliber/test_c1_streaks_suspension.py` 补第 3 层「零成交顺延行不打断」（含 `600825` 真实数字）；`tests/unit/test_derive.py` 补 2 例；相关单测 **409 passed**。
- 历史一致性：全表重演（`daily_bar` 3,430,198 行 / `derived_bar` 3,429,973 行，2025-03-17~2026-10-16）后 **差异 0 行**；改口径前共 **12 行**（`09-21~10-16` 3 行 + `603151` 2025-06-17/18、`605178` 2025-10-23~31 共 9 行），已逐日重算 `derived_bar` / `market_stat` / `ladder_day` / `promotion_day`。

**三、连带结果**
- `600825` 链条复原：09-21~10-12 **1→10 连板**、10-14/10-15 记 0、**10-16 = 11 板**（= 东财池 `cont_days_em`）。
- `reconcile(2026-10-16)`：**1 → 0**；`reconcile_range` 10-12~10-16 与 09-21~09-30 除 09-24 一只（`002264`，东财池判 ZT、自算 +9.27%，属既有口径差，与本次无关）外 **100%**。
- 10-16 复盘报告重发（`reports/2026-10-16.md` + `review_report`）：可用性 **PARTIAL → OK**，「自算连板 vs 东财：无差异 ✓」；`alert` #21/#22 已确认。重发时把 `notify.push` 临时 no-op，避免与当日已推结论重复轰炸。

### 修复 · 2026-10-16 R66：关注列表写操作自交付起就发错路径（线上 405）+ CPT↔emotion 文档对齐

> 起因：owner 要求「对齐两个仓的代码和文档」。对齐第一步就是拿真服务核端点，当场撞出这个事故。

- **`services/cpt_client.py`**：`track_add` / `track_remove` / `track_restore` 自 S1（`d712ae0`，2026-10-14 交付）起一直 POST **裸路径** `/api/dashboard/track` + body `{"action":"add"|"remove"|"restore", …}`，而 CPT 的 `cpt/web/app.py::_handle_track_post` 只比对 `/add` `/remove` `/restore` **三条字面量**、**从不读 `action`** ⇒ 线上稳定 **405**，**「关注 / 取关 / 复活」自交付起从未成功过一次**。失败还被 `_post_json` 吞成 `None` → `False`，`watchlist_service` 只报「CPT 不可达：track_add 返回失败」，看不出是路径错。现新增常量 `TRACK_ADD_PATH` / `TRACK_REMOVE_PATH` / `TRACK_RESTORE_PATH`，三条写操作发真路径、body 收敛为 `{code, note?}`（与 CPT 侧契约一致）。
- **门禁（跨仓）**：`tests/unit/test_cpt_client.py::TestTrackWritePathsMatchCptLive` —— 从 CPT `cpt/web/app.py::_handle_track_post` 的**函数体**里取 `path == "<字面量>"` 集合，断言本仓要用的三条路径 ⊆ 该集合，CPT 仓不可用时 skip。**双向反向控制已验**：本仓改回裸路径会红，CPT 改字面量也会红。
  - ⚠️ 该门禁的第一版是错的：写的是「这个串在文件里出现过」（`f'"{path}"' in app_py`），被 bug 放过 —— 裸前缀 `/api/dashboard/track` 在 `do_GET` 的 `startswith("/api/dashboard/track")` 里也出现。教训：先确定把哪段文本当成了什么，再断言它存在。
- **实测（直连真服务 `127.0.0.1:8010`，绕 basic auth）**：修前 `POST /api/dashboard/track` → **405**、`GET /api/dashboard/track/list` → **404**（列表是裸前缀）；修后 add → list 出现 → 幂等 add → remove → list 空，全通。探针行已硬删，`public.cpt_track` 回到只剩 `default/000002`。
- **文档对齐 `docs/18-情绪+缠论整合-功能与代码设计.md`**：文首加 R66 复核说明；§4.2 端点表改对（列表 = 裸前缀 `/api/dashboard/track`，三条写操作各自独立路径，补 `GET /{code}/history?days=30` 与 `GET /maintenance?apply=1` 两行 —— `/maintenance` 是 **GET** 不是 POST）；§13 风险 R6 更正为实做（新增独立脚本 `scripts/watchlist_daily_summary.py` + 独立 cron，**不是**复用 `cron_daily_report.py`）；§14 遗留第 3、4 条**销账**（CPT 侧 `/maintenance` 分发与 `history` UI 入口在其 R59/R66 已完成），第 7 条补 R66 推送白名单，新增第 8 条（本次写路径事故）与第 9 条（前端按钮仍无端到端自动化）。
- **CPT 侧同步**：`deploy/cron/track-maintenance-daily.sh` 头部过期注释标注（「分发没接上 ⇒ 404」是 M18 当时的实况，R59 已接，实跑 200）；`docs/known-traps.md` 新增 **#52「跨仓端点漂移：单侧单测把『自己拼出来的 URL』当成契约钉住」**。

### 新增 · 2026-10-15 Wind 额度渐进拉取 cron（兜底执行体）

> 背景：Wind 日额度加不了（用户拍板「慢慢拉，加个 cron 来做」）。两条链共用同一份额度，主执行体是两个 DSH 自动化（18:30 对账 40 次、19:00 ST PIT 20 次），cron 只做兜底。

- **`deploy/bin/wind-pull-daily.sh`**（新）：串行跑 `scripts/backfill_limit_state_wind.py --direction both --max-calls 40` 与 `scripts/backfill_st_pit_wind.py --max-calls 20`，对账优先；`flock` 防重入（锁在 `/home/ubuntu/logs/wind-pull-daily.lock` —— **不能放 `/tmp`**：本机 cron 在宿主 `/tmp`、DSH/手工跑在容器 `/tmp`，两个命名空间）；`set -uo pipefail`（不用 `-e`）、退出码恒 0（撞额度是预期结果）。
- **`deploy/cron/emotion-core-wind`**（新，装为 `/etc/cron.d/emotion-core-wind`）：`30 12 * * *`（=20:30 北京，排在两个自动化之后捡剩余额度）；已用 docker+chroot 装好并验证 cron 会按 ubuntu 身份执行。
- **`README.md`**：运维小节新增「cron：Wind 额度渐进拉取（兜底位）」表（三个执行体的时间/预算/落表）+ 装法 + 锁文件与日志路径。

### 新增 · 2026-10-15 ST PIT 预算化回补（窗口台账 + 断点续跑）

> 背景：`derived_bar` 要补 ST ±5% 档必须先有 ST PIT；旧路径逐股 `get_stock_events` 要烧 5000+ 次调用，且 2026-10-14 首次实跑因额度耗尽得到 297 条**永不闭合**的开区间并整表回滚。

- **`data/schema.py`**：新增 `ref_security_status_event`（原始「实施ST/撤销ST」事件）与 `ref_security_status_window`（窗口进度：`kind`=`seed/实施/撤销`、`status`=`ok/quota/error/partial`、`calls`、窗起止）两表；生产库已建（DDL 34 → **36** 张表）。
- **`services/ref_security_status.py`**：新增 `bulk_progress(start, end)`（窗口台账汇总）与 `backfill_security_status_bulk(start=None, end=None, budget_calls=None, force=False)` —— 5 个板块种子 + 半年窗口 × 实施/撤销；每窗口 1 次 Wind `search_stocks`（触顶 100 行自动二分）；额度耗尽 → 该窗口记 `quota` 并**停在原地**（不抛错、不写区间表）；**只有全部窗口 `ok` 才用原始事件重建 `ref_security_status`** —— 半途重建正是 10-14 那次「297 条开区间」事故的成因。同一接口可多次调用续跑，`budget_calls` 计数由 `_BudgetClient` 包住 WindClient。
- **`scripts/backfill_st_pit_wind.py`**：改用 `argparse`，支持 `--end/--max-calls/--force/--dry-run`；预算默认取 `EMOTION_WIND_DAILY_CALL_BUDGET`，再退回 40。
- **每日渐进**：已建 DSH 自动化「ST PIT 回补（Wind 逐窗渐进，每日预算）」（`daily 19:00 Asia/Shanghai`，`--max-calls 20`，workspace-write），**排在 18:30 逐日对账之后**（额度共用，对账优先）。
- **实测**：`--dry-run` = 17 个窗口（5 种子 + 2024H1~2026H2 实施 6 + 撤销 6）；真机 `--max-calls 4` 撞额度，窗口记 `quota`、剩 17 个待办、**区间表零写入**（旧事故形态不再复现）。新增 `tests/unit/test_ref_security_status.py` 的 `TestWindowPlan`/`TestBudgetClient`/`TestBulkResume` 共 11 项（窗口计划、预算计数、额度中止不重建、续跑后重建）。

### 变更 · 2026-10-15 健康检查补两条（对账阈值告警 + 题材覆盖率兜底）

- **`algorithms/health.py`**：`CHECKS` 由 5 条扩到 **7** 条 —— 新增 `check_limit_state_audit`（消费 `ref_limit_state_run`：`only_self > 0`、`only_wind/n_wind > 5%`、台账落后 `derived_bar` ≥ 3 交易日、`truncated/suspect` 待重抓名单点名；名单不全的日子不计入占比）+ `check_theme_coverage`（以 `derived_bar` 为参照系抓「题材当日空转/假 OK」、断档 ≥ 2 交易日、标签覆盖率 < 40%、`primary_theme` 非空 < 60%）。
- 两条均为**本仓新增**（lkl 既无 Wind 逐日对账、也无新浪题材源），不构成口径偏离，模块 docstring 已注明；阈值常量：`LIMIT_AUDIT_DAYS=10`、`LIMIT_AUDIT_STALE_DAYS=3`、`LIMIT_AUDIT_ONLY_WIND_PCT=5.0`、`THEME_STALE_DAYS=2`、`THEME_COVERAGE_MIN_PCT=40.0`、`THEME_PRIMARY_MIN_PCT=60.0`。
- 新增 `tests/unit/test_health.py` 的 `TestCheckLimitStateAudit`/`TestCheckThemeCoverage` 共 15 项（空台账、已知 ST 偏差静默、`only_self` 必报、占比阈值边界、坏状态点名且不入占比、当日空转、断档、低覆盖、`run()` 已含两条）。

### 修复 · 2026-10-15 Wind 逐日对账二分丢票（`0.01亿元` 下界问句不可解析）

> 首次实跑暴露：`2026-08-17` 台账记 `ok` 却有 7 只漏票（`only_self=7`），`2026-09-21` 同形态。

- **根因**：根问句下界取 `ROOT_CAP_FLOOR=0.01` 时，左子片问句被 Wind 判成「没找到数据」（与「零匹配」同形），该片被当成合法空片 ⇒ **静默丢票**；同时确认问句里的「在 X 亿元到 Y 亿元之间」Wind 也不认（回包没有表）。
- **修复**：① 下界改 `1.0`（亿元，对 A 股等价于不设限）；② 两侧问句统一为「总市值大于等于X亿元且总市值小于等于Y亿元」（闭合边界，切点重复交给并集去重）；③ 新增 `_window_contains` 交叉验证：空子片若落在「父触顶片已知有票」的市值区间内 → 新状态 **`suspect`**（**不进** `DONE_STATUSES`，下次运行自动重抓；`truncated` 仍只记不重试以免白烧额度）；④ `scripts/backfill_limit_state_wind.py` 加 `--redo`、suspect/`only_self` 告警。
- **实测**：`2026-08-17` `--redo` 重抓 `wind=103 self=110 only_self=7`，7 只当日涨幅 = 9.99%~10.05%（主板 6 只）/ 19.99%（创业板 `301282`），正是各自板块的涨停幅度，确为本库口径下的真涨停 ⇒ 丢票出自二分的问句解析、与 Wind 名单无关；`2026-09-21` 重抓 `wind=106 self=104 only_wind=2 only_self=0`，2 只为北交所 `920427/920478`（不在 `stock_basic`/`derived_bar` 覆盖范围，属口径差异）。`2026-08-17` 台账已手工置 `suspect` 待重抓（下次运行自动覆盖）。

### 新增 · 2026-10-15 Wind 逐日涨跌停对账通道（拍板 ③）

> 背景：`derived_bar.is_limit_up` 照搬 lkl 的涨停口径，**缺 ST ±5% 档**（`docs/04 §3` 记为已知偏差，2025-06-03 实测低估约 15.2%）。本次落一条**配额友好、可断点续跑**的 Wind 对账通道，仍不改判定逻辑。

- **`services/limit_state_wind.py`（新建）**：`build_question/parse_rows/split_point` 纯函数 + `fetch_day`（Wind `stock_data.search_stocks`，单日 1 次调用；返回触顶 100 行时按**市值对数二分**递归切片；`max_calls` 预算耗尽 → `exhausted`）+ `self_limit_codes`（只读 `derived_bar` 自算名单）+ `persist_day`（写名单 + 状态台账，**出错时不清空既有名单**）。
- **`data/schema.py`**：新增 `ref_limit_state`（PK `(date, code, direction)`）与 `ref_limit_state_run`（PK `(date, direction)`；状态 `ok/truncated/suspect/exhausted/error`）+ `idx_ref_limit_state_code`；生产库已建（32 → 34 张表）。
- **`scripts/backfill_limit_state_wind.py`（新建）**：从最新交易日往回拉；`--max-calls` 预算（默认 40，`EMOTION_WIND_DAILY_CALL_BUDGET` 可覆盖）；已 `ok/truncated` 的 (date, direction) 自动跳过 ⇒ **同一条命令每天重复跑即断点续拉**；`--dry-run` 只打计划；`--direction up|down|both`。
- **实测**：2025-06-03 `wind=83 self=67 only_wind=16 only_self=0`（库内 12 只当日涨幅 4.87%~5.13%，**自算零假阳性**）；2025-06-04 `wind=87 self=71 only_wind=16 only_self=0`。新增 `tests/unit/test_limit_state_wind.py` 15 项（解析 / 二分 / 预算耗尽 / 市值无区分度 / 错误传播 / 落库守卫）。
- **文档**：`docs/04` §3 追加 2026-10-15 更新段（Wind 问句能力、100 行硬上限与市值切片、落表与续跑方式、实测数字）。
- **重试上限（台账 `retries` 列）**：`ok` 永不重抓；`truncated`/`suspect` 在 `retries < --max-retries`（默认 2）前每天自动重抓、到顶即视为完成 —— 既让「修好解析后自动补票」，又不会被永远切不动的叶子天天烧额度；`exhausted`/`error` 不受上限影响（下次继续必有进展）。
- **每日渐进回补**：已建 DSH 自动化「Wind 逐日涨跌停对账（每日渐进回补）」（`daily 18:30 Asia/Shanghai`，`--direction both --max-calls 40`，权限 workspace-write）⇒ 额度每天有限，靠同一条命令重复跑续拉（m00753 决策③）。
- **已知偏差（拍板②：不补）**：`algorithms/promotion.py` 的 `_PAIRS_SQL` 里 perf 未挡 `tc.close > 0`（当日 `close=0` 会算出 -100%，拖低该层 `fail_perf`；2026-10-08 的 1->2 层实测 1 只、均值 -4.3594）—— 就地注释记录，不改口径。

### 变更 · 2026-10-15 题材属性桶剔除（拍板 ①）

> 用户拍板：**剔掉属性桶噪声**。`algorithms/theme.py` 的 STYLE_ONLY 一直逐字照搬 lkl，而新浪概念清单里的「参股 / 含X股 / 业绩预 / 解禁 / 资质 / 指数名额」类桶会被当题材落进题材组与逐票 `primary_theme`，实测当日的题材组几乎被这类桶占满。

- **`algorithms/theme.py`**：新增 `THEME_STYLE_BLACKLIST_ATTR`（`_cfg` 取值、可被 `utils/config.py` 覆盖）+ `is_style` 加一条判据。lkl 原三重判据（黑名单 / `全A*` 前缀 / `*重仓*` 包含）原样保留；**地域族（成渝特区 / 振兴沈阳 / 朝鲜改革 / 水域改革…）刻意不动**——炒地图是真实地域题材。这是本仓**唯一一处有意偏离 lkl 的判定规则**；Rust core `core/src/theme.rs` 保持 lkl 原值作为对账基准，`tests/oracle/test_theme_rust_vs_python.py` 无需改动。
- **`services/theme_service.py`**：新增 `_clean_tag_row`，在**写库前**用 `theme.theme_filter` 收口（`aggregate` 仍吃原始标签，避免改变 `_row_themes` 展开口径进而动到并列最高板 `top_code` 的取序）；全标签皆噪声的票仍保留行、`primary_theme` 落 NULL。
- **实测**（生产库 2026-10-14，`EMOTION_THEME_PROVIDER=sina_concept`，175 概念 / 3265 票）：**题材组 24 → 18**（剔掉参股金融、金融参股、含B股、本月解禁、业绩预降、准ST股），逐票 3265 行里 primary 非空 2749（516 行只剩属性桶标签）。
- `tests/unit/test_theme.py`、`tests/unit/test_theme_service.py` 同步扩充。

### 新增 · 2026-10-15 情绪+缠论整合（emotion-core 侧 R4 S1–S7）

> 唯一设计依据：`docs/18-情绪+缠论整合-功能与代码设计.md`。CPT 侧（C1/S0/C2①–④）已先落地。

- **`services/cpt_client.py`（S1）**：新增 `CPT_ENSURE_TIMEOUT=30.0`、`_norm_user`、`_post_json`；`fetch_snapshot` 加 keyword-only `ensure_factors: bool | None = None`（仅 `True` 才拼 `?ensure_factors=1`）；`fetch_snapshot/fetch_recommendation` 加 keyword-only `user`（透传 `X-CPT-User` 头）；新增 `track_list/add/remove/restore/advice/speak`（track 写操作走 POST + JSON body）、`points_view`（前端契约 §9.3）、`human_text`、`points_empty_reason`；`__all__` 新增 9 名。commit `d712ae0`。
- **`services/watchlist_service.py`（S2，新建，103 行）**：`list_codes/add/remove/restore/is_tracked`；CPT 不可达时读返回 `([], False)`、写返回 `(False, "CPT 不可达：…")`，**绝不回落本地 `watchlist` 表**（防真源分裂）；`watchlist.py` 顶部标 legacy。commit `38a46de`。
- **`algorithms/sizing.py` + `services/holding_service.py` + `data/holding_store.py` + `data/schema.py`（S3）**：`sizing.py` 加 `SIZING_BOUNDS` = `{"risk_pct": (0.001, 0.05), "liq_pct": (0.001, 0.05), "max_position_pct": (0.05, 1.0)}`；`plan_shares` 接受 `risk_pct/liq_pct/max_position_pct/lot`（默认值不变，已有调用方零改动）；`holding_service.decide` 加 `sizing_params/ensure_factors/cpt_user` + `_resolve_sizing_params`（越界回落默认 + `reasons` 记 `sizing_param_ignored`）；`holding_decision` 表加 `sizing_params jsonb` 列。commit `918c460`。
- **`presentation/server.py`（S4）**：`_opt_pct`（闭区间）+ `_opt_user`；`holding_payload/holdings_payload` 加 `sizing_params/ensure_factors/cpt_user`；`cpt_points_payload`（GET `/api/cpt/points` 恒只读）；`watchlist_payload`（GET `/api/watchlist` 恒只读）；`_handle_watchlist_post`（POST `/api/watchlist` add/remove/restore，body≤4KiB，非法 JSON→400）；`to_payload` 补 `inputs` 字段。临时端口 8202 实跑五端点全通过。commit `688f9b9`。
- **`presentation/templates/stock.html` + `static/stock.js` + `static/dashboard.css`（S5）**：holding 表单加 `<details class="holding-sizing">` 折叠区（三参数 number 输入）；verdict 头部加 `.verdict-actions` 包 `#watchlist-toggle` 按钮 + badge；`STOCK_SIZING_DEFAULTS/BOUNDS` 注入；`stock.js` 加 `SIZING_FIELDS` + `holdingQuery` + `renderHolding` 渲染 CPT 点位 + `refreshWatchlistToggle/toggleWatchlist/bindWatchlistToggle`；`dashboard.css` 加 `.holding-sizing/.verdict-actions/#watchlist-toggle/.holding-cpt-points`。commit `4391ace`。
- **CLI 扩展 + 常量一致性（S6）**：`holding_service.main()` 新增 `--risk-pct/--liq-pct/--max-position-pct`（闭区间校验）+ `--ensure-factors` + `--user`；`test_levels.py::TestConstantsMatchCptLive` 从 CPT 仓源码 import 真实常量（`BI_LOOKBACK/ZHONGSHU_LOOKBACK/BUY_BUFFER/SELL_BUFFER/STOP_LOSS_RATIO`）逐值比对防漂移。commit `f90f732`。
- **文档（S7）**：`docs/15` 追加 R4 小节；`CHANGELOG` 本条目；`README` 加 R4 入口 + `docs/18` 目录项 + HTTP 运维命令；`docs/18` ↔ `docs/17` 互引；订正 `docs/18`/`docs/17` 的 `sudo systemctl restart cpt-dashboard` 过时说法（实证 `kill -9 <MainPID>` 触发 `Restart=on-failure`）。
- **全量 `pytest tests/unit tests/architecture` → 2079 passed, ruff 全绿。**

### 变更 · 2026-10-14 ST PIT 批量回补入口（Wind）· 数据未落库

- `services/ref_security_status.py` 新增 `backfill_security_status_bulk()`：用 `search_stocks` 按半年窗口枚举「实施ST / 撤销ST」，替代逐股烧配额的 `get_stock_events` 路径（全量 2024 起约 20 次调用）。
  - 单窗返回触顶 `search_stocks` 100 行硬上限时自动二分窗口重问。
  - 起始日快照（种子）按 5 个板块拆问「YYYY-MM-DD 处于风险警示板的股票」；缺这一步，2024-01-01 前已 ST、窗口内只发生「撤销」的股票会被误判为全年非 ST。
  - 踩坑：窗口边界非自然周期时 Wind 把日期列名写成动态的 `2025年1月1日到2025年6月30日撤销日期`，按列名全名取值会让整个窗口静默解析出 0 事件 → 改为「列名包含」匹配（`_pick`）。
  - 撤销事件的精简列形只提供 `证券简称`（**当前**简称，非当时简称），不能用来推断档位 → 档位留 `None`，区间折叠层按「延续上一档位」保守处理。
  - 单窗网络失败重试 1 次后跳过；额度类失败中止枚举但继续落库已取到的数据。
- `scripts/backfill_st_pit_wind.py`：命令行入口，`PYTHONPATH=src ... .venv/bin/python scripts/backfill_st_pit_wind.py 2024-01-01`（按 code 全量替换，幂等）。
- 测试：`tests/unit/test_ref_security_status.py` 新增 `TestNormalizeStatus` / `TestParseBulkRows` / `TestBuildIntervals`，覆盖动态列名、精简列形、同日零长度区间、档位不可知延续；`pytest tests/unit/test_ref_security_status.py` → 29 passed。
- **数据侧未完成**：2026-10-14 首次实跑在第 ~12 次调用后遇 `Wind 额度/限流（backend_error）：账户积分余额不足`（充值 https://market.windalice.com/#/user/credits），只取到种子 116 只 + 实施事件 273 条、**撤销事件 0 条**。缺撤销事件会让所有 ST 区间永久不闭合，故已 `TRUNCATE ref_security_status` 回滚为空表（回滚前 380 行 / 297 只，其中未闭合区间 297 条）。充值后重跑该脚本即可。

### 新增 · 2026-10-14 题材源接入（新浪概念）+ 人气 / 连板回补

- **题材源激活（拍板 ①）**：新增 `data/providers/sina_concept.py`（概念节点清单
  `newFLJK.php?param=class` + 成分股 `Market_Center.getHQNodeData`，均为 GBK/JS 字面量），
  实测 **175 个概念 / 3265 只个股**；东财概念接口（`m:90 t:3`）在本机出口是持久 502，
  同 `providers/sina_industry.py` 记的坑。`data/theme_source.py` 新增
  `SinaConceptProvider`，`get_provider()` 读环境变量 `EMOTION_THEME_PROVIDER`
  （生产值写在 `deploy/env/emotion-core.env`，daily / report / watchdog 三个单元本就用
  `EnvironmentFile=` 读它 ⇒ **无需改 systemd 单元**）。**刻意不动 `utils/config.py`**：
  CONFIG 是 frozen dataclass，新增字段会改 `config_hash()`，让 `pipeline_state` /
  `signal` / `eval_result` 里的策略指纹平白换代。未激活时仍是 NullProvider，原有钉死
  测试（默认=Null、未知名字不抛错）全绿。
- **`theme_tag` / `theme_spot` 首次落库（拍板 ①）**：`services/theme_service.py` 补上
  自 2026-09 起挂着的 `# TODO: 写 theme_tag`（逐票 primary + secondary 数组），并新增
  当日概念板块热点榜落库；热点榜单独 try（附加产物失败不连累题材标签）。`data/schema.py`
  新增 `theme_spot` 表 + 日期索引（`create_all()` 在 src 里无调用方，已手动建表）。
  实测 2026-10-14：`theme_tag` **3265** / `theme_group` **24** / `theme_spot` **175** 行。
- **无历史 PIT（拍板 ① 接受）**：新浪这两个接口只返回**当前**名单与涨幅，无法按历史日期
  回放 ⇒ `theme_tag` 是「从接入日起逐日累积的快照」，**不回填历史**（回填等于把今天的
  概念成分当成昨天的）。docs/04 §3 第 5 项「历史题材 PIT 维持放弃」的结论不变。
- **人气票池回补（拍板 ②）**：`scripts/backfill_hot_rank_all.py` 对 `hot_universe` 的
  3831 只逐票回补东财 `getHisList` 排名史，写入 **1,388,762 行 / 48 分钟 / 2 只失败已补跑
  （+648 行）**。但**可用窗口只有约 1 年**：2025-10 之前每日仅 1~3 行、`min(rank)` 远大于
  100；2025-10 起 denseness 由 19/31 天升到 100%（2026 年每月 31/31 天、rank 1 可达）
  ⇒ 每日 top100 人气榜实际覆盖 ≈ 250 个交易日，这是源自身的历史深度，不是取数没取全。
- **连板 / 晋级一次性重算（拍板 ②）**：`scripts/recompute_ladder_promotion.py` 对 671 个
  交易日重算 `ladder_day`（**8745 行 / 670 日**）与 `promotion_day`（**3350 行 / 670 日**），
  0 失败 / 2.3 分钟。**前后对账**（对照重算前备份）：`ladder_day` 142/142 格全等；
  `promotion_day` 除 3 格 `fail_perf` 外全等 —— 该列是唯一由「当日 close / 昨收」派生的列，
  那 3 格原写于 2026-09-28~09-30 的日更修复窗口，用 `promotion._PAIRS_SQL` 独立复算得到与
  重算后一致的值 ⇒ 旧值不可信、新值可复现。
- **`theme` 步 detail 修复**：`orchestration/daily.py` 的 theme 步此前**丢弃**
  `theme_service.run` 的返回值 ⇒ `pipeline_state.detail` 恒为 `''`，于是「题材源挂了
  （0 组）」与「正常产出（24 组）」在打点里长得一模一样。现写 `题材组=N`（口径同 ladder 步，
  编排层不新增查询）。实测重跑：`status=OK / detail=题材组=24 / exit_code=0`，且重跑后三个
  表行数不变（幂等）。
- **验证**：`pytest tests/unit` → **1912 passed**；对改动文件 ruff 零新增；只留 theme 步走
  真实编排器（`daily.STEPS` 临时缩为 theme）跑通生产同日数据两次，均 exit 0。

### 修复 · 2026-10-09 巡检拍板落实（配额 300 / 决策去重 / SSE 超时 / strategy 单元 2h）

- **上调配额（拍板 ①）**：`utils/config.py` 的 `STRATEGY_MAX_LLM` 默认 `50` → **`300`**
  （20 只 × 15 策略）。实测每次 LLM 调用约 7.2s（10-09 `09:50:01`→`09:56:01` = 50 次 /
  6min01s）⇒ 300 次约 36min，故 live `emotion-core-strategy.service` 的
  `TimeoutStartSec` `3600` → **`7200`**（备份
  `/etc/systemd/system/emotion-core-strategy.service.bak-20261009-153349`；`systemctl show`
  实测 `TimeoutStartUSec=2h`），`portal` 仓快照同步。
- **决策按 code 去重（拍板 ④）**：`presentation/trade_api.py::_query_signals` 改为
  `DISTINCT ON (code)`（同一只票取分数最高；同分取 `created_at DESC`，再同取
  `prompt_hash DESC`，保证结果确定），`handle_trade_decisions` 再加 `seen_codes` 兜底 +
  WARNING。**根因**：`strategy_signal` 唯一键含 `prompt_hash`，上下文变化后重跑会追加版本行
  （版本行本身是设计）⇒ 同一 `(code, strategy)` 的多版本会同时命中，被 LKL-Trade 当成多笔
  `OPEN_POS`。线上实证：旧缓存批次 `fbc43162` = **13 条 action / 5 个 code（含重复）**，且该
  批次**从未被消费**（无 `results_*` 回执）⇒ 备份 `/home/ubuntu/trade/state.json.bak-20261009-152842`
  后清掉当日缓存，重启后新批次 `7ff2725c` = **4 条 action / 4 个 code / 无重复**
  （`000420`/`000546`/`000736`/`000710`），`state.json` 落盘同样去重。
- **SSE 超时（拍板 ②）**：`/plugins/events`（前端 `EventSource`）原先没有独立 location，落到
  `location /` 继承 `proxy_read_timeout 300s` ⇒ nginx 每 5 分钟切一次连接并记一条
  `upstream timed out (110)`（功能正常，纯日志噪声）。新增
  `location = /plugins/events`（`proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s;
  proxy_send_timeout 3600s`，同 `/dsh-bottom-terminal/` 的先例），备份
  `/etc/nginx/sites-enabled/dsh-web.bak-20261009-153349`。实测：改前 error.log 每 300s 一条
  （13:31:32 / 13:36:35 / 13:41:39），改后单条连接撑过 330s
  （`curl --max-time 330` → `code=200 time=330.00 exit=28`，即被测试自己的上限切断，而不是
  nginx 的 300s）。
- 新增测试 `tests/unit/test_trade_api_decisions.py`（5 例：SQL 形与参数、查询返回重复 code 时
  每 code 只出一条且落盘缓存去重、同日二次调用走缓存、无 BUY 不落缓存、非法日期 400）。
  红→绿已证：把 `trade_api.py` 还原到 HEAD 复跑有 2 例失败。

### 修复 · 2026-10-09 运行日志巡检（emotion-core 侧 C/D/E/F）

- **C 候选池口径（用户拍板）**：`services/strategy/universe.py::build_universe` 改为
  「手动自选（`watchlist`，code 升序，优先）+ 热门池（`hot_rank` 当日按 `rank, code`
  取 `STRATEGY_HOT_N`）」，**不再读 `limit_pool_em`**（涨停/炸板池与热门池高度重叠）；
  删掉 `OBSERVED_POOL_TYPES`。汇总行改为带**跳过原因分布**
  （`runner.py::_reason_distribution`，按次数降序）——此前「为什么只跑了 4 只票」在
  DB 与日志里都不可见。
  ⚠️ 算术：`STRATEGY_MAX_LLM=50` ÷ 15 个策略 = **每天只够 3.3 只票**；10-09
  universe 79 codes ⇒ 配额仍是硬上限，覆盖到哪只票由候选顺序决定。**2026-10-09 已拍板抬到
  300（见上一节），本节保留当时的口径。**
- **D 补跑能力**：`orchestration/strategy.py` 新增 `--codes 600825,000420`（显式候选，
  跳过候选池，走 `filter_codes`：`BOARD_PREFIXES` 过滤 + zfill(6) + 保序去重、不截断），
  用于补齐历史缺口；`run_for_date(trade_date, codes=None)` 主循环之后新增**自动补跑一轮**
  （本轮 LLM 失败 / 输出契约失败的 `(skill, code)`，在配额与熔断有余量时按
  `RETRY_ROUND_DELAY`（env `EC_STRATEGY_RETRY_DELAY`，默认 10s）重跑）。
  `_snapshot(..., merge=True)`：补跑是局部运行，必须与旧快照按 `(code, strategy)` 合并，
  否则会把当日完整快照（其余 code 的 items 与 skipped 分布）一起冲掉。
  实跑：10-08 `600825` 补 2 行（`ma_golden_cross`/`shrink_pullback`，均 PASS 15）；
  10-09 `000420` 首轮 `chan_theory` 失败 → 补跑轮后 15/15 入库。
- **E pool 单元重启上限归位**：`StartLimitIntervalSec` / `StartLimitBurst` 从 `[Service]`
  搬到 `[Unit]`（systemd 只认 `[Unit]` 里的新键名；放 `[Service]` 只打印 `Unknown key`
  后按默认值生效 —— 表现为「写了等于没写」）。live
  `/etc/systemd/system/emotion-core-pool.service` 已改（备份
  `/root/emotion-core-pool.service.bak-20261009-130607`），`systemctl show` 实测
  `StartLimitIntervalUSec=1h` / `StartLimitBurst=4`；`portal` 仓库快照同步。
- **F pool 逐日硬超时（根因修正）**：下一节「`setdefaulttimeout(15)` 就算修好」的结论
  **不成立** —— akshare 三池函数是 `requests.get(url, params=params)`，**不传 timeout**
  会被 requests 解析成**显式 `None`**（`TimeoutSauce(connect=None, read=None)` →
  `socket.create_connection(addr, None)` / `sock.settimeout(None)`），显式 None 是
  **永久阻塞**；进程级 `setdefaulttimeout` 只在调用方根本没传 timeout
  （`_GLOBAL_DEFAULT_TIMEOUT` 哨兵）时生效。实测 `requests.get(timeout=None)` 阻塞
  3.00s，而 `timeout=0.5` 在 0.50s 抛 ReadTimeout。修法：`pool.py` 逐日
  `SIGALRM`+`setitimer` 硬上限（`_DEFAULT_PER_DAY_TIMEOUT=180.0`，CLI
  `--per-day-timeout`，`_DayTimeout(BaseException)` 以穿透 `except Exception`），
  超时记 WARNING 后 `continue`，已写成功的天保留；`setdefaulttimeout(15)` 保留为
  纵深防御。live 实跑 3 天：73/88/91 行、exit 0 —— 此前 10-08 卡死 600s 被 TERM，
  当天池**完全没写库**，现 10-08 = 88 行 / 3 个池型。
- **遗留（未修，待拍板）**：`strategy_signal` 唯一键是
  `(trade_date, code, strategy, prompt_hash)` ⇒ 上下文变化后重跑会为同一
  `(date, code, strategy)` **追加新版本行**，而
  `presentation/trade_api.py::_query_signals`（`:43-52`）按 `trade_date + action` 取行、
  **没有版本去重** ⇒ 重复行会以重复 decision 出现在 `/api/trade/decisions`。

### 修复 · emotion-core-pool 卡死（2026-10-09）

> ⚠️ 本节对根因的描述已被上节 F 修正：`setdefaulttimeout(15)` 兜不住 akshare/requests
> 的显式 `timeout=None`，真正的护栏是逐日 `SIGALRM` 硬超时。

- **断 EM 接口挂死的根因**：`pool.py` 入口加 `socket.setdefaulttimeout(15)`。
  akshare 调东财接口走 `requests`，akshare **不传 timeout** 给底层 socket——EM
  接口挂死时进程无限等，靠 systemd `TimeoutStartSec=600` 兜底杀进程，但 service
  标 `failed` 后 timer 不会自动重试。15s 上限后 retry_fetch 仍做 3 次指数退避
  → 单池最坏 45s；3 天 × 3 池最坏 ~7min。DB 侧 `utils/db.py:_kwargs` 已显式
  `connect_timeout=15`，与全局不冲突。
- **service unit 自愈**：`emotion-core-pool.service` 加 `Restart=on-failure` +
  `RestartSec=300` + `StartLimitIntervalSec=3600` + `StartLimitBurst=4`。
  failed 不再僵死到下次 cron；明天 10-09 09:45 cron 触发时即使挂也只浪费
  一次重启，不会爆 systemd。
- **2026-10-08 实测触发链**：09:45 cron 启动 → 09-30 抓取卡死 → 09:55:48
  systemd 杀（status=15/TERM）→ service failed → 14h 没人动。第二天 10-09
  cron 自愈就是这一改。

### 新增 · 2026-10-10 R58-4 次级推荐档（RECOMMEND）+ BUY 即时飞书推送

- **新增 Action.RECOMMEND（domain/signal.py）**：`Action` 枚举加 `RECOMMEND = "RECOMMEND"`，与 BUY/SECONDARY/SELL 平级
- **algorithms/entry.py::c3_loose**：BUY c3 放宽版——`候选 ∈ 幸存集`（不要求唯一幸存），其它条件与 BUY 一致（c1/c2/c4/c5 + MIN_LEADER_DAYS + DIVERGE_MIN_TURNOVER 均不变）
- **algorithms/entry.py::_RECOMMEND_CONDITIONS**：RECOMMEND 档专用 checklist 五条件，仅 c3 用 c3_loose，c1/c2/c4/c5 字段名沿用主版（`c1_uniqueness` / `c2_exchange` / `c3_elimination` / `c4_min_days` / `c5_strength_diverge`）——`Checklist.passed` 单一聚合器可同源复用
- **algorithms/entry.py::check_recommend_signal**：RECOMMEND 候选仍取 `ladder.sole_top(MIN_LEADER_DAYS)`（与 BUY 同门槛不降档），禁买日（buy_window=NONE）也生成（与 SECONDARY 同观察纪律），落库 action=RECOMMEND
- **链路 BUY → RECOMMEND → SECONDARY**：主 BUY 通过即终止回退；BUY 未过且 RECOMMEND 通过时返回 RECOMMEND（不再尝试 SECONDARY）；BUY/RECOMMEND 都未过时再尝试 SECONDARY。同一 confirm_date 不会同时落 BUY+RECOMMEND 或 RECOMMEND+SECONDARY
- **services/notify.py::push_buy_signal**：BUY 信号刚落库即触发飞书 webhook（24h 内存 dict 节流 `(date,code)`，网络异常不污染 dedup，下次重试可推），与日报摘要推送并行（独立时点）。仅 BUY 触发；SECONDARY/RECOMMEND 是观察信号不触发
- **services/notify.py::_format_buy_signal**：BUY 简报飞书 text 格式——标的 + 板数 + buy_window + 5 条件 checklist（✓/✗/— 三态可见），不含持仓明细
- **domain/Signal 内存字段新增 `name` / `cont_days`**（仅内存使用，**不入库**）：`_persist` 只写 `(date, code, action) + window + checklist`；新字段供 BUY 推送模板使用
- **services/signal_service.py::_push_buy_safe**：run() 落 BUY 后调 push_buy_signal，异常吞掉不抛

业务影响：近 90 天 1 BUY → 估算 ~5 BUY（双幸存场景可触发 RECOMMEND）；SECONDARY 不变；RECOMMEND 仅观察不入交易导出

测试：

- `tests/unit/test_entry.py`：c3_loose 7 例（双幸存过、无竞争拒、新插队拒、不在幸存集拒、R2 关闭、与主版差异、_RECOMMEND_CONDITIONS 引用 c3_loose 而非 c3_elimination）
- `tests/unit/test_entry.py::TestCheckRecommendSignal`：4 例（NONE 日观察记录、双幸存场景落库、name/cont_days 内存填充、sole_top 取 MIN_LEADER_DAYS 不降档）
- `tests/unit/test_entry.py::TestChainOrder`：4 例（BUY 通过终止回退、BUY 失败 RECOMMEND 通过返 RECOMMEND、BUY/RECOMMEND 双败回退 SECONDARY、NONE 日 RECOMMEND/SECONDARY 都尝试）
- `tests/unit/test_signal_service.py::TestR58_4BuyWebhookHook`：5 例（BUY 触发、SECONDARY/RECOMMEND 不触发、push 异常不破 run、无信号不触发）
- `tests/unit/test_notify.py::TestPushBuySignal`：8 例（首次推、24h 同 code 节流、不同 code 不互阻、不同 date 不互阻、网络异常不污染 dedup、无 URL 静默、飞书 text payload 校验、0 板不抛）

总测试 1618 passed（unit 1571 + architecture 47），0 failed；ruff 仅遗留 4 个预存错（I001 / UP035 / UP042×2，HEAD `96dd7cb` 即存在，与本次无关）

Commits：`d3348c8` + `7be5916` + `54e1765` + `5d8f5c8`

---

### 修复 · 2026-10-12 面板「行业 / 生态评级」空值

- **生态评级理由/风险恒 NULL（0/669 行）**：`services/ecosystem_service.py::run()` 原为内联 `UPDATE market_stat SET dragon_env=%s`，只写评级、丢弃 `dragon_env.rate()` 已算出的 `goods`/`bads`，绕过了 `algorithms/dragon_env.py::persist()`（零调用方）→ `data/loader.py::update_market_stat_ecosystem()`（才写 `dragon_env` / `dragon_env_reasons` / `dragon_env_risks` 三列）。改走三列一次写全；`market_stat` 2026-10-12 行落库 G1–G4 / B1–B5 明细
- **`stock_basic.industry` 全空（0/5221）**：东财 `push2`/`push2delay` 从本机持久 502，代码回退新浪全市场源，而新浪只回 `f12/f14/f2`、**无 `f100`（行业）**；`fill_stock_basic.py` 的 `COALESCE(EXCLUDED.industry, stock_basic.industry)` 不覆写 ⇒ 首次 NULL 就永远 NULL。新增 `data/providers/cninfo_industry.py`（巨潮证监会分类，逐股 4 线程）+ `data/fill_industry.py`（抓一批写一批，`DEFAULT_BATCH=500`，`--only-missing` 可续跑）；新浪源 `providers/sina_industry.py` 降级为备源（仅覆盖 47%、不含科创板）
- **实跑验收**：11 批零失败，`industry` **5221/5221**（83 个行业；沪主板 1702 / 深主板 1494 / 创业板 1408 / 科创板 617，四板块各 100%）；`/api/stock?code=688111` → `industry=软件和信息技术服务业`；主面板 `/` HTTP 200，生态评级渲染「环境不利，建议观望」
- **口径裁决**：全部 5221 只用巨潮统一回填（0 Wind 额度）。被排除路径：东财 f100 出口封死、上交所官方列表无行业列、深交所仅 19 门类、巨潮 `p_stock2110` 不支持批量、申万 xls 无行业名称、Wind 逐股 5221 次撞配额、pytdx 被 `requirements.txt` 依赖政策排除
- **测试**：新增 4 个测试文件；`pytest tests/unit tests/architecture` → **1647 passed**；改动文件 ruff 干净
- **文档**：`docs/15` 新增 2026-10-12 小节；`docs/04` §1 补巨潮源、§5 记 Wind 不适用全市场行业回填；`docs/11` providers 树补新文件；README 补 `fill_industry` 运维入口（commit `6d801fb`）

遗留：`stock_basic.market_cap` 仍全 NULL；`dragon_env` 历史回填（669 日仅 10 日有评级）待评估 ROI

---

### 修复 · 2026-10-12 `stock_basic.market_cap` / `float_shares` 回填（5223/5223）

- **根因**：`fill_stock_basic.EM_FIELDS` 请求东财 `f20`（总市值），东财 `push2`/`push2delay` 从本机持久 502 后回退 `_sina_clist`，而 `_SINA_TO_EM` 映射表**没有 `mktcap`/`nmc`** ⇒ `f20` 缺列 ⇒ `_rows_from_frame` 取 None ⇒ upsert 的 `COALESCE` 保留 NULL。**而新浪 `Market_Center.getHQNodeData` 本来就返回 `mktcap`/`nmc`**，只是没取
- **量纲**（决定性）：新浪是**万元**、东财 `f20` 是**元**，差 10000 倍。用「`mktcap ÷ trade` 反推总股本」独立核对：000001 平安银行 = **194.06 亿股（与公开总股本精确吻合）**、600519 = 12.50 亿、688111 = 4.64 亿。新浪快照 `trade` 与库内 `daily_bar` 2026-10-12 收盘价逐只相同
- **修法**：`providers/eastmoney.py` 的 `_SINA_TO_EM` 补 `mktcap→f20` / `nmc→f21`，新增 `_SINA_UNIT_SCALE`（`f5` 股→手 ÷100、`f20`/`f21` 万元→元 ×10000）——**映射与量纲必须成对**，只加映射不加换算＝静默 10000 倍错
- **`float_shares` 零额外请求**：`f21 ÷ f2`（流通市值 ÷ 收盘价）；停牌股 `f2=0` 退回昨收 `f18`
- **验收**：`SELECT count(*), count(market_cap), count(float_shares), count(industry)` → **5223 / 5223 / 5223 / 5221**；`float_shares` 与深交所官方 `A股流通股本` 抽样 8 只对账，偏差 −1.40% ~ +0.00%（5 只 0 偏差）；`/api/stock?code=601811` → `market_cap=28378343000.0`（改前 `null`）
- **口径提示**：A+B 股公司（如 000016 深康佳A）的 `f20` 是 **A+B 合计**总市值（24.08 亿）而非 A 股口径（官方 15.97 亿），只影响 `market_cap`；`f21` 只含 A 股流通故 `float_shares` 不受影响
- **测试**：`pytest tests/unit tests/architecture` → **1657 passed**（commit `0624570`）

---

### 新增 · 2026-10-13 元数据收口：`list_date` / daily `basic` 步 / `ingest` 去重 / 评级回填

上一节留下的 4 条待办 + 2 条追加项全部收口。**`stock_basic` 五列首次全满**：
`count(*) / list_date / market_cap / float_shares / industry` = **5223 / 5223 / 5223 / 5223 / 5223**。

- **`list_date` 回填 5223/5223（新源）**：`stock_basic.list_date` 原先全 NULL。改用**交易所官方列表**——深 `ak.stock_info_sz_name_code("A股列表")` 2904 行、沪 `stock_info_sh_name_code("主板A股")` 1702 行 + `("科创板")` 618 行，**3 次 HTTP 调用、0 Wind 额度**（对比巨潮逐股 5221 次）、且深沪两市的「上市日期」类型不同（深是字符串 `1991-04-03`、沪是 `datetime.date`）。新增 `data/providers/exchange_list.py` + `data/fill_list_date.py`（幂等 `UPDATE ... WHERE list_date IS DISTINCT FROM v.d`，`_CHUNK=1000`）
- **`float_shares` 接进面板**：`data/stock_query.py::basic_row`、`services/diagnose_service.py` 的 `_FUNDAMENTAL_SQL` 与 `_seg_b`、`presentation/static/stock.js` 四处补齐，副标题显示「市值 X 亿 · 流通 Y 亿股」
- **daily 新增第 14 步 `basic`**（fail-soft）：位置在 `hot` 之后 `derive` 之前（**不能插 `sync`/`coverage` 之间**，有单测守护）；三段各自 try/except，任一段失败只写 `pipeline_state.detail`（`spot=FAILED(...) list_date=3 industry=2`）、**不阻断主链**；快照 `trade_date != 当日` 直接跳过；行业补缺限量 `_INDUSTRY_LIMIT=100` 防拖长日更窗口。这同时解决「`fill_stock_basic` 全仓无生产调用方 ⇒ 市值快照永不刷新」和「`fill_industry` 要不要接 daily」
- **`services/ingest.py` 去重（−139/+19）**：删掉与 `data.providers.eastmoney` 重复的 `_EM_CLIST`/`_EM_FS_ALL_A`/`_SPOT_MAP`/`_SINA_*`/`_sina_clist`/`_em_clist_paged`/`_em_clist`/`_reject_zero_prices`，改为 import；顺带清 4 个死常量。**先证伪「漂移是活 bug」**：唯一实体差异是 `_em_clist_paged` 漏传 `fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP`，而这两个键**不在 `utils/config.py` 的 CONFIG 里**（`_cfg` 对两者都回退默认 3 / 0.5）⇒ 潜伏漂移而非当期故障。等价性实跑：`ingest._em_clist(_EM_FS_ALL_A, list(_SPOT_MAP))` → 5223 行、列集合与 `_SPOT_MAP` 键集合相等、无 `f20`/`f21`
- **`dragon_env` 历史回填 669/669**：`ecosystem_service` 新增 `run(trade_date, mode="live")`（透传 `mode`）、`run_range(start, end)` 与 CLI。**0 Wind 额度**（只读库内表）。实跑 `dragon_env 2024-01-02~2026-10-12：669/669 日` → `dragon_env`/`dragon_env_reasons`/`dragon_env_risks` 三列各 669/669，分布 **NEUTRAL 396 / FAVORABLE 198 / UNFAVORABLE 75**。回填走 `replay` 口径（B3 判据允许看「胜出次日」，`bound = trade_date + 3d`），原先流水线跑过的 10 天重算后评级未变（10-08/10-09/10-12 仍 `UNFAVORABLE`）
- **顺带修掉一个被单测掩盖的必崩**：`algorithms/dragon_env.py::run_range` 原写 `persist(d, mode="replay")`，而 `persist()` 的 `persist_fn` 是**无默认值的 keyword-only 参数** ⇒ 真实调用必抛 `TypeError: persist() missing 1 required keyword-only argument`。之所以一直没暴露：唯一使用方是单测，它把 `persist` 整段 mock 掉、假函数还给了 `persist_fn=None` 默认值；生产上 `run_range` 零调用方。改为 `run_range(start, end, *, persist_fn)` 注入式（保持 `algorithms` 不直连 `data`，过 `tests/architecture/test_dependency_direction.py`），单测同步改成「假 persist 不给默认值」+ 新增「漏传必 TypeError」用例
- **测试**：`pytest tests/unit tests/architecture -q` → **1696 passed**（上一轮 1657）；新增 `tests/unit/test_exchange_list.py`、`tests/unit/test_fill_list_date.py`，改写 `test_ingest_fallback.py`（补丁目标随 `_em_clist` 迁到 providers 命名空间）、`test_daily.py`（14 步）、`test_dragon_env.py`、`test_ecosystem_service.py`
- **踩坑**：`FetchError` 是双参（`FetchError(msg, cause)`），`DataError`/`ProviderError` 是单参；`pd.NaT` 也是 `datetime` 实例（`to_date()` 必须**先 `pd.isna` 再 isinstance**）；**`python -m` 少了 `if __name__ == "__main__":` 就是静默空转**（669 日回填第一次「exit 0、无输出、DB 一行没变」就是这个）

### 修复 · 2026-10-13 审计：退市股污染覆盖率分母 + 3 个静默失效 + 文档订正 11 处

- **退市股混入 `stock_basic`（A1，最高危）**：东财 `_EM_FS_ALL_A` 的板块过滤**不排除已退市证券**，
  EM 恢复可用后一次抓取 5562 行里 **334 行 `f100='-'`**（PT金田A / 国华退 / 退市紫晶…，`f20` 与
  `f2` 皆空）。`fill_stock_basic` 原先无条件 upsert 且硬编码 `in_market=true` → 这 334 只被算进
  `services/coverage.py` 的硬门槛分母：覆盖率上限 1.0 → **5223/5562 = 0.939**
  （门槛 `CONFIG.MIN_COVERAGE=0.90`），安全垫 9.8% → 3.9%，日常少约 218 行即触发
  `EXIT_COVERAGE_BLOCKED=76` 硬阻断整条 daily。已按 `f100='-'` 丢弃（判据只认字面量，
  新浪备源无该列不误伤）。
- **`--help` 会真跑全量回填（A2）**：`fill_stock_basic.main()` 不解析 argv →
  `python -m emotion_core.data.fill_stock_basic --help` 直接把 339 行写进生产库（本次事故根因）。
  已补 `argparse` + `--dry-run`，`__main__` 改 `sys.exit(main())`。
- **写库事故已回滚**：`DELETE FROM stock_basic WHERE list_date IS NULL` → **339 行**（事故前
  `count(*) = count(list_date) = 5223`，故该条件恰好等于本次新增行）；回滚后
  `5223/5223/5223/5223/5223`、`industry='-'` 0 行。339 行留档
  `/home/ubuntu/work/stock_basic.delisted-accident-20261013.csv`。
- **交易所列表单市场失败隔离失效（A3）**：`exchange_list.fetch_list_date_map` 的 try 只包
  `_fetch_one`，解析循环在 try 之外 → 任一市场改列名会**连带丢掉另两个市场已拿到的数据**，
  违反自身 docstring。已整段入 try，另捕 `KeyError/ValueError/TypeError` 只跳该市场。
- **NaN 判据过窄（A4/A5）**：`str(rec.get("f14") or "")` 对 NaN 失效（NaN 是真值）→ `name`
  写成 `'nan'`，而 upsert 的 `name=EXCLUDED.name` 会无条件覆盖库内正确名；`f12=""` 经
  `zfill(6)` 塌缩成 `'000000'` 并静默入库。`_as_float`/`_as_text` 的 `isinstance(v, float)`
  漏掉 `np.float32('nan')` / `Decimal('NaN')` / `pd.NA`。判据统一改 `pd.isna`（try 包住）。
- **EM 空帧不回退（A6）**：`_em_clist` 在 `data=null` / `diff=[]` 时静默返回空帧 —— 不抛错也不
  回退；而 `services/ingest.py` 默认主源路径就是它 → 当日日线静默入库 0 行。已补空帧判定回退新浪。
- **`f21=0` 会覆盖有效股本（B5）**：`_float_shares_from` 只判 `price>0` 不判 `cap>0`，写出
  `float_shares=0.0` 会被 `COALESCE` 当有效值（它只拦 NULL）。已改 `cap <= 0` 一并作废。
- **文档订正 11 处**：`docs/15` 遗留 6 条中 5 条已被 `93975e7` 解决却未加注记（并与下一节「已…」
  口径矛盾）、`market_cap 5221/5221` 与 `5223/5223` 混用；`docs/11` 的 `data/db.py`
  （实际在 `utils/db.py`）、providers 列表里的 `wind`（不存在，Wind 在 `services/wind_client.py`）
  与 `pytdx`（实为 `pytdx_provider.py`）、`data/systemd/`（单元实际在 `orchestration/systemd/`，
  `close` 已撤销）；`docs/15` 阶段 4 设计「18 个模块 / 5 个 provider」→ 实测 23 个 .py /
  8 个 provider；`README` 的 `1850 passed` → `1708 passed`、`daily_bar` 337.8 万行 / 663 日 /
  5221 只 → 340.9 万行 / 669 日 / 5223 只、并注明 DDL 30 张但库内 emotion-core 侧实建 24 张
  （`ref_*` 4 张 + `ops_*` 2 张未建；同库另有 CPT 项目的 10 张 `cpt_*` 表）；`docs/15` 两处行号
  引用差 1（`diagnose_service.py:118`→`:117`、`eastmoney.py:104-107`→`:104-106`）。
- **测试**：`pytest tests/unit tests/architecture -q -p no:randomly` → **1708 passed**（上一轮 1696，
  本节 +12）；ruff 改动 7 个文件与 `HEAD` 逐条相同、**零新增**；现场 `--dry-run` 实证
  `过滤已退市证券 334 只（f100='-'），保留 5228 只在市` 且库未被改动。

### 修复 · 2026-10-13 二轮：补建 6 张未建表 + `stock_basic` 纳入 5 只在市新股

- **DDL 与库内不一致（已解）**：`schema.py` 声明 30 张表，库内 emotion-core 侧只建了 24 张。缺失的 6 张被 5 个服务模块**真实引用**（`ref_security_status` 47 处 / `ref_dividend` 38 / `ref_limit_rule` 29 / `ops_quota_ledger` 25 / `ref_rs` 24 / `ops_raw_manifest` 23），故修法是**建表**而非摘 DDL。`schema.create_all()` 幂等（`CREATE TABLE IF NOT EXISTS`），public 表 34 → **40**（30 + CPT 10）。
- **`stock_basic` 纳入 5 只在市新股**：一次正常 `fill_stock_basic.backfill()` 写入 `001381 皇冠新材`、`301569 联亚药业`、`301660 N粤芯-U`、`301718 通则康威`、`689009 九号公司-WD`，5223 → **5228**；`301660` 的历史缺口随之闭合。覆盖率（2026-10-12）分母 5228 / 分子 5223 = **99.90%**。
- 其中 3 只（`001381` / `301569` / `301718`）尚无上市日期：已实测确认不在深交所 `A股列表`（该列表最大 3017xx 为 `301717`）⇒ 属「已发行未上市」，为预期空值，后续每日 `basic` 步自动补齐。
- **新发现（未修，待拍板）**：`stock_basic.is_st` 全表 **0/5228** 为真，而名称带 `ST`/`*ST` 前缀的有 **198** 只；该列会进个股诊断 LLM prompt（`llm/prompts/stock.md:11`）。免费修法是在 `fill_stock_basic` 用名称前缀落 `is_st`（零 Wind 额度）。

### 修复 · 2026-10-13 `stock_basic.is_st` 落地（198/5228）+ 停牌股诊断崩溃

- **`stock_basic.is_st` 全表为 false** —— 简称带 `ST`/`*ST` 的 **198** 只全被写成 false。该列是 `ref_security_status`（T7，ST SCD2）的**入口过滤**（`SELECT code FROM stock_basic WHERE is_st = true`），全 false ⇒ Wind 的 ST 事件回填**永远取不到任何 code**。改为在 `fill_stock_basic` 从简称前缀落 `is_st`（先剥 `XD`/`XR`/`DR`，除权日简称是 `XD*STxx`），**零 Wind 额度**；PIT（历史 ST）语义仍由 `ref_security_status` 的 SCD2 承担，本次不动。
- **修停牌股个股诊断崩溃**：`GET /api/stock?code=000016`（*ST康佳A，停牌）返回 **Empty reply from server** —— `algorithms/stock.py:110 attach_volume_ratio` 在「前 5 根成交量全 0」时 `ZeroDivisionError`，被 `http.server` 吞成空回复；任何停牌股都会中招。均量为 0 时量比返回 `None`，并补回归测试。
- **`derived_bar` 口径断点（已定：前向生效，用户 m03030）**：`is_st` 转真**打开**了 `derive.py:88` 的 `WHERE NOT s.is_st`（该谓词历史上从未生效）⇒ `derived_bar` **自 2026-10-13 起不再收录 ST 股**，与 2024-01-02 ~ 10-12 的历史形成断点（当日影响约 198 行、其中涨停约 2 行；实测 `compute_derived(2026-10-12)` 返回 **5025 = 5223 − 198**，旧日期不掉行因为 upsert 从不 DELETE）。**不**用今天的 `is_st` 回扫历史：`derive` 阈值是板块口径（无 ST 的 5% 分支），库里 2465 行 ST 涨停其实是**该股变 ST 之前**的真实涨停。`algorithms/derive.py` 已就地加注释留痕；要消除断点须先上 `ref_security_status` 的 PIT 回填，详见 `docs/15-实施追踪.md`「is_st 落地」节。


### 新增 · 2026-10-13 R3 持仓决策：`holding_decision` + 价位/数量算法 + CPT 客户端 + `/api/holding` + 个股页卡片

- 新表 `holding_decision`（23 列，`UNIQUE (trade_date, code)`）落 `data/schema.py` → 本仓 DDL 31 张、库内 public 41 张（31 + CPT 共用 10 张 `cpt_*`）。
- `domain/holding.py`（`Holding` / `Levels` / `Sizing` / `Ladder` / `Decision`）、`algorithms/levels.py`（CPT 结构位优先 + 固定百分比兜底）、`algorithms/sizing.py`（固定风险 + 流动性上限 + 单票上限取小；按手数取整，主板 100 / 科创板 200；只剩单票上限时不给股数）。
- `services/cpt_client.py`（CPT 只读 HTTP：绝不抛、只回环、永不传 `ensure_factors`、两种 `data_quality` 形状）、`data/holding_store.py`、`services/holding_service.py`（`decide()` / `analyze_holdings()` / CLI，默认只读、`--persist` 才写，`(trade_date, code)` 幂等）。
- HTTP `GET /api/holding`（`code` / `cost` / `shares` / `capital` / `as_of` / `cpt`）+ `GET /api/holdings`，**恒只读**；个股页新增「持仓决策」卡片（`templates/stock.html` + `static/stock.js` + `static/style.css`）。
- 设计依据 `docs/17-持仓决策-功能与代码设计.md`（决策 D1–D9）；全量 `pytest tests/unit tests/architecture` → **1935 passed**。

### 修复 · 2026-10-13 `exchange_list` 加 20s 硬超时（daily `basic` 步曾卡死 8 小时）

- `data/providers/exchange_list.py` 的 `_fetch_one()` 由「只有 `retry_fetch` + 空帧检查」改为 **daemon 线程 + `t.join(_FETCH_TIMEOUT=20.0)`**，超时抛 `FetchError`，`fetch_list_date_map()` 按既有口径 warning 跳过该市场。
- 起因：2026-10-13 akshare 上交所 `stock_info_sh_name_code` 那条读挂住（无超时），daily 流水线自 `09:23` 卡到 `18:00+`，当日 `derive` / `market` / `ladder` / `signal` / `promotion` / `theme` / `strategy` / `outcome` / `health` 全未执行。
### 修复 · 2026-10-13 `derive` 步对停牌股 NaN 列容错（曾让当日其余 9 步全不跑）

- `services/derive_service.py` 的 `_load_bars` 原写 `int(r.volume) if r.volume else 0` —— **NaN 是真值**，该写法挡不住：`688496`（*ST清越，停牌）在 2026-10-13 的 `daily_bar` 行 open/high/low/volume/amount 全为 NULL（pandas 读成 NaN），`int(nan)` 抛 `ValueError: cannot convert float NaN to integer`，derive 步失败、当日 `market`/`ladder`/`signal`/`promotion`/`theme`/`ecosystem`/`strategy`/`outcome`/`health` 全没跑。
- 新增 `_to_int()` / `_to_float()` 两个容错转换（None / NaN / 不可解析 → 0 / 0.0），替换 `volume` 与 `turnover_rate` 两处；补 6 个单测（含「停牌股全空列」回归）。
- 补跑 `--date 2026-10-13 --from-step derive` → 14 步全 OK，`derived_bar` / `market_stat` 到达 2026-10-13。

### 修复 · 2026-10-14 面板元素级无头审计（A1–A6：持仓卡片样式落在孤儿文件 + 边界链接 + API 兜底）

以 playwright 按**元素**为单位逐页审计（直观 / 逻辑 / 算法 / 策略 / 个股，5 路由 × 3 标的），
下面只列真实缺陷（全页 `None` 那类是误报——实为 `<code>` 里的算法说明正文）：

- **A1（真 bug）** R3 持仓卡片的 `.holding-*` 全部写在 `static/style.css`，而模板只加载
  `static/dashboard.css` ⇒ 卡片功能在、样式全无（表单退回 `block`、按钮是个灰疙瘩）。
  整段迁入 `dashboard.css`；实测 `form.display=flex / gap=14px`、`input.borderRadius=6px`、
  `button.backgroundColor=rgb(9,105,218)`。
- **A2（真 bug）** 快照导航切到边界日时渲染出 `href="…?date=None"`（点进去等于不带日期），
  只靠 `pointer-events:none` 挡点击。改为输出 `<span class="btn-link disabled" aria-disabled="true">`，
  实测 `date=None` 归零。
- **A3（真 bug）** 持仓「分析」在旧面板进程下显示字面量 `undefined · undefined`；
  `renderHolding()` 增载荷形状校验，缺字段时提示「面板进程可能早于后端代码，需重启服务」。
- **A4** 删除两个孤儿静态文件 `static/style.css`、`static/app.js`（零引用；主题变量在
  `dashboard.css` 另有完整一份、主题切换 `base.html` 已内联同款实现）。
- **A5** 未识别的 `/api/*` 由「兜底成 status 载荷（`ok:true`）」收紧为
  `404 {"ok": false, "error": "未知 API 路径：…"}`；兜底只留给 `/api/status`（门户「今日速览」依赖）。
- **A6** 日历「无快照」日的 `.cal-day.no-data` 补 `opacity:.5`，与图例 `.dot-none` 同口径。

文档订正：`docs/11-代码架构设计.md:157`、`docs/09-展示层设计.md:204` 的静态资产名改为实际
文件（`dashboard.css` + `stock.js`）；新增回归测试 `TestStaticAssetWiring`（模板引用的资产必须
存在、`static/` 不留孤儿文件、卡片与日历规则必须在**被加载的那份** CSS 里）。全量
`pytest tests/unit tests/architecture` → **1945 passed**。

---

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
