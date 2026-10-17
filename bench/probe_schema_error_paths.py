"""Probe: does `SchemaError.path` point at the *specific field* (contract §2/§3)?

Lead (m01792) required parser-dev to fix `SchemaError.path` from `out.fields` to the
concrete field (e.g. `out.fields.v.expr`).  This probe is the independent check:
it feeds deliberately-broken schemas and prints `(type, path, msg)` verbatim.
Read-only with respect to dcg/.

Run: python -X utf8 bench/probe_schema_error_paths.py
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


def base(out_fields, name="probe"):
    """Canonical schema shape (contract §2/§4.1): top-level `out` carries the field tree,
    top-level `output` carries the shape/single/filter switches.

    早期版本把 `fields` 放进 `output` → 11 个用例全部只报 `path='output' msg='未知键: fields'`，
    那是**探针自己写错**（见 tests/test_schema.py 的错误路径断言）。
    """
    return {"name": name,
            "output": {"shape": "list", "single": False, "filter": False, "post": None,
                       "trace": False},
            "out": {"type": "object", "fields": out_fields}}


CASES = [
    ("missing type", base({"a": {"value": "$.a"}})),
    ("two keys", base({"a": {"type": "int64", "value": "$.a", "const": 1}})),
    ("no key", base({"a": {"type": "int64"}})),
    ("bad type name", base({"a": {"type": "string", "value": "$.a"}})),
    ("deep path", base({"a": {"type": "int64", "value": "$.a.b.c"}})),
    ("unknown function", base({"a": {"type": "int64", "expr": "$sum($.xs)"}})),
    ("bad backtick py", base({"a": {"type": "int64", "expr": "`1 +`"}})),
    ("undeclared bare name", base({"v": {"type": "object",
                                         "fields": {"a": {"type": "list", "items": "int64",
                                                          "value": "$.a"},
                                                    "b": {"type": "int64", "expr": "$.a[b]"}}}})),
    ("list without items", base({"a": {"type": "list", "value": "$.a"}})),
    ("nested object without fields", base({"a": {"type": "object", "value": "$.a"}})),
    ("single + dict shape", {"name": "probe",
                             "output": {"shape": "dict", "single": True, "filter": False},
                             "out": {"type": "object",
                                     "fields": {"a": {"type": "int64", "value": "$.a"}}}}),
]


def main() -> int:
    print("=" * 78)
    print("probe_schema_error_paths.py -- does SchemaError.path reach the field?")
    print(f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"dcg.__file__ = {dcg.__file__}")
    print("=" * 78)
    bad = 0
    for label, schema in CASES:
        try:
            dcg.validate(schema)
        except Exception as exc:  # noqa: BLE001 - we want the exact type/attrs
            path = getattr(exc, "path", None)
            msg = getattr(exc, "msg", None) or str(exc)
            print(f"{label:24s} {type(exc).__name__:14s} path={path!r}")
            print(f"{'':24s} msg={msg!r}")
            if path in (None, "", "out.fields"):
                bad += 1
                print(f"{'':24s} <-- path 不够具体（契约 §2/§3 要求指到字段级）")
        else:
            # validate() 放行了 —— 那么 generate() 呢？区分「延迟报错」与「静默接受」。
            try:
                dcg.generate(schema)
                print(f"{label:24s} <no error raised -- schema accepted>  "
                      f"generate(): ALSO accepted (静默通过)")
            except Exception as exc:  # noqa: BLE001
                print(f"{label:24s} <no error raised -- schema accepted>  "
                      f"generate() -> {type(exc).__name__}: "
                      f"path={getattr(exc, 'path', None)!r} "
                      f"msg={getattr(exc, 'msg', None) or str(exc)!r}")
            bad += 1
    print("-" * 78)
    print(f"cases={len(CASES)}  path-not-specific-or-accepted={bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
