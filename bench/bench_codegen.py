"""Codegen statistics + CONTRACT §4.2 nine-rule verdicts, v1 vs v2.

Measures from the *actual source text* (never from a self-report):
  * chars / lines / intermediate variables / append calls
  * the nine hard rules via bench/codegen_rules.py
  * determinism (two generate() calls byte-identical)
  * compile() call count (v1 compiles twice incl. a 118-char stub; v2 must be once, no stub)

Run:  python bench/bench_codegen.py
"""
from __future__ import annotations

import builtins
import hashlib
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _workload as W  # noqa: E402
import codegen_rules as CR  # noqa: E402


def source_id():
    """Pin every number to the revision it was measured on.

    parser-dev iterates codegen.py minute-by-minute, so an unpinned char count is
    meaningless: the same probe measured CAP=202/174 and M0=287/283 minutes apart.
    """
    out = [datetime.now().astimezone().isoformat(timespec="seconds")]
    for f in ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py"):
        p = os.path.join(W.ROOT, *f.split("/"))
        try:
            with open(p, "rb") as fh:
                out.append(f"{f}={hashlib.sha1(fh.read()).hexdigest().upper()[:12]}")
        except OSError:
            out.append(f"{f}=?")
    return "  ".join(out)


def self_test_analyser():
    """Validate the analyser against known-bad v1 source. If it cannot flag v1,
    an 'all clear' on v2 would be worthless."""
    srcs = W.load_v1_src()
    real = max(srcs, key=len)
    stub = min(srcs, key=len)
    verdicts = {name: ok for name, ok, _d, _o in CR.run_all(real)}
    checks = {
        "rule3 flags v1 buffer/doc passthrough": not verdicts["3 零只用于 return 的中转"],
        "rule8 flags v1 random names": not verdicts["8 变量名确定性（无 random）"],
        "rule2 flags v1 118-char stub": not dict(
            (n, ok) for n, ok, _d, _o in CR.run_all(stub)
        )["2 零恒等语句/空桩"],
        "rule2 passes on a clean hand-written body": CR.rule2_no_identity_statements(
            "def run(rows):\n    out = []\n    for r in rows:\n        out.append(r)\n"
            "    return out\n")[0],
        "rule1 flags relookup": not CR.rule1_global_rowhead_unpack(
            "def run(rows):\n    for r in rows:\n        out.append(r[5]['a'][1] + r[5]['a'][2])\n"
            "    return out\n")[0],
        "rule1 passes on unpack-once": CR.rule1_global_rowhead_unpack(
            "def run(rows):\n    for r in rows:\n        a = r[5]['a']\n"
            "        out.append(a[1] + a[2])\n    return out\n")[0],
        # rule 3 caliber (Lead ruling): output-path middle layers only.
        "rule3 exempts input adaptation (_in=_in['index'])": CR.rule3_no_passthrough_buffer(
            "def run(_in):\n    _in = _in['index']\n    out = []\n    return out\n")[0],
        "rule3 exempts field aliases (hour=_a / hourA=_a1)": CR.rule3_no_passthrough_buffer(
            "def run(R):\n    _a = R[5]['a']\n    hour = _a\n    hourA = _a[1]\n"
            "    return [hour, hourA]\n")[0],
        "rule3 passes on row-key access that is not a local container":
            CR.rule3_no_passthrough_buffer(
                "def run(R):\n    v = R['a']\n    return [v]\n")[0],
        "rule3 still flags the v1 middle layer": not CR.rule3_no_passthrough_buffer(
            "def run(datas):\n    buffer_x = {'doc': []}\n    doc_x = buffer_x['doc']\n"
            "    return buffer_x\n")[0],
    }
    print("--- analyser self-test (against captured v1 source) ---")
    for k, v in checks.items():
        print(f"  [{'ok' if v else 'BROKEN'}] {k}")
    return real, stub, all(checks.values())


def compile_spy(fn):
    """Count builtins.compile() calls, split into product compiles vs ast.parse.

    `ast.parse(src)` calls compile() with a flags argument (PyCF_ONLY_AST), so a
    naive count mixes compile-time *validation* of py-segments with the compile of
    the executable product.  Only "product-exec" is the rule-6 comparison basis
    (v1 = 2: the 118-char stub + the real artifact).  Probe:
    bench/probe_compile_count.py, evidence/v2_compile_count_probe.txt.
    """
    grabbed = []
    orig = builtins.compile

    def spy(src, *a, **k):
        if isinstance(src, str):
            # The *product* always contains `def run(`; compile-time validation of
            # backtick py-segments (ast.parse or `compile(seg,'...','eval')`) does
            # not, whatever the call signature looks like.
            kind = "product-exec" if "def run(" in src else "segment"
            grabbed.append((kind, src))
        return orig(src, *a, **k)

    builtins.compile = spy
    try:
        result = fn()
    finally:
        builtins.compile = orig
    return result, grabbed


def product_compiles(grabbed):
    return [s for k, s in grabbed if k == "product-exec"]


