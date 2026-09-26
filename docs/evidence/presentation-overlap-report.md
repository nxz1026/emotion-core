## 展示层

> 取证方法：全部结论来自实际读码（`read`/`grep`/`wc`）与线上部署配置（`/etc/nginx/sites-enabled/dsh-web`、`systemctl`、`ss -ltnp`）。
> 证据格式 `路径:行号`。无法确认的判断显式标注「未确认」。

### 1. 两边的展示层地图

| 侧 | 组成 | 规模 | 证据 |
|---|---|---|---|
| lkl | `dashboard/` 12 个 `.py` + 单文件 `index.html` + systemd unit + nginx 片段 | py 738 行 / html 328 行 / unit 16 行 / nginx 23 行 | `wc -l dashboard/*.py` = 738；`dashboard/index.html`(总 328 行)；`dashboard/lkl-dash.service`(16)；`dashboard/nginx-dash.conf.snippet`(23) |
| lkl | 报告/推送面 `lkl/services/review/{__init__.py,utils.py}` + `notify.py` + `alerts.py` | 771 + 284 + 55 + 79 = 1189 行 | `wc -l lkl/services/review/*.py lkl/services/notify.py lkl/services/alerts.py` |
| asel | `asel/presentation/` 21 个 `.py` + 3 个静态资产 | py 5338 行 / static 432 行 | `wc -l asel/presentation/*.py`=5338（= 视图 1860 + vm_* 2591 + render 480 + view_model 262 + view_loaders 145）；`static/{tokens.css 74, app.css 246, app.js 112}` |
| asel | 相邻支撑：`asel/snapshots/store.py` 97、`asel/orchestration/_serialize.py` 59、`asel/_payload.py` 23 | 179 行 | `wc -l` 实测 |

结构差异（一句话）：**lkl 是「后端 JSON API + 浏览器端 JS 渲染」；asel 是「服务端 Python 渲染 HTML + 只读快照」**，两侧不存在可逐函数对齐的同层模块。

### 2. Web 服务与路由对比

**服务实现**（都是纯标准库，均无第三方 web 依赖）：

| 项 | lkl | asel | 证据 |
|---|---|---|---|
| HTTP 栈 | `ThreadingHTTPServer` + `BaseHTTPRequestHandler` | `ThreadingHTTPServer` + `BaseHTTPRequestHandler` | `dashboard/app.py:10,26`；`asel/presentation/server.py:18,140,222` |
| 第三方 web 依赖 | 无（`grep flask/fastapi/django/tornado/aiohttp/bottle/starlette dashboard/` 空） | 无（同上，且 `asel/presentation/*.py` 无 `psycopg/sqlite3/asel.storage/asel.algorithms` 实际 import，仅文档字符串提及） | `grep -rn "flask\|fastapi..." dashboard/` 空输出；`grep -rn "psycopg\|sqlite3\|asel.storage" asel/presentation/*.py` 只命中 `__init__.py:9-10`、`server.py:4-5`、`views.py:3` 的注释 |
| 监听地址/端口 | `127.0.0.1:8098`（`DASH_PORT` 可覆盖） | `127.0.0.1:8899`（`--port` 默认） | `dashboard/app.py:20-21`；`server.py:37-38` |
| 路由方式 | `do_GET` 里 `if/elif` 字符串比较 + `parse_qs` | 纯函数 `route_get()` 返回 `(status, ctype, body)`，可直测；`base_path` 前缀剥离 | `dashboard/handler.py:21-74`；`server.py:77-137` |
| HTTP 方法 | 只实现 `do_GET`（无 `do_POST` → 继承默认 501） | `do_GET`/`do_HEAD` 放行，`POST/PUT/DELETE` 显式 405 + `Allow: GET, HEAD` | `handler.py:21`；`server.py:148-173` |
| basic auth | **应用层无认证**，认证在 nginx | **应用层无认证** | `handler.py` 全文无 auth；`server.py` 全文无 auth |
| nginx 反代路径 | 仓库片段：`/dash/` → 8098 + `auth_basic "Docs Download"` + `limit_except GET` | `--base-path /stock`，链接/静态资源全部带前缀 | `nginx-dash.conf.snippet:9-22`；`server.py:51-58,86-92,274-275` |
| 实际线上挂载 | `/stock-legacy/` → `127.0.0.1:8098/`（**无 auth_basic**） | `/stock/` → `127.0.0.1:8899`（**无 auth_basic**，无尾斜杠，与 `--base-path /stock` 一致） | `dsh-web:54-61`、`dsh-web:63-65`；`ss -ltnp` 实测 8098/8899 均在听 |
| 静态资源 | 无独立静态目录（HTML/CSS/JS 全内联） | 白名单 `STATIC_FILES`（tokens.css/app.css/app.js），URL 不参与路径拼接 | `handler.py:10` 整份 index.html 读入内存；`server.py:41-45,131-132` |
| 错误兜底 | 未知路由 404 纯文本；JSON 损坏返回 None | 视图异常 → 500 页面 + stderr，不落 traceback；未知路由 → 404 HTML | `handler.py:73-74`、`loader.py:46-47`；`server.py:185-190,133-137` |

