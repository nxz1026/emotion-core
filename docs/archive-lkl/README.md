# lkl 策略规格书归档（只读，禁止编辑）

本目录是 `longkonglong`(lkl) 的**文档与报告归档**，在 lkl 被删除前抢救出来的。

**为什么必须归档**：lkl 的策略口径由奎爷（朋友）裁决，共 22 处「拍板」记录在
`docs/PLAN.md`。代码可以重写，**这些口径裁决重写不出来**。

| 文件 | 内容 |
|---|---|
| `docs/PLAN.md` | **525 行策略规格书**：决策总表（D1~D7/A~Q3/E1~E4/F1~F9/A1~A8/S1~S4/W1~W4/V1~V14.1）+ §1 策略规格 + §2 库设计 + §3 模块架构 + 硬性代码规则 |
| `docs/REQUIREMENTS.md` | 术语速查表、策略直觉（Leader Election 类比）、交易桥契约 v2 |
| `docs/DATA_SOURCES.md` | 数据源实测选型结论与踩坑 |
| `docs/IMPL_PLAN.md` | 代码实施计划 |
| `docs/ANALYSIS_C.md`、`docs/BACKLOG.md`、`docs/theme-design.md` | 专题分析 |
| `adr/*.md` | 3 份架构决策记录 |
| `reports/*.md` | 40+ 份实盘日报 + 评估报告（可反查每一天的判定） |
| `README-lkl.md` | lkl 的 README（策略哲学 + 核心机制） |

**纪律**：本目录**只读**。若发现与实现不符，在 `docs/decisions/` 开 ADR 记录，
**不要直接改这里的文件** —— 它是历史证据，不是待改进的代码。

源仓库：`/home/ubuntu/DSH/longkonglong` @ `main` / `eb53a46`
