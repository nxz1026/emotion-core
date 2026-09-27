"""展示层 web 服务：单一端口 8098 + auth_basic。"""
from __future__ import annotations

import http.server
import base64
import json
from urllib.parse import urlparse, parse_qs

from emotion_core.utils.config import CONFIG
from emotion_core.presentation import loaders, translate

ROUTES = {
    "/": "intuitive",
    "/logic": "logic",
    "/algorithm": "algorithm",
}


class Handler(http.server.BaseHTTPRequestHandler):
    """请求处理器。"""

    def _auth_check(self) -> bool:
        """auth_basic 鉴权检查。"""
        auth = self.headers.get("Authorization")
        expected = base64.b64encode(
            f"{CONFIG.AUTH_USER}:{CONFIG.AUTH_PASSWORD}".encode()
        ).decode()
        return auth == f"Basic {expected}"

    def _send_json(self, data: dict, status: int = 200) -> None:
        """发送 JSON 响应。"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _send_html(self, html: str, status: int = 200) -> None:
        """发送 HTML 响应。"""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self) -> None:
        if not self._auth_check():
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Basic realm=emotion-core")
            self.end_headers()
            return

        parsed = urlparse(self.path)
        if parsed.path in ROUTES:
            self._send_html(f"<html><body><h1>{ROUTES[parsed.path]}</h1></body></html>")
        elif parsed.path.startswith("/api/"):
            self._send_json({"route": parsed.path, "status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()


def run_server(port: int = 8098) -> None:
    """启动 web 服务。"""
    server = http.server.HTTPServer(("", port), Handler)
    print(f"emotion-core dash running on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
