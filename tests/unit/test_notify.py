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
    """观察记录即时推送——24h 内存节流、防重推。

    A 止血（2026-10-21）后 `push_buy_signal` **默认关闭**（无正期望买点，不该
    主动推「今日买入信号」）；本类验证「显式打开后」的节流/格式化行为，
    默认关的行为由 `test_disabled_by_default` 钉住。
    """

    @pytest.fixture(autouse=True)
    def _push_on(self, monkeypatch):
        monkeypatch.setenv("EMOTION_PUSH_BUY", "1")

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

    def test_disabled_by_default(self, monkeypatch):
        """A 止血：不带 EMOTION_PUSH_BUY 时不推（默认关）。"""
        posted = []
        import requests as real_requests
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        monkeypatch.delenv("EMOTION_PUSH_BUY", raising=False)
        monkeypatch.delenv("PUSH_BUY", raising=False)
        notify.reset_buy_dedup()

        ok = notify.push_buy_signal("000017", "测试龙", 5, "STANDARD",
                                    self._cl(), date(2026, 10, 10))
        assert ok is False
        assert posted == []

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

    def test_payload_format_is_plain_language(self, monkeypatch):
        """简报走人话——代码/票名/板数保留，内部代号 c1..c5 不再出现；
        A 止血后正文不得出现买点/卖点/仓位（不再给操作指令）。"""
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        # 只验格式化层的人话；LLM 改写有独立用例，这里关掉免得断言被改写打断
        monkeypatch.setattr(notify, "_humanize_alert", lambda t, kind="daily": t)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 5, "ENHANCED",
                                self._cl(), date(2026, 10, 10))
        payload = posted[0]["kw"]["json"]
        assert payload["msg_type"] == "text"
        body = payload["content"]["text"]
        assert "000017" in body and "测试龙" in body
        assert "5 板" in body or "5板" in body
        assert "唯一最强连板股" in body                          # 人话条件
        assert "c1 " not in body and "c3 " not in body           # 不再出现内部代号
        # A 止血红线：正文不得出现买点/卖点/仓位这类操作指令
        for banned in ("买点", "卖点", "仓位", "满仓", "半仓"):
            assert banned not in body, f"观察记录里不该出现「{banned}」"

    def test_zero_cont_days_in_body(self, monkeypatch):
        """cont_days 缺数（Signal 字段未填）→ 显示 0 板，不抛。"""
        import requests as real_requests
        posted = []
        monkeypatch.setattr(real_requests, "post", self._spy_post(posted))
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)
        # 关掉 LLM 改写：格式化器输出的是「0 板」（带空格），改写后空格取决于模型 ⇒
        # 断言会随模型变而变。这里只验格式化层，板数渲染由它负责。
        monkeypatch.setattr(notify, "_humanize_alert", lambda t, kind="daily": t)
        notify.reset_buy_dedup()

        notify.push_buy_signal("000017", "测试龙", 0, "STANDARD",
                                self._cl(), date(2026, 10, 10))
        body = posted[0]["kw"]["json"]["content"]["text"]
        assert "0 板" in body or "0板" in body


class TestHumanizeAlert:
    """R58-5：飞书推送前 LLM 改写为人话；失败回退原文（不破主链路）。"""

    def test_empty_text_returned_as_is(self):
        notify.reset_humanize_dedup()
        assert notify._humanize_alert("", kind="daily") == ""

    def test_buy_is_humanized_others_pass_through(self, monkeypatch):
        """W9′：buy 也走改写；watchdog 等其它通道原样返回。"""
        notify.reset_humanize_dedup()

        class _StubAgnes:
            def __init__(self):
                pass
            def complete(self, *, prompt, temperature, max_tokens):
                assert "盘后观察记录" in prompt      # 用的是 buy 专用 prompt
                return "000017 测试龙 5 板，硬条件全过，进了观察名单。"

        import sys
        sys.modules["emotion_core.llm"] = type(sys)("emotion_core.llm")
        sys.modules["emotion_core.llm.agnes"] = type(sys)("emotion_core.llm.agnes")
        sys.modules["emotion_core.llm.agnes"].AgnesClient = _StubAgnes

        text = "[emotion-core] 今日观察记录（不出买入建议）\n000017(测试龙)  5 板"
        out = notify._humanize_alert(text, kind="buy")
        assert out != text and "000017" in out
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


