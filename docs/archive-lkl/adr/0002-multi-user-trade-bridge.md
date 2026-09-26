# ADR-0002 交易桥多用户改造（2026-09-03，奎爷）

## 状态
已采纳

## 背景
两个交易账户需各自认领 decisions/results/holdings，
DB 侧 position 表统一给策略（无账户维度），分歧交易端解决。

## 决策
- trade/user1/ 与 trade/user2/ 各一份相同 decisions
- 回执各自归档至 user{N}/consumed/<for_date>/
- DB 合并消费：all_pending 遍历，position 以最后一份 holdings 为准

## 后果
- holdings 多用户语义 = 快照覆盖（非按账户合并）
- position 表加账户维度需另立 ADR（涉及建表迁移）
