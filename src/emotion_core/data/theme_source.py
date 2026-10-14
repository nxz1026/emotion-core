"""ThemeProvider 协议：题材数据获取的抽象层。

设计：
- ThemeProvider 协议定义题材标签获取接口
- NullProvider 默认实现（降级不阻断，返回空列表）
- SinaConceptProvider：新浪概念板块（免费，0 Wind 额度，2026-10-14 起为**生效源**）
- WindProvider 占位（subprocess 调 node CLI，暂不实现）

生效源怎么选（2026-10-14）：
`get_provider()` 无参时读环境变量 ``EMOTION_THEME_PROVIDER``，未设置则仍是
NullProvider（单测与未部署环境的行为一字不变）。**刻意不进 `utils/config.py`**：
CONFIG 是 frozen dataclass，新增字段会改变 `config_hash()`，让 `pipeline_state` /
`signal` / `eval_result` 里的策略指纹平白换代（见 services/theme_service.py 的注释
与 MEMORY 里那条踩坑）。生产激活走 `deploy/env/emotion-core.env`（daily 单元的
`EnvironmentFile=` 已在读），因此**不需要改任何 systemd 单元**。
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Protocol

log = logging.getLogger("emotion_core.data.theme_source")

#: 激活环境变量名（值即 provider 名，如 ``sina_concept``）。
ENV_PROVIDER = "EMOTION_THEME_PROVIDER"


class ThemeProvider(Protocol):
    """题材标签获取协议。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        """获取当日题材标签行。

        Returns:
            标签行列表，每行格式：
            {"code": str, "primary_theme": str, "secondary_themes": list[str]}
        """


class NullProvider:
    """空实现：题材数据不可用时返回空列表，不阻断主链。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        return []

    def fetch_spots(self, trade_date: date) -> list[dict]:
        return []


class WindProvider:
    """Wind MCP 题材获取（占位，暂不实现）。"""

    def fetch_tags(self, trade_date: date) -> list[dict]:
        raise NotImplementedError("WindProvider 暂不实现，请使用 NullProvider")


class SinaConceptProvider:
    """新浪概念板块源：逐票题材标签 + 每日板块热点。

    `fetch_tags` 一次拉全量概念清单 + 全部成分股（约 175 个节点 / 8,000 条成员关系），
    无需按票查询。`primary_theme` 取该票所属**成员数最多**的概念（最主流题材），
    并列按节点码排序 —— 确定性，保证同日重跑产出同一行（theme_tag 有 (date,code) 唯一键）。

    不过滤节点：新浪概念清单里混着「融资融券 / 深股通 / 次新股」这类交易属性桶，
    但剔除它们的判据属 `algorithms/theme.py` 的 STYLE_ONLY 三重规则（照搬 lkl，有对账），
    在本层再造一套只会和算法层漂移。本源只负责「如其所是地取回来」。
    """

    def fetch_tags(self, trade_date: date) -> list[dict]:
        from emotion_core.data.providers.sina_concept import (
            SOURCE,
            fetch_concept_map,
        )

        _nodes, cmap = fetch_concept_map()
        rows: list[dict] = []
        for code, members in cmap.items():
            usable = [n for n in members if n.name]
            if not usable:
                continue
            ordered = sorted(usable, key=lambda n: (-n.member_count, n.node))
            rows.append({
                "code": code,
                "tag_date": trade_date,
                "primary_theme": ordered[0].name,
                "secondary_themes": [n.name for n in ordered[1:]],
                "source": SOURCE,
            })
        log.info("新浪概念 %s：%d 票标签", trade_date, len(rows))
        return rows

    def fetch_spots(self, trade_date: date) -> list[dict]:
        """当日概念板块热点榜行（涨跌幅 / 成交额 / 领涨股）。"""
        from emotion_core.data.providers.sina_concept import (
            SOURCE,
            fetch_concept_nodes,
        )

        return [{
            "date": trade_date,
            "node": n.node,
            "theme": n.name,
            "member_count": n.member_count,
            "pct_chg": n.pct_chg,
            "amount": n.amount,
            "leading_code": n.leading_code,
            "leading_name": n.leading_name,
            "source": SOURCE,
        } for n in fetch_concept_nodes()]


def get_provider(name: str | None = None) -> ThemeProvider:
    """获取题材 Provider 实例。

    Args:
        name: provider 名称（``"sina_concept"`` / ``"wind"`` / ``"null"``）。
            None 时读 ``EMOTION_THEME_PROVIDER``，未设置或未知名字 → NullProvider
            （未知名字**不抛错**：降级不阻断是本源一贯契约）。

    Returns:
        ThemeProvider 实例。
    """
    key = (name if name is not None else os.getenv(ENV_PROVIDER, "")).strip().lower()
    if key in {"sina", "sina_concept"}:
        return SinaConceptProvider()
    if key == "wind":
        return WindProvider()
    return NullProvider()