class TestPushWatchSignals:
    """R67-4：WATCH 信号即时推送——按 code 聚合、阈值 60、单日 top10、24h 节流。

    背景：`strategy_signal` 的 WATCH 此前完全不推送（2026-10-08 600825 有 3 条
    WATCH 而 owner 一条没收到）。BUY 走 `push_buy_signal`，这里是 WATCH 通道。
    """

    D = date(2026, 10, 8)

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        notify.reset_watch_dedup()
        for k in ("EMOTION_PUSH_WATCH", "PUSH_WATCH",
                  "EMOTION_WATCH_PUSH_MIN_SCORE", "WATCH_PUSH_MIN_SCORE",
                  "EMOTION_WATCH_PUSH_MAX_CODES", "WATCH_PUSH_MAX_CODES"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("EMOTION_LKL_WEBHOOK_URL", FEISHU_URL)

    @staticmethod
    def _spy(posted):
        import requests as real_requests

        def mock_post(url, **kw):
            posted.append({"url": url, "body": kw.get("json")})
            return _resp(200, {"code": 0})

        return mock_post, real_requests

    @staticmethod
    def _texts(posted):
        """飞书 payload 正文（`_payload` 出 `{"msg_type","content":{"text":…}}`）。"""
        out = []
        for p in posted:
            body = p["body"]
            content = body.get("content", body)
            if isinstance(content, dict):
                out.append(str(content.get("text", "")))
            else:
                out.append(str(content))
        return out

    def _row(self, code, strategy, score, reason="理由", name=None, **extra):
        row = {"code": code, "strategy": strategy, "score": score,
               "reason": reason, "action": "WATCH"}
        if name is not None:
            row["stock_name"] = name
        row.update(extra)
        return row

    def test_aggregates_by_code_and_drops_below_threshold(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row("600825", "bottom_volume", 75, "缩量回踩", name="新华传媒"),
                self._row("600825", "emotion_cycle", 45, "情绪低点"),
                self._row("600825", "bull_trend", 62, "趋势向上"),
                self._row("002343", "bull_trend", 59, "差一分")]
        sent = notify.push_watch_signals(rows, self.D)

        assert sent == 1, "只有 600825 有 ≥60 分信号"
        assert len(posted) == 1
        text = self._texts(posted)[0]
        assert "[策略观察·WATCH] 2026-10-08" in text
        assert "新华传媒(600825) 最高 75 分" in text
        assert "bottom_volume 75：缩量回踩" in text
        assert "bull_trend 62：趋势向上" in text
        assert "emotion_cycle 45" not in text, "45 分低于阈值不进正文"
        assert "002343" not in text

    def test_strategy_display_name_never_becomes_stock_name(self, monkeypatch):
        """runner 的 `name` 是策略显示名；有 stock_name 时必须用它。"""
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row("600825", "一阳三阴", 80, name="新华传媒")]
        notify.push_watch_signals(rows, self.D)

        text = self._texts(posted)[0]
        assert "新华传媒(600825)" in text
        assert "一阳三阴(600825)" not in text

    def test_falls_back_to_name_key_when_no_stock_name(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row("600825", "bottom_volume", 80, name=None)]
        rows[0]["name"] = "新华传媒"
        notify.push_watch_signals(rows, self.D)

        assert "新华传媒(600825)" in self._texts(posted)[0]

    def test_same_code_same_day_throttled(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        rows = [self._row("600825", "bottom_volume", 75, name="新华传媒")]

        assert notify.push_watch_signals(rows, self.D) == 1
        assert notify.push_watch_signals(rows, self.D) == 0, "24h 内同 (date,code) 节流"
        assert len(posted) == 1

    def test_different_date_pushes_again(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        rows = [self._row("600825", "bottom_volume", 75, name="新华传媒")]

        notify.push_watch_signals(rows, self.D)
        assert notify.push_watch_signals(rows, date(2026, 10, 9)) == 1
        assert len(posted) == 2

    def test_watch_dedup_is_separate_from_buy_dedup(self, monkeypatch):
        """同一 (date, code)：BUY 推过不能吃掉 WATCH 的配额。"""
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_PUSH_BUY", "1")
        notify.reset_buy_dedup()

        cl = SimpleNamespace(c1_uniqueness=True, c2_exchange=True,
                             c3_elimination=True, c4_min_days=True,
                             c5_strength_diverge=True, w1_crowding=True)
        notify.push_buy_signal("600825", "新华传媒", 5, "STANDARD", cl, self.D)
        sent = notify.push_watch_signals(
            [self._row("600825", "bottom_volume", 75, name="新华传媒")], self.D)

        assert sent == 1
        assert len(posted) == 2

    def test_disabled_by_env_posts_nothing(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_PUSH_WATCH", "0")

        sent = notify.push_watch_signals(
            [self._row("600825", "bottom_volume", 99, name="新华传媒")], self.D)

        assert sent == 0
        assert posted == []

    def test_missing_webhook_url_skips_silently(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.delenv("EMOTION_LKL_WEBHOOK_URL", raising=False)

        assert notify.push_watch_signals(
            [self._row("600825", "bottom_volume", 99, name="新华传媒")], self.D) == 0
        assert posted == []

    def test_all_below_threshold_sends_nothing(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row("600825", "a", 59), self._row("002343", "b", 10)]
        assert notify.push_watch_signals(rows, self.D) == 0
        assert posted == []

    def test_top10_cap_and_remainder_note(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row(f"60000{i}", "s", 90 - i) for i in range(3)]
        monkeypatch.setenv("EMOTION_WATCH_PUSH_MAX_CODES", "1")
        sent = notify.push_watch_signals(rows, self.D)

        assert sent == 1
        text = self._texts(posted)[0]
        assert "共 3 只，余下 2 只见报告⑥段" in text

    def test_reason_truncated_to_60_chars(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        long_reason = "缩" * 200
        notify.push_watch_signals(
            [self._row("600825", "bottom_volume", 75, long_reason, name="新华传媒")],
            self.D)

        text = self._texts(posted)[0]
        line = next(ln for ln in text.splitlines() if ln.startswith("· "))
        assert line == "· bottom_volume 75：" + "缩" * 60 + "…"

    def test_at_most_three_strategies_per_code(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [self._row("600825", f"s{i}", 90 - i, f"理由{i}", name="新华传媒")
                for i in range(5)]
        notify.push_watch_signals(rows, self.D)

        text = self._texts(posted)[0]
        bullets = [ln for ln in text.splitlines() if ln.startswith("· ")]
        assert len(bullets) == 3
        assert bullets[0].startswith("· s0 90") and bullets[-1].startswith("· s2 88")

    def test_http_exception_is_swallowed(self, monkeypatch):
        import requests as real_requests

        def boom(url, **kw):
            raise real_requests.ConnectionError("dns")

        monkeypatch.setattr(real_requests, "post", boom)

        assert notify.push_watch_signals(
            [self._row("600825", "bottom_volume", 75, name="新华传媒")], self.D) == 0

    def test_non_dict_and_empty_rows_are_noop(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        assert notify.push_watch_signals(None, self.D) == 0
        assert notify.push_watch_signals([], self.D) == 0
        assert notify.push_watch_signals(["600825", None], self.D) == 0
        assert posted == []

    def test_rows_without_code_skipped(self, monkeypatch):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)

        rows = [{"code": "", "strategy": "s", "score": 99, "reason": "r"}]
        assert notify.push_watch_signals(rows, self.D) == 0
        assert posted == []

    def test_bad_int_env_falls_back_to_default(self, monkeypatch, caplog):
        posted = []
        mock_post, requests = self._spy(posted)
        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.setenv("EMOTION_WATCH_PUSH_MIN_SCORE", "not-a-number")

        with caplog.at_level(logging.WARNING):
            # 回退 60 → 59 分仍被剔除
            sent = notify.push_watch_signals(
                [self._row("600825", "s", 59, name="新华传媒")], self.D)

        assert sent == 0
        assert any("不是整数" in r.getMessage() for r in caplog.records)

    def test_failed_http_does_not_poison_dedup(self, monkeypatch):
        """HTTP 异常不记节流 → 恢复后同一天仍可再推。"""
        import requests as real_requests
        posted = []

        def flaky(url, **kw):
            if not posted:
                posted.append("fail")
                raise real_requests.ConnectionError("dns")
            posted.append({"url": url, "body": kw.get("json")})
            return _resp(200, {"code": 0})

        monkeypatch.setattr(real_requests, "post", flaky)
        rows = [self._row("600825", "bottom_volume", 75, name="新华传媒")]

        assert notify.push_watch_signals(rows, self.D) == 0
        assert notify.push_watch_signals(rows, self.D) == 1, "失败的尝试不该占坑"
        assert len(posted) == 2