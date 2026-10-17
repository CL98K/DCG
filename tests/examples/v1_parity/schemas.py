"""v2 schemas that re-implement the six `format.py` examples.

Each schema carries a `# v1:` comment naming the original `format.py` line range and
the v1 construct it replaces.  This module is the "new version implements the old
version's test examples" deliverable (user request m01242); the parity tests in
`tests/examples/test_v1_parity.py` compare these products against the frozen v1
outputs, never against hand-written expectations.

Migration rules applied here (CONTRACT.md §6, §13.4)
----------------------------------------------------
* `#.field` (v1: reference a previously computed field of this schema)  ->  the v2
  bare identifier `field`; `#.field.i.j` -> `field[i][j]`.
* a `_field` bare name used in `ext` (v1: expand to the aliased path)  ->  the real
  path `$.n`; v2 has no `_field` aliasing layer, so the path is written directly.
* nested `{"fields": ...}` must be explicit in v2; v1 inferred nesting from "a dict
  appeared inside a list".
* deep paths (`$.a.b`, >= 2 symbol segments) are rejected by the **`expr`** side
  (JSONata / backtick, CONTRACT.md §6.1) but **accepted by the `value` side** at
  arbitrary depth (CONTRACT.md §8 O-4, README §3.3).  Example 5 uses `value` paths
  for exactly that reason; the earlier note here claiming "v2 rejects deep paths"
  unconditionally was wrong and is corrected by `bench/verify_v1_examples.py`.
"""
from __future__ import annotations

BT = chr(96)  # backtick

# ---------------------------------------------------------------------------
# Example 1 - format.py:578-623.  Nine fields, all shapes of `ext`.
# v1 output: ({'doc': [10 rows of 9 fields]}, [])
# ---------------------------------------------------------------------------
EX1 = {
    "name": "v1_ex1",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {"type": "object", "fields": {
        "hour": {"type": "list", "value": "$.5.a"},
        "hourA": {"type": "int64", "value": "$.5.a.1"},
        "hourB": {"type": "int64", "value": "$.5.a.2"},
        "hourC": {"type": "int64", "value": "$.2"},
        "hourD": {"type": "str", "expr": BT + "str($.5.a.1) + str(hourB)" + BT},
        "hourE": {"type": "int64", "expr": "hourA + hourB"},
        "hourF": {"type": "int64", "expr": "$.5.a.2 + $.5.a.1"},
        # v1: 'hour[2] + 123'
        "hourG": {"type": "int64", "expr": "hour[2] + 123"},
        # v1: '#.hour.3.2 + #.hour.3.1'  ->  hour[3][2] + hour[3][1]
        "hourH": {"type": "int64", "expr": "hour[3][2] + hour[3][1]"},
    }},
}

# ---------------------------------------------------------------------------
# Example 3 - format.py:700-730.  v1 struct fields had value:'' plus an `ext` that
# referenced `_field` bare names (_houseA/_hourB/_hourC); v2 writes the paths direct.
# v1 output: ({'docs': [[0,0,0],[2,-1,1],[2,0,2],...]}, [])
# ---------------------------------------------------------------------------
EX3 = {
    "name": "v1_ex3",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {"type": "object", "fields": {
        "hourA": {"type": "int64", "value": "$.1"},   # v1: ext '_houseA'
        "hourB": {"type": "int64", "value": "$.2"},   # v1: ext '_hourB'
        "hourC": {"type": "int64", "value": "$.3"},   # v1: ext '_hourC'
    }},
}

# ---------------------------------------------------------------------------
# Example 4 - format.py:734-764.  Identical to example 3 except `protocol: False`.
# v1 output: ({'docs': [{'hour':..,'hourA':..,'hourB':..,'hourC':..}, ...]}, [])
# i.e. `protocol: False` switches the ROW SHAPE from list to dict.  v2 models that
# with `shape: "rows"` (added 2026-10-17), so this example is now EXACTLY equal.
# ---------------------------------------------------------------------------
EX4 = {
    "name": "v1_ex4",
    "input": {"source": "index"},
    "output": {"shape": "rows", "single": False, "filter": True},
    "out": {"type": "object", "fields": {
        "hour": {"type": "list", "value": "$.5.a"},
        "hourA": {"type": "str", "value": "$.0"},
        "hourB": {"type": "int64", "value": "$.1"},
        "hourC": {"type": "int64", "value": "$.2"},
    }},
}

