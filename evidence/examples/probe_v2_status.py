"""Probe the CURRENT v2 status.  Read-only: imports dcg and reports what works.

Prints one line per capability so `docs/examples_v1_to_v2.md` and the v1-parity
tests can state exactly which v2 surface was available when the claim was made.

Run:  python -X utf8 evidence/examples/probe_v2_status.py
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BACKTICK = chr(96)
CAP = {
    "name": "cap",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {"type": "object", "fields": {
        "hour": {"value": "$.5.a", "type": "list"},
        "hourA": {"value": "$.5.a.1", "type": "int64"},
        "hourB": {"value": "$.5.a.2", "type": "int64"},
        "hourD": {"expr": BACKTICK + "str($.5.a.1) + str(hourB)" + BACKTICK, "type": "str"},
        "hourE": {"expr": "hourA + hourB", "type": "int64"},
    }},
}
ROW = (0, 0, 0, 0, 0, {"a": [0, 1, 2, [1, 2, 3]]})


def step(name, fn):
    try:
        out = fn()
    except BaseException as exc:  # noqa: BLE001 - probe: report everything
        print("[FAIL] %-28s %s: %s" % (name, type(exc).__name__, exc))
        return None
    print("[ ok ] %-28s %s" % (name, out))
    return out


def main():
    dcg = step("import dcg", lambda: __import__("dcg"))
    if dcg is None:
        return 1
    step("dcg.__all__", lambda: list(dcg.__all__))
    src = step("generate(CAP)", lambda: dcg.generate(CAP))
    if src:
        for line in src.splitlines():
            print("        | " + line)
    step("compile(CAP)", lambda: bool(compile(src, "<cap>", "exec")) if src else "no src")

    def run_cap():
        ns = {}
        exec(compile(src, "<cap>", "exec"), ns)
        return ns["run"]({"index": [ROW]})

    if src:
        step("exec+run(CAP)", run_cap)

    def jx(ex):
        return dcg.compile_expr(ex)

    for ex in ("$.5.a.1", "a + b", "miss & 'x'", "$sum(xs)"):
        step("compile_expr %r" % ex, (lambda e=ex: jx(e)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