**路由清单逐条对比**（lkl 16 条，asel 11 条）：

| 路由/API | lkl | asel | 判定 |
|---|---|---|---|
| `/` | 首页（`index.html` 整份返回，`handler.py:24-25`） | 今日概览（`server.py:99-101` → `core_views.py:40-64`） | 同名不同构，**不可原样合并**（渲染范式不同） |
| `/strategy` | 策略观察台页（`handler.py:26-27` → `strategyview.py:168-206`） | 无 | **仅 lkl** |
| `/api/strategy` | 策略快照 JSON（`handler.py:28-29`） | 无 | **仅 lkl** |
| `/api/dates` | 报告日期列表（`handler.py:30-31`） | 无（asel 无日期选择器，页面只反映最新快照） | **仅 lkl** |
| `/api/latest` | 最新日期（`handler.py:32-33`） | 无 | **仅 lkl** |
| `/api/report` | 日报 JSON（`handler.py:34-36`） | 无 | **仅 lkl** |
| `/api/download` | 日报 md 下载（`handler.py:37-43`） | 无 | **仅 lkl** |
| `/api/trade-files` | 交易交换文件列表（`handler.py:44-45`） | 无 | **仅 lkl** |
| `/api/trade-download` | 交换文件下载（`handler.py:46-51`） | 无 | **仅 lkl** |
| `/api/live` | 实时持仓/卖出建议（`handler.py:52-53`） | 无 | **仅 lkl** |
| `/api/trade-status` | 午间流程状态（`handler.py:54-55`） | 无 | **仅 lkl** |
| `/api/trade-board` | 七态交易看板（`handler.py:56-58`） | 无 | **仅 lkl** |
| `/api/signal-detail` | 信号证据卡（`handler.py:59-61`） | 无 | **仅 lkl** |
| `/api/evidence` | 题材+行情钻取（`handler.py:62-65`） | 无 | **仅 lkl** |
| `/api/alerts` | 未确认告警（`handler.py:66-68`） | 无 | **仅 lkl** |
| `/api/signal-calendar` | 信号日历（`handler.py:69-72`） | 无 | **仅 lkl** |
| `/series` | 无 | 时间序列（`server.py:99-101` → `core_views.py:67-117`） | **仅 asel** |
| `/health` | 无 | 数据质量（`server.py:99-101` → `core_views.py:137-174`） | **仅 asel**（lkl 的质量信息只存在于日报 md 第⑩段与 JSON `quality` 键，无独立页） |
| `/ladder` | 无 | 连板梯队（`server.py:103-113` → `component_views.py:52-100`） | **仅 asel**（lkl 的等价物是首页表格，见 §3） |
| `/pool` | 无 | 涨停池/触板池（`server.py:103-113` → `component_views.py:103-143`） | **仅 asel** |
| `/board` | 无 | 板块摘要（`server.py:103-113` → `component_views.py:191-258`） | **仅 asel** |
| `/promotion` | 无 | 晋级率与炸板（`server.py:103-113` → `component_views.py:306-390`） | **仅 asel** |
| `/emotion` | 无 | 市场情绪（`server.py:103-113` → `component_views.py:461-…`） | **仅 asel** |
| `/research` (+`?format=json\|csv`、`?backtest=`) | 无 | 研究只读页 + 导出/回测导出（`server.py:115-116`；`route_handlers.py:67-92`） | **仅 asel** |
| `/stock/<6位code>` | 无（只有 `/api/evidence` 后端钻取，无页面、前端也未调用） | 逐票复盘页（`server.py:61-74,122-130` → `stock_views.py:34-…`） | **仅 asel** |
| `/static/{tokens.css,app.css,app.js}` | 无 | 白名单静态资源（`server.py:131-132`；`route_handlers.py:42-64`） | **仅 asel** |