# ---------------------------------------------------------------------------
# Example 2 - format.py:626-697.  Only the first top-level struct group ('docs',
# 15 fields) is transliterated; it is the group whose v1 output is provably wrong
# (see the CYTHON_MODE defect).  `hour` is declared TWICE in v1 ('$.4' then '$.5.a');
# v2 rejects duplicate output field names at validate time, so the duplicate is
# modelled as two distinct fields, and the test records that difference.
# ---------------------------------------------------------------------------
EX2_DOCS = {
    "name": "v1_ex2_docs",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {"type": "object", "fields": {
        "keyx": {"type": "int64", "const": 100},
        "keyy": {"type": "int64", "expr": BT + "1 if keyx else 0" + BT},
        "key": {"type": "int64", "expr": "-$.1"},
        "val": {"type": "int64", "value": "$.2"},
        "day": {"type": "int64", "value": "$.3"},
        # v1 declares `hour` first as '$.4' and again as '$.5.a' in the same struct.
        # Both occurrences of the v1 row expression read the SAME generated local, so
        # the '$.4' value is unreachable and the list is emitted in both slots.  This
        # field is the list, i.e. what v1 actually emits.
        "hour": {"type": "list", "value": "$.5.a"},
        "hourX": {"type": "list", "expr": "[1, 2, 3]"},
        "hourY": {"type": "int64", "expr": "hourX[0]"},      # v1: '#.hourX.0'
        "hourZ": {"type": "int64", "expr": "day + 100"},     # v1: '#.day' + ext
        "hourXX": {"type": "int64", "expr": "hourY + hourZ"},
        "hourYY": {"type": "int64", "expr": "key"},          # v1: '#.key'
        # v1's SECOND 'hour' declaration; v2 has no duplicate field names, so the
        # same value is carried under a distinct name (position is what matters in
        # v2's list shape, and the test compares values positionally).
        "hour2": {"type": "list", "value": "$.5.a"},
        "hourA": {"type": "int64", "expr": "hour[0]"},       # v1: '#.hour.0'
        "hourB": {"type": "int64", "expr": "hour[1]"},
        "hourC": {"type": "int64", "expr": "hour[2]"},
    }},
}

# ---------------------------------------------------------------------------
# Example 5 - format.py:767-791.  EXACTLY transliterated (2026-10-17).
#
# Two corrections to the first version of this comment:
#  1. it is not "v2 rejects the deep paths": `compile_expr` rejects >= 2 symbol
#     segments (CONTRACT.md §6.1, expr side only) while `parse_value_path` accepts
#     them (§8 O-4), so the first two fields translate directly.
#  2. the third field is NOT a blocker any more.  v1 emits (frozen
#     `generated_source` line 8):  VAR_1[f"AIRPORTIATA_{len(data['data']['AIRPORTIATA'])}"]
#     - a runtime-computed member key.  The same thing is expressible in a v2
#     backtick segment, because single-segment chains such as `$.data` are valid
#     inside backticks (that needed a codegen fix: `pyseg` used to reject them as
#     bare names).  `EX5` below is therefore rule-equivalent, not just a
#     reproduction of the frozen dataset.
# ---------------------------------------------------------------------------
EX5_V1_PATHS = ("$.data.ID", "$.data.TASK_TYPE[0]",
                "$.data.f'AIRPORTIATA_{len(IATA)}'")

#: v1 example 5, translated exactly: the dynamic member key survives as a
#: backtick expression, so a payload with a different `AIRPORTIATA` length picks a
#: different `AIRPORTIATA_<n>` member - just like v1 does.
EX5 = {
    "name": "v1_ex5",
    "input": {"source": "rows"},          # v1's `datas: ''` = the payload IS the row list
    "output": {"shape": "list", "single": False, "filter": False},
    "out": {"type": "object", "fields": {
        "Flight_ID": {"type": "int64", "value": "$.data.ID"},
        "Task_Type": {"type": "str", "value": "$.data.TASK_TYPE[0]"},
        "Planned_Arrival_Time": {
            "type": "str",
            # == v1's f"AIRPORTIATA_{len(data['data']['AIRPORTIATA'])}"
            "expr": BT + "$.data['AIRPORTIATA_' + str(len($.data.AIRPORTIATA))]" + BT,
        },
    }},
}

# ---------------------------------------------------------------------------
# Example 6 - format.py:794-816.  Parse-only in v1 (no `datas`), and v1's index
# template is broken (`chain` is never imported), so there is no v1 output to match.
# The v2 request is only recorded here; it is not driven by a passing test.
# ---------------------------------------------------------------------------
EX6_INPUT_FIELD = "$.10"


def by_name():
    return {"ex1": EX1, "ex2_docs": EX2_DOCS, "ex3": EX3, "ex4": EX4,
            "ex5": EX5}
