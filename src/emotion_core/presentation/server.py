"""展示层 web 服务：单一端口 8098。

提供三层 dashboard：
- 直观层（/）：今日结论 + 推荐 + 买点
- 逻辑层（/logic）：市场参数
- 算法层（/algorithm）：计算公式 + 数据来源

basic auth 由 nginx 转发层处理（/etc/nginx/.htpasswd），
本服务不再重复鉴权。

Trade API（/api/trade/）：LKL-Trade 决策/结果 HTTP 交换。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from emotion_core.presentation import loaders, snapshot, strategy_view, trade_api
from emotion_core.utils import price as price_util

log = logging.getLogger("emotion_core.dash")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# nginx 转发前缀（proxy_pass /emotion/ -> /）
BASE_PATH = "/emotion"

# Jinja2 环境
_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

ROUTES = {
    "intuitive": "intuitive",
    "logic": "logic",
    "algorithm": "algorithm",
    "strategy": "strategy",
    "stock": "stock",          # 个股诊断（输入代码 → 分析数据 + 直观建议）
}


def _json_default(obj):
    """把 Decimal/date 等 psycopg 取值转成 JSON 友好的类型。"""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def get_static(path: str) -> tuple[bytes, str]:
    """加载静态文件。"""
    file_path = (STATIC_DIR / path).resolve()
    if not file_path.is_relative_to(STATIC_DIR.resolve()):
        return b"", ""
    if not file_path.exists() or not file_path.is_file():
        return b"", ""
    content_type = "text/css" if path.endswith(".css") else "application/javascript"
    return file_path.read_bytes(), content_type


def _recover_utf8(value: str) -> str:
    """还原被 latin-1 展宽的 UTF-8 查询参数。

    HTTP 请求行由 BaseHTTPRequestHandler 按 latin-1 解码：浏览器/`encodeURIComponent`
    发的是百分号编码，取出来本来就是正常中文；但 `curl "...?code=新华文轩"` 这种
    直接发原始 UTF-8 字节的请求会变成 "æ°åŽæ–‡è½©"。这里按"能否 latin-1 回编码 +
    UTF-8 解码"判定并还原；正常中文无法 latin-1 编码，故不受影响。
    """
    if not value or value.isascii():
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _query_date(parsed):
    """URL 上的 ?date= → 合法快照日（无/非法 → 最新快照日）。"""
    return snapshot.resolve_date(_query_arg(parsed, "date"))


def _query_arg(parsed, key: str, default: str = "") -> str:
    """取查询参数（含上面的 UTF-8 还原）。"""
    from urllib.parse import parse_qs
    raw = parse_qs(parsed.query).get(key, [default])[0]
    return _recover_utf8(raw)


def _render_template(template_name: str, **context) -> str:
    """渲染 Jinja2 模板。"""
    template = _jinja.get_template(template_name)
    return template.render(**context)


def _load_intuitive_data(trade_date=None) -> dict:
    """加载直观层数据（指定快照日）。

    ⚠️ 2026-10-07：DB 异常时**必须与「今天没数据」看起来不一样**。
    原实现在异常分支把 `phase` 兜成「未知」、计数兜成 0/`—`、`snapshot_date` 兜成
    None——于是**整库宕机在看板上与「今天无信号」完全同形**。而这正是运维的主监控面：
    上线首日若 DB 抖动，你看到的会是一片「正常」的空页，红色只出现在 journal 里。

    现在异常分支把 `phase` 置为显式的「数据加载失败」，并把异常文本经
    `data_error` 透传到模板（模板用 `.get`，缺键不影响既有页面）。
    """
    load_error: str | None = None
    try:
        market = loaders.load_market_snapshot(trade_date)
        ladder = loaders.load_ladder(trade_date)
        signals = loaders.load_signals(trade_date)
        top_ladder = loaders.load_top_ladder(trade_date)
        signal_counts = loaders.load_signal_counts(trade_date)
    except Exception as e:
        market, ladder, signals, top_ladder = {}, [], [], []
        # 键必须与 loaders.load_signal_counts() 完全一致：模板会读 ladder_total，
        # 缺键会让"降级展示"变成 500（UndefinedError），正是这页要避免的。
        signal_counts = {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0}
        load_error = f"{type(e).__name__}: {e}"[:200]
        log.exception("加载数据失败")

    # 市场情绪一句话
    if load_error:
        # 与「市场状态不明」区分开：前者是**读不到**，后者是**读到但看不懂**。
        phase = "数据加载失败"
        phase_desc = f"数据库读取异常，本页所有数字不可信：{load_error}"
    else:
        phase = market.get("phase", "未知")
        phase_desc = {
            "发酵": "市场正在上升，涨停股增多",
            "高潮": "市场狂热，涨停家数多但风险也在积累",
            "退潮": "市场下跌，建议观望",
            "冰点": "市场低迷，涨停股稀少",
        }.get(phase, "市场状态不明")

    # 生态评级通俗解释
    dragon_env = market.get("dragon_env", "")
    dragon_desc = {
        "FAVORABLE": "环境有利，可积极操作",
        "NEUTRAL": "环境一般，谨慎操作",
        "UNFAVORABLE": "环境不利，建议观望",
    }.get(dragon_env, "暂无评级")

    # 推荐股票（全部 BUY，可点进个股诊断页）
    recommendations = [s for s in signals if s.get("action") == "BUY"]
    recommendation = recommendations[0] if recommendations else None

    # R58-5：次级观察（SECONDARY / RECOMMEND）top 10，details 框点击展开
    # 仅看当日（snapshot_date 同日）——`signals` loader 已按 confirm_date=trade_date 过滤
    secondary_signals = [s for s in signals if s.get("action") == "SECONDARY"]
    recommend_signals = [s for s in signals if s.get("action") == "RECOMMEND"]
    # RECOMMEND 在前（语义更接近 BUY）、SECONDARY 在后；都按 created_at 升序
    # （队列式阅读，最早落库的在最上、最新入队的在最下便于看最新）
    observation_signals = sorted(
        recommend_signals + secondary_signals,
        key=lambda s: (s.get("action") != "RECOMMEND", s.get("created_at") or ""),
    )[-10:]

    # 负期望披露：从 signal_outcome 聚合历史均值（P1-2 审计修复）
    neg_exp = _load_negative_expectation()

    # 空态策略解释：为什么今天没有 BUY 信号（P1-1 审计修复）
    if load_error:
        # 读不到数据时**不能**说「尚未产出任何 BUY 信号」——那是把故障说成了结论。
        no_buy_reason = "数据加载失败，无法判断今天有无 BUY 信号（见 phase_desc）。"
    else:
        no_buy_reason = _explain_no_buy(phase, market, signal_counts)

    return {
        "phase": phase,
        "phase_desc": phase_desc,
        "data_error": load_error,
        "top_ladder": top_ladder,
        "signal_counts": signal_counts,
        "snapshot_date": market.get("date"),
        "buy_window": market.get("buy_window", "NONE"),
        "force_liquidate": market.get("force_liquidate", False),
        "limit_up_count": market.get("limit_up_count", "—"),
        "max_limit_days": market.get("max_limit_days", "—"),
        "limit_down_count": market.get("limit_down_count", "—"),
        "ladder_count": len(ladder),
        "signal_count": len(signals),
        "recommendation": recommendation,
        "recommendations": recommendations,
        "observation_signals": observation_signals,
        "dragon_env": dragon_env,
        "dragon_desc": dragon_desc,
        "accelerate": market.get("accelerate", False),
        "accel_reason": market.get("accel_reason", ""),
        "neg_exp": neg_exp,
        "no_buy_reason": no_buy_reason,
    }


def _load_negative_expectation() -> dict:
    """从 signal_outcome 聚合历史负期望数据（P1-2 审计修复）。

    返回 {"mean": float|None, "median": float|None, "n": int, "hit_rate": float|None}。
    数据不足时各字段为 None，模板侧自行降级。
    """
    from emotion_core.utils.db import query_df
    df = query_df(
        "SELECT count(*) AS n, "
        "avg(rule_ret_a) AS mean_a, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY rule_ret_a) AS median_a "
        "FROM signal_outcome WHERE rule_ret_a IS NOT NULL"
    )
    if df.empty:
        return {"mean": None, "median": None, "n": 0, "hit_rate": None}
    row = df.iloc[0]
    n = int(row["n"])
    if n == 0:
        return {"mean": None, "median": None, "n": 0, "hit_rate": None}
    mean_a = round(float(row["mean_a"]), 2) if row["mean_a"] is not None else None
    median_a = round(float(row["median_a"]), 2) if row["median_a"] is not None else None
    # 胜率：rule_ret_a > 0 的占比
    hit_df = query_df(
        "SELECT count(*) AS pos FROM signal_outcome "
        "WHERE rule_ret_a IS NOT NULL AND rule_ret_a > 0"
    )
    hit_rate = round(int(hit_df.iloc[0]["pos"]) / n * 100, 1) if not hit_df.empty and n > 0 else None
    return {"mean": mean_a, "median": median_a, "n": n, "hit_rate": hit_rate}


def _explain_no_buy(phase: str, market: dict, signal_counts: dict) -> str:
    """解释为什么今天没有 BUY 信号（P1-1 审计修复）。

    不再只说「signal 表 N 行」——给出市场阶段、门槛、可能原因。
    """
    total = signal_counts.get("total", 0)
    if total == 0:
        return ("策略自 2024-01-05 运行至今，尚未产出任何 BUY 信号。"
                "这可能因为市场长期处于退潮/冰点阶段，或信号门槛过高。")

    reasons = []
    # 阶段解释
    phase_explain = {
        "退潮": "市场处于退潮期，策略主动收紧买入窗口",
        "冰点": "市场处于冰点期，涨停稀少，不符合信号触发条件",
        "高潮": "市场虽处高潮但可能已到尾声，策略在等待确认",
        "发酵": "市场在发酵初期，尚未形成明确趋势",
    }
    if phase in phase_explain:
        reasons.append(phase_explain[phase])

    # 买入窗口
    bw = market.get("buy_window", "NONE")
    if bw == "NONE":
        reasons.append("当前买入窗口关闭（buy_window=NONE）")

    # 强制清仓
    if market.get("force_liquidate"):
        reasons.append("触发强制清仓条件（force_liquidate）")

    if not reasons:
        reasons.append("策略过滤条件较严（五条件 checklist + 生态评级）")

    return "；".join(reasons) + "。"


def _load_logic_data(trade_date=None) -> dict:
    """加载逻辑层数据（指定快照日）。"""
    try:
        market = loaders.load_market_snapshot(trade_date)
        ladder = loaders.load_ladder(trade_date)
        # 获取晋级率数据
        promotion = loaders.load_promotion(trade_date) if hasattr(loaders, 'load_promotion') else []
        # 获取题材数据
        themes = loaders.load_top_themes(trade_date) if hasattr(loaders, 'load_top_themes') else []
        # 获取人气榜
        hot_rank = loaders.load_hot_rank(trade_date) if hasattr(loaders, 'load_hot_rank') else []
        # 获取近10日趋势
        trend = loaders.load_market_trend() if hasattr(loaders, 'load_market_trend') else []
    except Exception as e:
        market, ladder, promotion, themes, hot_rank, trend = {}, [], [], [], [], []
        log.error("加载数据失败: %s", e)

    # 计算炸板数（从 market_stat 的 bomb_rate 估算）
    bomb_count = None
    if market.get("bomb_rate") and market.get("limit_up_count"):
        bomb_count = int(market["limit_up_count"] * market["bomb_rate"])

    # 一字板数量
    oneword_count = None
    if market.get("oneword_ratio") and market.get("limit_up_count"):
        oneword_count = int(market["limit_up_count"] * market["oneword_ratio"])

    return {
        "stat": market,
        "ladder": ladder,
        "promotion": promotion,
        "themes": themes,
        "hot_rank": hot_rank,
        "top_ladder": loaders.load_top_ladder(trade_date),
        "trend": trend,
        "trade_date": str(market.get("date", "") or ""),
        "bomb_count": bomb_count,
        "oneword_count": oneword_count,
    }


def _limit_price_formula() -> str:
    """涨停价文案：板比从唯一实现 utils/price 现算，展示层不得重写公式。

    此前这里写死主板版公式（×1.10 的分整数式），只对主板成立、且与
    utils/price.py 的分整数式不同源（审核文档 §9 第 4 条 / S5）。现改为引用
    实现名 + 由 board_pct_milli 现算出的板块取值，改实现即改文案。
    """
    pcts = sorted({price_util.board_pct_milli(c)
                   for c in ("600000", "300001", "680001",
                             "bj430001", "430001")})
    return ("(pre_close_cents * (1000 + pct) + 500) // 1000"
            "（分整数；pct = utils/price.board_pct_milli(code) ∈ "
            f"{pcts}）")


def _load_algorithm_data(trade_date=None) -> dict:
    """加载算法层数据（指定快照日）。"""
    from emotion_core.utils.config import CONFIG

    # 获取市场数据
    try:
        market = loaders.load_market_snapshot(trade_date)
    except Exception as exc:
        log.warning("server: load_market_snapshot 失败，返回空数据 — %s", exc)
        market = {}

    formulas = {
        "涨停价": _limit_price_formula(),
        "is_limit_up": ("close_cents == utils/price.limit_up_price_cents("
                        "pre_close_cents, code)"),
        "is_one_word": "low_cents >= limit_up_price_cents(pre_close_cents, code)",
        "is_exchange": ("is_limit_up AND low_cents < "
                        "limit_up_price_cents(pre_close_cents, code)"),
        "cont_days": "连续涨停天数（停牌断档不打断）",
        "phase": "优先级: 退潮 > 高潮 > 发酵 > 冰点",
        "promote_nominal": "今日 cont_days >= 昨日+1 的只数 / 昨日该层只数",
        "promote_exchange": "promote_nominal AND 今日 is_exchange",
    }

    thresholds = {
        "MIN_LEADER_DAYS": CONFIG.MIN_LEADER_DAYS,
        "CLIMAX_ZT": CONFIG.CLIMAX_ZT,
        "CLIMAX_AMPLITUDE": CONFIG.CLIMAX_AMPLITUDE,
        "FERMENT_ZT_PERF": CONFIG.FERMENT_ZT_PERF,
        "ICE_MAX_DAYS": CONFIG.ICE_MAX_DAYS,
        "ICE_ZT_MAX": CONFIG.ICE_ZT_MAX,
        "EBB_ZT_PERF": CONFIG.EBB_ZT_PERF,
        "EBB_LD_MIN": CONFIG.EBB_LD_MIN,
    }

    # 动态缺口：从代码事实扫描，取代 algorithm.html 里的硬编码列表
    try:
        from emotion_core.services.gaps import scan_gaps
        gaps = scan_gaps()
    except Exception as exc:
        log.warning("server: scan_gaps 失败 — %s", exc)
        gaps = []

    return {
        "formulas": formulas,
        "thresholds": thresholds,
        "market": market,
        "trade_date": str(market.get("date", "") or ""),
        "gaps": gaps,
    }


def render_dashboard(layer: str, **extra) -> str:
    """渲染 dashboard 页面。

    所有页面共用同一个**快照日期**（URL `?date=YYYY-MM-DD`）：默认最新快照日，
    非法或无快照的日期回落到最新日，并在顶部提示条如实显示当前用的是哪一天。
    """
    selected = snapshot.resolve_date(extra.get("date"))
    banner = snapshot.banner_ctx(selected)
    base_ctx = {
        "base_path": BASE_PATH,
        "active_layer": layer,
        "selected_date": selected,
        "banner": banner,
        "calendar": snapshot.calendar_ctx(selected, extra.get("month")),
        "calendar_open": layer == "intuitive",   # 直观层默认展开日历
        "date_query": snapshot.date_links(selected)["date_query"],
        "request_path": _request_path(extra.get("path"), layer),
        "dates_total": len(snapshot.available_dates()),
        "dates_min": (snapshot.available_dates()[-1].isoformat()
                      if snapshot.available_dates() else ""),
        "dates_max": (snapshot.available_dates()[0].isoformat()
                      if snapshot.available_dates() else ""),
        "recent_dates": snapshot.recent_snapshots_ctx(selected),
    }

    if layer == "intuitive":
        data = _load_intuitive_data(selected)
    elif layer == "logic":
        data = _load_logic_data(selected)
    elif layer == "algorithm":
        data = _load_algorithm_data(selected)
    elif layer == "strategy":
        data = {
            "dates_json": json.dumps(strategy_view.strategy_dates(), ensure_ascii=False),
            # 策略观察台有自己的日期下拉（数据源是报告文件），初始值跟随全局快照日
            "prefill_date": selected.isoformat() if selected else "",
        }
    elif layer == "stock":
        data = {"prefill_code": extra.get("code", ""),
                "prefill_date": selected.isoformat() if selected else ""}
    else:
        data = {}

    return _render_template(f"{layer}.html", **base_ctx, **data)


def _default_path(layer: str) -> str:
    """层级 → 该页路径（用于日历里的链接回到"当前页"）。"""
    return f"{BASE_PATH}/" if layer == "intuitive" else f"{BASE_PATH}/{layer}"


def _request_path(raw: str | None, layer: str) -> str:
    """当前页路径，一律带 `BASE_PATH` 前缀。

    nginx 用 `proxy_pass http://127.0.0.1:8098/`（带尾斜杠）把 `/emotion/` 前缀剥掉再转发，
    所以服务端在线上看到的是 `/`（直观层）、`/logic`……直接拿它拼日历导航与"跳转"表单，
    链接就会指到站点根（门户页）而不是当前层。这里补齐前缀，两个入口（直连 :8098、
    经 nginx）都能生成正确地址。
    """
    path = raw or _default_path(layer)
    if path.startswith(BASE_PATH):
        return path
    return f"{BASE_PATH}{path if path.startswith('/') else '/' + path}"


class DashboardHandler(BaseHTTPRequestHandler):
    """Dashboard 请求处理器。"""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        # 静态文件（nginx 代理后前缀被剥掉，所以路径是 /static/...）
        if parsed.path.startswith("/static/"):
            relative = parsed.path[len("/static/"):]
            content, content_type = get_static(relative)
            if content:
                self.send_response(200)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
            return

        # API
        if parsed.path == "/api/strategy":
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query)
            date = q.get("date", [""])[0]
            self._send_json(strategy_view.to_api(date))
            return
        if parsed.path == "/api/strategy/dates":
            self._send_json({"dates": strategy_view.strategy_dates()})
            return
        if parsed.path == "/api/dates":
            dates = snapshot.available_dates()
            self._send_json({
                "dates": [d.isoformat() for d in dates],
                "latest": dates[0].isoformat() if dates else None,
                "selected": (snapshot.resolve_date(_query_arg(parsed, "date")) or
                             (dates[0] if dates else None)),
            })
            return
        if parsed.path == "/api/alerts":
            # 未确认告警（门户「今日速览」用：只暴露条数与摘要，不做确认操作）
            from emotion_core.algorithms import alerts as alerts_mod
            items = alerts_mod.pending(30)
            attention = [a for a in items if a["level"] in ("WARN", "ERROR")]
            self._send_json({
                "pending": len(items),
                "attention": len(attention),
                "items": [{"id": a["id"], "level": a["level"], "source": a["source"],
                           "detail": a["detail"], "created_at": a["created_at"]}
                          for a in items[:5]],
            })
            return
        if parsed.path == "/api/stock":
            from emotion_core.services import stock_service
            code = _query_arg(parsed, "code")
            payload = stock_service.analyze(code, _query_date(parsed))
            self._send_json(payload, 200 if payload.get("ok") else 404)
            return
        if parsed.path == "/api/stock/llm":
            from emotion_core.services import stock_service
            code = _query_arg(parsed, "code")
            self._send_json(stock_service.llm_for(code, _query_date(parsed)))
            return

        # Trade API (LKL-Trade 决策/结果交换)
        if parsed.path == "/api/trade/decisions":
            payload, status = trade_api.handle_trade_decisions(parsed.query)
            self._send_json(payload, status)
            return
        if parsed.path == "/api/trade/results":
            payload, status = trade_api.handle_trade_results_get(parsed.query)
            self._send_json(payload, status)
            return
        if parsed.path == "/api/trade/health":
            payload, status = trade_api.handle_trade_health()
            self._send_json(payload, status)
            return

        if parsed.path.startswith("/api/"):
            # ⚠️ 兜底是有意的（门户「今日速览」需要一个永远可用的 status），
            # 但它让**拼错的接口也返回 200 + status 数据** ⇒ 前端会静默降级
            # 而不是报错，监控也看不出接口调用失败。这里记 warning 让错拼可见。
            log.warning("未识别的 API 路径，返回 status 兜底：%s", parsed.path)
            self._handle_status()
            return

        # Dashboard 页面
        path = parsed.path
        # nginx 将 /emotion/ 代理到 /，所以 / 也是直观层
        if path == "/" or path == BASE_PATH or path == BASE_PATH + "/":
            layer = "intuitive"
        elif path.startswith(BASE_PATH + "/"):
            layer = path[len(BASE_PATH):].strip("/") or "intuitive"
        elif path.startswith("/"):
            layer = path.strip("/")
        else:
            layer = path

        if layer in ROUTES:
            try:
                extra = {"code": _query_arg(parsed, "code"),
                         "date": _query_arg(parsed, "date"),
                         "month": _query_arg(parsed, "month"),
                         "path": parsed.path}
                html = render_dashboard(ROUTES[layer], **extra)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            except Exception as e:
                log.error("dashboard render failed: %s", e)
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>500 Internal Server Error</h1></body></html>")
            return

        # 根路径重定向
        if parsed.path == "":
            self.send_response(301)
            self.send_header("Location", f"{BASE_PATH}/")
            self.end_headers()
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>404 Not Found</h1></body></html>")

    def do_POST(self) -> None:
        """POST handler (Trade API results submission)."""
        parsed = urlparse(self.path)

        if parsed.path == "/api/trade/results":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload, status = trade_api.handle_trade_results_post(body)
            self._send_json(payload, status)
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"error": "not found"}')

    def _handle_status(self) -> None:
        """返回 emotion-core 状态数据（可带 ?date=）。"""
        try:
            market = loaders.load_market_snapshot(snapshot.resolve_date(
                _query_arg(urlparse(self.path), "date")))
            self._send_json({
                "ok": True,
                "date": str(market.get("date", "")),
                "phase": market.get("phase", ""),
                "limit_up_count": market.get("limit_up_count"),
                "max_limit_days": market.get("max_limit_days"),
                "dragon_env": market.get("dragon_env", ""),
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _send_json(self, data: dict, status: int = 200) -> None:
        """发送 JSON 响应。"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False,
                                    default=_json_default).encode())


def run_server(port: int = 8098) -> None:
    """启动 web 服务。"""
    server = HTTPServer(("", port), DashboardHandler)
    print(f"emotion-core dash running on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