**孤儿路由（有后端、无前端消费者）——lkl 独有**：`grep "api/" dashboard/index.html` 只命中 `api/report`(225)、`api/live`(226)、`api/trade-files`(249)、`api/trade-status`(250)、`api/trade-download`(258)、`api/signal-calendar`(272)、`api/dates`(309)、`api/latest`(312)、`api/download`(315)；`strategyview.py` 只命中 `api/strategy`(131)。故 `/api/trade-board`、`/api/signal-detail`、`/api/evidence`、`/api/alerts` **四个端点无任何前端调用**，且 `evidence.theme_evidence`(`dashboard/evidence.py:14`)、`signaldetail.elimination`(`dashboard/signaldetail.py:40`) 连路由都没有（全仓 grep 仅定义处）。这是 lkl 侧可直接裁掉的展示层代码。

### 3. 面板级映射表

lkl 面板全部由 `dashboard/index.html` 的 JS 函数渲染；asel 面板由 Python 视图渲染。

| 面板 | lkl | asel | 判定 |
|---|---|---|---|
| 顶部指标卡组（涨停/跌停/炸板率/昨涨停表现/名义高度/可交易高度/情绪周期/环境） | `index.html:91-107`（8 张卡） | `/` 今日概览：`core_views.py:44-50`（今日数据卡）+ `:51-53` metric-grid（`view_model.py:82-86` 只声明涨停家数/触板家数/一字家数） | **重复但不等价**：同一指标域，lkl 8 指标 vs asel 3 可靠指标（asel 明确把 `max_height`/`promotion_rate` 列为未实现，`view_model.py:89-95`） |
| ① 情绪面板（买点窗口 + 原因） | `index.html:189-196` | `/emotion` 情绪概览 + 指标卡 + 上游输入卡（`component_views.py:461-…`，指标序 `view_model.py:210-218`） | **重复（asel 更完整）**：lkl 只有窗口/原因两行，asel 有 state/trend/阈值版本/三态计数/上游来源表 |
| ② 梯队全景（板数/代码/名称/换手/标注） | `index.html:157-163` | `/ladder` 梯队概览 + 按板数分层卡 + 「连续性不可得/质量异常」区（`component_views.py:52-100`） | **重复（asel 更细）**：asel 多了 quality/unknown 分区与逐票链接 |
| ③ 题材结构（题材/最高板/领涨/梯队/状态/成员） | `index.html:165-171` | `/board` 板块摘要（板块概览 + 板块排序表，`component_views.py:191-258`） | **部分重叠**：lkl 是概念题材聚合（`theme_group`/`theme_tag`，`dashboard/evidence.py:16-19`），asel 是 main/star/gem 板块口径（`vm_board.py`）；**asel 无题材聚类面板 → 题材结构实质仅 lkl** |
| ④ 淘汰赛况（昨日板数→今日结果） | `index.html:173-179` | `/promotion` 晋级明细（前一日状态→今日状态→类别，`component_views.py:444-458`） | **重复**：同一语义（昨日最高板组今日结局），asel 带 quality/类别枚举 |
| ⑤ 晋级矩阵（层/基数/名义/换手/晋级率/失败均幅） | `index.html:181-187` | `/promotion` 晋级概览 + 晋级率区间卡 + 分母与计数 + 炸板/断板明细 + 触板不可证 + 质量缺口（`component_views.py:306-390`、`393-421`） | **重复（asel 更完整）**：asel 有上下界与分母分解（`view_model.py:134-157`），lkl 是单层矩阵 |
| ⑥ 明日参考（`no_signal_reason`） | `index.html:198-201` | 无 | **仅 lkl** |
| ⑦ 龙空龙环境（goods/bads 条件标签，三态） | `index.html:109-120` | 无（asel 情绪 state/trend 不是同一套生态评级） | **仅 lkl** |
| 持仓/信号（实时 sells + positions） | `index.html:203-218`（数据源 `/api/live`，`:225-226`） | 无 | **仅 lkl** |
| 交易交换文件 + 午间流程徽章 | `index.html:245-267` | 无 | **仅 lkl** |
| 信号日历（近 3 月命中率） | `index.html:269-306` | 无 | **仅 lkl** |
| 策略观察台整页 | `strategyview.py:168-206`（+`70-165` 内联 JS） | 无 | **仅 lkl** |
| 时间序列/趋势 | 无（日报只有 `stat`/`prev_stat` 两点对比，`index.html:94,101`） | `/series` 窗口概览 + 内联 SVG 折线 + 5/10/20 窗口卡 + 多指标 + 逐点 + 断点（`core_views.py:67-117`；`render.py:273-307` sparkline） | **仅 asel** |
| 数据质量页 | 无独立页（信息在日报 md 第⑩段 `review/__init__.py:689-708` 与 JSON `quality` 键） | `/health`（`core_views.py:137-174`，工程字段 run_id/git_sha/nulls/excluded 只在此出现） | **仅 asel**（形态不同：md 段 vs 页面） |
| 逐票复盘页 | 无（`/api/evidence` 是后端钻取，无页面） | `/stock/<code>` 头部/事实/连板/池/板块/晋级/来源 7 卡（`stock_views.py:34-…`） | **仅 asel** |
| 涨停池/触板池明细 | 无（首页只有涨停家数卡 `index.html:98`） | `/pool` 涨停池 + 触板池两卡（`component_views.py:103-143`） | **仅 asel** |
| 研究页 | 无 | `/research`（`research_views.py:55-…`，含 json/csv 导出与只读回测 `route_handlers.py:67-92`） | **仅 asel** |
| 主题切换按钮 | 无（只有 `prefers-color-scheme` 媒体查询，`index.html:20-26`） | `app.js:4-45` + 首帧引导 `render.py:32-38` + `render.py:89-90` | **仅 asel** |

