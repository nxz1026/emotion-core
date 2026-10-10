# 更新日志

�?�? notable 变更均以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)�?

---

## [Unreleased]

### 修复 · 2026-10-09 巡检拍板落实（配�? 300 / 决策去重 / SSE 超时 / strategy 单元 2h�?

- **上调配额（拍�? ①）**：`utils/config.py` �? `STRATEGY_MAX_LLM` 默认 `50` �? **`300`**
  �?20 �? × 15 策略）�?�实测每�? LLM 调用�? 7.2s�?10-09 `09:50:01`→`09:56:01` = 50 �? /
  6min01s）⇒ 300 次约 36min，故 live `emotion-core-strategy.service` �?
  `TimeoutStartSec` `3600` �? **`7200`**（备�?
  `/etc/systemd/system/emotion-core-strategy.service.bak-20261009-153349`；`systemctl show`
  实测 `TimeoutStartUSec=2h`），`portal` 仓快照同步�??
- **决策�? code 去重（拍�? ④）**：`presentation/trade_api.py::_query_signals` 改为
  `DISTINCT ON (code)`（同�?只票取分数最高；同分�? `created_at DESC`，再同取
  `prompt_hash DESC`，保证结果确定），`handle_trade_decisions` 再加 `seen_codes` 兜底 +
  WARNING�?**根因**：`strategy_signal` 唯一键含 `prompt_hash`，上下文变化后重跑会追加版本�?
  （版本行本身是设计）�? 同一 `(code, strategy)` 的多版本会同时命中，�? LKL-Trade 当成多笔
  `OPEN_POS`。线上实证：旧缓存批�? `fbc43162` = **13 �? action / 5 �? code（含重复�?**，且�?
  批次**从未被消�?**（无 `results_*` 回执）⇒ 备份 `/home/ubuntu/trade/state.json.bak-20261009-152842`
  后清掉当日缓存，重启后新批次 `7ff2725c` = **4 �? action / 4 �? code / 无重�?**
  （`000420`/`000546`/`000736`/`000710`），`state.json` 落盘同样去重�?
- **SSE 超时（拍�? ②）**：`/plugins/events`（前�? `EventSource`）原先没有独�? location，落�?
  `location /` 继承 `proxy_read_timeout 300s` �? nginx �? 5 分钟切一次连接并记一�?
  `upstream timed out (110)`（功能正常，纯日志噪声）。新�?
  `location = /plugins/events`（`proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s;
  proxy_send_timeout 3600s`，同 `/dsh-bottom-terminal/` 的先例），备�?
  `/etc/nginx/sites-enabled/dsh-web.bak-20261009-153349`。实测：改前 error.log �? 300s �?�?
  �?13:31:32 / 13:36:35 / 13:41:39），改后单条连接撑过 330s
  （`curl --max-time 330` �? `code=200 time=330.00 exit=28`，即被测试自己的上限切断，�?�不�?
  nginx �? 300s）�??
- 新增测试 `tests/unit/test_trade_api_decisions.py`�?5 例：SQL 形与参数、查询返回重�? code �?
  �? code 只出�?条且落盘缓存去重、同日二次调用走缓存、无 BUY 不落缓存、非法日�? 400）�??
  红→绿已证：�? `trade_api.py` 还原�? HEAD 复跑�? 2 例失败�??

### 修复 · 2026-10-09 运行日志巡检（emotion-core �? C/D/E/F�?

- **C 候�?�池口径（用户拍板）**：`services/strategy/universe.py::build_universe` 改为
  「手动自选（`watchlist`，code 升序，优先）+ 热门池（`hot_rank` 当日�? `rank, code`
  �? `STRATEGY_HOT_N`）�?�，**不再�? `limit_pool_em`**（涨�?/炸板池与热门池高度重叠）�?
  删掉 `OBSERVED_POOL_TYPES`。汇总行改为�?**跳过原因分布**
  （`runner.py::_reason_distribution`，按次数降序）�?��?�此前�?�为�?么只跑了 4 只票」在
  DB 与日志里都不可见�?
  ⚠️ 算术：`STRATEGY_MAX_LLM=50` ÷ 15 个策�? = **每天只够 3.3 只票**�?10-09
  universe 79 codes �? 配额仍是硬上限，覆盖到哪只票由�?��?�顺序决定�??**2026-10-09 已拍板抬�?
  300（见上一节），本节保留当时的口径�?**
- **D 补跑能力**：`orchestration/strategy.py` 新增 `--codes 600825,000420`（显式�?��?�，
  跳过候�?�池，走 `filter_codes`：`BOARD_PREFIXES` 过滤 + zfill(6) + 保序去重、不截断），
  用于补齐历史缺口；`run_for_date(trade_date, codes=None)` 主循环之后新�?**自动补跑�?�?**
  （本�? LLM 失败 / 输出契约失败�? `(skill, code)`，在配额与熔断有余量时按
  `RETRY_ROUND_DELAY`（env `EC_STRATEGY_RETRY_DELAY`，默�? 10s）重跑）�?
  `_snapshot(..., merge=True)`：补跑是�?部运行，必须与旧快照�? `(code, strategy)` 合并�?
  否则会把当日完整快照（其�? code �? items �? skipped 分布）一起冲掉�??
  实跑�?10-08 `600825` �? 2 行（`ma_golden_cross`/`shrink_pullback`，均 PASS 15）；
  10-09 `000420` 首轮 `chan_theory` 失败 �? 补跑轮后 15/15 入库�?
- **E pool 单元重启上限归位**：`StartLimitIntervalSec` / `StartLimitBurst` �? `[Service]`
  搬到 `[Unit]`（systemd 只认 `[Unit]` 里的新键名；�? `[Service]` 只打�? `Unknown key`
  后按默认值生�? —�?? 表现为�?�写了等于没写�?�）。live
  `/etc/systemd/system/emotion-core-pool.service` 已改（备�?
  `/root/emotion-core-pool.service.bak-20261009-130607`），`systemctl show` 实测
  `StartLimitIntervalUSec=1h` / `StartLimitBurst=4`；`portal` 仓库快照同步�?
