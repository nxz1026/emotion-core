"""notify 的环境变量读取、渠道识别与推送（不触网）。

两条历史背景：

1. `_cfg` 原实现只有 `getattr(CONFIG, name, default)`，而 CONFIG 是 frozen
   dataclass 且从不收录 `LKL_WEBHOOK_URL` → 恒返回空串 → webhook 恒跳过。这条通路
   自 V9 写下起一次都没通过，2026-09-27~09-28 连续 4 次 daily 失败因此一条没推出去。
   **不**给 CONFIG 加字段是有意为之：CONFIG 的字段集合参与 `config_hash()` 计算，
   加字段会让 pipeline_state / signal / eval_result 里已落库的策略指纹整体换代。
   webhook 属部署期密配置，与策略配置正交，故只走环境变量。

2. ★2026-10-07 本文件原有**两份** `TestGuessChannel` / `TestSummary` /
   `TestPushWithUrl`（分别定义在第 69/184、82/197、103/218 行）。Python 里同名类
   后者静默覆盖前者 ⇒ 约 120 行测试从未执行，且两份内容几乎相同，另有一���
   `test_push_empty_text_skips` **没有任何断言**（测了个寂寞）。
   现已合并为单份，并补上飞书渠道与「HTTP 200 但 body 非成功码」的覆盖。
"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

import pytest

from emotion_core.services import notify

FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("EMOTION_LKL_WEBHOOK_URL", "LKL_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)


def _resp(status: int = 200, body=None):
    """requests.Response 替身；`body` 供飞书判 code 用。"""
    r = type("R", (), {"status_code": status, "text": str(body or "")})()
    r.json = lambda: body if isinstance(body, dict) else {"code": -1}
    return r


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
    """_guess_channel 的渠道识别。

    ⚠️ 认不出就退 generic，而各家报文格式并不通用 —— 猜错的表现是
    **HTTP 200 但 body 是错误码**，看起来像推送成功。
    """

    def test_wecom_detected(self):
        assert notify._guess_channel(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send") == "wecom"

    def test_dingtalk_detected(self):
        assert notify._guess_channel("https://oapi.dingtalk.com/robot/send") == "dingtalk"

    def test_feishu_detected(self):
        """★2026-10-07：生产用的是飞书自定义机器人。"""
        assert notify._guess_channel(FEISHU_URL) == "feishu"

    def test_larksuite_detected(self):
        assert notify._guess_channel(
            "https://open.larksuite.com/open-apis/bot/v2/hook/x") == "feishu"

    def test_generic_fallback(self):
        assert notify._guess_channel("https://example.com/hook") == "generic"


class TestPayload:
    def test_feishu_uses_its_own_schema(self):
        """飞书要 msg_type/content；发 generic 的 {"text":...} 会被拒。"""
        p = notify._payload("feishu", "hello")
        assert p == {"msg_type": "text", "content": {"text": "hello"}}

    def test_feishu_truncates_to_4000(self):
        p = notify._payload("feishu", "x" * 9000)
        assert len(p["content"]["text"]) <= 4000

    def test_wecom_schema(self):
        assert "markdown" in notify._payload("wecom", "hello")

    def test_dingtalk_schema(self):
        assert "markdown" in notify._payload("dingtalk", "hello")


class TestRespOk:
    def test_non_200_is_failure_for_every_channel(self):
        assert notify._resp_ok("feishu", _resp(500)) is False
        assert notify._resp_ok("wecom", _resp(500)) is False

    def test_feishu_200_with_zero_code_is_success(self):
        assert notify._resp_ok("feishu", _resp(200, {"code": 0, "msg": "success"})) is True

    def test_feishu_200_with_error_code_is_failure(self):
        """★2026-10-07 回归：飞书报文非法时仍回 HTTP 200，成败只在 body 的 code。

        只看状态码会把「格式错」判成「已送达」——本函数就是为了不犯这个错。
        """
        assert notify._resp_ok("feishu", _resp(200, {"code": 19001, "msg": "bad param"})) is False

    def test_feishu_200_with_unparsable_body_is_failure(self):
        assert notify._resp_ok("feishu", _resp(200, None)) is False


class TestSummary:
    """_summary 从 MD 提取速览段并剥离持仓行。"""

    def test_extracts_summary_section(self):
        md = "# 报告\n\n## ⓪ 速览\n\n今日无异常。\n\n## ① 详情\n\nxxx\n"
        result = notify._summary(md)
        assert "今日无异常" in result
        assert "① 详情" not in result

    def test_falls_back_to_truncated_md_when_no_section(self):
        result = notify._summary("x" * 1000)
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
            return _resp(200)

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
            return _resp(500)

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_push_exception_does_not_propagate(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            raise real_requests.Timeout("timeout")

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://example.com/hook")

        assert notify.push("## ⓪ 速览\n\nx\n", None) is False

    def test_wecom_payload_truncates_to_4000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return _resp(200)

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://qyapi.weixin.qq.com/hook")

        notify.push(f"## ⓪ 速览\n\n{'x' * 5000}\n", None)
        assert len(posted[0]["markdown"]["content"]) <= 4000

    def test_dingtalk_payload_truncates_to_18000(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append(json)
            return _resp(200)

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send")

        notify.push(f"## ⓪ 速览\n\n{'y' * 20000}\n", None)
        assert len(posted[0]["markdown"]["text"]) <= 18000


class TestPushFeishu:
    """★2026-10-07：飞书端到端（不触网）——上线当天真正的推送通道。"""

    def test_feishu_push_succeeds_on_code_zero(self, monkeypatch):
        import requests as real_requests
        posted = []

        def mock_post(url, json=None, timeout=None):
            posted.append((url, json))
            return _resp(200, {"code": 0, "msg": "success"})

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)

        assert notify.push("## ⓪ 速览\n\n今日退潮，建议观望。\n", "2026-10-08") is True
        assert posted[0][0] == FEISHU_URL
        assert posted[0][1]["msg_type"] == "text"
        assert "退潮" in posted[0][1]["content"]["text"]

    def test_feishu_push_fails_on_error_code_despite_http_200(self, monkeypatch, caplog):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            return _resp(200, {"code": 19001, "msg": "invalid param"})

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)

        with caplog.at_level(logging.WARNING, logger="emotion_core.notify"):
            assert notify.push("## ⓪ 速览\n\nx\n", None) is False
        assert "非成功码" in caplog.text

    def test_feishu_push_exception_is_swallowed(self, monkeypatch):
        import requests as real_requests

        def mock_post(url, json=None, timeout=None):
            raise real_requests.ConnectionError("dns")

        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)

        assert notify.push("## ⓪ 速览\n\nx\n", None) is False


class TestPushBuySignal:
    """R58-4：BUY 即时推送——24h 内存节流、防重推、专注 BUY 信号简报。"""

    @staticmethod
    def _cl():
        return SimpleNamespace(c1_uniqueness=True, c2_exchange=True,
                               c3_elimination=True, c4_min_days=True,
                               c5_strength_diverge=True, w1_crowding=True)

    def _spy_post(self, posted):
        """统一 spy：append {args, kw} dict 让 posted[i]['kw']['json'] 可读。"""
        import requests as real_requests
        def mock_post(*a, **kw):
            posted.append({"args": a, "kw": kw})
            return _resp(200, {"code": 0})
        return mock_post

    def test_first_push_attempts(self, monkeypatch):
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        ok = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 10))
        assert ok is True
        assert len(posted) == 1

    def test_second_push_within_24h_skipped(self, monkeypatch):
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post",
                            lambda *a, **kw: posted.append(1) or _resp(200, {"code": 0}))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                self._cl(), date(2026, 10, 10))
        ok2 = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 10))
        assert ok2 is False, "24h 内同 (date,code) 重推应被节流"
        assert len(posted) == 1

    def test_different_code_pushes_again(self, monkeypatch):
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post",
                            lambda *a, **kw: posted.append(1) or _resp(200, {"code": 0}))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                self._cl(), date(2026, 10, 10))
        ok2 = notify.push_buy_signal("600001", "另一只", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 10))
        assert ok2 is True, "不同 code 不应互相节流"
        assert len(posted) == 2

    def test_different_date_pushes_again(self, monkeypatch):
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post",
                            lambda *a, **kw: posted.append(1) or _resp(200, {"code": 0}))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                self._cl(), date(2026, 10, 10))
        ok2 = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 11))
        assert ok2 is True, "不同 date 不应互相节流"
        assert len(posted) == 2

    def test_network_error_does_not_pollute_dedup(self, monkeypatch):
        """R58-4：网络异常时不记节流——下次重试可推。"""
        import requests as real_requests
        posted = []
        def mock_post(*a, **kw):
            posted.append(1)
            raise real_requests.ConnectionError("dns")
        monkeypatch.setattr(real_requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        ok1 = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 10))
        assert ok1 is False
        # 修复后重试
        monkeypatch.setattr(real_requests, "post",
                            lambda *a, **kw: posted.append(1) or _resp(200, {"code": 0}))
        ok2 = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                     self._cl(), date(2026, 10, 10))
        assert ok2 is True, "网络异常未污染 dedup，重试应能推"
        assert len(posted) == 2

    def test_no_url_skips_silently(self, monkeypatch):
        monkeypatch.delenv("EMOTION_LKL_WEBHOOK_URL", raising=False)
        notify.reset_buy_dedup()
        assert notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                      self._cl(), date(2026, 10, 10)) is False

    def test_payload_format_includes_checklist(self, monkeypatch):
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 5, "ENHANCED",
                                self._cl(), date(2026, 10, 10))
        payload = posted[0]["kw"]["json"]
        assert payload["msg_type"] == "text"
        body = payload["content"]["text"]
        assert "000017" in body and "测试龙" in body and "5板" in body
        assert "ENHANCED" in body
        assert "✓ c1 唯一换手高标" in body
        assert "✓ c3 淘汰赛身份" in body

    def test_zero_cont_days_in_body(self, monkeypatch):
        """cont_days 缺数（Signal 字段未填）→ 显示 0 板，不抛。"""
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 0, "STANDARD",
                                self._cl(), date(2026, 10, 10))
        body = posted[0]["kw"]["json"]["content"]["text"]
        assert "0板" in body


class TestHumanizeAlert:
    """R58-5：飞书推送前 LLM 改写为人话；失败回退原文（不破主链路）。"""

    def test_empty_text_returned_as_is(self):
        notify.reset_humanize_dedup()
        assert notify._humanize_alert("", kind="daily") == ""

    def test_non_daily_kind_passes_through(self):
        """kind='buy' / 'watchdog' 暂不 humanize，直接返回原文。"""
        text = "## ⓪ 速览\n**今日 BUY：000017 5板**"
        assert notify._humanize_alert(text, kind="buy") == text
        assert notify._humanize_alert(text, kind="watchdog") == text

    def test_llm_failure_falls_back_to_original(self, monkeypatch):
        """LLM 抛 LLMNotEnabledError / 网络异常 → 回退原文，不抛。"""
        notify.reset_humanize_dedup()

        class _StubAgnes:
            def __init__(self):
                raise RuntimeError("Agnes 未启用：mock 无密钥")

        # 把 agnes 模块整个替成抛错 stub
        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        text = "## ⓪ 速览\n今日高潮，涨停 50。BUY 1 只。"
        out = notify._humanize_alert(text, kind="daily")
        assert out == text   # 回退原文

    def test_llm_success_returns_humanized(self, monkeypatch):
        """LLM 正常返回 → 用 LLM 输出而非原文。"""
        notify.reset_humanize_dedup()

        class _StubAgnes:
            def __init__(self):
                pass
            def complete(self, *, prompt, temperature, max_tokens):
                return "今天市场处于高潮阶段，涨停家数 50 较高。出现 1 只 BUY 推荐，可考虑逢低介入。"

        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        text = "## ⓪ 速览\n**今日高潮** BUY 1 只 涨停 50"
        out = notify._humanize_alert(text, kind="daily")
        assert "高潮" in out and "涨停" in out
        assert out != text   # 真正被改写了

    def test_llm_empty_output_falls_back(self, monkeypatch):
        """LLM 返回空串 → 回退原文。"""
        notify.reset_humanize_dedup()

        class _StubAgnes:
            def __init__(self):
                pass
            def complete(self, *, prompt, temperature, max_tokens):
                return "   "

        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        text = "## ⓪ 速览\n**今日高潮**"
        out = notify._humanize_alert(text, kind="daily")
        assert out == text   # 空字符串回退

    def test_llm_result_cached_for_24h(self, monkeypatch):
        """同一段摘要第二次调用 LLM 走缓存（只调一次 LLM）。"""
        notify.reset_humanize_dedup()
        call_count = [0]

        class _StubAgnes:
            def __init__(self):
                pass
            def complete(self, *, prompt, temperature, max_tokens):
                call_count[0] += 1
                return f"LLM 输出 #{call_count[0]}"

        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        text = "## ⓪ 速览\n**今日高潮** BUY 1 只"
        out1 = notify._humanize_alert(text, kind="daily")
        out2 = notify._humanize_alert(text, kind="daily")
        assert out1 == out2 == "LLM 输出 #1"
        assert call_count[0] == 1   # 第二次走缓存

    def test_different_text_triggers_new_llm_call(self, monkeypatch):
        notify.reset_humanize_dedup()
        call_count = [0]

        class _StubAgnes:
            def __init__(self):
                pass
            def complete(self, *, prompt, temperature, max_tokens):
                call_count[0] += 1
                return f"#{call_count[0]}"

        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        a = notify._humanize_alert("alpha 文本", kind="daily")
        b = notify._humanize_alert("beta 文本", kind="daily")
        assert a == "#1" and b == "#2"
        assert call_count[0] == 2