"""Shared workload for DCG v2 independent verification.

Frozen methodology (接口实现细节文档.md 搂7, 绂佹鏀瑰姩):
  * same machine / same data / same process
  * gc.collect() before each timing rep
  * warm up, then take the MINIMUM of 3 reps (not mean, not median)
  * 200000 rows x 8 fields; row shape (code:str, a:int, b:int, c:int, d:int,
    payload:{'a':[4]})  -> payload is at row index 5

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
    """Frozen row generator (frozen shape (CONTRACT §7))."""
    rows = []
    for i in range(n):
        rows.append((f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
                     {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}))
    return rows


# --------------------------------------------------------------------------
# The 8-field M0 workload in the frozen v2 schema (CONTRACT 搂2).
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

# The 4-field CAP workload in the frozen v2 schema (char budget: <=180).
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

# The 8-field M0 workload in the frozen v2 schema (char budget: <=210).
V2_MINIMAL = V2_SCHEMA

# --------------------------------------------------------------------------
# CONTRACT 搂5 char-budget basis (Lead's gate reads V2_CAP/V2_M0 from here).
#
# The budget is measured on the *ext-faithful* form (backtick py-segment +
# alias references), NOT a terse JSONata re-expression.  Two axes separate:
#   (a) input.source: "index" makes the product open with `_in=_in['index']`
#       (+18 chars) AND changes the call convention: run() takes {"index": rows},
#       not a plain list of rows.
#   (b) expr form: the backtick segment `` `str($.5.a.1) + str(hourB)` `` names
#       hourB, so codegen must materialise the aliases (hour/hourA/hourB lines);
#       `$string($.5.a.1) & $string($.5.a.2)` calls _S inline and needs none.
# Measured (probe bench/probe_v2_m0_basis.py, evidence/v2_m0_basis_probe.txt):
#   CAP ext=205 (>180 FAIL) | CAP jsonata=176 (PASS)
#   M0  ext=293 (>210 FAIL) | M0  jsonata=210 (PASS)
# V2_CAP_EXT / V2_M0 are verbatim evidence/accept.py CAP/M0 so my bench and the
# Lead's gate measure ONE object.
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

    input.source == "index" -> {"index": rows}; "rows" / absent -> the bare list.
    """
    src = (schema.get("input") or {}).get("source")
    return {"index": rows} if src == "index" else rows


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
def bench_reps(fn, reps=REPS, warmup=True):
    """CONTRACT 搂7: warm up, gc.collect() before each rep; return all rep durations (s).

    The rule for this project: report the MINIMUM and also the spread --
    a single wall-clock rep on this box is not evidence.
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
    """CONTRACT 搂7: warm up, gc.collect() before each rep, return MIN of reps."""
    return min(bench_reps(fn, reps=reps, warmup=warmup))


def us_per_row(d, n=N):
    return d / n * 1e6


def rows_per_s(d, n=N):
    return n / d


# --------------------------------------------------------------------------
# hand-written reference (plain Python, no code generation)
# --------------------------------------------------------------------------
def ref_tight(datas):
    """Hand-written reference for the 8-field workload; returns a bare row list."""
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        h, hA, hB = a, a[1], a[2]
        ap([h, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123])
    return out


# --------------------------------------------------------------------------
# loaders
# --------------------------------------------------------------------------
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
        return False, f"dcg 缂哄け鍏紑闈? {missing}"
    return True, "ok"


def normalize(res):
    """Row list out of a v2 container (shape='list' returns the rows directly)."""
    if isinstance(res, dict):
        return res.get("doc", res)
    return res


def v2_rows_for_shape(res, shape, fields, n=None):
    """Turn a v2 run() result into `rows` (list of positional row lists).

    Per 接口实现细节文档.md 搂4.1:
      shape='list'  -> list of rows; single=True -> the single row itself
      shape='dict'  -> COLUMNAR {'field': [v per row]}
      shape='index' -> {row_index(int): row value}
      shape='rows'  -> [{field: v}, ...] (per-row dicts)
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
    if shape == "rows":
        if not isinstance(res, list):
            raise AssertionError(f"shape=rows must return list, got {type(res).__name__}")
        return [[row[f] for f in fields] for row in res]
    raise AssertionError(f"unknown shape {shape!r}")