- **F pool 逐日硬超时（根因修正�?**：下�?节�?�`setdefaulttimeout(15)` 就算修好」的结论
  **不成�?** —�?? akshare 三池函数�? `requests.get(url, params=params)`�?**不传 timeout**
  会被 requests 解析�?**显式 `None`**（`TimeoutSauce(connect=None, read=None)` �?
  `socket.create_connection(addr, None)` / `sock.settimeout(None)`），显式 None �?
  **永久阻塞**；进程级 `setdefaulttimeout` 只在调用方根本没�? timeout
  （`_GLOBAL_DEFAULT_TIMEOUT` 哨兵）时生效。实�? `requests.get(timeout=None)` 阻塞
  3.00s，�?? `timeout=0.5` �? 0.50s �? ReadTimeout。修法：`pool.py` 逐日
  `SIGALRM`+`setitimer` 硬上限（`_DEFAULT_PER_DAY_TIMEOUT=180.0`，CLI
  `--per-day-timeout`，`_DayTimeout(BaseException)` 以穿�? `except Exception`），
  超时�? WARNING �? `continue`，已写成功的天保留；`setdefaulttimeout(15)` 保留�?
  纵深防御。live 实跑 3 天：73/88/91 行�?�exit 0 —�?? 此前 10-08 卡死 600s �? TERM�?
  当天�?**完全没写�?**，现 10-08 = 88 �? / 3 个池型�??
- **遗留（未修，待拍板）**：`strategy_signal` 唯一键是
  `(trade_date, code, strategy, prompt_hash)` �? 上下文变化后重跑会为同一
  `(date, code, strategy)` **追加新版本行**，�??
  `presentation/trade_api.py::_query_signals`（`:43-52`）按 `trade_date + action` 取行�?
  **没有版本去重** �? 重复行会以重�? decision 出现�? `/api/trade/decisions`�?

### 修复 · emotion-core-pool 卡死�?2026-10-09�?

> ⚠️ 本节对根因的描述已被上节 F 修正：`setdefaulttimeout(15)` 兜不�? akshare/requests
> 的显�? `timeout=None`，真正的护栏是�?�日 `SIGALRM` 硬超时�??

- **�? EM 接口挂死的根�?**：`pool.py` 入口�? `socket.setdefaulttimeout(15)`�?
  akshare 调东财接口走 `requests`，akshare **不传 timeout** 给底�? socket—�?�EM
  接口挂死时进程无限等，靠 systemd `TimeoutStartSec=600` 兜底�?进程，但 service
  �? `failed` �? timer 不会自动重试�?15s 上限�? retry_fetch 仍做 3 次指数�??�?
  �? 单池�?�? 45s�?3 �? × 3 池最�? ~7min。DB �? `utils/db.py:_kwargs` 已显�?
  `connect_timeout=15`，与全局不冲突�??
- **service unit 自愈**：`emotion-core-pool.service` �? `Restart=on-failure` +
  `RestartSec=300` + `StartLimitIntervalSec=3600` + `StartLimitBurst=4`�?
  failed 不再僵死到下�? cron；明�? 10-09 09:45 cron 触发时即使挂也只浪费
  �?次重启，不会�? systemd�?
- **2026-10-08 实测触发�?**�?09:45 cron 启动 �? 09-30 抓取卡死 �? 09:55:48
  systemd �?（status=15/TERM）→ service failed �? 14h 没人动�?�第二天 10-09
  cron 自愈就是这一改�??

### 新增 · 2026-10-10 R58-4 次级推荐档（RECOMMEND�?+ BUY 即时飞书推�??

- **�? Action.RECOMMEND（domain/signal.py�?**：`Action` 枚举�? `RECOMMEND = "RECOMMEND"`，与 BUY/SECONDARY/SELL 平级
- **algorithms/entry.py::c3_loose**：BUY c3 放宽版�?��?�`候�?? �? 幸存集`（不要求唯一幸存），其它条件�? BUY �?致（c1/c2/c4/c5 + MIN_LEADER_DAYS + DIVERGE_MIN_TURNOVER 均不变）
- **algorithms/entry.py::_RECOMMEND_CONDITIONS**：RECOMMEND 档专�? checklist 五条件，�? c3 �? c3_loose，c1/c2/c4/c5 字段名沿用主版（`c1_uniqueness` / `c2_exchange` / `c3_elimination` / `c4_min_days` / `c5_strength_diverge`）�?��?�`Checklist.passed` 单一聚合器可同源复用
- **algorithms/entry.py::check_recommend_signal**：RECOMMEND 候�?�仍�? `ladder.sole_top(MIN_LEADER_DAYS)`（与 BUY 同门槛不降档），禁买日（buy_window=NONE）也生成（与 SECONDARY 同观察纪律），落�? action=RECOMMEND
- **链路 BUY �? RECOMMEND �? SECONDARY**：主 BUY 通过即终止回�?；BUY 未过�? RECOMMEND 通过时返�? RECOMMEND（不再尝�? SECONDARY）；BUY/RECOMMEND 都未过时再尝�? SECONDARY。同�? confirm_date 不会同时�? BUY+RECOMMEND �? RECOMMEND+SECONDARY
- **services/notify.py::push_buy_signal**：BUY 信号刚落库即触发飞书 webhook�?24h 内存 dict 节流 `(date,code)`，网络异常不污染 dedup，下次重试可推），与日报摘要推�?�并行（独立时点）�?�仅 BUY 触发；SECONDARY/RECOMMEND 是观察信号不触发
- **services/notify.py::_format_buy_signal**：BUY �?报飞�? text 格式—�?�标�? + 板数 + buy_window + 5 条件 checklist（✓/�?/�? 三�?�可见），不含持仓明�?
- **domain/Signal 内存字段新增 `name` / `cont_days`**（仅内存使用�?**不入�?**）：`_persist` 只写 `(date, code, action) + window + checklist`；新字段�? BUY 推�?�模板使�?
- **services/signal_service.py::_push_buy_safe**：run() �? BUY 后调 push_buy_signal，异常吞掉不�?

