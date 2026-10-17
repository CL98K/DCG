"""Probe: `value`-side path leniency (CONTRACT §8 O-1) -- independent check of Lead m02699.

Lead registered: `value="$..a"` etc. are silently compiled to a *different* path
(e.g. `R['a']`) instead of being refused.  This probe prints `str(exc)` AND `exc.msg`
separately, because the two entry points differ: `compile_expr`'s message has no column
while `generate`'s `str(exc)` carries one.

Read-only w.r.t. dcg/.

Run: python -X utf8 bench/probe_path_leniency.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg  # noqa: E402

PATHS = ["$..a", "$.a..b", "$.a.", "$...a", "$.*", "$.a.b.c", "$.5.a.1"]


def mk(value):
    return {"name": "leniency",
            "output": {"shape": "list", "single": False, "filter": False},
            "out": {"type": "object", "fields": {"v": {"type": "any", "value": value}}}}


def main() -> int:
    print("=" * 78)
    print("probe_path_leniency.py -- `value` side path leniency (CONTRACT §8 O-1)")
    print(f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print("=" * 78)
    for p in PATHS:
        print(f"\n### value={p!r}")
        try:
            dcg.validate(mk(p))
            print("  validate : accepted")
        except Exception as exc:  # noqa: BLE001
            print(f"  validate : {type(exc).__name__} path={getattr(exc, 'path', None)!r} "
                  f"msg={getattr(exc, 'msg', None) or exc!r}")
            continue
        try:
            src = dcg.generate(mk(p))
        except Exception as exc:  # noqa: BLE001
            print(f"  generate : {type(exc).__name__} str={str(exc)!r} "
                  f"msg={getattr(exc, 'msg', None)!r}")
            continue
        body = [ln.strip() for ln in src.splitlines() if "append" in ln]
        print(f"  generate : accepted -> {' | '.join(body)}")
        ns = {}
        exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102 - probe
        rows = [{"a": {"b": {"c": 11}}}, {"a": 7}]
        try:
            print(f"  run      : {ns['run'](rows)!r}")
        except BaseException as exc:  # noqa: BLE001
            print(f"  run      : {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
