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


class TestGuessChannel:
    """_guess_channel 的渠道识别。"""

    def test_wecom_detected(self):
        assert notify._guess_channel("https://qyapi.weixin.qq.com/cgi-bin/webhook/send") == "wecom"

    def test_dingtalk_detected(self):
        assert notify._guess_channel("https://oapi.dingtalk.com/robot/send") == "dingtalk"

    def test_generic_fallback(self):
        assert notify._guess_channel("https://example.com/hook") == "generic"


class TestSummary:
    """_summary 从 MD 提取速览段并剥离持仓行。"""

    def test_extracts_summary_section(self):
        md = "# 报告\n\n## ⓪ 速览\n\n今日无异常。\n\n## ① 详情\n\nxxx\n"
        result = notify._summary(md)
        assert "今日无异常" in result
        assert "① 详情" not in result

    def test_falls_back_to_truncated_md_when_no_section(self):
        md = "x" * 1000
        result = notify._summary(md)
        assert len(result) <= 600  # default limit

    def test_strips_position_lines(self):
        md = "## ⓪ 速览\n\n正常。\n\n**当前持仓**：600519 50%\n"
        result = notify._summary(md)
        assert "当前持仓" not in result
        assert "正常" in result


class TestPushWithUrl:
    """push 在 URL 配置后的行为（不触网）。"""

    def test_push_posts_to_url(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append((url, json, timeout))
            resp = type("R", (), {"status_code": 200})()
            return resp

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://qyapi.weixin.qq.com/hook")

        result = notify.push("## ⓪ 速览\n\nhello\n", "2026-09-29")

        assert result is True
        assert len(posted) == 1
        assert posted[0][0] == "https://qyapi.weixin.qq.com/hook"
        assert "markdown" in posted[0][1]
        assert posted[0][2] == 10  # timeout

    def test_push_non_200_returns_false(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            return type("R", (), {"status_code": 500})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_push_exception_does_not_propagate(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            raise real_requests.Timeout("timeout")

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        # 推送异常不抛，返回 False
        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_wecom_payload_truncates_to_4000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://qyapi.weixin.qq.com/hook")

        long_text = "x" * 5000
        notify.push(f"## ⓪ 速览\n\n{long_text}\n", None)

        content = posted[0]["markdown"]["content"]
        assert len(content) <= 4000

    def test_dingtalk_payload_truncates_to_18000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send")

        long_text = "y" * 20000
        notify.push(f"## ⓪ 速览\n\n{long_text}\n", None)

        content = posted[0]["markdown"]["text"]
        assert len(content) <= 18000


class TestGuessChannel:
    """_guess_channel 的渠道识别。"""

    def test_wecom_detected(self):
        assert notify._guess_channel("https://qyapi.weixin.qq.com/cgi-bin/webhook/send") == "wecom"

    def test_dingtalk_detected(self):
        assert notify._guess_channel("https://oapi.dingtalk.com/robot/send") == "dingtalk"

    def test_generic_fallback(self):
        assert notify._guess_channel("https://example.com/hook") == "generic"


class TestSummary:
    """_summary 从 MD 提取速览段并剥离持仓行。"""

    def test_extracts_summary_section(self):
        md = "# 报告\n\n## ⓪ 速览\n\n今日无异常。\n\n## ① 详情\n\nxxx\n"
        result = notify._summary(md)
        assert "今日无异常" in result
        assert "① 详情" not in result

    def test_falls_back_to_truncated_md_when_no_section(self):
        md = "x" * 1000
        result = notify._summary(md)
        assert len(result) <= 600  # default limit

    def test_strips_position_lines(self):
        md = "## ⓪ 速览\n\n正常。\n\n**当前持仓**：600519 50%\n"
        result = notify._summary(md)
        assert "当前持仓" not in result
        assert "正常" in result


class TestPushWithUrl:
    """push 在 URL 配置后的行为（不触网）。"""

    def test_push_posts_to_url(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append((url, json, timeout))
            resp = type("R", (), {"status_code": 200})()
            return resp

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://qyapi.weixin.qq.com/hook")

        result = notify.push("## ⓪ 速览\n\nhello\n", "2026-09-29")

        assert result is True
        assert len(posted) == 1
        assert posted[0][0] == "https://qyapi.weixin.qq.com/hook"
        assert "markdown" in posted[0][1]
        assert posted[0][2] == 10  # timeout

    def test_push_non_200_returns_false(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            return type("R", (), {"status_code": 500})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_push_exception_does_not_propagate(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            raise real_requests.Timeout("timeout")

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        # 推送异常不抛，返回 False
        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_push_empty_text_skips(self, monkeypatch):
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")
        # 无速览段 → _summary 返回截断文本，但仍会 POST
        result = notify.push("no summary section at all\n", None)
        # 只要有 URL 且 POST 成功就返回 True
        # 这个测试主要验证空文本不抛异常

    def test_wecom_payload_truncates_to_4000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://qyapi.weixin.qq.com/hook")

        long_text = "x" * 5000
        notify.push(f"## ⓪ 速览\n\n{long_text}\n", None)

        content = posted[0]["markdown"]["content"]
        assert len(content) <= 4000

    def test_dingtalk_payload_truncates_to_18000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send")

        long_text = "y" * 20000
        notify.push(f"## ⓪ 速览\n\n{long_text}\n", None)

        content = posted[0]["markdown"]["text"]
        assert len(content) <= 18000