业务影响：近 90 �? 1 BUY �? 估算 ~5 BUY（双幸存场景可触发）；SECONDARY 不变；RECOMMEND 仅观察不入交易导�?

测试�?

- `tests/unit/test_entry.py`：c3_loose 7 例（双幸存过、无竞争拒�?�新插队拒�?�不在幸存集拒�?�R2 关闭、与主版差异、_RECOMMEND_CONDITIONS 引用 c3_loose 而非 c3_elimination�?
- `tests/unit/test_entry.py::TestCheckRecommendSignal`�?4 例（NONE 日观察记录�?�双幸存场景落库、name/cont_days 内存填充、sole_top �? MIN_LEADER_DAYS 不降档）
- `tests/unit/test_entry.py::TestChainOrder`�?4 例（BUY 通过终止回�??、BUY 失败 RECOMMEND 通过�? RECOMMEND、BUY/RECOMMEND 双败回�?? SECONDARY、NONE �? RECOMMEND/SECONDARY 都尝试）
- `tests/unit/test_signal_service.py::TestR58_4BuyWebhookHook`�?5 例（BUY 触发、SECONDARY/RECOMMEND 不触发�?�push 异常不破 run、无信号不触发）
- `tests/unit/test_notify.py::TestPushBuySignal`�?8 例（首次推�??24h �? code 节流、不�? code 不互阻�?�不�? date 不互阻�?�网络异常不污染 dedup、无 URL 静默、飞�? text payload 校验�?0 板不抛）

总测�? 1618 passed（unit 1571 + architecture 47），0 failed；ruff 仅遗�? 4 个预存错（I001 / UP035 / UP042×2，HEAD `96dd7cb` 即存在，与本次无关）

Commits：`d3348c8` + `7be5916` + `54e1765` + `5d8f5c8`

---
## 2026-10-07 · （第二段：systemd 对齐 + close 撤销 + doctor 可失�? + 文档以实测为准）

### systemd：仓 �? `/etc/systemd/system` 双向对齐（两边现在都�? 11 个单元）

| 单元 | 修之�? | 修之�? |
|---|---|---|
| `dash.service` | 只在生产 | **按生产原样收进仓�?** |
| `strategy.{service,timer}` | 只在生产 | **按生产原样收进仓�?** |
| `close.{service,timer}` | 只在仓库�?**从未装到生产** | 🚫 **已撤�?** |
| 5 �? `.timer` �? `Asia/Shanghai` | �? `.timer.d/10-timezone.conf` 提供 | **直接写在 `.timer` 本体**；`/etc` 与仓内的 `.timer.d/` 均已删除 |

- **备份**：`~/ec-systemd-backup-20261007-065933.tar.gz`�?11 个单�? + 4 �? drop-in 目录，删改前打）�?
- **时区真相源合�?**是这次的重点。两份都存在时，改了本体忘了 drop-in（或反过来）�?
  漂移的表现是**排期静默�? 8 小时、没有任何报�?**。同步后实测 `systemctl list-timers`
  �? NEXT 时刻与同步前�?致（±20 秒内，来自新加的 `RandomizedDelaySec` 重掷），
  **没有出现 8 小时偏移**；`emotion-core-dash` �? `cpt-dashboard` 全程 active�?:8098 HTTP 200�?
- `daily.timer` 新增 `RandomizedDelaySec=120`（pool 60 / report 60 / watchdog 120 **原本就有**�?
  `strategy.timer` 没有，是刻意不加—�??17:50 紧跟 daily，不�?要抖动）�?

### 撤销 close 重复调度

`emotion-core-close.{service,timer}`�?15:05）执行的�?**完整 13 �? daily**，与 17:20 �?
`emotion-core-daily.timer` **全量重复**；�?? `docs/08` §4.2 / `docs/13` �?说的「收盘对账�??
模块 `orchestration/close.py` **从未存在**。已 `git rm`（历史可追回），并在
`docs/08`、`docs/10`、`docs/13`、`docs/15`、README 五处标注撤销原因�?

### doctor「日线双源�?�从永不失败改为可失�?

`_check_provider_consistency` 原先四个出口�? `True`，偏差只 `log.warning`
�? `doctor.run()` 里这�?�?**永远显示通过**。这是照�? lkl 时一起搬过来�?**失效�?�?**�?
不是本仓的设计�?�现在：查出偏差 �? `ok=False`，note 点名�? 5 只并保留总数�?
「没得查」（链关�? / 未注�? / 无样�? / 依赖不可用）仍返�? `True`�?
已标�?**唯一�?处有意偏�? lkl**，并改掉模块头�?�未新增别的分支」的表述�?3 条新测试 + 变异验证�?

### 文档矛盾�?律以实测为准

- `docs/15` 两处同名「部署状态�?�直接打架（`/cpt/` �?�? �? �?�? �? 已移除；
  `/dashboard/` 上游 8099 vs 8098）→ **合并为一�?**并按 2026-10-07 实测重写�?
  另附 nginx / systemd / 数据现状三张实测表�??
- `schema.py` �?27 张表」→ **30**（`len(DDL)`）；原表清单只列�? 23 个，已补全�??
- `docs/05` §1.3 标题「空壳（15 张）」但表内 13 行�?�正文结论也�? 13 �? 统一�? 13�?
  并把「其�? 13 张�?�更正为「其�? 11 张（5 落地 + 6 暂缓）�?��??
- **澄清�?个不�? bug 的矛�?**：`docs/05` 通篇的�??41 张表」是 **`longkonglong` �?**
  （lkl 时代）口径，�? emotion-core 用的 `emotion_core` 库不是同�?个库，已加互不校正说明�??
  实测 `emotion_core.public` 32 �? = DDL 30 �? 未建 6 + CPT 共用�? 8 �? `cpt_*`�?
- `README` 阶段 8 `�? 进行中` �? `�? 已完成`（按实测重写）�??

### 更正我自己写下的�?处错误陈�?

