# dashboard 归属核实报告

日期：2026-09-17 · 核实人：密米尔 · 结论：**假设部分成立、部分不成立**

---

## 一、结论

「dashboard」在本机是**三套独立应用**，名字撞车：

| 公网路由 | 端口 | 进程工作目录 → 入口 | 归属 repo | 实跑 |
|---|---|---|---|---|
| `/stock/` | 8098 | `/home/ubuntu/DSH/longkonglong` → `.venv/bin/python dashboard/app.py` | **Stock_Anlysis**（即本仓库 `dashboard/`） | 200，标题「龙空龙复盘」；`/strategy` 200 |
| `/dashboard/` | 8099 | `/home/ubuntu/apps/portal` → `/usr/bin/python3 app.py` | **github.com/nxz1026/dashboard**（门户） | 200 |
| `/dashboard/jc/` | 8077 | `/home/ubuntu/league-v2/repo` → `uvicorn web.api:app` | **league-v2**（竞彩看板） | 302（跳登录页，正常） |

**关键纠正**：`longkonglong/dashboard` 服务的是 **`/stock/`**，不是 `/dashboard/`。
GitHub repo `nxz1026/dashboard` 确实对应 `/dashboard/`（门户），但**线上部署的形态与 repo HEAD 已分叉**（详见 §2.3）。

---

## 二、证据

### 2.1 Nginx 路由（`sudo nginx -T` 合并后配置）

```
location /dashboard/     { proxy_pass http://127.0.0.1:8099/; }
location /dashboard/jc/  { proxy_pass http://127.0.0.1:8077/static/dashboard.html; }
location /stock/         { proxy_pass http://127.0.0.1:8098/; }
```

公网 URL 匿名访问返回 **401 的来源**：server 段 `auth_basic "Restricted"` + `auth_basic_user_file /etc/nginx/.htpasswd`，是鉴权不是故障。

### 2.2 端口 → 进程 → 工作目录（`sudo ss -tlnp` + `/proc/<pid>/cwd`）

- **8098** pid 389505，cwd `/home/ubuntu/DSH/longkonglong`，cmdline `.venv/bin/python /home/ubuntu/DSH/longkonglong/dashboard/app.py`
- **8099** pid 390346，cwd `/home/ubuntu/apps/portal`，cmdline `/usr/bin/python3 app.py`
- **8077** pid 389232，cwd `/home/ubuntu/league-v2/repo`，cmdline `.venvs/league/bin/python -m uvicorn web.api:app --host 127.0.0.1 --port 8077`

### 2.3 GitHub `nxz1026/dashboard` ↔ 线上 portal 对比

| 项 | repo HEAD `5b18d8c`（09-12 08:53「S7 门户精修」） | 线上 `/home/ubuntu/apps/portal/app.py` |
|---|---|---|
| 结构 | 模块化：`dashboard/{__init__,app,config,page,services}.py` + `tests/` + `README.md` | 单文件 `app.py`（root:root 600，mtime **2026-09-17 12:07**）+ `app.py.bak.pre-stock` |
| API 路由 | 6 条：`/api/dsh-url`、`/api/status`、`/api/cloud-status`、`/api/daily`、`/api/summary`、`/api/summary/generate` | 2 条：`/api/dsh-url`、`/api/status` |
| 页面 | 七卡 + agnes 总览 | 四卡：DSH / 简历 / 股票 / 竞彩 |
| 集成 | cloud 状态、日报、AI 摘要生成 | `STOCK_DASHBOARD=http://127.0.0.1:8098/`、`STOCK_REPORTS_DIR=/home/ubuntu/DSH/longkonglong/reports` |

→ 两边功能集**互有出入**（线上多了简历/竞彩/股票三卡集成，repo 多了 cloud/daily/summary 能力），不是简单的新旧关系，**已分叉**。

### 2.4 lkl dashboard 代码范围（Stock_Anlysis 已跟踪 155 文件中的 15 个）

- **入口/基建**：`app.py`（8098 入口）、`handler.py`（路由）、`httpbase.py`（响应封装）
- **视图层**：`index.html`、`strategyview.py`（`/strategy` 策略观察台）、`signaldetail.py`、`signal_calendar.py`、`evidence.py`、`live.py`
- **数据层**：`loader.py`（读 `reports/` 下 `YYYY-MM-DD.json` 及同名 `.md`、`strategy_*.json`）、`tradeboard.py`/`tradefiles.py`/`tradestatus.py`（读 `trade/`）
- **依赖 lkl 包**：`lkl.config`、`lkl.services.exit`、`lkl.trade.naming`、`lkl.utils.{dates,db,env}`
- **运维**：`lkl-dash.service`（systemd）、`nginx-dash.conf.snippet`
- **无独立部署副本**——线上直接运行 repo 目录内代码

---

## 三、踩坑记录

1. `/home/ubuntu/apps/portal/app.py` 权限 `root:root 600`，普通用户 `cat`/`diff` 被拒，必须 `sudo` 读；目录级 `diff -rq` 能报「differ」但单文件 diff 会失败，容易误判。
2. 命名撞车三连：GitHub 上的 `dashboard` repo 是**门户**；lkl 的 `dashboard/` 目录服务的是 `/stock/`；`/dashboard/jc/` 又属于 league-v2。
3. `/etc/nginx/sites-enabled/dsh-web` 普通用户无读权限，必须 `sudo nginx -T` 看合并后配置。
4. 公网 URL 匿名 `curl` 一律 401（basic auth），验证服务存活须**直连 `127.0.0.1:<port>`**。
5. 该目录原本无 `.git`，上游 `Stock_Anlysis` 已含全量项目——说明此前推送过，本地是丢了 `.git` 的副本。

---

## 四、待拍板

1. **Stock_Anlysis**：本地 `main` 领先上游 1 个提交（`eb530a2`：`send_json` 加 `default=str` + 首页竞彩看板入口），是否 push。
2. **门户（8099）**：线上单文件版 vs repo 模块化版已分叉，以哪边为准、是否重新部署。
