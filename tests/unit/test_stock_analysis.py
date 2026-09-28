"""个股诊断判定层（algorithms/stock.py）：纯函数，零 SQL / 零 LLM / 零网络。

这里守的是**结论可信度**：每条建议都要有数据依据、缺样本时要如实说"样本不足"
而不是编数、风险项要真的挂上、结论不能越过市场情绪窗口。
"""
from __future__ import annotations

from datetime import date, timedelta

from emotion_core.algorithms import stock as sa

D = date(2026, 9, 24)


def _series(closes, cont=0, one_word=False, exchange=False, vols=None,
            pct_last=None, limit_up=False):
    """构造最小可用 series（升序），字段名与 data/stock_query.recent_series 一致。"""
    n = len(closes)
    vols = vols or [100.0] * n
    out = []
    for i, c in enumerate(closes):
        out.append({
            "date": D - timedelta(days=n - 1 - i), "close": c, "high": c * 1.06,
            "low": c * 0.96, "open": c, "pre_close": closes[i - 1] if i else c,
            "volume": vols[i], "amount": 1_000_000.0, "turnover_rate": 3.5,
            "pct_chg": pct_last if i == n - 1 and pct_last is not None else 1.0,
            "is_limit_up": limit_up if i == n - 1 else False,
            "is_limit_down": False, "is_one_word": one_word if i == n - 1 else False,
            "is_exchange": exchange if i == n - 1 else False, "is_bomb": False,
            "touched_limit": limit_up if i == n - 1 else False,
            "cont_days": cont if i == n - 1 else 0, "amplitude": 4.0,
        })
    return out


BASIC = {"code": "601811", "name": "新华文轩", "is_st": False,
         "first_bar_date": date(2024, 1, 2)}
ENV_OK = {"phase": "高潮", "buy_window": "ENHANCED", "limit_up_count": 49,
          "limit_down_count": 13, "max_limit_days": 5, "bomb_rate": 0.22,
          "force_liquidate": False, "oneword_ratio": 0.09}
ENV_OFF = dict(ENV_OK, phase="退潮", buy_window="NONE")


def test_layer_label_matches_promotion_layers():
    assert sa.layer_label(1) == "1->2"
    assert sa.layer_label(4) == "4->5"
    assert sa.layer_label(5) == "5+->6+"
    assert sa.layer_label(9) == "5+->6+"
    assert sa.layer_label(0) == "0->1"


def test_trend_metrics_computes_ma_and_off_high():
    t = sa.trend_metrics(_series([10.0] * 19 + [20.0]))
    assert t["ma5"] == 12.0             # 最后 5 根收盘 10,10,10,10,20
    assert t["ma20"] == 10.5
    assert t["high20"] == 21.2          # 最后一根 high = close * 1.06
    assert t["off_high_pct"] < 0        # 收在 20 日高点之下
    assert t["above_ma20"] is True


def test_trend_metrics_ignores_nan_and_none():
    s = _series([10.0] * 6)              # 末根 NaN，前 5 根仍能算 MA5
    s[-1]["close"] = float("nan")
    t = sa.trend_metrics(s)
    assert t["price"] is None
    assert t["above_ma20"] is None
    assert t["ma5"] == 10.0             # NaN 不参与均值


def test_vol_ratio_uses_previous_20_bars():
    vols = [100.0] * 20 + [300.0]
    t = sa.trend_metrics(_series([10.0] * 21, vols=vols))
    assert t["vol_ratio"] == 3.0


def test_attach_volume_ratio_per_bar():
    series = _series([10.0] * 6, vols=[100.0, 100.0, 100.0, 100.0, 100.0, 250.0])
    out = sa.attach_volume_ratio(series)
    assert out[0]["vol_ratio5"] is None          # 第一根没有前值
    assert out[-1]["vol_ratio5"] == 2.5
    assert series[-1].get("vol_ratio5") is None  # 不改原对象


def test_verdict_st_and_limit_down_are_hard_veto():
    assert sa.verdict(dict(BASIC, is_st=True), sa.trend_metrics(_series([10.0] * 5)),
                      {}, ENV_OK, None, None, None, BASIC["first_bar_date"], D)["stance"] == sa.STANCE_AVOID
    s = _series([10.0] * 5)
    s[-1]["is_limit_down"] = True
    v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OK, None, None, None,
                   BASIC["first_bar_date"], D)
    assert v["stance"] == sa.STANCE_AVOID
    # 非 ST、非跌停、无连板 → 不该被硬否决，而是"观察等信号"
    assert sa.verdict(BASIC, sa.trend_metrics(_series([10.0] * 25)), {}, ENV_OK,
                      None, None, None, BASIC["first_bar_date"], D)["stance"] == sa.STANCE_WATCH


def test_verdict_market_window_closed_blocks_everything():
    """市场 buy_window=NONE 时，个股再好也不给参与（状态机是硬门）。"""
    s = _series([10.0] * 19 + [11.0], cont=2, exchange=True, limit_up=True)
    v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OFF,
                   {"layer": "2->3", "rate": 90.0, "total": 100}, None, None,
                   BASIC["first_bar_date"], D)
    assert v["stance"] == sa.STANCE_AVOID
    assert any("情绪窗口关闭" in t["name"] for t in v["tags"])
    assert any("buy_window=NONE" in r for r in v["reasons"])