README 曾写「仓内没有任何单元处�? enabled/active，emotion-core 的共享表没有东西会写」�??
**实测为误**：五�? timer 全部 `enabled + active`，`emotion-core-daily.timer` �?
2026-10-06 17:20 北京成功执行、`pipeline_state` 13 步全 `OK`。业务表停在 2026-09-30
是因�? 10-01~10-07 国庆休市、交易日守卫正常跳过�?**不是没有调度�?**�?

### 顺带修的门禁自身缺陷

- `test_dependency_direction.py` �? `str(rel)` 生成键，Windows 下得�? `algorithms\ladder.py`
  �? `_DEBT` 写的是正斜杠 �? 同一条目**同时**被判成�?�新增违规�?�和「已偿还」，
  两个测试互斥失败�? Linux/CI 全绿。改 `as_posix()`�?
- systemd 契约门禁两处过严：`-m` 目标只认 `def main(`（漏�? `presentation/server.py`
  �? `__main__` 守卫形�?�）；引用检查只�? `.service`（漏�? `strategy.timer` �?
  `After=emotion-core-daily.timer`）�??**门禁比约定更严不是更安全，是没人愿意维护它�??**

ruff format 债务 226 �? 225，基线已下调。生产全�? **1912 passed + 2 skipped**�?

---


### 同日另批：[Unreleased] �? 2026-10-07（以层为单位审计的收口：零告警三成因 + 三道新门禁）

本节�?有修复都�?**变异验证**（把修复改回原样，确认对应测试真的会红）�?
生产全量基线�?**1901 passed / 2 skipped**（修�? 1857 passed / 2 skipped）�??

### 修复 · 监控自噬链（这三条叠�? = 2026-10-06~07 的零告警�?

- **断档日历从被监控对象派生**：`algorithms/health.py::_recent_trading_days` 原来�?
  `SELECT DISTINCT date FROM daily_bar` —�?? 日历是行情自己的投影，于�? `daily_bar`
  �?停写，断档天数同步变小，**恰好在最该报警时沉默**。改用独立的 `trade_calendar`
  （生产实测覆�? 1990-12-19~2026-12-31，不�?化成 `weekday()` 降级）；
  日历读不到时返回 `[]` 并记 error，�?�不是拿坏日历算出一个偏多的「应到未到�?��??
- **watchdog �?出码只认新入�?**：`health.push()` 内部先按 `(source, 归一�? detail)`
  去重 �? 同一�?**没修复也�? ack** 的断档，第二天起新入队数恒为 0，watchdog 每次�? 0�?
  systemd �?路绿灯�?�改判�?�仍有未确认�? health 断档」，取数窗口与判重视窗同�? 200�?
- **`emotion-core-close.service` 丢弃返回�?**：`python -c "�?; run_daily()"` �?
  `run_daily` 的返回�?�丢在地�? �? 失败�? 1 / 覆盖率拦�? 76 全被吞；且不�? `main()`
  �? �? `basicConfig`，journal 里没有进度�?�改�? `-m emotion_core.orchestration.daily`�?

### 修复 · 行情口径

- **TDX `pre_close` 取到未来�?**：`pytdx_provider._tdx_all_bars` �? docstring 明写
  「TDX 返回降序」，�? `PytdxProvider.fetch_daily_bars` �? `backfill_tdx.fetch_code`
  都在**降序�?**上直�? `shift(1)` �? 每行拿到的是**次日**收盘价�?�pre_close 是涨停判�?
  基准（caliber C2），取未来价会让涨跌�?/涨停整体反向。改为先 `sort_values("date")`
  �? shift，且 shift 放在区间过滤**之前**，用区间外那根给首行播种�?
- **`normalize_frame` 自相矛盾**：`code` 被算进必�?列，可函数下�?行就�?
  `out["code"] = code`。⇒ `PytdxProvider` / `TencentProvider` 恒定抛�?�缺少列: code」，
  **从未成功返回过一�?**；之�?以没人发现，�? `test_tencent.py` �? `normalize_frame`
  整个 mock 掉了�?
- **Rust `board_pct_milli` 漏北交所 `92` 号段**：北交所 2023-04 起对新上市公司启�?
  920xxx，Python 判据�?直是 `("4","8","92")`，Rust 只抄�? `4`/`8` �? 920xxx 按主�? 10%
  算涨停价。（生产影响为零：C7 已把北交�?排除在判据层之外。）

### 修复 · 环境时区与可导入�?

- `orchestration/strategy.py` / `pool.py` �? `date.today()` �? `today_sh()`。机器时区是
  Etc/UTC，北京时�? 00:00~08:00 之间会少�?天（`entry.py:266` �? P1-3 明文禁止隐式
  `date.today()`）�??
- **`core/__init__.py` 改惰性加�?**（PEP 562）�?�`.so` �? `.gitignore` 的构建产物�?�仓�?
  没有编译它的 CI 步骤 �? 干净环境�? `import emotion_core.core` �?**模块导入�?**就崩
  （`AttributeError: 'NoneType' object has no attribute 'loader'`），连带
  `tests/oracle/*_rust_vs_python.py`、`test_dragon_env.py`、`test_ecosystem_service.py`
  在收集阶段即报错 —�?? **CI �? `pytest tests` 从未真正跑�?�过**�?
- **新增 `tests/conftest.py`**（本仓首�? conftest）：产物缺失时把依赖 Rust 的测试整�?
  skip 并在 stderr 说明原因。判据用 **AST 解析 import**（不是文本搜索，否则
  `test_dependency_direction.py` 里注释提到的 `dragon_env` 会被误伤）�??

### 门禁

- **新增 `tests/architecture/test_systemd_units.py`�?31 例）**：禁 `python -c` 入口�?
  校验 `-m` 目标真有 `main()`、`OnCalendar` 必须自带时区、drop-in 的生效�?�不得与
  本体矛盾、`Requires=` 指向�? service 必须存在�?
