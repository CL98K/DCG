#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无浏览器自检：在临时端口起 tools/app.py 的服务，打一遍全部 HTTP 接口并断言契约。

用法：
    python tools/selftest.py
退出码 0 = 全过，1 = 有失败。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

try:
    import app as srv  # noqa: E402
except Exception as exc:  # noqa: BLE001
    print("[FATAL] 无法导入 tools/app.py：%s: %s" % (type(exc).__name__, exc))
    print("        通常意味着 tools/dcg_bridge.py 还不存在，或仓库根不在 sys.path。")
    raise SystemExit(2)

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print("PASS  %s" % name)
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((name, False, "%s: %s" % (type(exc).__name__, exc)))
        print("FAIL  %s\n      %s: %s" % (name, type(exc).__name__, exc))


def main() -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    httpd.daemon_threads = True
    port = httpd.server_port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read()

    def post(path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def jget(path):
        status, raw = get(path)
        return status, json.loads(raw.decode("utf-8"))

    def t_static():
        for p, needle in (("/", b"<!DOCTYPE html"), ("/app.js", b""), ("/style.css", b"")):
            status, raw = get(p)
            assert status == 200, "%s -> %s" % (p, status)
            if needle:
                assert needle.lower() in raw.lower(), "%s 内容不像首页" % p

    def t_health():
        status, body = jget("/api/health")
        assert status == 200 and body.get("ok") is True, body
        assert body.get("dcg_version") == srv.dcg_bridge.dcg_version(), body
        assert body.get("port") == port, body

    def t_demo():
        status, body = jget("/api/demo")
        assert body.get("ok") is True and body.get("text", "").strip(), body
        json.loads(body["text"])

    demo = json.dumps({"orders": [
        {"sku": "A-1", "qty": 2, "price": 3.5, "tags": ["new", "sale"]},
        {"sku": "B-2", "qty": 1, "price": 10.0, "tags": ["hot"]},
    ]}, ensure_ascii=False)

    def t_infer_wrapper():
        status, body = post("/api/infer", {"text": demo})
        assert body.get("ok") is True, body
        assert body["mode"] == "wrapper" and body["source"] == "$.orders", body
        assert body["row_count"] == 2, body
        paths = {f["path"] for f in body["fields"]}
        assert {"$.sku", "$.qty", "$.price"} <= paths, paths
        assert any(f["type"] == "list" and f["path"] == "$.tags" for f in body["fields"]), body["fields"]

    def t_infer_rows():
        status, body = post("/api/infer", {"text": '[{"a": 1}, {"a": 2.5}]'})
        assert body.get("ok") is True and body["mode"] == "rows", body
        assert body["row_count"] == 2 and body["fields"][0]["type"] == "float64", body

    def t_infer_single():
        status, body = post("/api/infer", {"text": '{"a": 1, "b": "x"}'})
        assert body.get("ok") is True and body["mode"] == "single", body
        assert body["row_count"] == 1, body

    def t_infer_bad_json():
        status, body = post("/api/infer", {"text": "{oops"})
        assert body.get("ok") is False and body.get("stage") == "json", body
        assert body.get("error"), body

    def t_infer_empty_array():
        status, body = post("/api/infer", {"text": "[]"})
        assert body.get("ok") is True and body["row_count"] == 0, body
        assert body.get("fields") == [], body

    def build_schema():
        _, inf = post("/api/infer", {"text": demo})
        fields = {}
        for f in inf["fields"]:
            if len(fields) >= 3:
                break
            if f["type"] in ("int64", "int32", "float64", "str", "bool", "list"):
                fields[f["suggest"]] = {"type": f["type"], "value": f["path"]}
        return {"name": "order", "input": {"source": inf["source"]},
                "output": {"shape": "list", "trace": False},
                "out": {"type": "object", "fields": fields}}

    def t_run_ok():
        status, body = post("/api/run", {"text": demo, "schema": build_schema()})
        assert body.get("ok") is True, body
        assert body["out_count"] == 2 and len(body["out"]) == 2, body
        assert body["truncated"] is False, body
        assert "def run" in body["code"], body["code"][:200]
        assert isinstance(body["elapsed_ms"], (int, float)), body
        assert isinstance(body.get("manifest"), dict), body

    def t_run_bad_schema():
        bad = {"name": "x", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {"a": {"type": "nope", "value": "$.0"}}}}
        status, body = post("/api/run", {"text": "[1]", "schema": bad})
        assert body.get("ok") is False and body.get("stage") == "schema", body

    def t_run_bad_json():
        status, body = post("/api/run", {"text": "{oops", "schema": build_schema()})
        assert body.get("ok") is False and body.get("stage") == "json", body

    def t_unknown_api():
        try:
            get("/api/nope")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            return
        raise AssertionError("未知接口应返回 404")

    try:
        for name, fn in [
            ("静态页 index/app.js/style.css", t_static),
            ("GET /api/health", t_health),
            ("GET /api/demo", t_demo),
            ("POST /api/infer wrapper", t_infer_wrapper),
            ("POST /api/infer rows", t_infer_rows),
            ("POST /api/infer single", t_infer_single),
            ("POST /api/infer 非法 JSON", t_infer_bad_json),
            ("POST /api/infer 空数组", t_infer_empty_array),
            ("POST /api/run 成功", t_run_ok),
            ("POST /api/run schema 校验失败", t_run_bad_schema),
            ("POST /api/run JSON 失败", t_run_bad_json),
            ("未知 /api 路径 404", t_unknown_api),
        ]:
            check(name, fn)
    finally:
        httpd.shutdown()
        httpd.server_close()

    bad = [r for r in RESULTS if not r[1]]
    print("\nSELFTEST: %d PASS / %d FAIL (total %d)" % (len(RESULTS) - len(bad), len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