def main():
    print("=" * 78)
    print("DCG v2 -- bench_codegen.py   (CONTRACT §4.2 / §7 产物字符数)")
    print("revision: " + source_id())
    print("=" * 78)

    real, stub, analyser_ok = self_test_analyser()
    print(f"\nanalyser self-test overall: {'OK' if analyser_ok else 'BROKEN'}")
    print(f"v1 M0 compiled sources: 2 expected (stub {len(stub)} chars, real {len(real)} chars)")

    V1_DESC = {"CAP": W.V1_CAP_DESCRIBE, "M0": W.V1_DESCRIBE}

    # ---------------- v1: both canonical workloads ----------------
    print("\n" + "=" * 78)
    print("v1 (format.Format.parse) -- real generated source")
    print("=" * 78)
    v1 = {}
    for label, desc in V1_DESC.items():
        srcs = W.load_v1_src(desc, label)
        real = max(srcs, key=len)
        stub = min(srcs, key=len)
        v1[label] = real
        print(f"\n--- v1 {label}: compile() calls={len(srcs)} "
              f"sizes={sorted(len(s) for s in srcs)} ---")
        print(CR.render(real))
        print(CR.format_report(real, None, None, f"v1 {label} real source"))
        s1 = CR.stats(real)
        print(f"v1 {label}: chars={s1['chars_total']} lines={s1['lines_total']} "
              f"assigns={s1['n_assign']} appends={s1['n_append']}")

    # ---------------- v2 ----------------
    print("\n" + "=" * 78)
    print("v2 (dcg.generate) -- CAP (4 fields) and M0 (8 fields)")
    print("=" * 78)
    if not W.v2_ok():
        print(f"SKIPPED: {W.v2_ready()[1]}")
        print("Re-run this script once parser-dev/jx-dev land their modules.")
        print("\n[对照基线] v1 chars: " + "  ".join(
            f"{k}={len(s)}" for k, s in v1.items()))
        return

    import dcg

    # Lead ruling ①: A (v1-faithful ext) is the budget basis and carries PASS/FAIL;
    # B (equivalent v2-native) is informational only and must never be reported alone.
    loads = (("CAP[A: budget basis v1-faithful ext]", W.V2_CAP_EXT, 180, "verdict"),
             ("M0[A: budget basis v1-faithful ext]", W.V2_M0, 210, "verdict"),
             ("CAP[B: informational v2-native]", W.V2_CAP, 180, "informational"),
             ("M0[B: informational v2-native]", W.V2_SCHEMA, 210, "informational"))
    for label, schema, cap, group in loads:
        v1_label = "CAP" if label.startswith("CAP") else "M0"
        print("\n" + "-" * 78)
        print(f"v2 {label} (v1 {v1_label}={len(v1[v1_label])} chars, cap={cap})")
        print("-" * 78)
        try:
            src, gen_compiles = compile_spy(lambda: dcg.generate(schema))
            src_b = dcg.generate(schema)
            cy = None
            try:
                cy = dcg.generate(schema, cython=True)
            except Exception as e:
                print(f"cython=True generate failed: {type(e).__name__}: {e}")

            print(CR.render(src))
            print(CR.format_report(src, schema, cy, f"v2 {label}"))
            s2 = CR.stats(src, schema)
            print(f"v2 {label}: chars={s2['chars_total']} chars_nodoc={s2['chars_nodoc']} "
                  f"lines={s2['lines_total']} assigns={s2['n_assign']} "
                  f"appends={s2['n_append']}")
            tag = "" if group == "verdict" else " [informational — 不得单独用来宣称达标]"
            print(f"  char target <= {cap}: "
                  f"{'PASS' if s2['chars_total'] <= cap else 'FAIL'} "
                  f"(total) / {'PASS' if s2['chars_nodoc'] <= cap else 'FAIL'} (no-docstring) "
                  f"| v1={len(v1[v1_label])} reduction="
                  f"{100 * (1 - s2['chars_total'] / len(v1[v1_label])):.1f}%{tag}")
            print(f"  determinism (2x generate byte-identical): {src == src_b}")
            print(f"  compile() during generate(): product-exec="
                  f"{len(product_compiles(gen_compiles))} (rule 6: must be 1; v1 {v1_label} had "
                  f"{len(W.load_v1_src(V1_DESC[v1_label], v1_label))} = stub + real), "
                  f"py-segment compile-time validations="
                  f"{sum(1 for k, _ in gen_compiles if k == 'segment')}")
            print(f"  rule 4 (type is declaration, not conversion) -- 'int(' in source: "
                  f"{src.count('int(')} occurrences")
            if cy is not None:
                # Lead ruling ⑤: the cython product must NOT be charged to the
                # <=180/<=210 budget -- the budget is for the pure-Python product.
                # Hard assertion: cython shape == pure shape + one `cdef` line per
                # local, i.e. stripping every cdef line reproduces the pure source.
                cdef_lines = [ln for ln in cy.splitlines() if ln.strip().startswith("cdef ")]
                stripped = "\n".join(ln for ln in cy.splitlines()
                                     if not ln.strip().startswith("cdef "))
                same = stripped == src.rstrip("\n")
                print(f"  cython=True: {len(cy)} chars -> NOT charged to the {cap} budget "
                      f"(pure-Python product is {len(src)})")
                print(f"    shape rule (strip cdef lines == pure product): {same} "
                      f"(cdef lines={len(cdef_lines)})")
                if not same:
                    import difflib
                    print("    diff:", list(difflib.unified_diff(
                        stripped.splitlines(), src.splitlines(), lineterm="", n=0))[:8])
        except Exception as e:
            import traceback
            print(f"v2 {label} FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()

    # build() must also compile exactly once and leave no stub artifact
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            man, grabbed = compile_spy(lambda: dcg.build(W.V2_M0, td))
            print(f"\nbuild(): product-exec compiles = {len(product_compiles(grabbed))} "
                  f"(must be 1); py-segment validations = "
                  f"{sum(1 for k, _ in grabbed if k == 'segment')}; manifest keys = "
                  f"{sorted(man) if isinstance(man, dict) else man}")
            print("built files:", sorted(os.listdir(td)))
    except Exception as e:
        print(f"\nbuild() FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