**跨站互链（已存在双向引用，可直接用于合并导航）**：lkl 首页链到 asel —— `index.html:58` `href="/stock/"`；`strategyview.py:202` `href="/stock/"`。asel 链回 lkl —— `render.py:88` `href="https://140.83.62.161/stock-legacy/"` 文案「旧版股票复盘 →」。

### 4. 数据获取方式（直连DB vs 快照）—— 合并关键冲突

**lkl = 混合模式：日报页走文件快照，其余全直连 DB/业务服务。**

- 文件快照路径：`dashboard/loader.py:6` `REPORTS = <repo>/reports`；`:9-14` 扫 `YYYY-MM-DD.json`；`:38-47` 读日报 JSON。`/api/report`、`/api/dates`、`/api/latest`、`/api/download` 均只读文件（`handler.py:30-43`）。实测 `reports/` 有 33 份日报 JSON + 11 份 `strategy_*.json`。
- 直连 DB 的模块（6 个）：`evidence.py:11`（`from lkl.utils import db`）、`live.py:5`（`:10-15` 查 `position` 表）、`signal_calendar.py:6`（`:15-25` join `signal`×`signal_outcome`）、`signaldetail.py:11`（`:16-19` 查 `signal.checklist`）、`tradeboard.py:12`（`:17-19,32-37,47-50` 查 `signal`/`position`/`trade_event`/`pipeline_state`）、`strategyview.py:10`（`:59` 查 `stock_basic`）。
- import 业务服务的模块：`live.py:4` `lkl.services.exit.suggestions`；`signaldetail.py:42` `lkl.services.ladder.y_survivors`；`handler.py:67` `lkl.services.alerts.pending`。
- 文件/配置类：`tradefiles.py:9-12`（`config.TRADE_DIR` + `lkl.trade.naming.USERS`）、`tradestatus.py:11-17`（读 `trade/trade_status.json`）。
- 直连带来的耦合：`dashboard/*.py` 依赖 `lkl.utils.db`/`lkl.services.*`/`lkl.config`，即**展示层与业务层同进程、同库**；`handler.py` 甚至在做 SQL 结果到 JSON 的即时转换（无中间契约）。

**asel = 纯快照文件，零 DB。**

- `asel/presentation/view_loaders.py:27-46` 读三份共享快照，`:62-84` 逐组件快照，`:124-145` 逐票 5 份，`:87-111` 研究 8 份；全部 `Path.read_text` + `json.loads`，缺失/损坏进 errors 列表不抛。
- 快照文件名常量集中在 `view_model.py:69-76`（与 `vm_common` 同值但身份不同，`:66-68` 明确注释「请勿合并」，否则破坏 facade 兼容测试）。
- `server.py:1-11` 与 `__init__.py:8-10` 把「不 import storage/algorithms、不连库、不联网」写成模块纪律；grep 验证无实际 DB import（仅注释命中）。
- 写侧唯一入口是 `asel/snapshots/store.py`（`:49-72` 原子写、`:44-46` 规范化编码、`:88-96` manifest），落盘目录实测 `snapshots/` 有 8 份 `*_daily.json` + `health.json` + `market_fact_series.json`。

**冲突点（合并必须裁决）**：asel 的展示层之所以能只读快照，是因为有 L4 编排层负责产出；lkl 没有这层快照，它的 `reports/*.json` 是**日报副产品**而非全量事实快照（只有 `lkl/daily@2` 一个 schema，见 §5）。合并时若统一到 asel 快照模式，lkl 的 `/api/live`（实时持仓/卖出建议）、`/api/trade-status`、`/api/trade-files`、`/api/signal-calendar` 这四类**本质实时/文件态**的能力无快照可读，要么新增快照产出，要么保留直连；若统一到 lkl 直连模式，则 asel 的「只读、可离线复现、可 golden 对拍」纪律被破坏（asel 侧有 12 个 dashboard 测试文件依赖此纪律：`tests/test_dashboard*.py`、`test_view_model.py`）。

