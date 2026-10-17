"""Shared workload for DCG v2 independent verification.

Frozen methodology (CONTRACT.md §7, 禁止改动):
  * same machine / same data / same process
  * gc.collect() before each timing rep
  * warm up, then take the MINIMUM of 3 reps (not mean, not median)
  * 200000 rows x 8 fields; row shape (code:str, a:int, b:int, c:int, d:int,
    payload:{'a':[4]})  -> payload is at row index 5

The row generator is byte-for-byte identical to bench/baseline_v1.py and
bench/decompose.py so the three-way numbers stay comparable.

This module is verification-only. It never writes anything under dcg/.
"""
from __future__ import annotations

import gc
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BENCH = os.path.join(ROOT, "bench")
EVIDENCE = os.path.join(ROOT, "evidence")
FROZEN = os.path.join(BENCH, "frozen")

N = 200_000
REPS = 3


def make_rows(n=N):
    """Exactly bench/baseline_v1.py:15-20 (frozen)."""
    rows = []
    for i in range(n):
        rows.append((f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
                     {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}))
    return rows


# --------------------------------------------------------------------------
# v1 side.  M0 = format.py:589-603, minus the field using `#` position binding
# (`#.hour.3.2`) which CONTRACT §3.5 rejects for v2 -> 8 output fields.
# CAP = the 4-field workload frozen in bench/capture_src.py.
# Labels are "M0" / "CAP" on purpose: the generated function name is
# `_inline_call_<label>`, and the frozen char counts (M0=537, CAP=445) are
# measured with exactly these labels.
# --------------------------------------------------------------------------
V1_CAP_DESCRIBE = {
    "datas": "$.index",
    "mode": "",
    "protocol": True,
    "single": False,
    "filter": "",
    "_field": {},
    "struct": {
        "doc": [
            {"name": "hourA", "alias": "", "type": "int", "value": "$.5.a.1"},
            {"name": "hourB", "alias": "", "type": "int", "value": "$.5.a.2"},
            {"name": "hourD", "alias": "", "type": "str", "value": "",
             "ext": "str($.5.a.1) + str(hourB)"},
            {"name": "hourE", "alias": "", "type": "int", "value": "",
             "ext": "hourA + hourB"},
        ]
    },
}

V1_DESCRIBE = {
    "datas": "$.index",
    "mode": "",
    "protocol": True,
    "single": False,
    "filter": "",
    "_field": {},
    "struct": {
        "doc": [
            {"name": "hour",  "alias": "", "type": "list", "value": "$.5.a"},
            {"name": "hourA", "alias": "", "type": "int",  "value": "$.5.a.1"},
            {"name": "hourB", "alias": "", "type": "int",  "value": "$.5.a.2"},
            {"name": "hourC", "alias": "", "type": "int",  "value": "$.2"},
            {"name": "hourD", "alias": "", "type": "str",  "value": "",
             "ext": "str($.5.a.1) + str(hourB)"},
            {"name": "hourE", "alias": "", "type": "int",  "value": "",
             "ext": "hourA + hourB"},
            {"name": "hourF", "alias": "", "type": "int",  "value": "",
             "ext": "$.5.a.2 + $.5.a.1"},
            {"name": "hourG", "alias": "", "type": "int",  "value": "",
             "ext": "hour[2] + 123"},
        ]
    },
}

# --------------------------------------------------------------------------
# v2 side: same 8 fields expressed in the frozen v2 schema (CONTRACT §2).
#   hourD = str($.5.a.1) + str($.5.a.2)  ->  $string(...) & $string(...)
#   hourG = hour[2] + 123                ->  $.5.a.2 + 123   (numeric index)
# --------------------------------------------------------------------------
V2_SCHEMA = {
    "name": "flight",
    "output": {"shape": "list", "single": False, "filter": True,
               "post": None, "trace": False},
    "out": {
        "type": "object",
        "fields": {
            "hour":  {"type": "list",  "value": "$.5.a"},
            "hourA": {"type": "int64", "value": "$.5.a.1"},
            "hourB": {"type": "int64", "value": "$.5.a.2"},
            "hourC": {"type": "int64", "value": "$.2"},
            "hourD": {"type": "str",   "expr": "$string($.5.a.1) & $string($.5.a.2)"},
            "hourE": {"type": "int64", "expr": "$.5.a.1 + $.5.a.2"},
            "hourF": {"type": "int64", "expr": "$.5.a.2 + $.5.a.1"},
            "hourG": {"type": "int64", "expr": "$.5.a.2 + 123"},
        },
    },
}

