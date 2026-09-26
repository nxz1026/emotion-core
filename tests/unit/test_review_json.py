"""render_json：决策快照序列化（纯函数，不连库）。"""
import json
from datetime import date
from decimal import Decimal

import numpy as np

from emotion_core.algorithms.review import render_json
from emotion_core.domain.ladder import LadderDay


def test_render_json_schema_and_types():
    d = {
        "date": date(2026, 8, 31),
        "stat": {"phase": "高潮", "bomb_rate": Decimal("0.2893"),
                 "limit_up_count": np.int64(86), "force_liquidate": np.bool_(False)},
        "ladder_rows": [LadderDay(date=date(2026, 8, 31), code="002855",
                                  cont_days=5, is_exchange=True, is_top=True,
                                  is_sole_top=True, y_top_group_count=0,
                                  y_top_survivor_count=0)],
        "sole": None,
        "elimination": [{"code": "000017", "today": "断板"}],
        "quality": {"y_comp": (2, 1), "warns": []},
    }
    js = json.loads(render_json(d))
    assert js["schema"] == "lkl/daily@2"
    assert js["date"] == "2026-08-31"
    assert js["stat"]["bomb_rate"] == 0.2893
    assert js["stat"]["limit_up_count"] == 86
    assert js["stat"]["force_liquidate"] is False
    assert js["ladder_rows"][0]["code"] == "002855"
    assert js["quality"]["y_comp"] == [2, 1]


def test_render_json_v24_sections():
    """v2.4 新增段：晋级矩阵 DataFrame 与生态评级 dict 可序列化。"""
    import pandas as pd
    d = {
        "date": date(2026, 8, 31),
        "promotion": pd.DataFrame(
            [{"layer": "1->2", "promote_from": 59, "rate_nominal": 0.1525,
              "rate_exchange": None}]),          # None 须原样落 JSON null（F6）
        "dragon_env": {"rating": "UNFAVORABLE",
                       "goods": [{"cond": "G1", "ok": None, "note": "样本不足"}],
                       "bads": [{"cond": "B4", "ok": True, "note": "成员数 1"}]},
    }
    js = json.loads(render_json(d))
    assert js["promotion"][0]["rate_exchange"] is None
    assert js["promotion"][0]["rate_nominal"] == 0.1525
    assert js["dragon_env"]["rating"] == "UNFAVORABLE"
    assert js["dragon_env"]["goods"][0]["ok"] is None
    assert js["dragon_env"]["bads"][0]["ok"] is True


def test_quality_renders_four_states():
    """P1-12：diff 四态——None(缺池)≠empty(无差异)，绝不混渲染。"""
    import pandas as pd
    from emotion_core.algorithms import review as rv
    rv._fmt_caliber = lambda c: ""          # 口径元数据与四态无关
    # None = 池缺失 → 必须显式警告，绝不出现 ✓
    s = rv._sec_quality({"quality": {"diff": None, "warns": []},
                         "caliber": {}})
    assert "无法对账" in s and "✓" not in s
    # empty = 真无差异 → ✓
    s = rv._sec_quality({"quality": {"diff": pd.DataFrame(), "warns": []},
                         "caliber": {}})
    assert "无差异 ✓" in s
    # 有行 = 差异 → 人工复核
    s = rv._sec_quality({"quality": {"diff": pd.DataFrame({"a": [1]}),
                                     "warns": []}, "caliber": {}})
    assert "对账差异" in s