### 5. 视图模型与序列化契约对比

| 维度 | lkl | asel | 证据 |
|---|---|---|---|
| 独立 VM 层 | **无** | **有，10 个 `vm_*.py` 共 2591 行**：vm_models 389（25 个 `@dataclass`）、vm_research 845（9 个）、vm_stock 423（7 个）、vm_core 256、vm_emotion 186、vm_promotion 146、vm_ladder 136、vm_common 98、vm_board 57、vm_pool 55 | `wc -l asel/presentation/vm_*.py`；`grep -c "@dataclass" vm_models.py`=25、`vm_research.py`=9、`vm_stock.py`=7 |
| VM 层性质 | — | 纯函数，只选择/格式化，不求和、不补 0；缺失整块隐藏 | `view_model.py:1-9`（职责边界声明）、`:225-250`（格式化函数）、`vm_common.py:2-6` 只依赖 `typing` 与 `.._payload` |
| lkl 的等价物 | ①后端：`review.collect()` 一次取齐 → dict（`lkl/services/review/__init__.py:151-182`）；②前端：`index.html` 的 `renderCards/renderLadder/renderThemes/renderElimination/renderPromotion/renderEmotion/renderNoSignal/renderEnv/renderSellsPositions`（`index.html:91-218`）+ `strategyview.py:97-138` 的 `renderRows` | 全部在 Python：`build_overview/build_series/build_ladder/...` 在 `view_model.py:260-262` 末尾 facade 再导出 | 见左列行号 |
| lkl 的 `loader.py` 角色 | **文件 IO + 日期校验**（扫 `reports/*.json`、ISO 正则防路径注入 `:25-35`、损坏 JSON → None `:46-47`）——不是视图模型 | 对应物是 `view_loaders.py`（快照 IO + errors 列表） | `loader.py:9-59`；`view_loaders.py:27-46` |
| lkl 的 `evidence.py` 角色 | **DB 钻取数据层**（`theme_group`/`daily_bar`/`derived_bar` 原样返回，`_plain()` 做 numpy/Decimal → JSON 原生类型 `:52-60`） | 对应物是 vm_* 的字段选择 + `render.py` 的 `cell/esc` | `evidence.py:14-49`；`render.py:40-51` |
| JSON 契约 | **有，但是日报副产品**：`schema = "lkl/daily@2"`，`render_json` 在 `review/__init__.py:710-713`（`json.dumps(..., indent=1, default=_jsonable)`，`_jsonable` 在 `review/utils.py:265`）；落盘 `_atomic_write`（`review/utils.py:281`）；实测键 23 个：`schema,date,stat,prev_stat,ladder_rows,seal_map,sole,elimination,window,signal,secondary_signal,sells,positions,hot_rank,quality,caliber,themes,theme_tags,promotion,dragon_env,orphan_adoptions,no_signal_reason,usability` | **有，且是显式契约**：`snapshots/store.py:44-46` canonical JSON（`sort_keys=True`、`ensure_ascii=False`、`Decimal→str`、`date/datetime→ISO`、`Path→str`）+ `:80-85` sha256 + `:88-96` manifest | 见左列 |
| 编码纪律 | `_jsonable`（`utils.py:265-280`） | `store.encode()` 明确「Decimal→str 不失真、null 是未知禁止转 0」`store.py:8-10,33-41` | 见左列 |
| HTTP 层序列化 | `httpbase.send_json` 用 `json.dumps(obj, ensure_ascii=False, default=str)`（`:21-24`）——`default=str` 会把 `Decimal`/`date` 直接字符串化，与 store 的规范化编码不是同一套规则 | 无 JSON API，页面直接是 HTML 字符串 | `dashboard/httpbase.py:7-37` |
| 跨层 payload 助手 | 无（各模块各写 `_plain`/`_n`：`evidence.py:52`、`signal_calendar.py:61`、`review/utils.py:265`） | `asel/_payload.py:14-23`（`mapping`/`field_of`），**被展示层 import**：`vm_common.py:6` `from .._payload import mapping as _mapping` | 见左列 |
| 编排侧序列化 | 无独立模块 | `asel/orchestration/_serialize.py:22-58`（`canonical`/`json_key`/`scope_boards`/`row_first`，59 行，供 7 个 pipeline 复用；`:9-12` 明确说明「故意不收 `_run_id`」） | `_serialize.py` 全文 |

