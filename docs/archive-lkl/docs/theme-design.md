# 题材拓扑模块设计（theme_tag / theme_group）

> 依据：奎爷 2026-09-01 反馈（身位×题材×催化剂二维结构）+ Wind 勘探报告
> （reports/wind-probe-2026-08-31.md）。LLM 铁律不破：LLM 只做离线标注生产，
> 信号/回测只消费持久化标签表（运行时确定性）。

## 1. 表结构

```sql
CREATE TABLE theme_tag (            -- 个股×交易日 题材坐标（奎爷 JSON 结构落库）
  date date, code text,             -- PK
  primary_theme text,               -- 主标签=当期炒作叙事（非静态行业）
  secondary_themes jsonb,           -- 次标签数组
  catalyst text,                    -- 催化剂摘要（公告/新闻原文摘录）
  catalyst_source text,             -- announcement | news | concept
  evidence_date date,               -- 证据日期（防过期催化剂）
  role text,                        -- 独立事件高标|梯队龙头|中位梯队|低位|首板孤立
  confidence numeric(4,3),          -- <0.70 自动标待审
  source text,                      -- wind | llm | manual（manual 优先级最高）
  reviewed boolean DEFAULT false
);
CREATE TABLE theme_group (          -- 日×题材 聚合（复盘③数据源，含首板治盲区）
  date date, theme text,            -- PK
  highest_board int, top_code text,
  mid_count int,                    -- 3板~最高-1
  low_count int,                    -- 2板
  first_board_count int,            -- 首板数（新主线萌芽信号）
  completeness numeric(5,1),        -- 梯队完整度=f(高度,断层,首板,成员数)
  status text,                      -- 主升|分歧|扩散|首日一致|萌芽
  member_count int
);
```

## 2. 数据流（每日盘后，emotion 之后、review 之前）

1. **成员**：derived_bar 当日涨停（含首板）→ 候选池；
2. **骨架**：`get_stock_basicinfo` 概念标签+主营 → 风格黑名单过滤 → 股×题材集；
3. **聚合**：按题材 join 梯队板数 → theme_group（首板数一并算）；
4. **催化剂**：高标/唯一候选 → `get_company_announcements` /
   `get_financial_news` → catalyst + evidence_date；
5. **primary_theme 裁决**（奎爷五优先级）：近三日直接催化 > 当日同题材共涨停数 >
   纵向梯队存在性 > 中军同步 > 主营吻合度。前四条结构化可算；第五条（吻合度）
   规则打分不足时 LLM 离线补标（source=llm，低置信送人工）。

## 3. 风格标签黑名单（唯一事实源：config.THEME_STYLE_*）

08-31 实跑演进出的三类规则：精确黑名单（连板/打板/龙虎榜/成交主力/高振幅/
超涨/首板*/预增预减/破净/股票质押/昨日涨停系 等）、前缀（全A*/标普*/富时*/
纳入*/股权激励*/财报披露*/首板*）、包含（*重仓*/*标的*/*综合*/*国资*/
*点位贡献*/*指数*）。**theme_tag 存原始标签、聚合时过滤**——黑名单演进不需
重拉 Wind。凡命中不得作为 primary_theme，仅作风格注记。

## 4. FALSE_RELATION 防线

- 名称相似（前2字同）但概念标签交集为空 且 申万行业不同 → 强制 role 分离
  + 送人工审（例：海鸥住工 002084 家居/控制权变更 vs 海鸥股份 603269 冷却塔/液冷）；
- 概念标签存在但近30日无公告/新闻佐证 → catalyst_source=concept 且
  confidence≤0.5（弱映射，例：中通国脉"算力"标签但公告明示未开展）。

## 5. 分期

| 期 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| P1 | 两表 + basicinfo 骨架 ingest + 黑名单 + theme_group 聚合 + 复盘③段 | 仅 Wind，无 LLM | ✅ 4d8f646 |
| P2 | 催化剂裁决 + role/confidence + 公告证据链（THEME_EVENT_RULES 关键词规则） | Wind 公告 | ✅ 本次 |
| P3 | 回测 11 个题材特征列 + 核心假设检验（269 候选分组：梯队完整 vs 孤板） | P1/P2 数据积累 | 待做 |

P2 实跑暴露并修掉的误报（三道护栏，tests/test_theme.py 锁死）：
①**标题规则**——关键词须标题命中，或标题为事件型（进展/提示/筹划/签署…）
才允许正文命中（防"重大信息内部报告制度"、"利润分配方案"模板词，新赛股份/
时代出版案例）；②**否定护栏**——命中前 12 字以 未/不/无/没有 收尾即弃
（"不存在重大资产重组"，新赛案例）；③**日期窗口**——证据日期须落
[trade_date−(LOOKBACK+3), trade_date]，陈旧公告不作当日催化（我爱我家 6 月
半年报案例）。call 量：17 票 ≈34 次/日（basicinfo+公告各一），78s。

## 6. 待定问题

- **P1 调用量**：basicinfo 每股一次。仅梯队（cont_days≥2）≈25 次/日；
  含首板 ≈80~100 次/日。Wind key 配额未验证——先 10 只试跑观察错误码再定。
- **completeness 公式**：建议 = w1·最高板 + w2·(1-断层率) + w3·ln(1+首板数)
  + w4·成员数，权重待 P3 用晋级率回归定标（不拍脑袋）。