# The 4-field CAP workload in the frozen v2 schema (char verdict: v1=445 -> v2 <=180).
V2_CAP = {
    "name": "cap",
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {
        "type": "object",
        "fields": {
            "hourA": {"type": "int64", "value": "$.5.a.1"},
            "hourB": {"type": "int64", "value": "$.5.a.2"},
            "hourD": {"type": "str",   "expr": "$string($.5.a.1) & $string($.5.a.2)"},
            "hourE": {"type": "int64", "expr": "$.5.a.1 + $.5.a.2"},
        },
    },
}

# The 8-field M0 workload in the frozen v2 schema (char verdict: v1=537 -> v2 <=210).
V2_MINIMAL = V2_SCHEMA

# --------------------------------------------------------------------------
# CONTRACT §7 char-budget basis (Lead's gate reads V2_CAP/V2_M0 from here).
#
# The budget compares v2 against the v1 artifact on the SAME workload, so the
# basis must be the v1-faithful *ext* form (backtick py-segment + alias
# references), NOT a terse JSONata re-expression.  Two axes separate the two:
#   (a) input.source: "index" makes the product open with `_in=_in['index']`
#       (+18 chars) AND changes the call convention: run() takes {"index": rows}
#       (the v1 `datas` layout), not a plain list of rows.
#   (b) expr form: the backtick segment `` `str($.5.a.1) + str(hourB)` `` names
#       hourB, so codegen must materialise the aliases (hour/hourA/hourB lines);
#       `$string($.5.a.1) & $string($.5.a.2)` calls _S inline and needs none.
# Measured (codegen sha1 46138B90..., runtime CD760A6F..., 2026-10-17 00:49,
# probe bench/probe_v2_m0_basis.py, evidence/v2_m0_basis_probe.txt):
#   CAP ext=205 (>180 FAIL) | CAP jsonata=176 (PASS, exactly on the cap)
#   M0  ext=293 (>210 FAIL) | M0  jsonata=210 (PASS, exactly on the cap)
# V2_CAP_EXT / V2_M0 are verbatim evidence/accept.py CAP/M0 so my bench, the
# Lead's gate and parser-dev all measure ONE object.
# --------------------------------------------------------------------------
V2_CAP_EXT = {
    "name": "cap",
    "input": {"source": "rows"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {
        "type": "object",
        "fields": {
            "hourA": {"type": "int64", "value": "$.5.a.1"},
            "hourB": {"type": "int64", "value": "$.5.a.2"},
            "hourD": {"type": "str", "expr": "`str($.5.a.1) + str(hourB)`"},
            "hourE": {"type": "int64", "expr": "hourA + hourB"},
        },
    },
}

V2_M0 = {
    "name": "m0",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {
        "type": "object",
        "fields": {
            "hour":  {"type": "list",  "value": "$.5.a"},
            "hourA": {"type": "int64", "value": "$.5.a.1"},
            "hourB": {"type": "int64", "value": "$.5.a.2"},
            "hourC": {"type": "int64", "value": "$.2"},
            "hourD": {"type": "str", "expr": "`str($.5.a.1) + str(hourB)`"},
            "hourE": {"type": "int64", "expr": "hourA + hourB"},
            "hourF": {"type": "int64", "expr": "$.5.a.2 + $.5.a.1"},
            "hourG": {"type": "int64", "expr": "hour[2] + 123"},
        },
    },
}


def v2_input(rows, schema):
    """Wrap `rows` the way a schema's input.source demands.

    input.source == "index" (the v1 `datas` layout) -> {"index": rows};
    "rows" / absent -> the bare list.
    """
    src = (schema.get("input") or {}).get("source")
    return {"index": rows} if src == "index" else rows



# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
def bench_reps(fn, reps=REPS, warmup=True):
    """CONTRACT §7: warm up, gc.collect() before each rep; return all rep durations (s).

    The Lead's rule for this project: report the MINIMUM and also the spread --
    v1 measured 0.480 us/row (frozen) vs 0.588 us/row on the same machine, so a
    single rep is not evidence.
    """
    if warmup:
        fn()
    out = []
    for _ in range(reps):
        gc.collect()
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def bench(fn, reps=REPS, warmup=True):
    """CONTRACT §7: warm up, gc.collect() before each rep, return MIN of reps."""
    return min(bench_reps(fn, reps=reps, warmup=warmup))


def us_per_row(d, n=N):
    return d / n * 1e6


def rows_per_s(d, n=N):
    return n / d


# --------------------------------------------------------------------------
# hand-written reference (bench/decompose.py variant A, frozen)
# --------------------------------------------------------------------------
def ref_tight(datas):
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        h, hA, hB = a, a[1], a[2]
        ap([h, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123])
    return {"doc": out}


# --------------------------------------------------------------------------
# loaders
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# v1 engine loading.
#
# The v2 package `dcg/` shadows the v1 module `dcg.py`, and v1's `format.py:13`
# does `from dcg import ...`, so `import format` alone now fails.  The bridge
# (single implementation, owned by the Lead) pins sys.modules["dcg"] to v1's
# dcg.py only for the duration of the v1 import, then restores the v2 package,
# so v1 and v2 remain usable in the same process (§7 same-process comparison).
# --------------------------------------------------------------------------
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)
from _v1bridge import load_v1_engine, v1_datas  # noqa: E402


