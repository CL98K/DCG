"""Empirical probe of the v1 -> v2 specification gaps (read-only, task-5).

Every claim in `docs/examples_v1_to_v2.md` about "v2 has no equivalent" is produced
here by actually calling the public `dcg` surface, never by reading the contract and
assuming.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "tests", "examples")):
    if p not in sys.path:
        sys.path.insert(0, p)

import dcg  # noqa: E402

ROW = (0, 0, 0, 0, 0, {"a": [0, 1, 2, [1, 2, 3]]})


def base(**out_fields):
    return {
        "name": "gap_probe",
        "input": {"source": "index"},
        "output": {"shape": "list", "single": False, "filter": True},
        "out": {"type": "object", "fields": out_fields},
    }


def case(label, fn):
    try:
        value = fn()
    except Exception as e:
        print("%-46s RAISES %s: %s" % (label, type(e).__name__, e))
        return
    print("%-46s OK -> %r" % (label, value))


def run(schema, payload=None):
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<gap>", "exec"), ns)
    return ns["run"](payload if payload is not None else {"index": [ROW]})


print("v2 spec-gap probe (public surface only)")
print("-" * 96)

# 1. v1 `show: False`: compute the field but keep it OUT of the output row.
case("v1 `show: False` on a field",
     lambda: dcg.validate(base(keep={"type": "int64", "value": "$.2"},
                               hidden={"type": "int64", "value": "$.3",
                                       "show": False})))
case("  ... and what generate() does with it",
     lambda: run(base(keep={"type": "int64", "value": "$.2"},
                      hidden={"type": "int64", "value": "$.3",
                              "show": False})))

# 2. v1 `_field` aliasing layer (ext referencing a bare alias name).
case("v1 `_field` alias used from an `ext`",
     lambda: dcg.validate(base(h={"type": "int64", "expr": "`_houseA + 1`"})))
case("  ... bare identifier that is not a field",
     lambda: dcg.compile_expr("_houseA + 1"))
case("  ... generate() on a non-existent bare name",
     lambda: dcg.generate(base(h={"type": "int64", "expr": "`_houseA + 1`"})))
case("  ... generate() on a non-existent bare name (no backtick)",
     lambda: dcg.generate(base(h={"type": "int64", "expr": "nosuch + 1"})))
case("  ... the real path that replaces the alias",
     lambda: run(base(h={"type": "int64", "expr": "$.2 + 1"})))

# 3. deep paths
for path in ("$.a.b", "$.5.a.1.b", "$.data.ID", "$.data.TASK_TYPE[0]"):
    case("compile_expr(%r)" % path, lambda p=path: dcg.compile_expr(p))

# 4. v1 f-string pseudo-path `$.data.f'AIRPORTIATA_{len(IATA)}'`
case("compile_expr(f-string pseudo-path)",
     lambda: dcg.compile_expr("$.data.f'AIRPORTIATA_{len(IATA)}'"))

# 5. whole-batch semantics v1 used: comma-separated multi-value `_field`
case("compile_expr($.data.AIRPORTIATA_1,$.data.AIRPORTIATA_2)",
     lambda: dcg.compile_expr("$.data.AIRPORTIATA_1,$.data.AIRPORTIATA_2"))

# 6. v1 `protocol: True/False` (row shape) - does v2 accept the key at all?
def with_protocol():
    s = base(h={"type": "int64", "value": "$.2"})
    s["output"]["protocol"] = False
    return dcg.validate(s)


case("v2 `output.protocol` key", with_protocol)

# 7. v2's own shapes, for the record
for shape in ("list", "dict", "index"):
    def go(sh=shape):
        s = base(h={"type": "int64", "value": "$.2"})
        s["output"]["shape"] = sh
        return run(s)
    case("v2 shape=%r product" % shape, go)

# 8. example 6's index request in v2 terms
def ex6_index():
    s = {
        "name": "ex6_index",
        "input": {"source": "index"},
        "output": {"shape": "index", "single": False, "filter": False,
                   "index": {"key": "merge"}},
        "out": {"type": "object", "fields": {"number": {"type": "str", "value": "$.0"}}},
    }
    return dcg.generate(s)[:200]


case("v2 shape='index' + index key (ex6 analogue)", ex6_index)
print("-" * 96)
print("dcg.__all__ = %s" % json.dumps(list(dcg.__all__)))
