"""Early v2 defect probe (verifier-dev).  Read-only: never writes under dcg/.

Purpose: while the public surface (`dcg/__init__.py`) is still a stub, `generate()` is
already callable via `dcg.codegen`.  This probe records hard facts about the CURRENT
revision so defect reports are backed by a minimal reproduction instead of hearsay.

It deliberately does NOT run the e2e suite or the performance suite (Lead instruction);
it only (a) inspects generated text, (b) runs a 3-row minimal input to observe semantics.

Run:  python -X utf8 bench/probe_v2_early.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
import _workload as W  # noqa: E402


def per_schema_part(src: str) -> str:
    """The schema-specific part: everything from `def run` onwards.

    The historical 445-char baseline counted only the per-schema handler body; its
    runtime helpers lived outside the compiled snippet.  So a fair comparison must
    report BOTH numbers.
    """
    i = src.find("def run")
    return src[i:] if i >= 0 else ""


def lambda_assignment_violations(src: str) -> list:
    """`lambda R: x = 1` is a SyntaxError; report any assignment nested in a lambda body."""
    bad = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            for sub in ast.walk(node.body):
                if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    bad.append(f"line {sub.lineno}: assignment inside lambda body")
    return bad


def main() -> int:
    from dcg.codegen import generate

    print("=" * 78)
    print("early v2 defect probe -- dcg.codegen.generate (public surface not exported yet)")
    print("=" * 78)

    for label, schema, cap in (("CAP(4 fields)", W.V2_CAP, 180),
                               ("M0(8 fields)", W.V2_SCHEMA, 210)):
        src = generate(schema)
        run_part = per_schema_part(src)
        print(f"\n--- {label} ---")
        print(f"chars_total={len(src)}  chars_run_part={len(run_part)}  "
              f"cap={cap}  full<=cap:{len(src) <= cap}  run_part<=cap:{len(run_part) <= cap}")
        try:
            ast.parse(src)
            print("ast.parse: OK (valid Python)")
        except SyntaxError as e:
            print(f"ast.parse: **SyntaxError** line {e.lineno}: {e.msg}\n  {e.text}")
        viol = lambda_assignment_violations(src)
        print(f"lambda-body assignments: {viol or 'none'}")

    print("\n--- minimal 3-row semantics (filter + row shape) ---")
    src = generate(W.V2_CAP)
    ns = {}
    exec(compile(src, "<probe>", "exec"), ns)   # noqa: S102 - probe only
    run = ns["run"]
    rows = [("f0", 1, 10, 3, 4, {"a": [1, 2, 3, 4]}),      # payload at r[5]
            None,                                          # falsy -> must be dropped
            ("f1", 1, 20, 3, 4, {"a": [5, 6, 7, 8]})]
    print("input rows: 3 (row[1] is None -> must be dropped when output.filter=true)")
    try:
        out = run(rows)
        print(f"len(out)={len(out)}   out={out}")
        print(f"filter verdict: {'OK' if len(out) == 2 else 'FAIL (filter 未丢弃假值行)'}")
    except Exception as e:
        print(f"run() 含 None 行 -> **{type(e).__name__}: {e}**")
        print("filter verdict: FAIL — 契约 output.filter=true 应丢弃假值行, "
              "当前 filter 条件用的是整个输入列表 (产物里 `for R in _in if _in`) 而不是行 R")
        out = run([rows[0], rows[2]])
        print(f"(去掉 None 行后) len(out)={len(out)}   out={out}")
    # CAP: hourA=$.5.a.1 -> a[1]=2 ; hourB=$.5.a.2 -> a[2]=3 ;
    #      hourD=$string($.5.a.1)&$string($.5.a.2) -> "23" ; hourE=2+3=5
    expected = [[2, 3, "23", 5], [6, 7, "67", 13]]
    print(f"expected {expected}  (CAP: a[1], a[2], str&str, a[1]+a[2])")
    print(f"value match ignoring container type: "
          f"{[list(r) for r in out] == expected}   row types: "
          f"{[type(r).__name__ for r in out]} vs expected list")

    print("\n--- generated helpers vs dcg/runtime.py (duplication check) ---")
    runtime = open(os.path.join(ROOT, "dcg", "runtime.py"), encoding="utf-8").read()
    for name in ("_ad(", "_S(", "_num(", "def _S", "def _ad", "def _num"):
        print(f"  runtime.py contains {name!r}: {name in runtime}")
    print(f"  generated source defines _S/_ad/_num itself: "
          f"{all(x in src for x in ('def _S', 'def _ad', 'def _num'))}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
