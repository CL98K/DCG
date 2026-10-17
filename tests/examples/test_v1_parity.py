"""v1 `format.py` examples vs their v2 re-implementation (task-5).

What this suite proves, and how
-------------------------------
* The v1 expectations are **generated, not transcribed**: every golden below comes
  from `evidence/examples/v1_examples_frozen.json`, produced by really running
  `format.py:577-816` through `bench/_v1bridge.py`.  `harness.golden(n)` fails loudly
  if that record is absent.
* Where v1 is broken the breakage is asserted as a **passing** test (the defect is a
  fact about v1, established by real execution) and the corresponding v2 side is
  asserted against the value v1 *would* have produced.
* Where v2 genuinely cannot express a v1 construct, the test asserts the exact
  boundary and the reason, so the limitation cannot regress into a silent difference.
  Nothing here is skipped.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# NOTE (Lead, 2026-10-17): this file lives in `tests/examples/`, which has no
# `__init__.py`, so a *relative* import (`from .v1_parity import ...`) cannot resolve --
# pytest aborts the whole session during collection with
# `ImportError: attempted relative import with no known parent package`.
# The dir itself is already on sys.path from the line above, so import the package.
from v1_parity import harness, schemas  # noqa: E402
from v1_parity.harness import golden, v2_run  # noqa: E402


@pytest.fixture(scope="module")
def ex1():
    return golden(1)


def _rows(g):
    """The v1 input payload, exactly as the example passed it to the handler."""
    return g.datas


# ---------------------------------------------------------------------------
# the evidence itself
# ---------------------------------------------------------------------------
def test_frozen_record_has_six_really_executed_examples():
    gs = harness.all_goldens()
    assert [g.index for g in gs] == [1, 2, 3, 4, 5, 6]
    assert [g.format_py_lines[0] for g in gs] == [578, 626, 700, 734, 767, 794]
    # five examples run their transform and pprint the result; example 6 is
    # parse-only (its block ends without any `datas`).
    assert [g.ran for g in gs] == [True, True, True, True, True, False]
    assert gs[5].pprint_n_calls == 0


# ---------------------------------------------------------------------------
# example 1 - the nine field shapes, incl. `#.hour.3.2` and `hour[2] + 123`
# ---------------------------------------------------------------------------
def test_ex1_paths_and_ext_shapes():
    g = golden(1)
    assert g.exception is None, g.traceback
    # v1 wraps the result with its per-row error bucket when TRYE_MODE is on.
    assert g.result["doc"][0] == [
        [0, 1, 2, [1, 2, 3]], 1, 2, 0, "12", 3, 3, 125, 5]
    assert g.errors == []


def test_ex1_v2_matches_v1_rows():
    g = golden(1)
    _src, out = v2_run(schemas.EX1, _rows(g))
    assert out == g.result["doc"]


def test_ex1_v2_rows_cover_all_ten_input_rows():
    g = golden(1)
    _src, out = v2_run(schemas.EX1, _rows(g))
    assert len(out) == len(g.result["doc"]) == 10
    # hourC is data[2]; v1's own row data proves the third column is read, not guessed
    assert [r[3] for r in out] == [row[2] for row in g.datas["index"]]


# ---------------------------------------------------------------------------
# example 3 - `_field` bare names in `ext`
# ---------------------------------------------------------------------------
def test_ex3_v1_expands_field_aliases():
    g = golden(3)
    assert g.exception is None, g.traceback
    assert g.result["docs"][:3] == [[0, 0, 0], [2, -1, 1], [2, 0, 2]]
    assert g.errors == []


def test_ex3_v2_matches_v1_rows():
    g = golden(3)
    _src, out = v2_run(schemas.EX3, _rows(g))
    assert out == g.result["docs"]


# ---------------------------------------------------------------------------
# example 2 - v1 loses every row, under its own default CYTHON_MODE
# ---------------------------------------------------------------------------
def test_ex2_v1_cython_mode_loses_every_row():
    """v1 as written: `docs` is empty and all ten rows land in the error bucket."""
    g = golden(2)
    assert g.exception is None, g.traceback
    assert g.result["docs"] == []
    assert len(g.errors) == 10
    assert [r[0] for r in g.errors] == ["a%d" % i for i in range(10)]


def test_ex2_the_cause_is_cython_mode_not_trye_mode():
    """Flipping ONE flag at a time pins the defect on CYTHON_MODE."""
    g = golden(2)
    v = g.variants
    # CYTHON_MODE stays on, TRYE_MODE off -> the swallowed TypeError surfaces
    assert v["trye_off"].get("error") == "TypeError: Expected list, got int"
    assert "_cython_" in v["trye_off"]["traceback"]
    # CYTHON_MODE off (TRYE_MODE untouched) -> the correct ten rows
    assert v["cython_off"].get("error") is None
    assert harness._parse(v["cython_off"]["result"])[0]["docs"] != []


def test_ex2_v2_produces_the_rows_v1_lost():
    """The rows v2 emits are the rows v1 would have emitted with CYTHON_MODE off."""
    g = golden(2)
    expected = harness._parse(g.variants["cython_off"]["result"])[0]["docs"]
    _src, out = v2_run(schemas.EX2_DOCS, _rows(g))
    assert out == expected
    assert len(out) == 10


# ---------------------------------------------------------------------------
# example 4 - `protocol: False` switches the v1 ROW SHAPE; v2 `shape: "rows"` matches it
# ---------------------------------------------------------------------------
def test_ex4_v1_protocol_false_emits_row_dicts():
    g = golden(4)
    assert g.exception is None, g.traceback
    row = g.result["docs"][0]
    assert isinstance(row, dict)
    assert row == {"hour": [0, 1, 2], "hourA": "a0", "hourB": 0, "hourC": 0}


def test_ex4_v2_rows_shape_is_exactly_equal():
    """`shape: "rows"` emits one {field: value} dict per row - v1's protocol: False."""
    g = golden(4)
    _src, out = v2_run(schemas.EX4, g.datas)
    assert isinstance(out[0], dict), "rows shape emits per-row dicts"
    assert out == g.result["docs"], "row dicts must match v1 value-for-value"
    assert len(out) == len(g.result["docs"]) == 10


