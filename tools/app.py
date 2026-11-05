#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DCG Schema Builder —— 本地 Web 工具的服务端。

用法：
    python tools/app.py                 # 起服务并打开浏览器（默认 127.0.0.1:8765）
    python tools/app.py --port 8790     # 换端口
    python tools/app.py --no-browser    # 不自动开浏览器

只依赖 Python 标准库；DCG 通过 tools/dcg_bridge.py 加载。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
sys.path.insert(0, BASE)

import dcg_bridge  # noqa: E402  （同目录；app.py 只依赖它的三个函数）

MAX_BODY = 32 * 1024 * 1024  # 32 MB
DEMO_TEXT = """{
  "orders": [
    {"sku": "A-1", "qty": 2, "price": 3.5, "tags": ["new", "sale"]},
    {"sku": "B-2", "qty": 1, "price": 10.0, "tags": ["hot"]},
    {"sku": "C-3", "qty": 7, "price": 1.25, "tags": []}
  ]
}"""


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "DCGSchemaBuilder/1.0"
    # 关掉默认的逐请求 stderr 日志，保留 4xx/5xx 的关键行
    def log_message(self, fmt, *args):
        code = fmt % args
        if " 200 " in code and ("/api/" in code or code.endswith('" 0')):
            return
        sys.stderr.write("[http] " + code + "\n")

    # ------------------------------------------------------------------ util
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _send_file(self, path: str, ctype: str) -> None:
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None, "空请求体"
        if length > MAX_BODY:
            return None, "请求体超过 32 MB"
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception as exc:  # noqa: BLE001
            return None, "请求体不是合法 JSON：%s" % exc

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                self._send_json({"ok": True, "dcg_version": dcg_bridge.dcg_version(),
                                 "python": sys.version.split()[0], "port": self.server.server_port})
                return
            if path == "/api/demo":
                self._send_json({"ok": True, "text": DEMO_TEXT,
                                 "hint": "样本是 dict 包一层 list：schema 的 input.source 会自动填 $.orders"})
                return
            if path in ("/", "/index.html"):
                self._send_file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
                return
            if path.startswith("/api/"):
                self._send_json({"ok": False, "stage": "http", "error": "未知接口 %s" % path}, 404)
                return
            # 静态资源：只允许 web/ 下的文件，禁止跳出
            name = unquote(path.lstrip("/"))
            if not name or "/" in name or "\\" in name or name.startswith("."):
                self._send_json({"ok": False, "stage": "http", "error": "非法路径"}, 404)
                return
            full = os.path.join(WEB, name)
            if not os.path.isfile(full):
                self._send_json({"ok": False, "stage": "http", "error": "文件不存在"}, 404)
                return
            ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript; charset=utf-8",
                     "css": "text/css; charset=utf-8", "png": "image/png", "svg": "image/svg+xml",
                     "ico": "image/x-icon", "json": "application/json; charset=utf-8"}
            suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            self._send_file(full, ctype.get(suffix, "application/octet-stream"))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "stage": "http", "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        path = urlparse(self.path).path
        body, err = self._read_json_body()
        if err:
            self._send_json({"ok": False, "stage": "http", "error": err}, 400)
            return
        try:
            if path == "/api/infer":
                text = body.get("text") if isinstance(body, dict) else None
                if not isinstance(text, str) or not text.strip():
                    self._send_json({"ok": False, "stage": "http", "error": "缺少 text（原始 JSON 文本）"}, 400)
                    return
                self._send_json(dcg_bridge.infer(text))
                return
            if path == "/api/run":
                text = body.get("text") if isinstance(body, dict) else None
                schema = body.get("schema") if isinstance(body, dict) else None
                if not isinstance(text, str) or not text.strip():
                    self._send_json({"ok": False, "stage": "http", "error": "缺少 text（原始 JSON 文本）"}, 400)
                    return
                if not isinstance(schema, dict):
                    self._send_json({"ok": False, "stage": "http", "error": "缺少 schema（对象）"}, 400)
                    return
                self._send_json(dcg_bridge.run(schema, text))
                return
            self._send_json({"ok": False, "stage": "http", "error": "未知接口 %s" % path}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 兜底：绝不让异常穿透成 HTML 500
            self._send_json({"ok": False, "stage": "internal",
                             "error": "%s: %s" % (type(exc).__name__, exc)}, 500)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DCG Schema Builder（本地 Web 工具）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    url = "http://%s:%d/" % (args.host, args.port)
    print("DCG Schema Builder 已启动： %s" % url)
    print("按 Ctrl+C 停止。  (dcg %s, python %s)" % (dcg_bridge.dcg_version(), sys.version.split()[0]))
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
