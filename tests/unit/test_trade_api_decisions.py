"""presentation/trade_api.py 交易决策去重测试。

2026-10-09 日志巡检（用户拍板「修」）：`strategy_signal` 的唯一键含
`prompt_hash`，同一 `(code, strategy)` 重跑会追加版本行，同一只票还可能有多条
策略同时给 BUY —— 原来 `_query_signals` 直接取全部行并 `ORDER BY score DESC`，
于是同一个 code 会变成多笔 `OPEN_POS`（2026-10-09 的缓存里是 13 条 action /
5 个 code）。

本文件钉三件事：
1. 查询本身按 code 去重（`DISTINCT ON (code)`），且同分取最新版本；
2. 即使查询返回重复 code，组装 actions 时也只出一条（兜底层）；
3. 落库的自选/决策缓存（state.json）仍按日期短路，不重复查库。
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from emotion_core.presentation import trade_api


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """把 state.json 指到临时目录，避免碰生产的 /home/ubuntu/trade/。"""
    path = tmp_path / "state.json"
    monkeypatch.setattr(trade_api, "STATE_FILE", path)
    return path


def test_query_sql_dedups_by_code_and_prefers_latest_version(monkeypatch):
    """查询必须按 code 去重，同分取 created_at 最新、prompt_hash 兜底。"""
    captured = {}

    def fake_query_df(sql, params):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(trade_api, "_query_df", fake_query_df)
    trade_api._query_signals(date(2026, 10, 9))

    sql = captured["sql"]
    assert "DISTINCT ON (code)" in sql
    assert "ORDER BY code, score DESC, created_at DESC, prompt_hash DESC" in sql
    assert "ORDER BY score DESC, code" in sql
    assert captured["params"] == (date(2026, 10, 9), "BUY")


def _rows(*pairs):
    return [
        {
            "code": code,
            "strategy": strategy,
            "action": "BUY",
            "score": score,
            "confidence": 0.9,
            "reason": f"{strategy}-{code}",
        }
        for code, strategy, score in pairs
    ]


def test_duplicate_codes_collapse_to_one_action(monkeypatch, state_file, caplog):
    """兜底层：查询返回重复 code 时，只保留第一条，并记一条 WARNING。"""
    duplicated = _rows(
        ("000736", "wave_theory", 88),
        ("000736", "bull_trend", 78),  # 同 code 的第二条（历史版本/多策略）
        ("000420", "hot_theme", 70),
        ("000546", "growth_quality", 65),
    )
    monkeypatch.setattr(trade_api, "_query_signals", lambda for_date: duplicated)

    with caplog.at_level("WARNING", logger="emotion_core.trade_api"):
        payload, status = trade_api.handle_trade_decisions("date=2026-10-09")

    assert status == 200
    codes = [a["code"] for a in payload["actions"]]
    assert codes == ["000736", "000420", "000546"]
    assert len(codes) == len(set(codes))
    assert all(a["action"] == "BUY" and a["exec"] == "OPEN_POS" for a in payload["actions"])
    assert "duplicate BUY signal for 000736" in caplog.text
    # 缓存里存的也必须是去重后的结果
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert [a["code"] for a in saved["decisions"]["2026-10-09"]["actions"]] == codes


def test_second_call_serves_cache_without_querying_again(monkeypatch, state_file):
    """同一天第二次请求走缓存：batch_id 不变，且不再查库。"""
    calls = []

    def fake_query_signals(for_date):
        calls.append(for_date)
        return _rows(("000002", "wave_theory", 90))

    monkeypatch.setattr(trade_api, "_query_signals", fake_query_signals)

    first, _ = trade_api.handle_trade_decisions("date=2026-10-09")
    second, _ = trade_api.handle_trade_decisions("date=2026-10-09")

    assert first["batch_id"] == second["batch_id"]
    assert len(calls) == 1


def test_no_buy_signals_returns_empty_actions_and_no_cache(monkeypatch, state_file):
    """没有 BUY 时不落缓存（否则额度用尽/未评估的一天会被永久钉成空批次）。"""
    monkeypatch.setattr(trade_api, "_query_signals", lambda for_date: [])

    payload, status = trade_api.handle_trade_decisions("date=2026-10-08")

    assert status == 200
    assert payload == {"batch_id": "", "for_date": "2026-10-08", "actions": []}
    assert not state_file.exists() or "2026-10-08" not in json.loads(
        state_file.read_text(encoding="utf-8")
    ).get("decisions", {})


def test_invalid_date_still_rejected(monkeypatch, state_file):
    monkeypatch.setattr(trade_api, "_query_signals", lambda for_date: [])
    payload, status = trade_api.handle_trade_decisions("date=2026-13-99")
    assert status == 400
    assert "invalid date" in payload["error"]