def load_v1(desc=None, label="M0"):
    """Return (handler, manifest_info). v1 is read-only reference."""
    fmt = load_v1_engine()
    fmt.Format.OPT_MODE = True
    fmt.Format.CYTHON_MODE = False
    fmt.Format.SAFE_MODE = False
    fmt.Format.DEBUG = False
    info, handler = fmt.Format.parse(label, desc if desc is not None else V1_DESCRIBE)
    return handler, info


def load_v1_src(desc=None, label="M0"):
    """Capture the source v1 actually compiles (bench/capture_src.py technique).

    format.py compile()s a lot of internal helper snippets too; only the generated
    handler bodies are kept (`def _inline_call_...`)."""
    import builtins
    fmt = load_v1_engine()
    fmt.Format.OPT_MODE = True
    fmt.Format.CYTHON_MODE = False
    grabbed = []
    orig = builtins.compile

    def spy(src, *a, **k):
        if isinstance(src, str) and "_inline_call_" in src:
            grabbed.append(src)
        return orig(src, *a, **k)

    builtins.compile = spy
    try:
        fmt.Format.parse(label, desc if desc is not None else V1_DESCRIBE)
    finally:
        builtins.compile = orig
    return grabbed


def load_v2(schema=None, cython=False, inline_optional=True):
    """generate() -> (callable run, source). Requires dcg/ to be importable."""
    import dcg
    src = dcg.generate(schema if schema is not None else V2_SCHEMA,
                       cython=cython, inline_optional=inline_optional)
    ns = {}
    exec(compile(src, "<dcg-generated>", "exec"), ns)
    return ns["run"], src


def v2_ok():
    """True when the v2 implementation is importable and exposes the frozen API."""
    try:
        import dcg
    except Exception:
        return False
    return all(hasattr(dcg, n) for n in ("validate", "generate", "build", "gen_name",
                                         "compile_expr", "Unsupported", "Registry"))


def v2_ready():
    """(ready, reason) -- friendly gate for the benchmark scripts."""
    try:
        import dcg
    except Exception as e:
        return False, f"import dcg failed: {type(e).__name__}: {e}"
    missing = [n for n in ("validate", "generate", "build", "gen_name",
                           "compile_expr", "Unsupported", "Registry")
               if not hasattr(dcg, n)]
    if missing:
        return False, f"dcg 缺失公开面: {missing}"
    return True, "ok"


def normalize(res):
    """Row list out of any v1/v2 container.

    v1 returns {'doc': [[...], ...]} (row-shaped columns under 'doc');
    v2 shape='list' returns [[...], ...] directly.
    """
    if isinstance(res, dict):
        return res.get("doc", res)
    return res


def v2_rows_for_shape(res, shape, fields, n=None):
    """Turn a v2 run() result into `rows` (list of positional row lists).

    Per the Lead's ruling (2026-xx, m00101) and CONTRACT.md §4.1:
      shape='list'  -> list of rows; single=True -> the single row itself
      shape='dict'  -> COLUMNAR {'field': [v per row]}  (NOT v1's row-shaped 'doc')
      shape='index' -> {row_index(int): row value}
    """
    if shape == "list":
        if isinstance(res, list) and res and not isinstance(res[0], list) and n == 1:
            return [res]
        return res
    if shape == "dict":
        if not isinstance(res, dict):
            raise AssertionError(f"shape=dict must return dict, got {type(res).__name__}")
        keys = list(res.keys())
        missing = [f for f in fields if f not in res]
        if missing:
            raise AssertionError(f"shape=dict missing fields {missing}; got {keys}")
        extra = [k for k in keys if k not in fields]
        if extra:
            raise AssertionError(f"shape=dict has unexpected keys {extra}")
        cols = [res[f] for f in fields]
        return [list(t) for t in zip(*cols)]
    if shape == "index":
        if not isinstance(res, dict):
            raise AssertionError(f"shape=index must return dict, got {type(res).__name__}")
        for k in res:
            if not isinstance(k, int):
                raise AssertionError(f"shape=index keys must be int, got {type(k).__name__}")
        order = sorted(res)
        return [res[k] for k in order]
    raise AssertionError(f"unknown shape {shape!r}")
