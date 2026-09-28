"""个股诊断的真库校准（默认跳过，`EC_LIVE_DB=1` 才跑）。

单元测试是 DB-free 的（与仓库基线一致），但晋级率这种统计口径**必须**在真库上
和算法层的纯实现对齐一次，否则页面数字与 `promotion_day` 会分叉。这里的第 2 个
用例就是那次校准：同一天的单日口径，`stock_query.promotion_table(pair_date=d)`
与 `algorithms/promotion.promotion_matrix(d)` 必须逐层、逐字段（4 位小数）相等。

跑法：
    EC_LIVE_DB=1 PYTHONPATH=src .venv/bin/pytest tests/e2e/test_stock_live.py -q
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EC_LIVE_DB") != "1",
    reason="真库校准：设 EC_LIVE_DB=1 才跑（默认 DB-free 基线不受影响）")


@pytest.fixture(scope="module")
def live():
    from emotion_core.data import stock_query as q
    d = q.latest_trade_date()
    if d is None:
        pytest.skip("库内无日线数据")
    return q, d


def test_promotion_caliber_matches_algo_layer_single_day(live):
    """单日口径必须与 promotion.promotion_matrix 完全一致。"""
    q, d = live
    from emotion_core.algorithms import promotion
    mine = {r["layer"]: r for r in q.promotion_table(d, pair_date=d)}
    for row in promotion.promotion_matrix(d):
        got = mine[row["layer"]]
        assert got["total"] == row["promote_from"], row["layer"]
        assert got["promoted"] == row["promote_nominal"], row["layer"]
        assert got["promoted_ex"] == row["promote_exchange"], row["layer"]
        assert got["rate_ratio"] == row["rate_nominal"], row["layer"]


def test_analyze_is_readonly_and_grounded(live, monkeypatch):
    """整链路只读，且结论里的数字确实来自库。"""
    q, d = live
    from emotion_core.services import stock_service as svc

    writes: list[str] = []
    for name in ("execute", "insert", "upsert", "delete"):
        monkeypatch.setattr(q, name, lambda *a, _n=name, **k: writes.append(_n),
                            raising=False)
    svc.clear_cache()
    out = svc.analyze("601811", d)
    assert out["ok"], out.get("error")
    assert writes == []
    v = out["verdict"]
    assert v["reasons"] and v["disclaimer"]
    if v["stats"]["layer"]:
        promo = {r["layer"]: r for r in out["promotion_table"]}[v["stats"]["layer"]]
        assert v["stats"]["rate"] == promo["rate"]     # 结论引用的就是该层统计
        assert v["stats"]["promote_from"] == promo["total"]
    assert out["trend"]["price"] is not None