- **`tests/caliber/test_price_unique_impl.py` 扫描范围扩到 `.rs`**：此前只�? `*.py`�?
  于是 Rust 侧可以自由长出第二套板块比例而无人拦阻�?�新增�?�Rust↔Python 板块前缀
  对等」断�?�?**源码�?**，不�?�? cargo —�?? 生产机上没有 cargo，无法就地重�? `.so`�?
  �? `.so` 重编前这是唯�?能立刻发现分歧的东西）�??
  该断�?首版�? Rust �? `starts_with` 过滤、Python 侧实�? `startswith`，导致断�?恒绿�?
  已修，并�? `test_python_board_prefix_extraction_is_not_vacuous` 钉住「提取器不许空转」�??
- `scripts/check_doc_numbers.py` 此前 rc=1，抓�? 9 处真实计数错�?
  （`README.md` DDL 声明 24 实为 30；`docs/15-实施追踪.md` 完成度�?�览 8 个层的文件数
  全偏小）�?**改文档让它对**，现�? rc=0�?
- 静�?��?�务棘轮基线下调：ruff check **291 �? 279**，format 待格式化 226（持平）�?

### CI：第�?次真正跑�?

`2026-10-07` 之前**这条 CI 从未绿过**。第�?次去看日志才发现，连续三�? push 全红�?
`Unit and integration tests` �? 20 failed / 1489 passed / 2 errors。根因全�?
「干�? runner �? 生产机�?�，不是代码改动引入的：

| # | 症状 | 根因 | 处理 |
|---|---|---|---|
| 1 | 18 �? `RuntimeError: 读不�? ~/.dbconfig` | `db.py` �? `psycopg.connect` **之前**先读凭据；这批测试只 mock �? connect，生产机能过**纯靠那台机器恰好�? `~/.dbconfig`** | CI 里起 Postgres service 并写 `~/.dbconfig` 指向它；不在 workflow 里另抄一�? DDL |
| 2 | `test_health::TestRun` 真去连库 | CHECKS �? pipeline 项调 `algorithms/pipeline.py::failures()`，那�?**另一个模块自己的** `query_df`，原 fixture �? patch �? `health.query_df` | fixture �? `_pipeline_mod.failures` 替身 |
| 3 | `test_wind_client::test_availability_key_missing` | 只覆�? `config_path`，`cli_script` 落到机器相关的默认路径；�? `availability()` 先判 CLI 存在 �? 这条测试能否过取决于**跑测试的机器装没�? wind** | 两个路径都显式给（与 `test_availability_ok` 同形�? |
| 4 | 建表步骤绿灯但表不存�? | `connect()` 不开 autocommit，`create_all()` 也不自己提交 �? 连接�?�? DDL 被回�? | 建表�? `transaction()` |

**当前两套口径（别把它们混为一谈）**�?

- **CI（GitHub Actions�?**�?**1457 passed / 2 skipped**。起 Postgres 16�?
  用仓�? `schema.create_all()` �? 30 张表�?**依赖 Rust 的测试整份跳�?**
  （oracle 对账 6 �? + `test_dragon_env` + `test_ecosystem_service` + `test_daily`�?
  —�?? `.so` �? gitignore 的构建产物，�? CI 里没�? cargo 去编它�??
- **生产�? oracle**�?**1901 passed / 2 skipped**（`.so` 在，Rust 对账全跑）�??
- 两�?�差�? **444** 就是那批 Rust 依赖测试�?**Rust↔Python 对账目前只在生产机跑**
  （`docs/06` §3.3：每�? `cargo build` 之后必须跑一遍）�?

> 想让 CI 也覆�? Rust 对账，需�? workflow 里加 Rust 工具�? + `cargo build --release`
> + 拷贝 `.so`。本机没�? Rust 工具链，**未经验证**，故没有写进�? —�?? 不写没验证过的步骤�??

### 未修（需 owner 裁决，不是�?�修不好」）

- **生产 `.so` 仍无 `92` 修复**：机器上没有 cargo，`src/core_lib/emotion_core_rust.so`
  无法就地重建（当�? md5 `44ebfc09…`、mtime 2026-09-30 01:47:57 UTC）�?�须在别�?
  `cargo build --release` 后拷回，并按 `docs/06` §3.3 �? `pytest tests/oracle` 对账�?
- **`Checklist.passed` �? None 返回 True** 核实�?**有意契约**（UNKNOWN 不否决，
  Python/Rust/回测三边�?致，`tests/oracle/test_exit_rust_vs_python.py:89` 断言），
  已补 docstring 说明，不改行为�??
- `docs/evidence`、`docs/archive-lkl` 等历史目录仍保留旧数字，按门禁的
  「历史目录不要求对齐」原则豁免；若要�?并更新需单独立项�?

> 上一版此处列�? systemd 漂移、`close` 职责重复、doctor 双源永不失败�?
> `docs/15` 两处部署状�?�冲�? —�?? **四项已在 2026-10-07 第二段全部处理完�?**�?
> 见本文件上方对应条目�?

### 同日另批：飞�? webhook 接线（上线前 360° 审计�?

- **webhook 从未配置**，告警与日报只留在本机，等于上线当天无人值守。现接入
  �? CPT 同一个飞书群�?
- **原实现根本发不出飞书**：`_guess_channel` 只认企业微信/钉钉，飞书落�?
  `generic` �? `{"text": ...}`，�?�飞书自定义机器人要
  `{"msg_type":"text","content":{"text":...}}`。更阴的是它**看起来成�?**—�??
  飞书在报文非法时仍回 HTTP 200，成败只�? body �? `code` 字段�?0=成功），
  而原判断只看 `status_code`。已�? `feishu` 渠道 + 正确报文 +
  `_resp_ok()` 额外校验 `body.code`（不符时�? warning）�??
- **drop-in 不在仓内，换机必然漏�?**：watchdog / report 单元的注释都写着
  「webhook 可在 drop-in 里补」，但那文件既不在仓库也不在任何部署脚本里�??
  改为�?**仓内单元**�? `EnvironmentFile=-.../deploy/env/emotion-core.env`
  （前�? `-` = 文件缺失不报错，未配时静默跳过推送，�? notify 语义�?致）�?
  daily �? health 步同样会推，�?并接上�??
