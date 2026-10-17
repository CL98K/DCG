#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal reproductions for the v1 `format.py` defects that task-5 must expose.

Every repro below runs against the REAL v1 engine loaded through the sanctioned
bridge (`bench/_v1bridge.py` -> `load_v1_engine`); nothing here is copied from v1
source and nothing is stubbed.  Each case records the exception type + the FULL
message verbatim (project rule: errors are quoted, never paraphrased).

MODE is process-global and leaks between cases, so every case explicitly resets
ALL six flags first.  CYTHON_MODE is forced False unless a case says otherwise:
we are testing pure-Python codegen semantics, and leaving it True makes Cython
shell out to a compiler on every case (slow + noisy) without changing semantics.

Run:  python -X utf8 evidence/examples/probe_v1_defects.py
Out:  evidence/examples/v1_defects.json  + stdout transcript
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import traceback
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "bench")):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULTS = {
    "DEBUG": False,
    "OPT_MODE": True,
    "FREE_MODE": False,
    "SAFE_MODE": False,
    "TRYE_MODE": False,
    "CYTHON_MODE": False,
}


# --------------------------------------------------------------------------
# describe fixtures
# --------------------------------------------------------------------------
# A. no 'filter' key at all.  Everything else is a minimal, valid describe.
DESCRIBE_NO_FILTER = {
    "datas": "$.index",
    "protocol": True,
    "single": False,
    "_field": {},
    "struct": {"docs": [{"name": "a", "alias": "", "type": "int", "value": "$.0"}]},
}

# B. `_field` bare-name reference in an `ext`: `_k` must be expanded to data[0].
DESCRIBE_BARE_FIELD = {
    "datas": "$.index",
    "protocol": True,
    "single": False,
    "filter": "",
    "mode": "",
    "_field": {"_k": "$.0"},
    "struct": {"docs": [{"name": "key", "alias": "", "value": "", "ext": "_k"}]},
}

# C. index template -> dcg.py:138 emits the `chain(...)` return expression.
DESCRIBE_INDEX = {
    "datas": "$.index",
    "protocol": True,
    "filter": None,
    "index": {"key": "merge", "number": ""},
    "_field": {"_k": "$.0"},
    "struct": [{"name": "key", "show": True, "value": "", "ext": "_k"}],
}

ROWS_B = {"index": [("a0", 1), ("a1", 2), ("a2", 3)]}


def reset(fmt, **over):
    flags = dict(DEFAULTS)
    flags.update(over)
    for k, v in flags.items():
        setattr(fmt.Format, k, v)
    return flags


def run_case(fmt, name, describe, datas, *, call=True, expect_note="", **modes):
    """Parse + (optionally) call the handler; capture exception verbatim."""
    flags = reset(fmt, **modes)
    rec = {
        "case": name,
        "modes": flags,
        "note": expect_note,
        "parse_error": None,
        "parse_traceback": None,
        "call_error": None,
        "call_traceback": None,
        "result": None,
        "generated_source": None,
    }
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            _info, handler = fmt.Format.parse("DSS", describe)
    except BaseException:
        rec["parse_error"] = traceback.format_exc().strip().splitlines()[-1]
        rec["parse_traceback"] = traceback.format_exc()
        rec["stdout"] = buf.getvalue()
        return rec

    rec["stdout"] = buf.getvalue()
    # DEBUG=False prints nothing; DEBUG=True prints the generated source.  We keep
    # both: a source dump is the clearest evidence for D-B / D-C.
    if call:
        try:
            with contextlib.redirect_stdout(buf):
                rec["result"] = repr(handler(datas))
        except BaseException:
            rec["call_error"] = traceback.format_exc().strip().splitlines()[-1]
            rec["call_traceback"] = traceback.format_exc()
        rec["stdout"] += buf.getvalue()
    return rec


