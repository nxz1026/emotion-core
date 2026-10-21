"""盘中触发判据（ADR 0003 D5）。纯函数，零 IO。

## 这个模块是什么

ADR 0003 定的分工：**原始采集归 CPT**（`cpt.adapters.a_share_public.SinaQuoteClient.fetch_quotes`
已经在产），**触发判据归本仓**。这个文件就是「判据」那半边——从 E3 契约形状的
quote 字典里，判断「这只票此刻是不是封了首板」。

它**不连网、不碰库、不读 `CONFIG`**。输入是纯数据，输出是纯数据。
这样 D10 条件 ①（CPT 稳定）没满足之前，判据本身可以先做对、先测完。

## 为什么常量是模块级 `Final` 而不是 `utils/config.py`

`config_hash()` 是策略指纹，钉在 `pipeline_state` / `signal` / `eval_result` 上。
盘中判据是**新增能力**，不是对现有策略的修改。把它的常量塞进 `CONFIG` 会让
所有历史行的指纹换代，等于宣称「策略变了」——而策略没变。
ADR D3 已经这么定了，这里照办。

## 四条口径（每条都是被真实数据教出来的）

1. **`>=` 不是 `>`**：涨停价是 `round(pre_close * (1 + pct), 2)`，浮点乘完常是
   `10.999999`。用 `>` 会把真封板漏掉。CPT 侧同样用 `>=`。
2. **`None` 不参与比较**（F6，与 `state.py` 同精神）：停牌时新浪把价量一起填 0
   → 整档 `None`。把 `None` 当 `0` 会判成「买一量不足」，把「没数据」说成
   「封单太小」——两件事完全不同。
3. **`chg_pct >= 9.7` **只用来筛候选**，不用来判涨停**。CPT 的
   `cpt/domain/a_share_rules.py` 模块头写明：「±10% 阈值不可靠（ST/科创/创业/北交
   规则不同），直接读 `public.derived_bar.is_limit_up`」。盘中拿不到 derived_bar，
   所以这里**按代码算涨跌停价**（`limit_price`），9.7 只是拉榜单时省请求。
   两者混用会让 20% 板（创业板/科创板）永远漏判。
4. **连续 N 轮确认**：单帧可能是撤单前的一瞬间。`SEAL_CONFIRM_TICKS = 2` 要求
   同一个 `(code, 当日)` 连续两轮都成立。状态由调用方持有（本模块无状态），
   用 `SealTracker` 承载。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any, Final

from emotion_core.utils import price as price_util

__all__ = [
    "CHG_PCT_MIN",
    "EARLY_SEAL_DEADLINE",
    "MAX_WATCH_CODES",
    "SCAN_INTERVAL_SEC",
    "SEAL_BID1_VOL_MIN",
    "SEAL_CONFIRM_TICKS",
    "SEAL_WEAKEN_RATIO",
    "SealTracker",
    "board_pct_limit",
    "chg_pct",
    "is_at_limit",
    "is_main_board",
    "is_st_name",
    "limit_price",
    "seal_hit",
    "seal_weaken",
]

# ───────────────────────── ADR 0003 D3 常量 ─────────────────────────
#
# 改任一常量**必须**同步改 ADR 0003 的 D3 表格 —— 数字即口径。

#: 扫描间隔（秒）。新浪/腾讯批量接口的容忍度上限；更密无收益。
SCAN_INTERVAL_SEC: Final[int] = 30

#: 候选筛选阈值（百分比）。**只用于拉榜单**，不用来判涨停 —— 见模块 docstring 第 3 条。
#: 10% 板取 9.7 留出计算误差；20%/30% 板另判（`board_pct_limit`）。
CHG_PCT_MIN: Final[float] = 9.7

#: 早封截止时间。C3 实测：≤10:00 封板的首板是唯一接近盈亏平衡的片段
#: （封住率 65.6%、组合 −0.38%，只差 5.4pp）。
EARLY_SEAL_DEADLINE: Final[time] = time(10, 0)

#: 买一挂单量门槛。**单位是手**（1 手 = 100 股），与新浪原始字段一致。
#: 初值取「海洋王」实测 19.6 万手的一档门槛，待样本调。
SEAL_BID1_VOL_MIN: Final[int] = 30_000

#: 连续多少轮成立才算命中。抗撤单抖动 —— 单帧可能是撤单前的一瞬间。
SEAL_CONFIRM_TICKS: Final[int] = 2

#: 买一量衰减到这个比例以下视为「松动」（炸板前兆）。
SEAL_WEAKEN_RATIO: Final[float] = 0.30

#: 单轮最多看多少只。限制五档请求数（新浪 50 只/请求，40 只 = 1 次）。
MAX_WATCH_CODES: Final[int] = 40

#: 一手多少股。新浪的 volume 字段单位就是股，门槛用「手」表达，比较时要乘回来。
_SHARES_PER_LOT: Final[int] = 100

#: 各板块涨跌幅（百分比）。**按代码前缀判，不是按 ±10% 一刀切**。
#: 主板 10；创业板 300/301 与科创板 688/689 是 20；北交所 8xx/4xx 是 30；
#: ST 股在主板是 5（**但 ST 的判定要靠名字，不能只看代码** —— 见 `is_st_name`）。
_PCT_LIMIT_MAIN: Final[float] = 10.0
_PCT_LIMIT_GROWTH: Final[float] = 20.0
_PCT_LIMIT_BSE: Final[float] = 30.0
_PCT_LIMIT_ST: Final[float] = 5.0

#: 主板前缀（沪深两市）。CPT 的 `a_share_snapshot.py` 用同一组（1702 只）。
_MAIN_PREFIXES: Final[tuple[str, ...]] = (
    "600",
    "601",
    "603",
    "605",  # 沪市主板
    "000",
    "001",
    "002",
    "003",  # 深市主板 / 中小板（002 已并入主板）
)


def board_pct_limit(code: str, *, name: str | None = None) -> float:
    """这只票的涨跌停幅度（百分比）。

    Args:
        code: 6 位纯代码（E3 契约）。
        name: 可选。**判断 ST 必须要它** —— ST 只看代码分不出来，
            而 ST 在主板是 5% 而不是 10%。不传就按「非 ST」算，
            这是**有意的乐观**：漏判 ST 会得出偏高的涨停价（判不出封板），
            而不是偏低的（假封板）。宁可少报不可错报。

    Returns:
        `10.0` / `20.0` / `30.0` / `5.0`。
    """
    code = code.strip()
    if name is not None and is_st_name(name):
        # ST 判定优先：*ST 在创业板也是 20%，但主板 ST 是 5%。
        # 这里保守按 5%，因为 ST 里绝大多数在主板；真要精确得查板块。
        return _PCT_LIMIT_ST if code[:3] in _MAIN_PREFIXES else _PCT_LIMIT_GROWTH
    if code.startswith(("300", "301")) or code.startswith(("688", "689")):
        return _PCT_LIMIT_GROWTH
    # 920 是北交所 2026 年启用的 6 位统一代码号段，**必须先判** ——
    # 它既不以 8/4 开头，也不在主板前缀里，漏掉会掉进兜底按 10% 算。
    # 实测：920296 N文峰 昨收 11.45，10% 涨停价 12.60、30% 是 14.89。
    if code.startswith(("920", "8", "4")):
        return _PCT_LIMIT_BSE
    return _PCT_LIMIT_MAIN


def is_st_name(name: str | None) -> bool:
    """名字里带 ST 就是 ST。**只能靠名字** —— 代码里没有任何 ST 信息。"""
    if not name:
        return False
    return "ST" in name.upper()


def is_main_board(code: str) -> bool:
    """是不是主板。C3 的切片只做主板首板（创业板/科创板 20% 板没跑样本）。"""
    return code.strip().startswith(_MAIN_PREFIXES)


def limit_price(code: str, pre_close: float, *, name: str | None = None) -> float | None:
    """涨停价（元）。**公式唯一实现在 `utils/price.py`**，这里只补 ST 的幅度。

    为什么不能在这里自己写一遍：`tests/caliber/test_price_unique_impl.py` 有源码级
    门禁——涨停价只许有一个实现（第二套公式出错时没人比对，2026-10-07 就吃过一次
    北交所 920 号段漏判的亏）。这里唯一多出来的信息是「ST 靠名字、主板 5%」，
    所以走 `pct_milli=` 覆盖，不复制四舍五入逻辑。

    Returns:
        涨停价；`pre_close <= 0` 或不可用时返回 `None`（停牌/无昨收）。
    """
    if pre_close is None or pre_close <= 0:
        return None
    pct_milli = int(round(board_pct_limit(code, name=name) * 10))
    cents = price_util.limit_up_price_cents(
        int(round(pre_close * 100)), code, pct_milli=pct_milli)
    return cents / 100


def chg_pct(quote: dict[str, Any]) -> float | None:
    """相对昨收的涨幅（百分比）。

    Returns:
        涨幅；昨收缺失或 ≤0 时返回 `None`（**不给 0.0** —— 「没有数据」与
        「平盘」是两件事，F6）。
    """
    pre_close = _num(quote.get("prev_close"))
    last = _num(quote.get("last"))
    if pre_close is None or pre_close <= 0 or last is None:
        return None
    return (last / pre_close - 1.0) * 100.0


def is_at_limit(quote: dict[str, Any], *, code: str | None = None) -> bool | None:
    """此刻是不是涨停。

    Args:
        quote: E3 形状的 quote 字典（`last` / `prev_close` / `name`）。
        code: 6 位代码。不给就用 `quote["code"]` 去掉市场前缀后的 6 位。

    Returns:
        `True` 封板价、`False` 没到、`None` **判不了**（缺昨收/缺最新价）。
        `None` 与 `False` **必须分开** —— 停牌是 `None`，没涨停是 `False`，
        混起来会让「今天没有一只封板」和「今天没采到数据」变成同一件事。
    """
    code = code or _code6(quote)
    pre_close = _num(quote.get("prev_close"))
    last = _num(quote.get("last"))
    limit = limit_price(code, pre_close if pre_close else 0.0, name=quote.get("name"))
    if limit is None or last is None:
        return None
    # >= 而不是 >：涨停价是四舍五入到分的，实盘成交价可以正好等于它。
    return last + 1e-9 >= limit


def seal_hit(
    quote: dict[str, Any],
    prev_days: int | None,
    now: time,
    *,
    code: str | None = None,
) -> bool:
    """单帧判据：这一轮算不算「封住首板」。

    四条全满足才算：

    1. 处在涨停价（`is_at_limit` 为 `True`；`None` 不算）。
    2. `prev_days == 0` —— **首板**。`prev_days` 为 `None` 时**不成立**：
       不知道昨天是不是板，就不能说今天是首板（ADR C3 只对首板做过统计）。
    3. `now <= EARLY_SEAL_DEADLINE` —— 早封。C3 的切片就是这一条。
    4. 买一量 `>= SEAL_BID1_VOL_MIN` 手（按股比：`SEAL_BID1_VOL_MIN * 100`）。

    注意第 4 条读的是 `bid1_vol`，**不是** `bids[0]["volume"]`——两者在 E3 契约里
    都有且相等，但扁平键在停牌时明确为 `None`，档位数组可能整段缺失。

    Returns:
        单帧是否成立。真正的触发还要过 `SealTracker` 的连续确认。
    """
    if now > EARLY_SEAL_DEADLINE:
        return False
    if is_at_limit(quote, code=code) is not True:
        return False
    if prev_days != 0:
        # None（不知道）与 >=1（连板）都不算首板
        return False
    bid1_vol = _num(quote.get("bid1_vol"))
    if bid1_vol is None:
        return False
    return bid1_vol >= SEAL_BID1_VOL_MIN * _SHARES_PER_LOT


def seal_weaken(quote: dict[str, Any], seal_bid1_vol: float, *, code: str | None = None) -> bool:
    """封板松动了没有（炸板前兆）。

    两个条件任一成立即松动：

    1. 价格跌破涨停价（`is_at_limit` 为 `False`）—— 炸板实锤。
    2. 买一量衰减到封板时 `SEAL_WEAKEN_RATIO` 以下 —— 撤单在发生。

    数据缺失（`is_at_limit` 为 `None`、封板量没记）时返回 `False`：
    「不知道」不该触发「松动」告警，否则停牌会天天误报。

    Args:
        quote: 当前帧。
        seal_bid1_vol: **触发那一刻**记下的买一量（股）。用它作基准，
            不是用上一帧 —— 逐帧比较会把自己滚成 0。
    """
    state = is_at_limit(quote, code=code)
    if state is False:
        return True
    if state is None:
        return False
    current = _num(quote.get("bid1_vol"))
    if current is None or seal_bid1_vol <= 0:
        return False
    return current <= seal_bid1_vol * SEAL_WEAKEN_RATIO


@dataclass
class SealTracker:
    """连续 N 轮确认的状态载体。**本模块唯一有状态的东西**。

    为什么状态不放进 `seal_hit`：判据要可重放、可单测。把「上一帧看到什么」
    塞进纯函数，就再也无法在同一个输入上得到同一个结果。调用方持有 tracker，
    纯函数只回答「此刻这一帧」。

    ⚠️ 按 `(code)` 分桶，**不跨日**：调用方换日时调用 `reset()`（或新建一个）。
    不自动按日期分桶，是因为 `date` 只出现在 quote 里、可能缺失 —— 让调用方
    显式换日，比在这里猜「字符串是不是今天」更可靠。
    """

    need: int = SEAL_CONFIRM_TICKS
    _streak: dict[str, int] = field(default_factory=dict)
    _sealed: dict[str, float] = field(default_factory=dict)

    def feed(
        self, quote: dict[str, Any], *, prev_days: int | None, now: time, code: str | None = None
    ) -> bool:
        """喂一帧，返回「这一帧是否让连续计数达标并首次触发」。

        Returns:
            `True` 仅在这一帧**第一次**达标时。之后继续喂同一只票返回 `False`
            （已经触发过了，不该每 30 秒重复推一条飞书）。
        """
        key = code or _code6(quote)
        if seal_hit(quote, prev_days, now, code=key):
            self._streak[key] = self._streak.get(key, 0) + 1
        else:
            self._streak[key] = 0
        if self._streak.get(key, 0) >= self.need and key not in self._sealed:
            vol = _num(quote.get("bid1_vol"))
            self._sealed[key] = vol if vol is not None else 0.0
            return True
        return False

    def sealed_volume(self, code: str) -> float | None:
        """这只票触发那一刻的买一量（股）。没触发过返回 `None`。"""
        return self._sealed.get(code)

    def is_sealed(self, code: str) -> bool:
        return code in self._sealed

    def reset(self) -> None:
        """换日/换场次时调用。"""
        self._streak.clear()
        self._sealed.clear()


# ───────────────────────────── helpers ─────────────────────────────


def _num(value: Any) -> float | None:
    """把 E3 quote 里的值收成 float，`None` 原样返回。

    `None` 与 `0.0` **必须分开**：停牌时新浪把价量一起填 0，CPT 侧已经把它们
    规整成 `None`；但「量真的是 0」会保留 `0.0`（E3 契约第 2 条）。
    这里不把 `0.0` 变成 `None` —— 那会抹掉「买一是 0 手」这个真实观测。
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _code6(quote: dict[str, Any]) -> str:
    """从 quote 里取 6 位纯代码。

    E3 契约规定外层字典的键就是 6 位纯代码，但 quote 内部的 `code` 是**全限定**
    （`sh600519`）。这里统一取后 6 位数字；取不到就返回空串，让上游的
    `is_at_limit` 走主板兜底而不是崩掉。
    """
    raw = str(quote.get("code") or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else raw