- **`.gitignore` 差点�? webhook 提交进去**：原规则只有 `.env` �? `.env.*`�?
  **匹配不到** `deploy/env/emotion-core.env`。已显式�? `deploy/env/*` 并只放行
  `*.example`，同时补模板文件；用 `git check-ignore` 验证过�??
- **env 不复�? CPT 那份**：`EnvironmentFile` 会把文件里每个键都注入进程环境，
  指向 CPT �? `cpt-dashboard.env` 会连带把 `CPT_LLM_API_KEY` 注入本项目，凭据
  越界比没配更糟�?�故单独建只�? webhook 的文件（600），值从 CPT 复制�?
- 顺带修一�?**测试自欺**：`tests/unit/test_notify.py` 里三个测试类**各定义两�?**
  （后者静默覆盖前者，�? 120 行从未执行），另有一条测�?**没有任何断言**�?
  已合并为单份并补飞书覆盖�?40 条（原实际执�? 22 条）�?

---

## 2026-10-01 · (Rust ecosystem 移植)

### 新增

- **src/emotion_core/core/src/ecosystem.rs**: Rust 实现 g1~g4 + b1~b5 + verdict + rate + ladder_health + promotion_strength（PyO3 绑定�?
- **tests/oracle/test_ecosystem_rust_vs_python.py**: Rust vs Python 对账测试 47/47

### 重构

- **algorithms/dragon_env.py**: 纯判定�?�辑下沉�? Rust（_python 文件保留 DB 查询�? + 薄包装）
  - g1/g4/g3/b3/b1/b2/_verdict �? 调用 `_rust.*`（ecosystem.rs�?
  - ladder_health/promotion_strength �? 调用 `_rust.*` 返回 Rust 结构�?
  - DB 查询（_series / _B3_SQL / query_df）保留在 Python �?
  - 函数 docstring 标注 �? Rust: ecosystem.rs::*

### 构建

- **Cargo.toml**: 新增 pyo3 依赖（已存在�?
- **.gitignore**: 移除 emotion_core_rust.so（不再跟踪编译产物）

---


### 同日另批：[Unreleased] �? 2026-10-01 (零覆盖模块收�?)

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

- **pool.py:74**: dry-run 路径调已移除�? `ingest._retry` �? 改用 `retry_fetch`
- **pytdx_provider.py:47**: `_TLS` �? `_tls` 模块级变量名大小写不�?�?
- **test_exactly_half**: priced 计数错误修正
- **test_sina_index_volume**: 下标偏移修正（下�? 8 = �? 9 个字段）

### 文档

- **docs/06**: T16 标记完成
- **docs/15**: 测试数量 1165�?1765�?98/98 模块全覆�?

---


### 同日另批：[Unreleased] �? 2026-10-01 (审计报告修复)

### 修复

- **presentation/server.py**: `get_static` 目录穿越漏洞 �? �? `is_relative_to` 校验 (H1)
- **presentation/server.py**: HTML 回显异常 �? 移除 `{e}` 拼接，改为�?�用 500 + server-side log (H2)
- **services/ingest.py**: `upsert()` f-string SQL 注入风险 �? �? `_UPSERT_ALLOWED_TABLES` 白名�? (M4)
- **Rust unwrap 修复**: 全部 `unwrap()` �? `SAFETY` 注释 + `accelerate.rs` NaN 排序�? `unwrap_or` (M7)
- **utils/fetch.py**: `retry_fetch` 首次 sleep 移除 �? 仅重试间隔�??�? (L2)

---


### 同日另批：[Unreleased] �? 2026-10-01 (产品审计修复)

### 修复

- **P0-2 买点参�??**: `services/stock_service.py` 增加 `_buy_point_reference()`（打板价 + 同层级历史赔率），注�? `buy_point` �? API payload；`stock.html` 增加买点卡片；`stock.js` 增加 `renderBuyPoint()`；`dashboard.css` 增加买点样式�?88e9079�?
- **P1-1 空�?�策略解�?**: `server.py` 增加 `_load_negative_expectation()`（从 signal_outcome 聚合�?+ `no_buy_reason`（策略解释）；`intuitive.html` 空�?�从 DB 心跳改为策略原因�?59103a0�?
- **P1-2 负期望披�?**: `intuitive.html` 页头增加负期望警示框（均�?/中位�?/胜率/样本量）�?59103a0�?
- **P1-3 坦白机制**: `services/gaps.py` 10 项动态检查器；`algorithm.html` 缺口清单条件渲染�?6605cb7�?
- **P2-1 免责声明**: `stock.html` 顶部增加显式免责警示框（BUY/AVOID 动作配套强免责）�?13de08b�?
- **P2-2 日期交互**: `strategy.html` 移除独立 `<select>` 日期选择器，统一使用 base.html 日历组件�?13de08b�?

### 数据

- **schema.py**: 买点参�?�复用现�? promotion_matrix + layer_forward 统计

### 文档

- **docs/13**: 回写至当前阶段（1822 tests / 阶段 8）（24f2805�?
- **docs/06**: P0/P1/P2 全部标记完成
- **README**: 测试计数 495�?1822
- **CHANGELOG**: 本段

---


### 同日另批：[Unreleased] �? 2026-10-01 (P2 批次)

### 新增

- **services/ref_dividend.py**: 分红除权�? `ref_dividend` + `backfill_dividend()` 服务，Wind 回填除权除息日（不复权体系下供分析参考）
- **services/ref_rs.py**: 相对强弱�? `ref_rs` + `calculate_rs()` 服务，计算个股相对基准指数（默认沪深300）的 20�?/60日强度排�?
- **tests/unit/test_ref_dividend.py**: ref_dividend 测试 13/13（mock WindClient + db�?
- **tests/unit/test_ref_rs.py**: ref_rs 测试 8/13（period_return + get_prices + get_top_rs�?

### 数据

- **schema.py**: 新增 2 张表 �? `ref_dividend`（分红除权历史）, `ref_rs`（相对强弱排名）；索�? 2 条；总表�? 27�?29

### 文档

- **docs/06**: P2 T13/T15 标记完成
- **docs/15**: 测试数量 1165�?1372，�?�表�? 27�?29