结论：**asel 的 vm 层不是对「lkl 直连 DB」的重构，而是对「lkl 日报 JSON + 浏览器 JS 渲染」这一组合的 Python 化重构**——它把 lkl 分散在 `review.collect()`（取数）与 `index.html` JS（选字段/格式化/渲染）里的逻辑，切成「vm_*（选字段+格式化）+ render.py（HTML 原语）+ *_views.py（页面组装）」三段，并把取数换成读快照。两侧不存在可逐行对齐的 VM 模块，**不存在可原样搬移的 vm 代码**。

### 6. 报告与推送能力对比

| 能力 | lkl | asel | 证据 |
|---|---|---|---|
| Markdown 日报 | **有**，⓪+十段+⑫，段级容错 + 必需段失败拒绝发布 | **无** | `review/__init__.py:224-708`（各 `_sec_*`）、`:714-748`（`render_markdown`，`_REQUIRED`/`_SECTIONS`）、`:750-771`（`publish` 四出口：终端 + md + json + DB `review_report` upsert） |
| 报告 JSON 快照 | **有**（`lkl/daily@2`，见 §5） | 无（但有全量事实快照） | `review/__init__.py:710-713`、`:765-766` |
| 渲染工具函数 | `review/utils.py` 284 行（`_pct/_num/_pctv/_delta_of/_fmt_stat/_ladder_tag/_seal_notes/_fmt_caliber/_usability/_counter_items/_strip_position_block/_jsonable/_atomic_write`，共 13 个） | `render.py` 480 行（HTML 原语，面向页面而非报告） | `grep -n "^def " review/utils.py`；`grep -n "^def " render.py` |
| webhook 推送 | **有**：`notify.push`（企业微信/钉钉/generic 三通道自动识别，摘要只送结论并**正则剥离持仓**） | **无**（`grep -rn "webhook\|requests\.post\|dingtalk\|qyapi\|def push" asel/` 空输出） | `notify.py:16-19,22-28,31-35,38-56`；`_POS_RE` 在 `:13` |
| 告警闭环 | **有**：分级入库 + `(source, detail)` 幂等去重 + ack + 未确认 webhook 推送 | **无** | `alerts.py:15-79`（`record:18`、`record_dedup:29-45`、`pending:48-55`、`ack:58-66`、`push_pending_webhook:69-79`） |
| `reports/` 目录性质 | **是产物目录且是展示层数据源**（33 份 JSON + 11 份 strategy JSON，`loader.py` 直接读） | **是文档产物目录，不是代码、也不被展示层读**（实测 18 个 `.md` + `history/`；`grep -rn "markdown\|\.md\"" asel/ --include=*.py` 在展示层零命中） | `loader.py:6`；`ls reports/`；grep 结果 |
| 推送依赖声明 | **未声明**：`notify.py:49` 函数内 `import requests`，但 `pyproject.toml:6-13` 依赖只有 akshare/pandas/pyarrow/psycopg/httpx/pytdx —— 无 `requests` | — | 见左列 |

**确认：asel 的 `reports/` 是产物/文档，不是代码；asel 展示层没有报告渲染与推送的任何等价物。** 该能力整体属于 **仅 lkl**。

### 7. 部署与端口冲突检查

| 项 | lkl | asel | 判定 |
|---|---|---|---|
| systemd unit | 仓库内 `dashboard/lkl-dash.service`（`:11` `ExecStart=.venv/bin/python dashboard/app.py`）；线上 `/etc/systemd/system/lkl-dash.service` 与仓库文件**逐字一致** | 仓库**没有** unit 文件（只有 `scripts/systemd/asel-production-daily.service`，是快照调度 oneshot，不是 dashboard）；线上 `/etc/systemd/system/asel-dash.service` 是手工部署，README 只描述 | 证据：`cat /etc/systemd/system/{lkl,asel}-dash.service`；`find . -name "*.service"` 仅 `asel-production-daily.service` |
| 端口 | `127.0.0.1:8098`（`app.py:21`） | `127.0.0.1:8899`（`server.py:38`） | **不冲突**，且 `ss -ltnp` 实测两个端口同时 LISTEN（8098 pid 447182、8899 pid 583032） |
| nginx 路径 | `/stock-legacy/` → `127.0.0.1:8098/`（`dsh-web:54-61`）；仓库片段写的是 `/dash/`（`nginx-dash.conf.snippet:9,14`）—— **片段已过时** | `/stock/` → `127.0.0.1:8899`（`dsh-web:63-65`），无尾斜杠，与 `--base-path /stock` 匹配 | **路径不冲突**；`dsh-web:30` 的注释「龙空龙…/stock/ 入口」是过期注释（真实路由以 `:54-55`、`:63-64` 为准） |
| 认证方式 | 应用层无认证；仓库片段带 `auth_basic "Docs Download"` + `.htpasswd-dl`（`nginx-dash.conf.snippet:10-11`）；**线上 `/stock-legacy/` 块无 `auth_basic`**（`dsh-web:54-61`） | 应用层无认证；线上 `/stock/` 块无 `auth_basic`（`dsh-web:63-65`） | **两侧当前均无认证**，是合并前后都需处理的安全项 |
| 写方法防护 | 片段有 `limit_except GET { deny all; }`（`nginx-dash.conf.snippet:22`）；**线上块无此限制**；应用层只实现 `do_GET`（POST → 默认 501） | 应用层 `POST/PUT/DELETE` → 405 + `Allow`（`server.py:154-173`）；线上无 nginx 侧限制 | asel 更严；合并时 lkl 需补 405 |
| 跨仓耦合 | 无（自带 `.venv`） | **线上 unit 用 lkl 的 venv**：`asel-dash.service:9` `ExecStart=/home/ubuntu/DSH/longkonglong/.venv/bin/python -m asel.presentation.server ...`；README 同述 | 已在部署层耦合；合并后应统一 venv/解释器 |
| 服务状态 | `lkl-dash.service` active running | `asel-dash.service` active running | `systemctl list-units` 实测 |