def test_verdict_low_layer_exchange_board_is_buy():
    s = _series([10.0] * 19 + [11.0], cont=2, exchange=True, limit_up=True)
    v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OK,
                   {"layer": "2->3", "rate": 34.5, "total": 5480, "promoted": 1891,
                    "perf_median": 2.28, "win_rate": 58.6, "perf_n": 5467,
                    "fail_perf": -1.92}, None, {"n": 350, "median": 1.04, "win_rate": 52.6},
                   BASIC["first_bar_date"], D)
    assert v["stance"] == sa.STANCE_BUY
    assert any("34.5%" in r and "N=5480" in r for r in v["reasons"])
    assert any("5 日前瞻" in r for r in v["reasons"])
    assert any("-1.92%" in r for r in v["risks"])


def test_verdict_high_board_or_one_word_is_low_absorb_only():
    for kwargs in ({"cont": 5, "exchange": True}, {"cont": 2, "one_word": True}):
        s = _series([10.0] * 19 + [11.0], limit_up=True, **kwargs)
        v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OK,
                       {"layer": "5+->6+", "rate": 53.4, "total": 949},
                       None, None, BASIC["first_bar_date"], D)
        assert v["stance"] == sa.STANCE_LOW_ABSORB


def test_verdict_without_samples_says_insufficient_not_fake_number():
    """分母不足（promo_row=None）时不得出现任何编造的百分比。"""
    s = _series([10.0] * 19 + [11.0], cont=3, exchange=True, limit_up=True)
    v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OK, None, None, None,
                   BASIC["first_bar_date"], D)
    assert v["stats"]["rate"] is None
    assert any("样本不足" in r for r in v["reasons"])
    assert not any("%" in r and "晋级率" in r for r in v["reasons"])


def test_verdict_low_rate_below_layer_does_not_claim_advantage():
    s = _series([10.0] * 19 + [11.0], cont=2, exchange=True, limit_up=True)
    v = sa.verdict(BASIC, sa.trend_metrics(s), {}, ENV_OK,
                   {"layer": "2->3", "rate": None, "total": 2}, None, None,
                   BASIC["first_bar_date"], D)
    assert v["stats"]["rate"] is None


def test_verdict_no_limit_up_watches_trend():
    v = sa.verdict(BASIC, sa.trend_metrics(_series([10.0] * 25)), {}, ENV_OK,
                   None, None, None, BASIC["first_bar_date"], D)
    assert v["stance"] == sa.STANCE_WATCH
    assert any("20 日线" in r for r in v["reasons"])


def test_risk_tags_cover_st_new_issuer_bomb_and_window():
    s = _series([10.0] * 19 + [11.0], cont=5, one_word=True, limit_up=True)
    trend = sa.trend_metrics(s)
    structure = {"bomb_n": 4, "sample_n": 60, "limit_up_n": 6}
    tags = sa.risk_tags(dict(BASIC, is_st=True), trend, structure, ENV_OFF,
                        D - timedelta(days=30), D)
    names = {t["name"] for t in tags}
    assert {"ST", "次新", "一字板", "5连板高位", "炸板体质", "情绪窗口关闭"} <= names
    assert all(t["why"] for t in tags)  # 每个标签都必须带解释


def test_st_detected_from_name_when_flag_missing():
    """真库 is_st 全为 false（EM clist 502 未填充），必须靠名称兜底。"""
    assert sa.is_st_name("ST新华锦") and sa.is_st_name("*ST美丽")
    assert not sa.is_st_name("新华文轩") and not sa.is_st_name(None)
    assert sa.is_st({"is_st": True, "name": "新华文轩"}) is True
    trend = sa.trend_metrics(_series([10.0] * 25))
    v = sa.verdict({"code": "600735", "name": "ST新华锦", "is_st": False},
                   trend, {}, ENV_OK, None, None, None, date(2024, 1, 2), D)
    assert v["stance"] == sa.STANCE_AVOID
    assert any(t["name"] == "ST" for t in v["tags"])


def test_is_new_issuer_boundary():
    assert sa.is_new_issuer(D - timedelta(days=89), D) is True
    assert sa.is_new_issuer(D - timedelta(days=90), D) is False
    assert sa.is_new_issuer(None, D) is False


def test_every_verdict_carries_disclaimer_and_nonempty_reasons():
    for cont, env in ((0, ENV_OK), (2, ENV_OK), (6, ENV_OFF)):
        s = _series([10.0] * 19 + [11.0], cont=cont, exchange=True, limit_up=cont > 0)
        v = sa.verdict(BASIC, sa.trend_metrics(s), {}, env,
                       {"layer": sa.layer_label(cont), "rate": 40.0, "total": 100},
                       None, None, BASIC["first_bar_date"], D)
        assert v["disclaimer"].startswith("统计参考")
        assert v["reasons"] and v["one_liner"] and v["stance_text"]
