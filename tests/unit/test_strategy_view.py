"""策略观察台（presentation.strategy_view）：_safe_date / strategy_dates / load_strategy / to_api。"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from emotion_core.presentation import strategy_view


class TestSafeDate:
    def test_valid_date(self):
        assert strategy_view._safe_date("2026-09-25") == date(2026, 9, 25)

    def test_invalid_format(self):
        assert strategy_view._safe_date("2026/09/25") is None

    def test_empty_string(self):
        assert strategy_view._safe_date("") is None

    def test_none(self):
        assert strategy_view._safe_date(None) is None

    def test_garbage(self):
        assert strategy_view._safe_date("not-a-date") is None


class TestStrategyDates:
    def test_empty_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == []

    def test_valid_snapshot(self, monkeypatch, tmp_path):
        snap = {"items": [{"code": "000001", "score": 80}]}
        (tmp_path / "strategy_2026-09-25.json").write_text(
            json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == ["2026-09-25"]

    def test_invalid_filename_ignored(self, monkeypatch, tmp_path):
        (tmp_path / "other_file.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == []

    def test_empty_items_ignored(self, monkeypatch, tmp_path):
        snap = {"items": []}
        (tmp_path / "strategy_2026-09-25.json").write_text(
            json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == []

    def test_multiple_dates_sorted_desc(self, monkeypatch, tmp_path):
        for d in ["2026-09-20", "2026-09-25", "2026-09-22"]:
            snap = {"items": [{"code": "000001", "score": 80}]}
            (tmp_path / f"strategy_{d}.json").write_text(
                json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == [
            "2026-09-25", "2026-09-22", "2026-09-20"]

    def test_corrupt_file_ignored(self, monkeypatch, tmp_path):
        (tmp_path / "strategy_2026-09-25.json").write_text(
            "{not valid json", encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.strategy_dates() == []


class TestLoadStrategy:
    def test_load_valid(self, monkeypatch, tmp_path):
        snap = {"date": "2026-09-25", "items": [{"code": "000001", "score": 80}]}
        (tmp_path / "strategy_2026-09-25.json").write_text(
            json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        result = strategy_view.load_strategy("2026-09-25")
        assert result == snap

    def test_invalid_date_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.load_strategy("not-a-date") is None

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        assert strategy_view.load_strategy("2026-09-25") is None


class TestToApi:
    def test_no_payload_returns_note(self, monkeypatch, tmp_path):
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        result = strategy_view.to_api("2026-09-25")
        assert result["note"]
        assert result["items"] == []

    def test_with_payload(self, monkeypatch, tmp_path):
        snap = {"date": "2026-09-25",
                "items": [{"code": "000001", "score": 80},
                          {"code": "600000", "score": 90}]}
        (tmp_path / "strategy_2026-09-25.json").write_text(
            json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        monkeypatch.setattr(strategy_view, "query_df",
                            lambda sql, params=(): SimpleNamespace(
                                itertuples=lambda: iter([])))
        result = strategy_view.to_api("2026-09-25")
        assert len(result["items"]) == 2
        assert result["items"][0]["score"] == 90  # 降序排列

    def test_items_without_code_skipped(self, monkeypatch, tmp_path):
        snap = {"date": "2026-09-25",
                "items": [{"score": 80}, {"code": "000001", "score": 90}]}
        (tmp_path / "strategy_2026-09-25.json").write_text(
            json.dumps(snap), encoding="utf-8")
        monkeypatch.setattr(strategy_view, "REPORTS", tmp_path)
        monkeypatch.setattr(strategy_view, "query_df",
                            lambda sql, params=(): SimpleNamespace(
                                itertuples=lambda: iter([])))
        result = strategy_view.to_api("2026-09-25")
        codes = [i.get("code") for i in result["items"]]
        assert "000001" in codes
