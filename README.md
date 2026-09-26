# emotion-core

A 股市场情绪周期 · 事实底座 · 决策系统

---

## 这是什么

`emotion-core` 是 `longkonglong`（下称 **lkl**）与 `a_share_emotion_leader`（下称 **asel**）
两个仓库的**合并目标仓库**。

- **lkl**：龙空龙策略盘后复盘器 —— 决策系统（情绪周期状态机 → 龙头淘汰 → 买入信号 → 交易桥）
- **asel**：A 股情绪龙头事实底座 —— 数据/制度/质量/只读展示（零决策能力）

合并结论见 `docs/evidence/merge-analysis-2026-09-26.md`：
**这不是两个重叠项目要合并，是一次做了一半的重写要把另一半做完。**

---

## 当前阶段

**阶段 0：规划与口径裁决（未开始编码）**

本仓库目前**只有文档**。在功能层、算法层、数据源层、编排层的设计定稿并逐层批准之前，
不写任何业务代码。展示层最后定（它是一个只读快照的独立 web，与主干解耦）。

讨论顺序（自上而下从终态反推）：

| # | 层 | 状态 | 文档 |
|---|---|---|---|
| 1 | **功能层** | 🚧 讨论中 | `docs/01-功能层讨论.md` |
| 2 | 数据源层 | ⏳ 待开始 | — |
| 3 | 算法层 | ⏳ 待开始 | — |
| 4 | 编排层 | ⏳ 待开始 | — |
| 5 | 展示层 | ⏳ 最后定 | — |

---

## 计划中的仓库布局（尚未创建）

```
emotion-core/
├── docs/                 设计文档与决策记录（当前唯一有内容的目录）
│   ├── 01-功能层讨论.md
│   ├── decisions/        ADR：口径裁决与关键决策
│   └── evidence/         取证材料（源仓库分析、对账结果）
├── src/emotion_core/     主干代码（分层：sources / semantics / algorithms / orchestration）
├── tests/
└── scripts/              运维脚本与 systemd unit
```

包名 `emotion_core`；分层边界与 import 方向将在算法层/编排层讨论时定稿。

---

## 两个源仓库（只读参照，不改动）

| 仓库 | 路径 | 分支 | HEAD |
|---|---|---|---|
| lkl | `/home/ubuntu/DSH/longkonglong` | `main` | `eb53a46` |
| asel | `/home/ubuntu/DSH/a_share_emotion_leader` | `p0` | `fde92ac` |

两者 `git remote` 指向同一个 GitHub 仓库 `nxz1026/Stock_Anlysis.git`，但**历史不相交**。
合并期间这两个仓库**冻结为只读参照**，不作为提交目标。

---

## 关键约束（来自取证，合并必须正面处理）

1. **单位口径不同**：lkl `volume`=手 / asel `volume_shares`=股（实测比值恰好 100）；
   换手率 百分数 / 比值（0.01）。**不冻结单位就合并 = 静默错 100 倍。**
2. **`pre_close`**：lkl 存列；asel 用 `LAG(close)` 重算，除权日实测错 339/192,593 行。
3. **连板口径**：lkl `cont_days` 停牌断档不打断（实测 45/43,860 行虚增）；asel 缺口断开。
4. **ST 口径**：lkl 整体剔除；asel 建了规则但生产硬编码 `variant="normal"` 空转。
5. **两套"市场状态"**：lkl 4 相状态机（含 buy_window）；asel 3 档阈值（cold_max 20 vs lkl 40）。
6. **asel 的增量设计大半未落地**：15 张表 0 行、`asel/sources/` 零生产 import、
   声明式 DAG（`steps.py`）生产未接线。