def example_literals():
    """Load the example `describe` / `datas` objects VERBATIM out of format.py.

    We do not hand-copy them: `probe_v1_examples.uncomment_block()` recovers the
    un-commented source, and `ast.literal_eval` of the assignment gives back
    exactly the object v1 would have seen.  `import datetime` is provided so
    example 5's `datetime.datetime(...)` literals evaluate.
    """
    import datetime
    import probe_v1_examples as P

    ns = {"datetime": datetime}
    out = {}
    for c in P.uncomment_block()[1]:
        text = "\n".join(c["text"])
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rec = {"span": (c["start"], c["end"]), "describe": None, "datas": None}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for key in ("describe", "datas"):
                if key not in targets:
                    continue
                try:
                    rec[key] = ast.literal_eval(node.value)
                except ValueError:
                    rec[key] = eval(  # noqa: S307 - repo-local data literal only
                        compile(ast.Expression(node.value), "<fixture>", "eval"),
                        {"__builtins__": {}}, dict(ns))
        if rec["describe"] is not None:
            out[len(out) + 1] = rec
    return out


def example_describes():
    """Back-compat shim: {n: (start, end, describe)}."""
    return {k: (v["span"][0], v["span"][1], v["describe"])
            for k, v in example_literals().items()}


def source_of(fmt, describe, **modes):
    """Return the generated Python source as printed by DEBUG=True."""
    reset(fmt, **dict(modes, DEBUG=True))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fmt.Format.parse("DSS", describe)
        except BaseException:
            pass
    return buf.getvalue()


