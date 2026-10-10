"""买入五条件逐项：构造候选/ctx 断言 checklist 行为（不连库）。

语义来源 lkl/tests/test_entry.py；本文件差异仅限 emotion-core 契约（见 entry.py 模块文档）：
- Checklist 是 frozen dataclass：断言看字段（`checklist()` 投影），落库行另经 _persist 核对；
- c5 阈值为显式 ctx["diverge_min"]（lkl 临时改全局 config，本实现不碰全局）；
- W1 极性同 lkl：警告行恒 True（扎堆与否写在说明里），永不否决。

IO 全部桩掉（ladder.build/y_survivors/market_stat/候选质量/事务），真实库对账另跑
（见交付记录：同候选同窗口与 lkl.services.entry 逐项差分）。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from emotion_core.algorithms import entry
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Action, Checklist, SignalSource
from emotion_core.utils.config import config_hash
from emotion_core.algorithms.entry import (
    check_recommend_signal,
    check_secondary_signal,
    check_signal,
    checklist,
    checklist_secondary,
    current_window,
    passed_of,
    split_checklist,
)

D = date(2026, 9, 8)        # 评估日
PREV = date(2026, 9, 7)     # 前一交易日


def cand(cont: int = 5, ex: bool = True, code: str = "000017") -> LadderDay:
    return LadderDay(date=D, code=code, cont_days=cont, is_exchange=ex, is_top=True,
                     is_sole_top=True, y_top_group_count=3, y_top_survivor_count=1)


def ctx(window: str = "STANDARD", sole_code: str | None = "000017",
        y_comp: tuple[int, int] = (3, 1), y_surv: tuple[set[str], set[str]] | None = None,
        min_days: int | None = None, **extra: Any) -> dict[str, Any]:
    sole = cand(code=sole_code) if sole_code else None
    if y_surv is None:                     # 缺省：候选在昨日组且唯一幸存
        y_surv = ({"000017", "600001", "600002"}, {"000017"})
    base: dict[str, Any] = {
        "sole": sole, "window": window,
        "min_days": entry.MIN_LEADER_DAYS if min_days is None else min_days,
        "diverge_min": entry.DIVERGE_MIN_TURNOVER, "y_comp": y_comp,
        "y_surv": y_surv, "rows": [cand()],
    }
    base.update(extra)
    return base


@dataclass(frozen=True)
class RichCand:
    """带质量列的候选（= lkl LadderRow 形状）：验证 _field 的「读候选属性」回退路径。"""
    date: date
    code: str
    cont_days: int
    is_exchange: bool
    name: str = "测试龙"
    turnover_rate: float | None = 8.0
    bomb_times: int | None = 0


# ────────────────────────── 五条件（纯判定，直接喂 ctx） ──────────────────────────

class TestConditions:
    def test_all_pass_standard(self):
        rows = [fn(cand(), ctx()) for fn, _, _ in entry._CONDITIONS]
        assert all(ok for ok, _ in rows), rows

    def test_c1_rejects_non_sole(self):
        ok, _ = entry.c1_uniqueness(cand(), ctx(sole_code="000001"))
        assert not ok
        ok, _ = entry.c1_uniqueness(cand(), ctx(sole_code=None))
        assert not ok

    def test_c2_rejects_one_word(self):
        ok, note = entry.c2_exchange(cand(ex=False), ctx())
        assert not ok and "一字" in note

    def test_c3_requires_competition_and_single_survivor(self):
        assert not entry.c3_elimination(cand(), ctx(y_comp=(1, 1)))[0]   # 昨日无竞争
        assert not entry.c3_elimination(cand(), ctx(y_comp=(3, 2)))[0]   # 双幸存
        assert entry.c3_elimination(cand(), ctx(y_comp=(3, 1)))[0]

    def test_c3_rejects_newcomer_not_in_yesterday_group(self):
        """P1-2：新插队高标（不在昨日最高组）必须被拒。"""
        newcomer = ({"600001", "600002", "600003"}, {"600001"})
        ok, note = entry.c3_elimination(cand(), ctx(y_surv=newcomer))
        assert not ok and "不在昨日最高组" in note

    def test_c3_rejects_when_survivor_is_other_stock(self):
        """P1-2：昨日组唯一幸存者另有其票（候选已被淘汰）必须被拒（集合等值判定）。"""
        other = ({"000017", "600001", "600002"}, {"600001"})
        ok, note = entry.c3_elimination(cand(), ctx(y_surv=other))
        assert not ok and "另有其票" in note

    def test_c3_r2_switch_off(self, monkeypatch):
        monkeypatch.setattr(entry, "REQUIRE_YESTERDAY_COMPETITION", False)
        ok, note = entry.c3_elimination(cand(), ctx(y_comp=(1, 1)))
        assert ok and "R2 关闭" in note

    def test_c4_min_days(self):
        assert not entry.c4_min_days(cand(cont=3), ctx())[0]
        assert entry.c4_min_days(cand(cont=4), ctx())[0]
        assert entry.c4_min_days(cand(cont=3), ctx(min_days=3))[0]   # R1 参数矩阵

    def test_c5_only_in_enhanced(self):
        assert entry.c5_strength_diverge(cand(), ctx(window="STANDARD"))[0]
        assert not entry.c5_strength_diverge(
            cand(), ctx(window="ENHANCED", turnover_rate=2.0))[0]
        assert entry.c5_strength_diverge(
            cand(), ctx(window="ENHANCED", turnover_rate=2.0, bomb_times=3))[0]

    def test_c5_secondary_threshold(self):
        """次级放宽：3.5% 过、主版 5.0% 不过——阈值来自 ctx 而非全局 config。"""
        low = ctx(window="ENHANCED", turnover_rate=4.0)
        assert not entry.c5_strength_diverge(cand(), low)[0]
        assert entry.c5_strength_diverge(
            cand(), {**low, "diverge_min": entry.SECONDARY_DIVERGE_MIN_TURNOVER})[0]

    def test_c5_missing_turnover_is_unknown_pass(self):
        """F6/A8：换手率缺失 ≠ 0——警告不否决，说明写清缺口。"""
        ok, note = entry.c5_strength_diverge(cand(), ctx(window="ENHANCED"))
        assert ok is True and "换手率缺失" in note

    def test_c5_outside_pool_window_is_unknown(self):
        """V4④a：EM 池窗口外（不可核验）→ None，不参与 passed。"""
        ok, note = entry.c5_strength_diverge(
            cand(), ctx(window="ENHANCED", c5_verifiable=False))
        assert ok is None and "不可核验" in note

    def test_c5_reads_candidate_quality_columns(self):
        """lkl 路径：候选自带质量列（turnover_rate/bomb_times）时按属性判定。"""
        rich = RichCand(date=D, code="000017", cont_days=5, is_exchange=True,
                        turnover_rate=8.0)
        assert entry.c5_strength_diverge(rich, ctx(window="ENHANCED"))[0]
        weak = RichCand(date=D, code="000017", cont_days=5, is_exchange=True,
                        turnover_rate=2.0)
        assert not entry.c5_strength_diverge(weak, ctx(window="ENHANCED"))[0]
        bombed = RichCand(date=D, code="000017", cont_days=5, is_exchange=True,
                          turnover_rate=2.0, bomb_times=2)
        assert entry.c5_strength_diverge(bombed, ctx(window="ENHANCED"))[0]

    def test_c1_note_uses_candidate_name(self):
        rich = RichCand(date=D, code="000017", cont_days=5, is_exchange=True)
        ok, note = entry.c1_uniqueness(rich, ctx())
        assert ok and "测试龙(000017) 5板" in note

    def test_c3_secondary_requires_yesterday_peer_competition(self):
        ok, note = entry._c3_secondary(cand(cont=3), ctx(min_days=3, y_peer=2))
        assert ok and "次级降档" in note
        assert not entry._c3_secondary(cand(cont=3), ctx(min_days=3, y_peer=1))[0]

    def test_c3_loose_accepts_multi_survivor_with_candidate_inside(self):
        """R58-4：c3_loose——候选在昨日组幸存集里（不要求唯一幸存）。"""
        # 昨日 3 只组，今日 2 只幸存；候选在幸存集里 → 过
        surv = ({"000017", "600001", "600002"}, {"000017", "600001"})
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(3, 2), y_surv=surv))
        assert ok and "不要求唯一幸存" in note

    def test_c3_loose_rejects_no_competition(self):
        """R58-4：昨日组只 1 只无竞争 → 拒（与主版 c3 同口径）。"""
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(1, 1)))
        assert not ok and "≥2竞争" in note

    def test_c3_loose_rejects_no_survivor(self):
        """R58-4：昨日组≥2 但今日 0 幸存 → 拒（无胜出者）。"""
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(3, 0)))
        assert not ok and "≥1幸存" in note

    def test_c3_loose_rejects_newcomer_not_in_yesterday_group(self):
        """R58-4：候选不在昨日最高组（新插队）→ 拒，与主版 c3 同。"""
        new = ({"600001", "600002", "600003"}, {"600001"})
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(3, 1), y_surv=new))
        assert not ok and "不在昨日最高组" in note

    def test_c3_loose_rejects_candidate_not_in_survivors(self):
        """R58-4：候选在昨日组但不在幸存集（昨日组里有其它票今日仍换手）→ 拒。"""
        # 候选在昨日组，但今日幸存者是另一只 → 候选被淘汰
        sit = ({"000017", "600001", "600002"}, {"600001"})
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(3, 1), y_surv=sit))
        assert not ok and "候选不在昨日组幸存者中" in note

    def test_c3_loose_r2_switch_off(self, monkeypatch):
        """R58-4：R2 关闭时不要求昨日竞争。"""
        monkeypatch.setattr(entry, "REQUIRE_YESTERDAY_COMPETITION", False)
        ok, note = entry.c3_loose(cand(), ctx(y_comp=(1, 1)))
        assert ok and "R2 关闭" in note

    def test_c3_loose_distinguishes_from_main(self):
        """R58-4：c3_loose 与 c3_elimination 的关键差异——双幸存时主版拒、宽松版过。"""
        sit = ({"000017", "600001", "600002"}, {"000017", "600001"})
        assert not entry.c3_elimination(cand(), ctx(y_comp=(3, 2), y_surv=sit))[0]
        assert entry.c3_loose(cand(), ctx(y_comp=(3, 2), y_surv=sit))[0]

    def test_recommend_conditions_uses_loose_c3(self):
        """R58-4：_RECOMMEND_CONDITIONS 的 c3 是 c3_loose，不是 c3_elimination。"""
        fns = [fn for fn, _, _ in entry._RECOMMEND_CONDITIONS]
        assert entry.c3_loose in fns
        assert entry.c3_elimination not in fns

    def test_w1_crowding_warns_but_never_vetoes(self):
        """警告恒 True（信息在说明里）：按值聚合的任何调用方都不会被警告否决。"""
        crowded = ctx(rows=[cand(cont=5), cand(cont=5, code="600001"),
                            cand(cont=3, code="600002")])
        ok, note = entry.w1_crowding(cand(), crowded)
        assert ok is True and "⚠" in note and "5板 2 只" in note
        ok2, note2 = entry.w1_crowding(
            cand(), ctx(rows=[cand(cont=5), cand(cont=2, code="600001")]))
        assert ok2 is True and "无同身位" in note2
        assert entry.w1_crowding(cand(), ctx(rows=[])) == (True, "无同身位扎堆")


# ────────────────────────── 聚合器 / 结构分离 ──────────────────────────

class TestAggregators:
    def test_passed_of_filters_unknown(self):
        """UNKNOWN(None) 不否决；任一条件 False 即不过。"""
        cl = Checklist(True, None, True, True, None, w1_crowding=False)
        assert passed_of(cl) is True
        assert passed_of(Checklist(True, True, False, True, True, None)) is False

    def test_split_checklist_separates_warning(self):
        cl = Checklist(True, True, True, True, True, w1_crowding=True)
        conds, warns = split_checklist(cl)
        assert isinstance(warns, entry.WarningOnly)
        assert conds.w1_crowding is None and warns.w1_crowding is True
        assert conds == Checklist(True, True, True, True, True, None)

    def test_warning_never_vetoes(self):
        """V4 结构化：警告即使为 False 也无否决权——锁定「警告不否决」语义。"""
        cl = Checklist(True, True, True, True, True, w1_crowding=False)
        conds, warns = split_checklist(cl)
        assert warns.w1_crowding is False and passed_of(conds) is True


# ────────────────────────── 组合入口（桩掉全部 IO） ──────────────────────────

def patch_io(monkeypatch, *, today: list[LadderDay], prev: list[LadderDay],
             window: str | None = "STANDARD", survivors: set[str] | None = None,
             quality: dict[str, Any] | None = None) -> dict[str, Any]:
    """桩掉 entry 的全部 IO：ladder.build / y_survivors / buy_window 读 / 质量预取 / 事务。

    返回记录器：{"sql": [(sql, params)]}——落库语句与参数可直接断言。
    """
    calls: dict[str, Any] = {"sql": []}
    monkeypatch.setattr(entry.ladder, "build",
                        lambda d: list(today) if d == D else list(prev))
    monkeypatch.setattr(entry.ladder, "y_survivors",
                        lambda d: {"000017"} if survivors is None else set(survivors))
    monkeypatch.setattr(entry, "prev_trading_day", lambda d: PREV)
    monkeypatch.setattr(entry, "today_sh", lambda: D)
    monkeypatch.setattr(entry, "_candidate_view", lambda c: dict(quality or {}))
    monkeypatch.setattr(entry, "_read_window", lambda d: window)

    @contextmanager
    def fake_transaction() -> Iterator[Any]:
        class _Conn:
            def execute(self, sql: str, params: tuple) -> None:
                calls["sql"].append((sql, params))
        yield _Conn()

    monkeypatch.setattr(entry, "transaction", fake_transaction)
    return calls


def _prev_rows() -> list[LadderDay]:
    """昨日：4 板换手组 3 只（y_codes）+ 2 板换手 2 只（次级 c3 竞争证据）。"""
    return ([LadderDay(PREV, c, 4, True, True, False, 0, 0)
             for c in ("600001", "600002", "600003")]
            + [LadderDay(PREV, "600004", 2, True, False, False, 0, 0),
               LadderDay(PREV, "600005", 2, True, False, False, 0, 0)])


class TestCurrentWindow:
    def test_missing_row_is_none(self, monkeypatch):
        patch_io(monkeypatch, today=[], prev=[], window=None)
        assert current_window(D) == "NONE"

    def test_null_or_empty_normalizes_to_none(self, monkeypatch):
        patch_io(monkeypatch, today=[], prev=[], window="")
        assert current_window(D) == "NONE"

    def test_passthrough_window_values(self, monkeypatch):
        patch_io(monkeypatch, today=[], prev=[], window="ENHANCED")
        assert current_window(D) == "ENHANCED"
        patch_io(monkeypatch, today=[], prev=[], window="STANDARD")
        assert current_window(D) == "STANDARD"


class TestCheckSignal:
    def test_all_pass_persists_buy(self, monkeypatch):
        calls = patch_io(monkeypatch, today=[cand(cont=4, code="600001")],
                         prev=_prev_rows(), window="STANDARD", survivors={"600001"})
        sig = check_signal(D)
        assert sig is not None
        assert (sig.code, sig.date, sig.action, sig.status) == \
            ("600001", D, Action.BUY, "SUGGESTED")
        assert sig.source is SignalSource.LIVE
        assert sig.checklist.passed is True
        sql, params = calls["sql"][0]
        assert "ON CONFLICT (confirm_date, code, action)" in sql
        assert params[:4] == (D, "600001", "BUY", "STANDARD")
        rows = json.loads(params[4])
        assert [r[0] for r in rows] == ["c1 唯一换手高标", "c2 换手板非一字",
                                        "c3 淘汰赛身份", "c4 最低板数门槛",
                                        "c5 强度与分歧补偿", "W1 同身位扎堆"]
        assert [r[1] for r in rows] == [True] * 6      # W1 恒 True（说明里写扎堆与否）
        # §9 第 10/7 条：落库带 source 与 config_hash，且 source 保留首个来源
        assert "source" in sql and "config_hash" in sql
        assert params[5] == entry.CONFIG.STRATEGY_VERSION
        assert params[6] == "live"
        assert params[7] == config_hash() and len(params[7]) == 12
        assert "COALESCE(signal.source, EXCLUDED.source)" in sql

    def test_source_marked_replay(self, monkeypatch):
        calls = patch_io(monkeypatch, today=[cand(cont=4, code="600001")],
                         prev=_prev_rows(), survivors={"600001"})
        sig = check_signal(D, SignalSource.REPLAY)
        assert sig is not None and sig.source is SignalSource.REPLAY
        assert calls["sql"][0][1][6] == "replay", "回填信号必须在库内标 replay"

    def test_none_window_blocks_buy_and_tries_secondary(self, monkeypatch):
        """V14.1：禁买日主 BUY 恒不落库，但仍尝试次级（接线存在）。"""
        tried: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="600001")], prev=_prev_rows(),
                 window="NONE", survivors={"600001"})
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: tried.append(d) or None)
        assert check_signal(D) is None
        assert tried == [D]

    def test_no_candidate_tries_secondary(self, monkeypatch):
        tried: list[date] = []
        patch_io(monkeypatch, today=[], prev=_prev_rows())
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: tried.append(d) or None)
        assert check_signal(D) is None and tried == [D]

    def test_secondary_skipped_when_main_signal_persisted(self, monkeypatch):
        """主通过 → 不再产次级（避免同一 confirm_date 同 code 双 action）。"""
        tried: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="600001")], prev=_prev_rows(),
                 survivors={"600001"})
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: tried.append(d) or None)
        assert check_signal(D) is not None and tried == []

    def test_main_rejected_falls_through_to_secondary(self, monkeypatch):
        """主未全过（c3：候选不在昨日最高组）→ 尝试次级。"""
        tried: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="000017")], prev=_prev_rows(),
                 survivors={"000017"})
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: tried.append(d) or None)
        assert check_signal(D) is None
        assert tried == [D]


class TestCheckRecommendSignal:
    """R58-4：次级推荐档（RECOMMEND）的链路与落库。"""

    def test_generated_on_none_window(self, monkeypatch):
        """禁买日 RECOMMEND 仍生成观察记录（不导出）。"""
        # 用 600001 候选——它在 _prev_rows() 默认 4 板组里，c3 才能通过
        calls = patch_io(monkeypatch, today=[cand(cont=4, code="600001")],
                         prev=_prev_rows(), window="NONE",
                         survivors={"600001"})
        sig = entry.check_recommend_signal(D)
        assert sig is not None and sig.action is Action.RECOMMEND
        sql, params = calls["sql"][0]
        assert params[:4] == (D, "600001", "RECOMMEND", "NONE")

    def test_dual_survivor_passes_when_main_fails(self, monkeypatch):
        """R58-4：双幸存场景主版 c3 拒、c3_loose 过 → RECOMMEND 落库。"""
        calls = patch_io(monkeypatch,
                         today=[cand(cont=4, code="600001")],
                         prev=_prev_rows(),
                         survivors={"600001", "600002"})   # 2 个幸存
        sig = entry.check_recommend_signal(D)
        # c3_loose 接受双幸存 + 候选 ∈ 幸存集 → 通过
        assert sig is not None and sig.action is Action.RECOMMEND
        sql, params = calls["sql"][0]
        assert params[:4] == (D, "600001", "RECOMMEND", "STANDARD")

    def test_signal_carries_name_and_cont_days(self, monkeypatch):
        """R58-4：Signal 内存字段 name/cont_days 在 RECOMMEND 也填充。"""
        # 600001 是 _prev_rows 默认组里的候选 → c3 过
        calls = patch_io(monkeypatch, today=[cand(cont=4, code="600001")],
                         prev=_prev_rows(), survivors={"600001"})
        # patch_io 已经把 _candidate_view 桩成空 dict；这里**再**覆写一次带 name
        monkeypatch.setattr(entry, "_candidate_view",
                            lambda c: {"name": "测试龙", "turnover_rate": 8.0})
        sig = entry.check_recommend_signal(D)
        assert sig is not None
        assert sig.name == "测试龙"
        assert sig.cont_days == 4   # cand(cont=4) 的 cont_days

    def test_uses_min_leader_days_not_secondary(self, monkeypatch):
        """R58-4：RECOMMEND 候选取 ladder.sole_top(MIN_LEADER_DAYS)，不降档。"""
        called_with: list[int] = []
        original = entry.ladder.sole_top

        def spy(rows, min_days=None):
            called_with.append(min_days)
            return original(rows, min_days)
        monkeypatch.setattr(entry.ladder, "sole_top", spy)
        # 600001 在 _prev_rows 里 → c3 过
        patch_io(monkeypatch, today=[cand(cont=4, code="600001")],
                 prev=_prev_rows(), survivors={"600001"})
        sig = entry.check_recommend_signal(D)
        assert sig is not None
        # sole_top 收到的 min_days 应该是 MIN_LEADER_DAYS(4)，不是 SECONDARY_MIN_LEADER_DAYS(3)
        assert entry.MIN_LEADER_DAYS in called_with
        assert entry.SECONDARY_MIN_LEADER_DAYS not in called_with


class TestChainOrder:
    """R58-4：BUY → RECOMMEND → SECONDARY 的链路优先级。"""

    def test_buy_persists_skips_recommend_and_secondary(self, monkeypatch):
        """主 BUY 通过 → RECOMMEND/SECONDARY 都不跑。"""
        rec_called: list[date] = []
        sec_called: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="600001")], prev=_prev_rows(),
                 survivors={"600001"})
        monkeypatch.setattr(entry, "check_recommend_signal",
                            lambda d, source=None: rec_called.append(d) or None)
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: sec_called.append(d) or None)
        sig = check_signal(D)
        assert sig is not None and sig.action is Action.BUY
        assert rec_called == [] and sec_called == []

    def test_buy_fails_recommend_passes_returns_recommend(self, monkeypatch):
        """主 BUY 未过 → RECOMMEND 通过 → 返回 RECOMMEND（不再尝试 SECONDARY）。"""
        sec_called: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="000017")], prev=_prev_rows(),
                 survivors={"000017", "600001"})  # 双幸存 → 主版 c3 拒
        # RECOMMEND 用宽松 c3：构造一个 mock 替 _evaluate 在 RECOMMEND 路径里返回过
        rec_pass = entry.Checklist(c1_uniqueness=True, c2_exchange=True,
                                    c3_elimination=True, c4_min_days=True,
                                    c5_strength_diverge=True, w1_crowding=True)
        rec_rows = [(lbl, True, "ok") for fn, _, lbl in entry._RECOMMEND_CONDITIONS] + \
                   [("W1 同身位扎堆", True, "无同身位扎堆")]
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: sec_called.append(d) or None)
        # patch _evaluate: 主版(主评估路径) 走原逻辑失败，RECOMMEND 路径走宽松版通过
        original = entry._evaluate
        def fake_evaluate(cand, window, **kw):
            conds = kw.get("conditions", entry._CONDITIONS)
            if conds is entry._RECOMMEND_CONDITIONS:
                return rec_pass, rec_rows
            return original(cand, window, **kw)
        monkeypatch.setattr(entry, "_evaluate", fake_evaluate)
        sig = check_signal(D)
        assert sig is not None and sig.action is Action.RECOMMEND
        # RECOMMEND 通过时 SECONDARY 不应被调用
        assert sec_called == []

    def test_buy_and_recommend_both_fail_falls_through_to_secondary(self, monkeypatch):
        """BUY 失败 + RECOMMEND 也失败 → 落 SECONDARY。"""
        sec_called: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=3, code="000017")], prev=_prev_rows())
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: sec_called.append(d) or None)
        sig = check_signal(D)
        assert sig is None
        assert sec_called == [D]

    def test_none_window_tries_recommend_and_secondary(self, monkeypatch):
        """禁买日 → RECOMMEND 与 SECONDARY 都尝试（沿用 V14.1 观察纪律）。"""
        rec_called: list[date] = []
        sec_called: list[date] = []
        patch_io(monkeypatch, today=[cand(cont=4, code="000017")], prev=_prev_rows(),
                 window="NONE", survivors={"000017"})
        monkeypatch.setattr(entry, "check_recommend_signal",
                            lambda d, source=None: rec_called.append(d) or None)
        monkeypatch.setattr(entry, "check_secondary_signal",
                            lambda d, source=None: sec_called.append(d) or None)
        assert check_signal(D) is None
        assert rec_called == [D] and sec_called == [D]


class TestCheckSecondarySignal:
    def test_generated_on_none_window(self, monkeypatch):
        """V14.1（奎爷 2026-09-09 拍板）：禁买日次级仍生成观察记录，
        buy_window 原样存 NONE——不伪装成可执行窗口。"""
        calls = patch_io(monkeypatch, today=[cand(cont=3, code="000017")],
                         prev=_prev_rows(), window="NONE")
        sig = check_secondary_signal(D)
        assert sig is not None and sig.action is Action.SECONDARY
        sql, params = calls["sql"][0]
        assert params[:4] == (D, "000017", "SECONDARY", "NONE")
        rows = json.loads(params[4])
        assert rows[2][0] == "c3 淘汰赛身份" and "次级降档" in rows[2][2]
        assert [r[1] for r in rows] == [True] * 6

    def test_relaxed_thresholds_used(self, monkeypatch):
        """次级门槛：3 板过 c4、ENHANCED 下 4.0% 换手过 c5（主版 5.0% 不过）。"""
        calls = patch_io(monkeypatch, today=[cand(cont=3, code="000017")],
                         prev=_prev_rows(), window="ENHANCED",
                         quality={"turnover_rate": 4.0, "bomb_times": 0})
        cl = checklist_secondary(cand(cont=3, code="000017"), "ENHANCED")
        assert cl.c4_min_days is True and cl.c5_strength_diverge is True
        sig = check_secondary_signal(D)
        assert sig is not None and calls["sql"][0][1][3] == "ENHANCED"

    def test_skipped_without_secondary_candidate(self, monkeypatch):
        calls = patch_io(monkeypatch, today=[cand(cont=2, code="000017")],
                         prev=_prev_rows(), window="STANDARD")
        assert check_secondary_signal(D) is None and calls["sql"] == []

    def test_missing_turnover_passes_with_warning(self, monkeypatch):
        """换手率缺失（F6 不补零）：c5 不确定 → 不否决，信号仍生成。"""
        calls = patch_io(monkeypatch, today=[cand(cont=3, code="000017")],
                         prev=_prev_rows(), window="ENHANCED")
        sig = check_secondary_signal(D)
        assert sig is not None and sig.checklist.c5_strength_diverge is True
        assert "换手率缺失" in json.loads(calls["sql"][0][1][4])[4][2]


class TestChecklistProjection:
    def test_checklist_projects_rows_into_contract(self, monkeypatch):
        patch_io(monkeypatch, today=[cand(cont=4, code="600001")], prev=_prev_rows(),
                 survivors={"600001"})
        cl = checklist(cand(cont=4, code="600001"), "STANDARD")
        assert cl == Checklist(True, True, True, True, True, True)
        assert cl.passed is True

    def test_min_days_override_flows_into_projection(self, monkeypatch):
        patch_io(monkeypatch, today=[cand(cont=3, code="000017")], prev=_prev_rows())
        assert checklist(cand(cont=3), "STANDARD").c4_min_days is False
        assert checklist(cand(cont=3), "STANDARD", min_days=3).c4_min_days is True

    def test_as_of_keeps_replay_reproducible(self, monkeypatch):
        """P1-3：as_of 显式化——EM 池窗口外 c5 记 UNKNOWN，30 天后重跑同结论。"""
        patch_io(monkeypatch, today=[cand(cont=4, code="000017")], prev=_prev_rows())
        far = date(2026, 12, 31)
        assert checklist(cand(cont=4), "ENHANCED", as_of=far).c5_strength_diverge is None
        assert checklist(cand(cont=4), "ENHANCED",
                         as_of=D).c5_strength_diverge is True
