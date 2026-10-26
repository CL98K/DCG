"""Shared workload fixtures for DCG's independent verification.

Two things live here:

* **Row generators** (`make_rows`) and the two frozen schemas (`V2_SCHEMA` /
  `V2_CAP` / `V2_CAP_EXT` / `V2_M0`) — the exact objects the codegen contract
  tests generate and execute against. These are *not* benchmarks: the row shape
  and field types are what make the compile-time provability rules
  (`_ok_num` / `_never_undef` / inline emission) bite, so the tests pin them.
* **Loaders** (`load_v2`, `v2_ok`, `v2_ready`, `normalize`,
  `v2_rows_for_shape`) and the hand-written reference (`ref_tight`) used for
  value-for-value equivalence checks.

Row shape: `(code:str, a:int, b:int, c:int, d:int, payload:{'a':[4]})`
-> `payload` is at row index 5.

This module is verification-only. It never writes anything under `dcg/`.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

N = 200_000


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
    "output": {"shape": "list", "trace": False},
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

# The 4-field CAP workload in the frozen v2 schema (char budget retired 2026-10-21).
V2_CAP = {
    "name": "cap",
    "output": {"shape": "list"},
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

# The 8-field M0 workload in the frozen v2 schema (char budget retired 2026-10-21).
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
# V2_CAP_EXT / V2_M0 are verbatim evidence/accept.py CAP/M0 so these tests and
# the Lead's gate measure ONE object.
# --------------------------------------------------------------------------
V2_CAP_EXT = {
    "name": "cap",
    "input": {},
    "output": {"shape": "list"},
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
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
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


# --------------------------------------------------------------------------
# README 附录 / 正文里内联演示的 schema —— 由 tests/test_doc_products.py
# 用 `# dcg: <本名>` 标记反向锁住文档里的产物块。改这里等于改文档，反之亦然。
# --------------------------------------------------------------------------

DOC_DEEP = {
    "name": "deep",
    "output": {"shape": "list"},
    "out": {"type": "object",
            "fields": {"v": {"type": "int64", "expr": "`$.a.b.c.d`"}}},
}

DOC_IN0 = {
    "name": "in0",
    "input": {"source": "$"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}},
}

DOC_IN1 = {
    "name": "in1",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}},
}

DOC_IN2 = {
    "name": "in2",
    "input": {"source": "$.data.0.rows"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}},
}

DOC_IN3 = {
    "name": "in3",
    "input": {"source": "$['x y'].k"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}},
}

DOC_SHAPE_ROWS = {
    "name": "shape_rows",
    "output": {"shape": "rows"},
    "out": {"type": "object", "fields": {
        "sku": {"type": "str", "value": "$.sku"},
        "n":   {"type": "int64", "value": "$.n"}}},
}

DOC_BEFORE_PATH = {
    "name": "before_path",
    "input": {"source": "$"},
    "output": {"shape": "list", "before": "= $.rows"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_BEFORE_SORT = {
    "name": "before_sort",
    "output": {"shape": "list", "before": "= $sort($, function($a,$b){ $a > $b })"},
    "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$"}}},
}

DOC_BEFORE_PY = {
    "name": "before_py",
    "output": {"shape": "list", "before": "= `[r for r in $ if r['n'] > 1]`"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_BEFORE_PYBLOCK = {
    "name": "before_pyblock",
    "output": {"shape": "list",
               "before": "= py{ let rows = [r for r in $.rows if r[0] > 1]; return rows }"},
    "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}},
}

DOC_BEFORE_PYBLOCK_DOLLAR = {
    "name": "before_pyblock_dollar",
    "output": {"shape": "list",
               "before": "= py{ let rows = [r for r in $ if r[0] > 1]; return rows }"},
    "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}},
}

DOC_AFTER_OBJ = {
    "name": "after_obj",
    "input": {"source": "$.rows"},
    "output": {"shape": "list", "after": "= {'n': $count($), 'rows': $}"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_AFTER_PY = {
    "name": "after_py",
    "output": {"shape": "list", "after": "= `json.dumps($)`"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_AFTER_PYBLOCK = {
    "name": "after_pyblock",
    "output": {"shape": "list",
               "after": "= py{ let col = [r[0] * 100 for r in $]; return col }"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_AFTER_DICT = {
    "name": "after_dict",
    "input": {"source": "$.batch.rows"},
    "output": {"shape": "dict", "after": "= {'rows': $count($.v), 'col': $}"},
    "out": {"type": "object", "fields": {
        "v": {"type": "int64", "value": "$.0"},
        "w": {"type": "int64", "value": "$.1"}}},
}

DOC_BEFORE_AFTER = {
    "name": "before_after",
    "output": {"shape": "list", "before": "= `[r for r in $ if r[0] > 1]`",
               "after": "= {'n': $count($)}"},
    "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}},
}

DOC_TRACE = {
    "name": "t",
    "output": {"shape": "list", "trace": True},
    "out": {"type": "object", "fields": {"v": {"type": "int64", "const": 1}}},
}

DOC_OPTIONAL = {
    "name": "opt1",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "optional": True, "value": "$.a"}}},
}

DOC_AGG_PLAIN = {
    "name": "agg_plain",
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        "sku": {"type": "str", "value": "$.sku"},
        "item": {"type": "object", "fields": {
            "s": {"type": "str", "value": "$.sku"},
            "n": {"type": "int64", "value": "$.n"}}},
    }},
}

DOC_AGG_ON = {
    "name": "agg_on",
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        "sku": {"type": "str", "value": "$.sku"},
        "stat": {"type": "object", "aggregate": True, "fields": {
            "n": {"type": "int64", "value": "$.n"}}},
    }},
}

DOC_CYTHON = {
    "name": "cy",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}},
}

DOC_ALL5 = {
    "name": "all5",
    "input": {"source": "$.batch.rows"},
    "output": {"shape": "dict",
               "before": "= $sort($, function($a,$b){ $a[0] > $b[0] })",
               "after": "= {'rows': $count($.v), 'col': $}",
               "trace": True},
    "out": {"type": "object", "fields": {
        "v": {"type": "int64", "value": "$.0"},
        "w": {"type": "int64", "value": "$.1"}}},
}

DOC_COMBO2 = {
    "name": "combo2",
    "output": {"shape": "rows",
               "before": "= `[r for r in $ if r['qty'] > 0]`",
               "after": "= {'count': $count($), 'items': $}"},
    "out": {"type": "object", "fields": {
        "sku": {"type": "str", "value": "$.sku"},
        "qty": {"type": "int64", "value": "$.qty"}}},
}

# same schema, re-generated with inline_optional=False
DOC_INLINE_OPT_OFF = DOC_OPTIONAL

def inline_optional_off_src():
    """README §A.9 的 `inline_optional=False` 产物（工厂，避免 import 时就算）。"""
    import dcg as _dcg
    return _dcg.generate(DOC_OPTIONAL, inline_optional=False)

def v2_input(rows, schema):
    """把 `rows` 包成 schema 的 `input.source` 路径要求的样子。

    `input.source` 是行集合在输入数据里的路径（缺省 `$` = 整份输入就是行集合）。
    这里只支持单段命名路径：`"$.index"` -> `{"index": rows}`，其余（含缺省）
    直接给裸列表。
    """
    src = (schema.get("input") or {}).get("source") or "$"
    if src.startswith("$.") and src.count(".") == 1 \
            and src[2:].isidentifier():
        return {src[2:]: rows}
    return rows


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
    """(ready, reason) -- friendly gate for the workload helpers."""
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

    Per DCG 接口实现细节文档.md 搂4.1:
      shape='list'  -> list of rows
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