**结论：端口/路径无冲突，两个服务可并行存在；冲突集中在「认证缺失（两侧）、lkl 无 405 防护、asel 借用 lkl venv、nginx 注释与片段过时」这四项运维债。**

### 8. 合并判定清单

| 模块 | 判定 | 理由（证据） | 风险 |
|---|---|---|---|
| `asel/presentation/` 全套（server/views/render/vm_*/route_handlers/view_loaders/view_model/static） | **保留为展示层主干（择一）** | 服务端渲染 + 只读快照 + base_path + 405 + 静态白名单 + 逃逸/空值纪律（`render.py:1-8`、`server.py:41-45,154-173`）；且被 12 个测试文件锁定 | 迁移期需同时维持 lkl 现有 URL 可用，避免 `/stock-legacy/` 断链 |
| `dashboard/httpbase.py` | **可删（真重复）** | 与 `server.py:194-214` `_send` 职责相同；lkl 版用 `default=str` 序列化，弱于 store 的规范化编码（`httpbase.py:21-24`） | 无（仅 lkl 内部使用） |
| `dashboard/loader.py` | **合并（概念对应 `view_loaders.py`）** | 同为「文件 IO + 非法输入 → None/errors」；lkl 版多一条 ISO 正则防路径注入（`loader.py:25-35`），可反向补进 asel | 低；`REPORTS` 目录语义与 asel `snapshots/` 不同 |
| `dashboard/evidence.py` `raw_bars` | **择一/重写为快照视图** | 直连 `daily_bar`/`derived_bar`（`evidence.py:33-49`），asel 无等价端点；但其前端消费者不存在（§2 孤儿路由） | 若保留需新增快照或保留直连，破坏 asel 只读纪律 |
| `dashboard/evidence.py` `theme_evidence` | **可删（死代码）** | 全仓 grep 只有定义（`evidence.py:14`），无路由、无前端 | 无 |
| `dashboard/signaldetail.py` `signal_detail` | **仅 lkl，需保留（重写为快照视图）** | 五条件三态证据卡（`:14-32`、`_three_state:35-37`）语义为 lkl 独有；但端点 `/api/signal-detail` 无前端消费者 | 无前端 → 保留价值存疑，先确认产品是否还需要 |
| `dashboard/signaldetail.py` `elimination` | **可删（死代码）** | 只有定义（`:40-48`），无路由、无前端；语义已由 asel `/promotion` 晋级明细覆盖（`component_views.py:444-458`） | 无 |
| `dashboard/tradeboard.py` + `tradestatus.py` + `tradefiles.py` | **仅 lkl，保留（隔离为可选模块）** | 多用户交易交换桥 + 七态看板 + 午间流程态（`tradeboard.py:57-61`、`tradestatus.py:15-24`、`tradefiles.py:25-45`）；asel 零等价物（§3 面板表） | 与 asel 只读/无 DB 纪律冲突；`tradefiles.py:37-45` 的路径穿越防护须保留 |
| `dashboard/live.py` | **仅 lkl，保留（隔离）** | 实时持仓/卖出建议直连 DB + 调 `lkl.services.exit`（`live.py:4-27`）；日报快照里的 `sells/positions` 是死数据，lkl 自己也标注「非日报快照死数据」（`live.py:1`、`index.html:204`） | 唯一破坏「展示层不连库」的 lkl 模块；合并后建议降级为 lkl 域内私有 API |
| `dashboard/signal_calendar.py` | **仅 lkl，保留（隔离）** | 近 3 月命中率日历，join `signal`×`signal_outcome`（`:15-25`），asel 无 | 同上（直连 DB） |
| `dashboard/strategyview.py` | **仅 lkl，保留（隔离）** | 独立策略快照观察台 + 自包含 HTML 页（`:70-206`），asel 无策略概念 | 内含第二份重复 CSS/JS（见下条），应改为复用 asel 静态资产 |
| `dashboard/index.html` | **择一：面板内容迁入 asel 视图，文件废弃** | lkl 首页 9 个渲染函数（`:91-218`）中，②③④⑤①与 asel `/ladder`、`/board`、`/promotion`、`/emotion` 重叠（§3），⑥⑦/持仓/交换文件/日历为 lkl 独有；asel 已有服务端渲染范式 | 需逐面板确认独有面板（⑥明日参考、⑦龙空龙环境、持仓/信号、交换文件、信号日历）的归属，否则丢功能 |
| lkl CSS 调色板（`index.html:9-26`）与 `strategyview.py:177-194` | **真重复，可删两份** | 两份均为同一 GitHub 调色板，且与 `asel/presentation/static/tokens.css:19-35`（亮）`:37-…`（暗）同值（`--bg #ffffff/#0d1117`、`--fg #1f2328/#e6edf3`、`--border #d0d7de/#30363d`、`--link #0969da/#2f81f7`、`--up #cf222e`、`--down #1a7f37`） | 低；注意 lkl 用 `:root`+媒体查询、asel 用 `[data-theme]`，合并需统一为 asel 的 data-theme 方案 |
| lkl 前端表格排序 JS（`index.html:122-155`） | **真重复，可删** | asel 已有 `app.js:57-…` `sortDashboardTable` + `:47-55` `filterDashboardTable`（服务端渲染 + 静态 JS） | 低 |
| `lkl/services/review/`（771+284） | **仅 lkl，保留（这是 lkl 的核心交付，不在展示层合并范围）** | md+json+DB 四出口、段级容错、必需段拒绝发布（`review/__init__.py:714-771`）；asel 无报告渲染 | 它与展示层的接口是 `reports/*.json`（`loader.py:6`）——合并后若 asel 视图改读快照，需决定日报 JSON 是否继续作为数据源 |
| `lkl/services/notify.py` | **仅 lkl，保留** | 三通道 webhook + 持仓脱敏（`notify.py:13,16-19,31-35`）；asel 无 | 依赖 `requests` 未进 `pyproject.toml:6-13`；合并时应补声明或改用已有 `httpx` |
| `lkl/services/alerts.py` | **仅 lkl，保留** | 告警分级/幂等/ack/webhook（`alerts.py:29-79`）；asel 无；`/api/alerts` 端点无前端消费者 | 端点无 UI；合并时需决定补前端还是留 API |
| `asel/snapshots/store.py` | **保留（写侧唯一入口）** | 原子写 + 规范化编码 + manifest sha256（`:33-96`） | 低 |
| `asel/orchestration/_serialize.py` | **保留（编排侧，非展示层）** | 7 个 pipeline 共用的规范化工具（`:1-12,22-58`） | 低 |
| `asel/_payload.py` | **保留，但需正视跨层 import** | 包根助手，被 `vm_common.py:6` 引用；`_payload.py:3-5` 自述「presentation 不能 import algorithms 之外也没有共同低层模块可放」 | 低；合并后建议提升为公共 `common/` 层 |
| `asel/presentation/static/{tokens.css,app.css,app.js}` | **保留为唯一前端资产** | 主题切换（`app.js:4-45`）、表格排序/过滤（`:47-55,57-…`）、token 化配色（`tokens.css:1-2`） | 低 |
| 孤儿端点 `/api/trade-board`、`/api/signal-detail`、`/api/evidence`、`/api/alerts` | **待裁决：补前端或删端点** | 前端零调用（`grep "api/" index.html`、`strategyview.py:131`） | 删前需确认无外部/脚本消费者（**未确认**：未检查仓库外是否有调用方） |
| nginx 配置（`dsh-web` + 过时段落） | **合并运维项** | `/stock/`→8899、`/stock-legacy/`→8098 并存（`:54-65`）；两侧均无 `auth_basic`；`dsh-web:30` 注释过期；`nginx-dash.conf.snippet` 的 `/dash/`+auth 与线上不符 | 合并后需重排前缀（保留旧 URL 301）并补认证 |
| systemd `lkl-dash.service` / `asel-dash.service` | **合并为单 unit（择一端口）** | 两 unit 结构相同（`User=ubuntu`、`Restart=always`），asel 复用 lkl venv（`asel-dash.service:9`） | 合并会中断 `/stock-legacy/`；建议先并存、双跑验证后再 301 |