# ---------------------------------------------------------------------------
# example 5 - all three fields translate, including the runtime-computed member key
#
# CORRECTION (2026-10-17): the first version of these tests asserted that v2
# "cannot express the example at all, because `$.data.ID` is a deep path".  That
# conflated the two sides: `compile_expr` (JSONata / backtick) rejects >= 2 symbol
# segments (CONTRACT §6.1), but `parse_value_path` accepts arbitrary depth (§8 O-4),
# and `value:` is what this example needs.  The third field's runtime key is
# expressible too, in a backtick segment - these tests prove both halves.
# ---------------------------------------------------------------------------
def test_ex5_expr_side_rejects_the_deep_path_the_example_needs():
    import dcg
    for path in schemas.EX5_V1_PATHS[:2]:
        with pytest.raises((dcg.Unsupported, dcg.SchemaError)):
            dcg.compile_expr(path)
    # and the reason is the documented one: >= 2 symbol segments on the expr side
    with pytest.raises(dcg.Unsupported):
        dcg.compile_expr("$.a.b")
    # a single symbol segment is fine, which is why example 1 still migrates
    assert dcg.compile_expr("$.a") is not None
    assert dcg.compile_expr("$.5.a.1") is not None


def test_ex5_value_side_accepts_the_deep_path_and_matches_v1():
    """Deep paths are an *expr*-side restriction (CONTRACT §8 O-4); this example uses value."""
    from dcg.schema import parse_value_path
    for path in schemas.EX5_V1_PATHS[:2]:
        assert parse_value_path(path) is not None
    g = golden(5)
    _src, out = v2_run(schemas.EX5, g.datas)
    assert out == g.result == [[14528086, "W", None]]


def test_ex5_dynamic_key_is_rule_equivalent_not_a_constant():
    """v1's third field is `VAR[f"AIRPORTIATA_{len(IATA)}"]` - a runtime key.

    EX5 keeps that dynamic: a payload whose AIRPORTIATA has length 2 must read
    `AIRPORTIATA_2`, not the `AIRPORTIATA_3` the frozen dataset happens to use.
    A constant key would silently disagree here.
    """
    g = golden(5)
    src = g.raw.get("generated_source") or ""
    assert 'f"AIRPORTIATA_{len(' in src, "v1 builds the member key at run time"
    assert len(g.datas[0]["data"]["AIRPORTIATA"]) == 3  # frozen payload -> _3

    synthetic = {"data": {"ID": 7, "TASK_TYPE": "W", "AIRPORTIATA": "UR",
                          "AIRPORTIATA_1": "x", "AIRPORTIATA_2": None,
                          "AIRPORTIATA_3": "must-not-be-used"}}
    _src, out = v2_run(schemas.EX5, [synthetic])
    assert out == [[7, "W", None]], "the key follows AIRPORTIATA's length (2 -> _2)"


# ---------------------------------------------------------------------------
# example 6 - v1's index shape never worked
# ---------------------------------------------------------------------------
def test_ex6_v1_index_shape_raises_before_producing_anything():
    g = golden(6)
    assert not g.ran, "example 6 has no `datas`, so it only parses"
    # the defect itself is reproduced (with its verbatim message) in the defect probe
    import io
    import json
    import os
    p = os.path.join(harness.EVIDENCE, "v1_defects.json")
    assert os.path.exists(p), "run evidence/examples/probe_v1_defects.py first"
    with io.open(p, encoding="utf-8") as fh:
        defects = json.load(fh)
    text = json.dumps(defects)
    assert "NameError: name 'chain' is not defined" in text
    assert "KeyError: 'filter'" in text
