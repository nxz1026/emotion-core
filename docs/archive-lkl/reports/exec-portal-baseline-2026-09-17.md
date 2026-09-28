# 执行报告：门户基线统一 + dashboard 归属澄清

日期：2026-09-17 · 执行人：密米尔 · 拍板：奎爷（门户按 A 落地 / Stock_Anlysis dashboard 保留）

---

## 一、结论

| # | 动作 | 结果 |
|---|---|---|
| 1 | `nxz1026/dashboard` 以**线上运行为准**：单文件 `app.py` 取代模块化包 | 推送 `5b18d8c..2258d7a` ✅ |
| 2 | `nxz1026/Stock_Anlysis` 的 `dashboard/` **保留不删**，补归属澄清 | 推送 `bf3edaa..d9452cb`（含此前挂着的 `eb530a2`）✅ |
| 3 | 线上服务健康 | `8098` 200、`8099` 200 ✅ |
| 4 | 挂着的两处改动是否已生效 | 实测**已在线上生效，无需重启** ✅ |

---

## 二、证据

### 2.1 门户 repo（`2258d7a`）

**原文对账**（提交内容 = 线上运行内容）：

```
403ceb4bb9c6049a1031aa9b03898445  /tmp/portal-app.py
403ceb4bb9c6049a1031aa9b03898445  /home/ubuntu/apps/portal/app.py
```

**删除**（6 个作废文件）：`dashboard/{__init__,app,config,page,services}.py`、`tests/test_dashboard.py`
（旧路由 `/api/cloud-status`、`/api/daily`、`/api/summary`、`/api/summary/generate` 一并作废，git 历史可回滚）

**远端树**（`git ls-tree -r 2258d7a`）：

```
.gitignore
README.md
app.py
```

**实跑验收**（repo 内 `app.py` 换端口 18099 起服）：

| 请求 | 结果 |
|---|---|
| `GET /` | 200，`<title>NDORACLE 门户</title>` |
| `GET /api/status` | 200，真数据：`dsh` 401（=正常需 token）、`resume` 405、`stock` 200 + 报告 `2026-09-17` 48 项、Top1 `bull_trend/601872/82` |
| `GET /api/dsh-url` | 200，返回 DSH 公网地址 |
| `GET /nope` | 404 |

**推送核对**：`git ls-remote … refs/heads/main` = `2258d7a` = 本地 `main`

### 2.2 Stock_Anlysis（`d9452cb`）

- `dashboard/` **15 个文件完好**，`git status` 干净
- README 增「归属澄清」段（`/stock/` 8098 复盘 UI ≠ `/dashboard/` 8099 门户 ≠ `/dashboard/jc/` 8077 竞彩）
- **测试未跑**：`.venv` 未安装 pytest（`pyproject` 的 dev extra 缺失），改用定向实证替代：

```
send_json(datetime) -> 无异常, code 200
对照：裸 json.dumps(datetime) -> TypeError: Object of type datetime is not JSON serializable
```

- **线上生效确认**：`curl 127.0.0.1:8098/` 页面已含「体彩竞彩看板 / dashboard/jc」入口；
  进程启动 `12:00:46` 晚于 `dashboard/index.html` mtime `12:00:42`（`httpbase.py` 改于 09-15）→ 两处改动均已被运行进程加载
- **推送核对**：`ls-remote` = `d9452cb` = 本地 `main`

---

## 三、踩坑记录

1. **token 落盘**：`/tmp` 克隆若用 `https://x-access-token:$TOKEN@…` 内联 URL，token 会写进 `.git/config`。
   本次推完已 `git remote set-url` 清成干净 URL（已验证）。
2. **假成功陷阱**：`/tmp/dash-repo` 无 git 身份时 `git commit` 报 `Author identity unknown` 失败，
   紧接的 `git push` 却输出 `Everything up-to-date`（因为没东西可推）——**极易误判为推送成功**。
   铁律：push 后必须 `git ls-remote` 比对 sha。
3. **pytest 缺失**：`.venv` 未装 pytest，README 宣称的 272 离线用例当前**跑不起来**（`pip install -e ".[dev]"` 可补）。
4. **分叉根源**：`/home/ubuntu/apps/portal` 无 `.git`、文件 `root:root 600`，被直接就地编辑 → 与 repo 分叉。
5. **token 暴露面**：`/api/dsh-url` 会把带 token 的 DSH 公网地址返回给任何通过 Basic Auth 的访问者。
   这是门户「DSH 入口」的设计功能，但该 token 等同入口凭据——分享页面截图/录屏时注意。

---

## 四、待拍板

- **部署目录是否纳入 git 管理**（`/home/ubuntu/apps/portal` → clone 或软链），以消除「就地改 → 再分叉」的循环。
