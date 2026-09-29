"""notify._cfg 的环境变量读取（2026-09-29 新增覆盖）。

背景：`_cfg` 原实现只有 `getattr(CONFIG, name, default)`，而 CONFIG 是 frozen
dataclass 且从不收录 `LKL_WEBHOOK_URL` → 恒返回空串 → webhook 恒跳过。这条通路
自 V9 写下起一次都没通过，2026-09-27~09-28 连续 4 次 daily 失败因此一条没推出去。

**不**给 CONFIG 加字段是有意为之：CONFIG 的字段集合参与 `config_hash()` 计算，
加字段会让 pipeline_state / signal / eval_result 里已落库的策略指纹整体换代。
webhook 属部署期密配置，与策略配置正交，故只走环境变量。
"""
from __future__ import annotations

import logging

import pytest

from emotion_core.services import notify


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("EMOTION_LKL_WEBHOOK_URL", "LKL_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)


class TestCfg:
    def test_absent_everywhere_falls_back_to_default(self):
        assert notify._cfg("LKL_WEBHOOK_URL", "") == ""

    def test_emotion_prefixed_env_wins(self, monkeypatch):
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://a/hook")
        assert notify._cfg("LKL_WEBHOOK_URL", "") == "https://a/hook"

    def test_bare_name_env_also_read(self, monkeypatch):
        monkeypatch.setenv("LKL_WEBHOOK_URL", "https://b/hook")
        assert notify._cfg("LKL_WEBHOOK_URL", "") == "https://b/hook"

    def test_prefixed_beats_bare(self, monkeypatch):
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://prefixed/hook")
        monkeypatch.setenv("LKL_WEBHOOK_URL", "https://bare/hook")
        assert notify._cfg("LKL_WEBHOOK_URL", "") == "https://prefixed/hook"

    def test_empty_env_treated_as_unset(self, monkeypatch):
        """空串视同未配置——否则配错成空串会盖住后面的来源。"""
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "")
        monkeypatch.setenv("LKL_WEBHOOK_URL", "https://bare/hook")
        assert notify._cfg("LKL_WEBHOOK_URL", "") == "https://bare/hook"

    def test_config_attribute_still_works(self, monkeypatch):
        monkeypatch.setattr(notify, "CONFIG",
                            type("C", (), {"SOME_KEY": "from-config"})())
        assert notify._cfg("SOME_KEY", "fallback") == "from-config"

    def test_env_beats_config(self, monkeypatch):
        """环境变量优先于 CONFIG（部署期覆盖策略期配置）。"""
        monkeypatch.setattr(notify, "CONFIG",
                            type("C", (), {"SOME_KEY": "from-config"})())
        monkeypatch.setenv("EMOTION_SOME_KEY", "from-env")
        assert notify._cfg("SOME_KEY", "fallback") == "from-env"


class TestPushWithoutUrl:
    def test_push_skips_silently_when_unconfigured(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="emotion_core.notify"):
            assert notify.push("## ⓪ 速览\n\nx\n", None) is False
        assert "未配置" in caplog.text