---

## 2026-09-30 · (cron 看门狗修�? + 文档口径同步)

### 修复

- **cron 看门狗重启分支失效（服务�? oracle�?2026-09-30 修）**：原 crontab �?
  `*/5 * * * * curl -sf -o /dev/null http://127.0.0.1:8098/ || systemctl restart emotion-core-dash`
  的重启分支在 cron（非交互）里必被 polkit 拒绝，原�?
  `Access denied as the requested operation requires interactive authentication`，即看门狗实际从未生效�??
  改为�?
  `*/5 * * * * curl -sf --max-time 8 -o /dev/null http://127.0.0.1:8098/ || sudo -n systemctl restart emotion-core-dash >> /home/ubuntu/logs/ec-dash-watchdog.log 2>&1`
  （旧 crontab 备份 `/tmp/crontab.backup.20260930`）�??
  **铁律：所�? systemd 重启�?�? `sudo -n systemctl restart <unit>`**（裸 `systemctl restart` �? SSH/cron 非交互场景必�? polkit 拒）�?
  e2e 验证（模�? cron 环境�? 8099 死端口）：重启分�? rc=0、`MainPID 1673194 �? 1674630`、服�? active、日�? 0 字节�?

### 测试

- 全量测试口径更新�? **1859 collected / 1857 passed + 2 skipped**�?2026-09-30 实测，HEAD 43d3da9）；
  修复前基线（HEAD c1909cd�?1839 collected / 1837 passed + 2 skipped�?

### 文档

- **全量文档遍历同步**：`README` / `CHANGELOG` / `docs/11` / `docs/13-交接文档-2026-09-26` / `requirements.txt`
  统一测试口径并标注历史（1822 / 926 �? 2026-09-26 时点，历史口径）；README 补历史信号回填运�? CLI
  与个股诊断三段式（A/B/C）接线说明；`docs/11` 标注 `diagnose_service.py` 已接�? `/api/stock`�?

---


### 同日另批：[Unreleased] �? 2026-09-30 (个股诊断三段式接�? / R2)

### 新增

- **services/diagnose_service.py**: 三段式个股诊断（docs/03 §4.2�?
  - A 段：当日身份（连�?/换手/梯队层级/唯一�?高板�?+ 五条件�?�项 `PASS/FAIL/UNKNOWN` + 通俗原因 + W1 同身位扎堆警�?
  - B 段：历史身份（首�? bar / 次新�?+ 历史唯一�?高板日期与其�? T+1/T+3/T+5 + 历史信号与结果（signal �? signal_outcome�?
  - C 段：晋级率双口径 + divergence + 题材完整性（theme_group.completeness�?+ 生�?�评级（market_stat.dragon_env�?+ 同状态赔�?
- **/api/stock**: 新增 `diagnose` 段（既有键名与层级不动）；昂贵统计由 stock_service �? TTL 缓存注入，不重复全表聚合
- **presentation/templates/stock.html + static/stock.js**: 个股页新�? A/B/C 三张卡片（`renderDiagnose`），无数据显示�?�暂无�??
- **services/replay_service.py**: 新增 `__main__` + argparse（`--start/--end/--dry-run`），历史信号回填终于有运维入�?
  （`PYTHONPATH=src .venv/bin/python -m emotion_core.services.replay_service --start 2024-01-01 --end 2026-09-30`），
  `run()` 的签名与行为�?字未改；`--dry-run` 只预览区间与交易日数，不写库
- **orchestration/daily.py**: `_run_step` 改为返回�? detail 并落 `pipeline_state.detail`—�??
  signal 步写 `signals=N code=�? action=�? buy_window=�? source=live`，ladder 步写 `候�??=N`，其余步留空

### 修复

- **R2 审计缺口�?**：`services/diagnose_service.py` 此前是零调用方的并行死实现，现接�? `/api/stock`�?
  五条件判定只�? `algorithms/entry.py`（本模块不重写规则），梯队只�? `algorithms/ladder.py` 落库�? ladder_day
- **降级纪律**：任�?段缺�?/缺列/异常只写 note（`applicable=false` + 「当日非候�?��?�，不编�? PASS/FAIL），诊断段不可能�? `/api/stock` 500
- **P1-B 仓库侧（`signal` �? live 行不可观测）**：`pipeline_state` �? latest-state UPSERT，`mark_done` �? detail
  默认空串 �? 「跑了但没信号�?�与「根本没跑�?�在库内无法区分，`status=OK` 因此会被误读成�?�有信号」�??
  daily 现在把每步的产物摘要写进 detail，`signal OK ''` 这类空证据不再出�?
  �?2026-09-29/30 实测重算�? `signals=0 buy_window=NONE`，即禁买日无候�?�，不是步骤没跑�?
- **过期注释**：`orchestration/report.py`、`orchestration/pool.py`、`tests/unit/test_review_entrypoints.py`
  的�?�daily 12 步�?�→ 13 步；`algorithms/dragon_env.py` 的�??1822 tests」→ 实测口径

### 测试

- **tests/unit/test_diagnose_service.py**: 5 �? 16 个用例（A/B/C 各段、UNKNOWN 不否决�?�entry 行序变化�?�? `entry.checklist`、DB 异常降级�?
- **tests/unit/test_stock_service.py**: 新增 diagnose 段接线用例；保持单测 DB-free（新�? `diagnose_service.query_df` 空表桩）
- **tests/unit/test_daily.py**: 新增 8 个用例覆�? detail 传�?��?�返�? None 的桩、signal/ladder detail 落库

### 文档

- **口径统一（外�? 2026-09-30 复核发现�?**: 测试数改用实测口径（修复前基�? 1839 collected / 1837 passed + 2 skipped�?
  修复�? 1859 collected / 1857 passed + 2 skipped，旧文里�? 1822 / 926 标为 2026-09-26 时点历史口径）；`docs/15` 五张「行数�?�表原列实为**字节�?**，全部用 `wc -l` 重算并在表头注明口径�?
  `docs/15` 的�??10 层依赖拓扑�?�标注为**设计层口径�?�非目录�?**（`models/`、`review/`、`trade/` 在仓内不存在�?
  真身分别�? `domain/`、`algorithms/review/`（入�? `orchestration/report.py`）�?�`presentation/trade_api.py`�?
