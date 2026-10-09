"""orchestration/pool.py 东财三池同步 CLI 测试。

2026-10-09 日志巡检 F：新增「逐日硬超时」相关用例——不联网、不碰生产库，
用假 fetcher 构造挂死/失败路径，另有一条真 SIGALRM 用例确保测的不是桩。
"""

from __future__ import annotations

import logging
import signal
import socket
import threading
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.orchestration import pool
from emotion_core.services import ingest

#: 假交易日（含事故当天回补的三天：09-29 成功、09-30 挂死、10-09 成功）。
DAYS3 = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 9)]


def _ingest_patch(fetcher, verify=None):
    """屏蔽 ingest 的两处外部边界：抓取与 DB 校验。"""
    return (
        patch("emotion_core.services.ingest.fetch_limit_pool", side_effect=fetcher),
        patch(
            "emotion_core.services.ingest.verify_pre_close",
            side_effect=verify if verify is not None else (lambda _d: []),
        ),
    )


class TestTradingDays:
    def test_returns_last_n(self):
        mock_dates = [date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 12)]
        with patch("emotion_core.utils.dates.trading_days", return_value=mock_dates):
            result = pool._trading_days(date(2024, 6, 15), 2)
            assert result == [date(2024, 6, 11), date(2024, 6, 12)]

    def test_n_larger_than_available(self):
        mock_dates = [date(2024, 6, 10)]
        with patch("emotion_core.utils.dates.trading_days", return_value=mock_dates):
            result = pool._trading_days(date(2024, 6, 15), 5)
            assert result == [date(2024, 6, 10)]


class TestMain:
    def test_dry_run_success(self):
        mock_pools = {"limit_pool": (MagicMock(return_value=MagicMock()), "mock")}
        with (
            patch(
                "emotion_core.orchestration.pool._trading_days", return_value=[date(2024, 6, 15)]
            ),
            patch("emotion_core.services.ingest._POOLS", mock_pools),
            patch(
                "emotion_core.utils.fetch.retry_fetch", side_effect=RuntimeError("network error")
            ),
        ):
            result = pool.main(["--date", "2024-06-15", "--dry-run"])
            # dry-run catches exceptions, prints them, returns 0
            assert result == 0

    def test_success_returns_zero(self):
        p_fetch, p_verify = _ingest_patch(lambda _d: 5)
        with (
            patch(
                "emotion_core.orchestration.pool._trading_days", return_value=[date(2026, 9, 29)]
            ),
            p_fetch,
            p_verify,
        ):
            assert pool.main(["--date", "2026-09-29"]) == 0

    def test_runtime_error_day_is_accounted(self):
        def fetcher(_d: date) -> int:
            raise RuntimeError("东财三池全部无数据")

        p_fetch, p_verify = _ingest_patch(fetcher)
        with (
            patch(
                "emotion_core.orchestration.pool._trading_days", return_value=[date(2026, 9, 29)]
            ),
            p_fetch,
            p_verify,
        ):
            assert pool.main(["--date", "2026-09-29"]) == 1

    def test_date_format_error(self):
        result = pool.main(["--date", "bad-date"])
        assert result == 2

    def test_no_trading_days(self):
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[]):
            result = pool.main(["--latest"])
            assert result == 1


class TestPerDayTimeout:
    """逐日硬超时：某天挂死不得拖死整轮，已成功的天必须保留。"""

    def test_hang_on_day2_skips_to_day3_and_keeps_written_days(self, caplog):
        written: list[date] = []

        def fetcher(d: date) -> int:
            written.append(d)
            if d == DAYS3[1]:
                time.sleep(5)  # 模拟 akshare 永久阻塞；0.2s 上限应把它打断
            return 7

        p_fetch, p_verify = _ingest_patch(fetcher)
        started = time.monotonic()
        with (
            patch("emotion_core.orchestration.pool._trading_days", return_value=list(DAYS3)),
            p_fetch,
            p_verify,
            caplog.at_level(logging.INFO, logger="emotion_core.pool"),
        ):
            rc = pool.main(["--last", "3", "--per-day-timeout", "0.2"])
        elapsed = time.monotonic() - started

        assert written == DAYS3, "第 2 天超时后必须继续第 3 天"
        assert rc == 1, "部分天失败沿用旧语义：非 0"
        assert elapsed < 3, f"没有在 0.2s 内跳天，实际 {elapsed:.2f}s"

        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(str(DAYS3[0]) in m and "三池=7" in m for m in infos), "第 1 天数据丢了"
        assert any(str(DAYS3[2]) in m and "三池=7" in m for m in infos), "第 3 天数据丢了"
        assert not any(str(DAYS3[1]) in m and "三池=" in m for m in infos), "挂死那天不该算成功"
        assert any(str(DAYS3[1]) in m and "硬超时" in m for m in warnings), (
            f"缺少含日期与超时的 WARNING：{warnings}"
        )

    def test_all_days_hang_still_nonzero(self):
        def fetcher(_d: date) -> int:
            time.sleep(5)

        p_fetch, p_verify = _ingest_patch(fetcher)
        started = time.monotonic()
        with (
            patch("emotion_core.orchestration.pool._trading_days", return_value=list(DAYS3)),
            p_fetch,
            p_verify,
        ):
            rc = pool.main(["--last", "3", "--per-day-timeout", "0.2"])
        assert rc == 1, "三天全失败仍必须非 0（原 RuntimeError 护栏）"
        assert time.monotonic() - started < 3

    def test_disabled_timeout_restores_old_behavior(self, caplog):
        """per_day_timeout<=0：不装定时器，慢调用照旧跑完。"""

        def fetcher(_d: date) -> int:
            time.sleep(0.3)  # 若 0.2s 上限还在，必定被打断
            return 1

        p_fetch, p_verify = _ingest_patch(fetcher)
        with (
            patch("emotion_core.orchestration.pool._trading_days", return_value=list(DAYS3)),
            p_fetch,
            p_verify,
            caplog.at_level(logging.WARNING, logger="emotion_core.pool"),
        ):
            rc = pool.main(["--last", "3", "--per-day-timeout", "0"])
        assert rc == 0
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0.0, "定时器没清理"
        assert not any("硬超时" in r.getMessage() for r in caplog.records)

    def test_runtime_error_continues_when_disabled(self):
        def fetcher(d: date) -> int:
            if d == DAYS3[1]:
                raise RuntimeError("东财三池全部无数据")
            return 2

        p_fetch, p_verify = _ingest_patch(fetcher)
        with p_fetch, p_verify:
            failed = pool._sync_days(list(DAYS3), 0)
        assert failed == [DAYS3[1]]


