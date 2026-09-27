"""展示层 web 服务：单一端口 8098。

提供三层 dashboard：
- 直观层（/）：今日结论 + 推荐 + 买点
- 逻辑层（/logic）：市场参数
- 算法层（/algorithm）：计算公式 + 数据来源

basic auth 由 nginx 转发层处理（/etc/nginx/.htpasswd），
本服务不再重复鉴权。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from emotion_core.presentation import loaders, strategy_view
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
}


def get_static(path: str) -> tuple[bytes, str]:
    """加载静态文件。"""
    file_path = STATIC_DIR / path
    if not file_path.exists() or not file_path.is_file():
        return b"", ""
    content_type = "text/css" if path.endswith(".css") else "application/javascript"
    return file_path.read_bytes(), content_type


def _render_template(template_name: str, **context) -> str:
    """渲染 Jinja2 模板。"""
    template = _jinja.get_template(template_name)
    return template.render(**context)


def _load_intuitive_data() -> dict:
    """加载直观层数据。"""
    try:
        market = loaders.load_market_snapshot()
        ladder = loaders.load_ladder()
        signals = loaders.load_signals()
    except Exception as e:
        market, ladder, signals = {}, [], []
        log.error("加载数据失败: %s", e)

    # 市场情绪一句话
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

    # 推荐股票
    recommendation = None
    for s in signals:
        if s.get("action") == "BUY":
            recommendation = s
            break

    return {
        "phase": phase,
        "phase_desc": phase_desc,
        "buy_window": market.get("buy_window", "NONE"),
        "force_liquidate": market.get("force_liquidate", False),
        "limit_up_count": market.get("limit_up_count", "—"),
        "max_limit_days": market.get("max_limit_days", "—"),
        "limit_down_count": market.get("limit_down_count", "—"),
        "ladder_count": len(ladder),
        "signal_count": len(signals),
        "recommendation": recommendation,
        "dragon_env": dragon_env,
        "dragon_desc": dragon_desc,
        "accelerate": market.get("accelerate", False),
        "accel_reason": market.get("accel_reason", ""),
    }


def _load_logic_data() -> dict:
    """加载逻辑层数据。"""
    try:
        market = loaders.load_market_snapshot()
        ladder = loaders.load_ladder()
        # 获取晋级率数据
        promotion = loaders.load_promotion() if hasattr(loaders, 'load_promotion') else []
        # 获取题材数据
        themes = loaders.load_top_themes() if hasattr(loaders, 'load_top_themes') else []
        # 获取人气榜
        hot_rank = loaders.load_hot_rank() if hasattr(loaders, 'load_hot_rank') else []
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
        "trend": trend,
        "trade_date": market.get("date", ""),
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


def _load_algorithm_data() -> dict:
    """加载算法层数据。"""
    from emotion_core.utils.config import CONFIG

    # 获取市场数据
    try:
        market = loaders.load_market_snapshot()
    except Exception:
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

    return {
        "formulas": formulas,
        "thresholds": thresholds,
        "market": market,
    }


def render_dashboard(layer: str) -> str:
    """渲染 dashboard 页面。"""
    base_ctx = {
        "base_path": BASE_PATH,
        "active_layer": layer,
    }

    if layer == "intuitive":
        data = _load_intuitive_data()
    elif layer == "logic":
        data = _load_logic_data()
    elif layer == "algorithm":
        data = _load_algorithm_data()
    elif layer == "strategy":
        data = {
            "dates_json": json.dumps(strategy_view.strategy_dates(), ensure_ascii=False),
        }
    else:
        data = {}

    return _render_template(f"{layer}.html", **base_ctx, **data)


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
        if parsed.path.startswith("/api/"):
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
                html = render_dashboard(ROUTES[layer])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body><h1>Error</h1><pre>{e}</pre></body></html>".encode())
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

    def _handle_status(self) -> None:
        """返回 emotion-core 状态数据。"""
        try:
            market = loaders.load_market_snapshot()
            self._send_json({
                "ok": True,
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
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


def run_server(port: int = 8098) -> None:
    """启动 web 服务。"""
    server = HTTPServer(("", port), DashboardHandler)
    print(f"emotion-core dash running on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
