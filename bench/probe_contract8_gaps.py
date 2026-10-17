"""Probe: independent reproduction of the two CONTRACT §8 已知落差 items (Lead m02234).

O-1  `$..a` (descendant-style path) must be structurally REFUSED per contract §3.5
     (`..` is in the rejected set) -- but Lead registered it as "silently compiled to R['a']".
O-2  `output.post` / `output.trace` are accepted but emit zero code -- Lead registered
     "product bytes identical with and without them".

Read-only w.r.t. dcg/.  Every printed line is a raw observation, no interpretation.

Run: python -X utf8 bench/probe_contract8_gaps.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
for p in (ROOT, BENCH):
    if p not in sys.path:
        sys.path.insert(0, p)

import dcg  # noqa: E402


def sha1(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return "n/a"


def hdr(title):
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    hdr("probe_contract8_gaps.py -- O-1 `$..a` / O-2 post+trace no-op")
    print(f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"dcg.__file__      = {dcg.__file__}")
    for rel in ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py", "dcg/jx/compiler.py"):
        print(f"{rel:18s} sha1={sha1(os.path.join(ROOT, rel))}")

    # ---------------- O-1 ---------------------------------------------------------
    hdr("O-1  expr='$..a'  (contract §3.5 says `..` must be refused)")

    print("[1] dcg.compile_expr('$..a')")
    try:
        ir = dcg.compile_expr("$..a")
        print(f"    NO ERROR -> IR = {ir!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: path={getattr(exc, 'path', None)!r} "
              f"msg={getattr(exc, 'msg', None) or str(exc)!r}")

    print("[1b] dcg.compile_expr('$.a')  (control: single-level must work)")
    try:
        print(f"    ok -> IR = {dcg.compile_expr('$.a')!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {exc!r}")

    schema = {"name": "o1",
              "output": {"shape": "list", "single": False, "filter": False},
              "out": {"type": "object",
                      "fields": {"a": {"type": "int64", "expr": "$..a"}}}}
    print("[2] dcg.validate(schema with expr='$..a')")
    try:
        dcg.validate(schema)
        print("    NO ERROR -> schema accepted")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: path={getattr(exc, 'path', None)!r} "
              f"msg={getattr(exc, 'msg', None) or str(exc)!r}")

    print("[3] dcg.generate(...) + exec + run one row  {'a': 5}")
    try:
        src = dcg.generate(schema)
    except Exception as exc:  # noqa: BLE001
        print(f"    generate raised {type(exc).__name__}: "
              f"{getattr(exc, 'msg', None) or str(exc)!r}")
    else:
        print(f"    generated {len(src)} chars:")
        for line in src.splitlines():
            print(f"      | {line}")
        ns = {}
        try:
            exec(compile(src, "<o1-generated>", "exec"), ns)
        except BaseException as exc:  # noqa: BLE001
            print(f"    exec raised {type(exc).__name__}: {exc}")
        else:
            run = ns["run"]
            row = [0, 0, 0, 0, 0, {"a": 5}]
            for label, arg in (("list rows", [row]),
                               ("{'index': rows}", {"index": [row]})):
                try:
                    print(f"    run({label}) -> {run(arg)!r}")
                except BaseException as exc:  # noqa: BLE001
                    print(f"    run({label}) raised {type(exc).__name__}: {exc}")

    # ---------------- O-2 ---------------------------------------------------------
    hdr("O-2  output.post / output.trace accepted but emit no code")

    base_out = {"shape": "list", "single": False, "filter": False}
    fields = {"a": {"type": "int64", "value": "$.a"},
              "b": {"type": "int64", "value": "$.b"}}

    def sch(out_extra):
        out = dict(base_out)
        out.update(out_extra)
        return {"name": "o2", "output": out,
                "out": {"type": "object", "fields": dict(fields)}}

    cases = [("no post/trace", {}),
             ("post='$.a'", {"post": "$.a"}),
             ("trace=True", {"trace": True})]
    srcs = {}
    for label, extra in cases:
        s = sch(extra)
        try:
            dcg.validate(s)
            v = "accepted"
        except Exception as exc:  # noqa: BLE001
            v = f"{type(exc).__name__}: {getattr(exc, 'msg', None) or exc}"
        try:
            srcs[label] = dcg.generate(s)
            g = f"generated {len(srcs[label])} chars"
        except Exception as exc:  # noqa: BLE001
            srcs[label] = None
            g = f"generate raised {type(exc).__name__}: {getattr(exc, 'msg', None) or exc}"
        print(f"  {label:16s} validate={v:20s} {g}")

    a, b = srcs["no post/trace"], srcs["post='$.a'"]
    c = srcs["trace=True"]
    print()
    print(f"  bytes(post) == bytes(no post)   : {a == b if None not in (a, b) else 'n/a'}")
    print(f"  bytes(trace=True) == bytes(no t): {a == c if None not in (a, c) else 'n/a'}")

    print()
    print("  -- post 的其它形状（是否只有字符串校验）--")
    for bad in ("$.a + 1", "`$.a`", 123, None, "$sum($.a)"):
        s = sch({"post": bad})
        try:
            dcg.validate(s)
            v = "accepted"
        except Exception as exc:  # noqa: BLE001
            v = (f"{type(exc).__name__}: path={getattr(exc, 'path', None)!r} "
                 f"msg={getattr(exc, 'msg', None) or exc!r}")
        try:
            src_bad = dcg.generate(s)
            same = (src_bad == a)
        except Exception as exc:  # noqa: BLE001
            same = f"generate raised {type(exc).__name__}"
        print(f"    post={bad!r:14s} validate={v}   identical_product={same}")
    print()
    print(f"  b == {len(b) if b else 'n/a'} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
