# 16 - Trade API 协议

> LKL-Trade 与 oracle 之间的 HTTP 决策/结果交换协议。

## 概述

Trade API 运行在 emotion-core 的展示层（`presentation/server.py`），通过 nginx 反代暴露在 `/trade/` 路径下。

```
LKL-Trade (client)  <--HTTPS-->  nginx (/trade/)  -->  emotion-core :8098/api/trade/
                                                                     presentation/trade_api.py
```

## 端点

### GET /trade/decisions?date=YYYY-MM-DD

获取指定日期的交易决策。首次请求时从 `strategy_signal` 表生成 batch_id 并缓存；后续请求返回缓存版本。

**请求：**
```
GET /trade/decisions?date=2026-09-30
```

**响应：**
```json
{
  "batch_id": "1a0960df-77c4-4aa2-8b4c-5891a2ecaeeb",
  "for_date": "2026-09-30",
  "actions": [
    {
      "code": "000504",
      "action": "BUY",
      "exec": "OPEN_POS",
      "volume": 100,
      "reason": "4连板后炸板分歧转一致..."
    }
  ]
}
```

**逻辑：**
- 查询 `strategy_signal` 表：`WHERE trade_date=%s AND action='BUY' ORDER BY score DESC`
- 每个 signal 转换为 action（固定 volume=100, exec=OPEN_POS）
- 同日期首次请求生成 batch_id（UUID4），后续返回缓存
- 无 BUY 信号时返回空 actions 数组

### POST /trade/results

提交 LKL-Trade 执行结果。

**请求：**
```
POST /trade/results
Content-Type: application/json

{
  "batch_id": "1a0960df-77c4-4aa2-8b4c-5891a2ecaeeb",
  "for_date": "2026-09-30",
  "trades": [
    {
      "code": "000504",
      "action": "BUY",
      "status": "executed",
      "price": 12.34,
      "volume": 100,
      "reason": "ma_golden_cross"
    }
  ]
}
```

**响应：**
```json
{"status": "ok", "batch_id": "1a0960df-..."}
```

**幂等性：**
- 同一 `batch_id` 重复提交返回 `{"note": "idempotent"}`
- 结果文件写入 `~/trade/results_{for_date}_{timestamp}.json`

### GET /trade/results?date=YYYY-MM-DD

获取指定日期的最新结果（oracle 端读取用）。

### GET /trade/health

健康检查。

```json
{"ok": true, "service": "emotion_core_trade_api"}
```

## 去重机制

使用 `batch_id` 替代 SFTP 模式的 5 层去重：

| 层次 | SFTP 模式 | HTTP 模式 |
|------|-----------|-----------|
| 1 | ledger (executed.json) | state.json `processed` 数组 |
| 2 | intent (pending.json) | 无（同步请求） |
| 3 | _consumed_archived() | 无 |
| 4 | by_ref 会话内追踪 | 无 |
| 5 | remote.pull() newest-only | 无 |

**状态文件：** `~/trade/state.json`

```json
{
  "decisions": {
    "2026-09-30": {
      "batch_id": "1a0960df-...",
      "for_date": "2026-09-30",
      "actions": [...]
    }
  },
  "processed": ["1a0960df-...", "other-batch-id"]
}
```

## nginx 配置

```nginx
location /trade/ {
    proxy_pass http://127.0.0.1:8098/api/trade/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 64k;
}
```

## 安全

- HTTPS 由 nginx 提供（Let's Encrypt 证书）
- basic auth 由 nginx 层处理
- `client_max_body_size 64k` 限制请求体大小

## 部署

```bash
# 重启服务
sudo systemctl restart emotion-core-dash

# 验证
curl -k https://140.83.62.161/trade/health
curl -k https://140.83.62.161/trade/decisions?date=2026-09-30
```

## 代码位置

- **Handler:** `src/emotion_core/presentation/trade_api.py`
- **Server:** `src/emotion_core/presentation/server.py`（路由注册 + do_POST）
- **Docs:** `docs/16-Trade-API-Protocol.md`
