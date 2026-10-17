"""Probe: which *contract-forbidden* schemas does `validate()` (and even `generate()`)
let through silently?  Follow-up to `probe_schema_error_paths.py`, whose error-path cases
all reported path at field level but whose "accepted" cases needed a second pass.

Read-only w.r.t. dcg/.  Raw observation only.

Run: python -X utf8 bench/probe_deferred_errors.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg  # noqa: E402


def sha1(rel):
    try:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return "n/a"


def mk(field):
    return {"name": "deferred",
            "output": {"shape": "list", "single": False, "filter": False},
            "out": {"type": "object", "fields": {"a": field}}}


CASES = [
    ("deep path $.a.b.c (contract §6: unsupported)", mk({"type": "int64", "value": "$.a.b.c"})),
    ("deep path $.a.b", mk({"type": "int64", "value": "$.a.b"})),
    ("control: single-level $.a", mk({"type": "int64", "value": "$.a"})),
    ("list without items (contract: required)",
     mk({"type": "list", "value": "$.a"})),
    ("nested object without fields (contract: required)",
     mk({"type": "object", "value": "$.a"})),
]


def main() -> int:
    print("=" * 78)
    print("probe_deferred_errors.py -- contract-forbidden shapes accepted by validate/generate")
    print(f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    for rel in ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py", "dcg/jx/compiler.py"):
        print(f"{rel:18s} sha1={sha1(rel)}")
    print("=" * 78)
    for label, schema in CASES:
        print(f"\n### {label}")
        try:
            dcg.validate(schema)
            print("  validate : accepted (no error)")
        except Exception as exc:  # noqa: BLE001
            print(f"  validate : {type(exc).__name__} path={getattr(exc, 'path', None)!r} "
                  f"msg={getattr(exc, 'msg', None) or exc!r}")
            continue
        try:
            src = dcg.generate(schema)
            print(f"  generate : accepted, {len(src)} chars")
            print("  source:")
            for line in src.splitlines():
                print(f"    | {line}")
        except Exception as exc:  # noqa: BLE001
            print(f"  generate : {type(exc).__name__} path={getattr(exc, 'path', None)!r} "
                  f"msg={getattr(exc, 'msg', None) or exc!r}")
            continue
        ns = {}
        try:
            exec(compile(src, "<generated>", "exec"), ns)
        except BaseException as exc:  # noqa: BLE001
            print(f"  exec     : {type(exc).__name__}: {exc}")
            continue
        # dict rows: bare `$.a` is a dict-key access, so the row must be a mapping
        rows = [{"a": {"b": {"c": 11}}, "code": "x"}, {"a": 7}]
        for arg_label, arg in (("run([row])", rows), ("run({'index':[row]})", {"index": rows})):
            try:
                print(f"  {arg_label:24s} -> {ns['run'](arg)!r}")
            except BaseException as exc:  # noqa: BLE001
                print(f"  {arg_label:24s} -> {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
