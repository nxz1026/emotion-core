"""Lab 只读页面（`:8091`）：把实验台的数字摊开给人看。

显示纪律（docs/20 §9.3）
- 每个数字都带样本量 n 和口径；n<30 置灰标「样本不足」，绝不给分位数；
- 缺失显示「—」，**永不显示 0**；
- 排序按置信下界（LCB），不按均值；
- 红绿只表达「相对主线改善/恶化」，不表达「能赚钱」；
- 唯一写操作：待批提案的 [采纳]/[驳回] → 只改 `lab.lab_promotion.status`。

页面与主线完全隔离：独立进程、独立端口、只读 public 表、只写 lab schema。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jinja2

from emotion_core.lab import db as labdb

log = logging.getLogger(__name__)

PORT = 8091
HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "templates"
STATIC_DIRS = (HERE / "static", HERE.parent / "presentation" / "static")

BASELINE_ID = "baseline_mainline"
MIN_N = 30

_STAT_LABEL = {
    "expectancy": "每信号期望",
    "fill_rate": "成交率",
    "win_rate": "胜率",
    "lcb": "置信下界",
    "median": "中位",
    "worst": "最差",
}


_NUM_FIELDS = (
    "n", "fill_rate", "expectancy", "median", "win_rate", "lcb", "worst",
    "sub_worst",
)


def _floats(row: dict[str, Any]) -> dict[str, Any]:
    """psycopg 把 NUMERIC 取成 Decimal，模板里格式化会变 "63.50" —— 统一转 float。"""
    for key in _NUM_FIELDS:
        if key in row and row[key] is not None:
            row[key] = int(row[key]) if key == "n" else float(row[key])
    return row


def _row_score(conn: Any, variant_id: str) -> dict[str, Any] | None:
    df = labdb.query_df(
        "SELECT * FROM lab.lab_score WHERE variant_id = %s "
        "ORDER BY window_end DESC LIMIT 1",
        (variant_id,),
    )
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    detail = row.get("detail")
    if isinstance(detail, str):
        detail = json.loads(detail)
    row["detail"] = detail or {}
    return _floats(row)


def _all_variants(conn: Any) -> list[dict[str, Any]]:
    df = labdb.query_df(
        "SELECT v.variant_id, v.status, v.note, v.created_at, s.window_start, "
        "s.window_end, s.n, s.fill_rate, s.expectancy, s.win_rate, s.lcb "
        "FROM lab.lab_variant v LEFT JOIN LATERAL ("
        "    SELECT * FROM lab.lab_score x WHERE x.variant_id = v.variant_id "
        "      AND x.window_name IN ('hold', 'full') "
        "    ORDER BY (x.window_name = 'hold') DESC, x.window_end DESC LIMIT 1"
        ") s ON true "
        "ORDER BY s.lcb DESC NULLS LAST"
    )
    return [] if df.empty else [_floats(r) for r in df.to_dict("records")]


def _proposals(conn: Any) -> list[dict[str, Any]]:
    df = labdb.query_df(
        "SELECT id, proposal, actions, evidence, status, decided_by, decided_at "
        "FROM lab.lab_promotion ORDER BY (status = 'pending') DESC, id DESC LIMIT 50"
    )
    return [] if df.empty else df.to_dict("records")


def _alarms(base: dict[str, Any] | None, variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """退化报警：只用能算的事实，不编阈值。"""
    out: list[dict[str, Any]] = []
    if not base:
        return [
            {
                "level": "warn",
                "title": "还没有主线基线分数",
                "detail": "先跑 lab.panel --build 再跑 lab.score --persist，页面才有对照。",
            }
        ]
    exp = base.get("expectancy")
    if exp is not None and exp < 0:
        out.append(
            {
                "level": "danger",
                "title": f"主线每信号期望为负：{exp:+.2f}%",
                "detail": (
                    f"n={base.get('n')}，成交率 {base.get('fill_rate')}%，"
                    f"置信下界 {float(base.get('lcb')):+.2f}%。口径：口径B 触及即成交、费后 0.15%、"
                    "T+2 开盘卖。这不是退化，是现行规则本身没有正期望。"
                ),
            }
        )
    lcb = base.get("lcb")
    if lcb is not None and lcb > 0:
        out.append(
            {
                "level": "ok",
                "title": f"主线置信下界为正：{lcb:+.2f}%",
                "detail": "95% 单侧下界 > 0，说明该口径下主线仍可能有效。",
            }
        )
    slices = (base.get("detail") or {}).get("slices") or {}
    bad = [
        (k, v) for k, v in slices.items()
        if v.get("mean") is not None and v["mean"] < 0 and (v.get("n") or 0) >= MIN_N
    ]
    if bad:
        bad.sort(key=lambda kv: kv[1]["mean"])
        top = "；".join(f"{k} {v['mean']:+.2f}%（n={v['n']}）" for k, v in bad[:4])
        out.append(
            {
                "level": "warn",
                "title": f"{len(bad)} 个切片为负",
                "detail": f"最差几片：{top}。切片只描述历史，不改规则。",
            }
        )
    thin = [
        k for k, v in slices.items() if (v.get("n") or 0) < MIN_N
    ]
    if thin:
        out.append(
            {
                "level": "muted",
                "title": f"{len(thin)} 个切片样本不足（n<{MIN_N}）",
                "detail": "这些格子只看方向，不看数字：" + "、".join(sorted(thin)[:6]),
            }
        )
    stale = [v for v in variants if v.get("variant_id") != BASELINE_ID and v.get("status") == "active"]
    if stale:
        out.append(
            {
                "level": "muted",
                "title": f"{len(stale)} 条变体处于 active 但未走提案",
                "detail": "P1 只有基线，出现别的 active 变体说明有人绕过了提案制。",
            }
        )
    return out


def _ctx() -> dict[str, Any]:
    conn = labdb.connect_lab()
    try:
        base = _row_score(conn, BASELINE_ID)
        variants = _all_variants(conn)
        proposals = _proposals(conn)
    finally:
        conn.close()
    return {
        "today": date.today().isoformat(),
        "base": base,
        "variants": variants,
        "proposals": proposals,
        "alarms": _alarms(base, variants),
        "min_n": MIN_N,
        "stat_label": _STAT_LABEL,
    }


def _render(tpl_name: str, ctx: dict[str, Any]) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    env.filters["pct"] = lambda v, nd=2: "—" if v is None else f"{v:+.{nd}f}%"
    env.filters["num"] = lambda v: "—" if v is None else v
    return env.get_template(tpl_name).render(**ctx).encode("utf-8")


def _decide(promo_id: str, decision: str, who: str = "user") -> None:
    status = "approved" if decision == "approve" else "rejected"
    conn = labdb.connect_lab()
    try:
        labdb.execute(
            conn,
            "UPDATE lab.lab_promotion SET status = %s, decided_by = %s, decided_at = now() "
            "WHERE id = %s AND status = 'pending'",
            (status, who, int(promo_id)),
        )
        conn.commit()
    finally:
        conn.close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "emotion-lab/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, _render("lab_index.html", _ctx()))
            elif path.startswith("/lab/variant/"):
                vid = path.rsplit("/", 1)[-1]
                conn = labdb.connect_lab()
                try:
                    row = _row_score(conn, vid)
                    variants = _all_variants(conn)
                finally:
                    conn.close()
                if row is None:
                    self._send(404, b"no such variant")
                else:
                    self._send(
                        200,
                        _render(
                            "lab_variant.html",
                            {"today": date.today().isoformat(), "v": row,
                             "variants": variants, "min_n": MIN_N,
                             "stat_label": _STAT_LABEL},
                        ),
                    )
            elif path.startswith("/static/"):
                self._static(path.rsplit("/", 1)[-1])
            else:
                self._send(404, b"not found")
        except Exception as exc:  # noqa: BLE001 - 页面失败要说人话，不能 500 空页面
            log.exception("页面渲染失败")
            self._send(500, f"页面渲染失败：{exc}".encode())

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        form = parse_qs(body)
        if parsed.path.startswith("/lab/proposal/"):
            promo_id = parsed.path.rsplit("/", 2)[-2]
            decision = (form.get("decision") or [""])[0]
            if decision in {"approve", "reject"}:
                _decide(promo_id, decision)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        self._send(404, b"not found")

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        for folder in STATIC_DIRS:
            path = folder / name
            if path.is_file():
                ctype = "text/css" if name.endswith(".css") else "application/javascript"
                self._send(200, path.read_bytes(), f"{ctype}; charset=utf-8")
                return
        self._send(404, b"not found")

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def serve(port: int = PORT) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"Lab 页面：http://127.0.0.1:{port}/（Ctrl-C 退出）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lab 只读页面")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args(argv)
    if not args.serve:
        ap.print_help()
        return 2
    serve(args.port)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