class TestDayDeadline:
    """真超时用例：打断真实阻塞，而不是只测假桩。"""

    def test_real_sigalrm_interrupts_blocking_sleep(self):
        started = time.monotonic()
        with pytest.raises(pool._DayTimeout):
            with pool._day_deadline(0.2):
                time.sleep(5)
        assert time.monotonic() - started < 2, "SIGALRM 没打断阻塞 sleep"
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0.0, "退出时没取消定时器"
        assert signal.getsignal(signal.SIGALRM) is not pool._on_alarm, (
            "退出时没还原 SIGALRM handler"
        )

    def test_timeout_not_swallowed_by_generic_except(self):
        """_DayTimeout 必须穿过下游的 `except Exception` 兜底（继承 BaseException）。"""
        with pytest.raises(pool._DayTimeout):
            with pool._day_deadline(0.2):
                try:
                    time.sleep(5)
                except Exception:  # noqa: BLE001 —— 模拟 retry_fetch 的重试兜底
                    pytest.fail("超时被 except Exception 吞掉")

    def test_real_fetch_limit_pool_does_not_swallow_timeout(self):
        """接真 ingest.fetch_limit_pool + 真 retry_fetch：卡死的池函数不会把
        超时吞成「三池全部无数据」的 RuntimeError。"""

        def hang(**_kw) -> int:
            time.sleep(5)
            return 0

        with patch("emotion_core.services.ingest._POOLS", {"ZT": (hang, {"名称": "name"})}):
            with pytest.raises(pool._DayTimeout):
                with pool._day_deadline(0.2):
                    ingest.fetch_limit_pool(DAYS3[0])

    def test_disabled_deadline_is_a_noop(self):
        with pool._day_deadline(0):
            time.sleep(0.01)
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0.0

    def test_non_main_thread_refuses_silent_downgrade(self):
        captured: dict[str, str] = {}

        def run() -> None:
            try:
                with pool._day_deadline(1.0):
                    pass
            except RuntimeError as exc:
                captured["err"] = str(exc)

        t = threading.Thread(target=run)
        t.start()
        t.join()
        assert "主线程" in captured.get("err", ""), "非主线程必须报错而不是静默无兜底"


class TestSocketTimeoutGuard:
    """2026-10-09 日志巡检 F：`setdefaulttimeout(15)` 只是纵深防御，不是本事故的修复。

    代码级原因：akshare 池函数 `requests.get(url, params=params)` 不传 timeout，
    requests 显式构造 `Timeout(connect=None, read=None)`，urllib3 对显式 None
    原样下传 → `socket.create_connection(..., None)` / `sock.settimeout(None)`
    永久阻塞，进程默认超时被绕过。真正的兜底是 `_day_deadline` 的逐日 SIGALRM。
    """

    def test_module_import_sets_socket_default(self) -> None:
        # pool 模块已经在 conftest 加载 emotion_core 时被 import 过。
        # 现在的 socket 默认值应当是 pool 设的 15s（不是 None）。
        assert socket.getdefaulttimeout() == 15, (
            f"expected 15s after pool module import, got {socket.getdefaulttimeout()!r}"
        )

    def test_socket_timeout_within_normal_calls(self) -> None:
        """socket timeout 不能让普通 DB / 业务调用立刻失败——15s 已够慢查询余量。"""
        current = socket.getdefaulttimeout()
        assert current is not None, "pool module 没设 socket default — 回归！"
        assert current >= 5, f"timeout 太严，已可能被正常慢请求打死: {current}s"