- **docs/05、docs/13**: `signal` 「只�? 6 行�?��??172 个历史信号�?�标注为 2026-09-26 时点数字�?
  并补�? 2026-09-30 实测�?162 行全 `source='replay'`、`live=0`）；�?6 行�?�的真实出处�?
  `tests/oracle/fixtures/signal_live.json` 这个只有 6 条的 fixtures 骨架
- **docs/06**: 新增「重�? Rust 产物后必须对账�?�小节（`pytest tests/oracle` + `.so` 指纹
  1,592,696 B / md5 `44ebfc09f0a813cead5c1c2b7c076469`�?

---


### 同日另批：[Unreleased] �? 2026-09-30

### 新增

- **services/wind_client.py**: Wind MCP CLI 适配器，窄接口（通用调用 + 配额记账 + 原始响应留存），复用 wind-mcp-skill CLI�?8d659fe�?
- **services/ref_limit_rule.py**: 制度规则�? `ref_limit_rule` + 10 条种子数据（主板±10%/创业板�?20%/科创板�?20%/北交�?±30%/ST±5%），查询服务 `get_limit_pct(market, board, as_of)`�?8d659fe�?
- **services/ref_security_status.py**: ST 安全状�?�表 `ref_security_status` + `backfill_security_status()` 服务，对 is_st=true 股票逐只�? Wind `get_stock_events`�?8d659fe�?
- **services/wind_manifest.py**: `record_call()` 将每�? Wind 调用落库�? `ops_raw_manifest`（原�? JSON 留存�?+ `ops_quota_ledger`（配额消耗账本）�?8d659fe�?
- **tests/unit/test_wind_client.py**: WindClient 单测 13/13，覆盖成�?/配额/鉴权/参数/超时/空输�?/count 递增�?8d659fe�?
- **tests/unit/test_ref_limit_rule.py**: 制度规则种子与查询测�? 7/7�?8d659fe�?

### 数据

- **schema.py**: 新增 4 张表 �? `ref_limit_rule`, `ref_security_status`, `ops_raw_manifest`, `ops_quota_ledger`；索�? 4 条；总表�? 23�?27�?8d659fe�?

### 文档

- **docs/06**: P1 T6/T7/T9/T10 标记完成�?8d659fe�?
- **docs/15**: 测试数量 1147�?1165，�?�表�? 23�?27（当前变更）
- **CHANGELOG**: P1 批次新增条目（当前变更）

---

## 2026-09-29 · (历史)

### 修复

- **alerts**: `notify.push` 异常不再外抛，加 `try/except Exception` 兜底（be19aa6�?
- **daily**: psycopg3 �? `Connection.executemany()`，改�? `with conn.cursor() as cur: cur.executemany()`（e2d0bdf�?
- **presentation/server.py**: `_load_algorithm_data` 静默吞错�? `log.warning`，返回空 dict 时留日志痕迹（当前变更）
- **pytdx_provider**: 硬编�? TDX 服务�? IP 改为环境变量 `TDX_HOSTS` 配置，格�? `host:port,host:port`（当前变更）
- **pool**: 两个新单元补 `TimeoutStartSec`，防止东财接口挂死不�?（fc2ae24�?
- **monitor**: 五层监控同时失效导致故障静默 3 天，补测试覆盖（9515f45�?
- **report**: 接�?�复盘报告，修四个断点并建独�? CLI（c1c7bdb�?
- **pool**: 「空池被当成功�?�静默错误（a980ab0�?
- **data/loader.py**: `stat.bomb_rate`→`stat.bomb_count`、`stat.max_limit_days`→`stat.max_height`，修复字段名不匹配（P1-F�?
- **revision.py**: �? `_ALLOWED_TABLES` 白名单防 SQL 注入（P1-G�?

### 新增

- **tests/architecture/**: 架构方向违规扫描�?19 条反向依�? allowlist 登记，新增即红（f1fb4a8�?
- **tests/unit/test_ingest_fallback.py**: ingest 降级路径测试 17/17�?57a5645�?
- **tests/unit/test_alert_webhook.py**: alert webhook 推�?��?�道测试 8/8（be19aa6�?
- **tests/unit/test_daily.py**: orchestration/daily 核心路径测试 19/19�?78f0657�?
- **tests/unit/test_loader.py**: data/loader.py 测试 12/12（P1-F�?
- **tests/unit/test_revision.py**: revision.py 校验测试 13/13（P1-G�?
- **presentation**: 日历 B 方案 �? 460px 左侧 + 右侧�?近快照栏（b257321�?
- **api**: 只读端点 `/api/alerts`，供门户今日速览（dc27f0f�?

### 文档

- **docs/13**: 移除虚构符号 `MarketInputs`/`StateResult`/`_bridge`，改为实际类�? `DayMetrics`/`EmotionState`（当前变更）
- **docs/15**: 测试数量 754�?1038，文件数量同步修正（当前变更�?
- **docs/01**: 过时字段�? `CLIMAX_COUNT`→`CLIMAX_ZT`、`ICE_COUNT_MAX`→`ICE_ZT_MAX`（当前变更）
- **README**: 同步当前进度�? web 功能清单�?49a222c�?

---

## [0.1.0] �? 2026-09-27

### 新增

- 整体架构�?9 层（utils/domain/algorithms/data/services/orchestration/presentation/llm/核心）全量实�?
- Rust 核心：`core/src/state.rs` + PyO3 绑定
- 交易流水线：日终采集 �? 派生指标 �? 状�?�机 �? 阶梯 �? 入场 �? 复盘
- systemd 定时器：daily/pool/report/watchdog 四件�?
- Web 展示层：三层 dashboard（直�?/逻辑/算法�?+ 暗色主题
- LLM 层：策略解读 prompt 模板
- 数据回填：TDX/Sina/EastMoney 三源，akshare 同源备源