def main():
    from _v1bridge import load_v1_engine

    fmt = load_v1_engine()

    cases = []
    cases.append(run_case(
        fmt, "A_missing_filter_key__OPT_MODE_default_true", DESCRIBE_NO_FILTER,
        {"index": [("a0",)]},
        expect_note="expect: bare KeyError 'filter' from format.py:552",
    ))
    cases.append(run_case(
        fmt, "A2_missing_filter_key__OPT_MODE_false", DESCRIBE_NO_FILTER,
        {"index": [("a0",)]}, OPT_MODE=False,
        expect_note="control: same describe works once OPT_MODE is off",
    ))
    cases.append(run_case(
        fmt, "B_field_bare_name__FREE_MODE_off__TRYE_off", DESCRIBE_BARE_FIELD,
        ROWS_B, expect_note="expect: NameError on bare _k (not expanded)",
    ))
    cases.append(run_case(
        fmt, "B2_field_bare_name__FREE_MODE_on", DESCRIBE_BARE_FIELD,
        ROWS_B, FREE_MODE=True,
        expect_note="control: with FREE_MODE the name IS expanded to data[0]",
    ))
    cases.append(run_case(
        fmt, "B3_field_bare_name__FREE_MODE_off__TRYE_on", DESCRIBE_BARE_FIELD,
        ROWS_B, TRYE_MODE=True,
        expect_note="expect: NO exception, every row silently lands in try bucket",
    ))
    cases.append(run_case(
        fmt, "C_index_template__empty_rows", DESCRIBE_INDEX, {"index": []},
        expect_note="expect: NameError name 'chain' is not defined",
    ))
    cases.append(run_case(
        fmt, "C2_index_template__with_rows", DESCRIBE_INDEX, ROWS_B,
        FREE_MODE=True, expect_note="expect: NameError name 'chain' is not defined",
    ))

    src = {
        "B_field_bare_name__FREE_MODE_off": source_of(fmt, DESCRIBE_BARE_FIELD),
        "B2_field_bare_name__FREE_MODE_on": source_of(fmt, DESCRIBE_BARE_FIELD, FREE_MODE=True),
        "C_index_template": source_of(fmt, DESCRIBE_INDEX, FREE_MODE=True),
        "A_missing_filter_key__OPT_MODE_default_true": source_of(fmt, DESCRIBE_NO_FILTER),
    }

    # ------------------------------------------------------------------
    # The REAL example describes, driven through THIS harness so we can
    # (a) reset MODE ourselves and (b) actually CALL the handler -- which the
    # commented-out examples do for 1..5 but NOT for example 6.
    # ------------------------------------------------------------------
    ex = example_literals()
    ex_rows_6 = [("k0", 1, 2, 3, 4, 5, 6, 7, 8, 9, "K", "X"),
                 ("k1", 1, 2, 3, 4, 5, 6, 7, 8, 9, "K", "X")]
    real_cases = []
    real_cases.append(run_case(
        fmt, "REAL_ex6_verbatim__TRYE_on_as_written", ex[6]["describe"], {"index": ex_rows_6},
        expect_note="ex6 as written: TRYE on, FREE_MODE off, never called in format.py",
        DEBUG=False, TRYE_MODE=True, CYTHON_MODE=False))
    real_cases.append(run_case(
        fmt, "REAL_ex6_verbatim__TRYE_off", ex[6]["describe"], {"index": ex_rows_6},
        expect_note="ex6 with the per-row try/except disabled -> the real error surfaces",
        DEBUG=False, TRYE_MODE=False, CYTHON_MODE=False))
    real_cases.append(run_case(
        fmt, "REAL_ex6_verbatim__empty_rows", ex[6]["describe"], {"index": []},
        expect_note="ex6 with ZERO rows: proves the chain() NameError is unconditional",
        DEBUG=False, TRYE_MODE=False, CYTHON_MODE=False))
    real_cases.append(run_case(
        fmt, "REAL_ex6_verbatim__FREE_MODE_on_TRYE_on", ex[6]["describe"], {"index": ex_rows_6},
        expect_note="ex6 + FREE_MODE=on (what ex3 uses) -> fields expand, chain still broken",
        DEBUG=False, FREE_MODE=True, TRYE_MODE=True, CYTHON_MODE=False))

    # Every OTHER example, re-run with the per-row try/except OFF.  If the example
    # only "worked" because TRYE silently bucketed every row, this exposes it.
    for n in (1, 2, 3, 4, 5):
        if ex[n]["datas"] is None:
            continue
        real_cases.append(run_case(
            fmt, f"REAL_ex{n}_verbatim__TRYE_off", ex[n]["describe"], ex[n]["datas"],
            expect_note=f"real ex{n} describe+datas, per-row try/except disabled",
            DEBUG=False, TRYE_MODE=False, CYTHON_MODE=False))

    src["REAL_ex6_verbatim__FREE_MODE_on"] = source_of(
        fmt, ex[6]["describe"], DEBUG=True, FREE_MODE=True, TRYE_MODE=True, CYTHON_MODE=False)
    src["REAL_ex6_verbatim__FREE_MODE_off"] = source_of(
        fmt, ex[6]["describe"], DEBUG=True, FREE_MODE=False, TRYE_MODE=True, CYTHON_MODE=False)

    print("real example spans:", {k: v["span"] for k, v in ex.items()})
    print("real example datas present:", {k: v["datas"] is not None for k, v in ex.items()})
    cases.extend(real_cases)

    print("=" * 78)
    print("v1 format.py DEFECT REPROS (real engine via bench/_v1bridge.py)")
    print("=" * 78)
    print(f"python: {sys.version.split()[0]}   file: {fmt.__file__}")
    print()
    for c in cases:
        print("-" * 78)
        print("CASE:", c["case"])
        print("modes:", ", ".join(f"{k}={v}" for k, v in c["modes"].items()))
        print("note :", c["note"])
        if c["parse_error"]:
            print("PARSE ERROR:", c["parse_error"])
            print(c["parse_traceback"])
        else:
            print("parse: OK")
            if c["call_error"]:
                print("CALL ERROR:", c["call_error"])
                print(c["call_traceback"])
            else:
                print("result:", c["result"])
        print()

    print("=" * 78)
    print("GENERATED SOURCE (DEBUG=True)")
    print("=" * 78)
    for k, v in src.items():
        print("-" * 78)
        print("CASE:", k)
        print(v.strip() or "<empty>")
        print()

    out = os.path.join(ROOT, "evidence", "examples", "v1_defects.json")
    with io.open(out, "w", encoding="utf-8") as fh:
        json.dump({
            "engine_file": fmt.__file__,
            "python": sys.version,
            "defaults": DEFAULTS,
            "describes": {
                "no_filter_key": DESCRIBE_NO_FILTER,
                "bare_field": DESCRIBE_BARE_FIELD,
                "index_template": DESCRIBE_INDEX,
            },
            "cases": cases,
            "generated_source": src,
        }, fh, ensure_ascii=False, indent=1)
    print("frozen:", out)


if __name__ == "__main__":
    main()
