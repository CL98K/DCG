#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Freeze the REAL behaviour of the 6 commented-out examples in format.py:577-816.

Why this exists
---------------
``format.py:577-816`` contains a commented-out ``__main__`` block with the only
in-repo end-to-end usage examples of the v1 engine.  It was NEVER executed
automatically, so the v1 expected outputs are not recorded anywhere.  task-5
requires them frozen from a REAL run, not from reading the source.

Method
------
1. Read format.py, take lines 577..816 (1-based), strip the leading ``# `` / ``#``
   comment prefix -- i.e. un-comment the exact original text, no edits.
2. Split at the ``#####`` separator lines into per-example chunks.
3. **Run ONE example per subprocess.**  This is not a stylistic choice: v1 keeps
   live state in module/class globals (``MODE``, ``STRUC``, the generated-function
   namespace) and the examples demonstrably contaminate each other inside one
   interpreter -- e.g. example 2's first ``docs`` list comes out EMPTY when it runs
   after example 1, but POPULATED with 10 rows in a fresh process.  A golden that
   depends on which example ran before it is not a golden, so isolation is
   mandatory.
4. Inside that subprocess, reset every v1 MODE flag to its documented default
   (dcg.py:35-40) and then run the chunk verbatim against the real v1 engine
   loaded through the sanctioned bridge (``bench/_v1bridge.py``).
5. Record stdout, the raw value passed to ``pprint.pprint``, and any exception
   with its FULL traceback text (project rule: errors are quoted verbatim).
6. Additionally re-parse the same describe with the per-row ``try/except``
   disabled (``TRYE_MODE=False``).  v1 wraps every row in ``try/except`` and
   silently pushes failures into a side bucket, so an example can "succeed"
   while producing nothing; the TRYE-off run is what tells the two apart.

Outputs (frozen evidence, do not hand-edit):
    evidence/examples/v1_examples_raw.txt      human-readable full transcript
    evidence/examples/v1_examples_frozen.json  machine-readable per-example record

Run:  python -X utf8 evidence/examples/probe_v1_examples.py
      python -X utf8 evidence/examples/probe_v1_examples.py --only 3   # internal
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import textwrap
import traceback
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "bench")
for p in (ROOT, BENCH):
    if p not in sys.path:
        sys.path.insert(0, p)

FIRST, LAST = 577, 816  # inclusive, 1-based
SEP_RE = re.compile(r"^#\s*#{10,}\s*$")
JSON_TAG = "@@REC@@"

DEFAULTS = {
    "DEBUG": False,
    "OPT_MODE": True,
    "FREE_MODE": False,
    "SAFE_MODE": False,
    "TRYE_MODE": False,
    "CYTHON_MODE": True,
}


def uncomment_block():
    """Return (lines, chunks) with the comment prefix stripped from format.py:577-816.

    Each chunk is ``{"start": int, "end": int, "text": [str, ...]}`` where start/end
    are the 1-based format.py line numbers of the chunk's first/last non-blank line.
    """
    path = os.path.join(ROOT, "format.py")
    with io.open(path, encoding="utf-8") as fh:
        src = fh.readlines()

    lines = []  # (format.py 1-based lineno, un-commented text)
    for i in range(FIRST - 1, LAST):
        raw = src[i].rstrip("\n")
        if raw.strip() == "":
            lines.append((i + 1, ""))
        elif raw.startswith("# "):
            lines.append((i + 1, raw[2:]))
        elif raw == "#":
            lines.append((i + 1, ""))
        elif raw.startswith("#"):
            lines.append((i + 1, raw[1:]))
        else:
            raise AssertionError(f"line {i + 1} is not commented: {raw!r}")

    chunks, cur = [], []
    for lineno, ln in lines:
        if SEP_RE.match("#" + " " + ln) or SEP_RE.match(ln) or set(ln.strip()) == {"#"}:
            if cur:
                chunks.append(cur)
                cur = []
            continue
        if ln.strip() == 'if __name__ == "__main__":':
            # the whole block lives inside this guard; we exec each chunk directly
            continue
        cur.append((lineno, ln))
    if cur:
        chunks.append(cur)

    out = []
    for c in chunks:
        body = [(n, t) for n, t in c if t.strip()]
        if not body:
            continue
        nums = [n for n, _ in body]
        # each example body is indented one level inside the `if __name__` block
        text = textwrap.dedent("\n".join(t for _, t in c)).splitlines()
        out.append({"start": min(nums), "end": max(nums), "text": text})
    return [t for _, t in lines], out


class _FakePprint:
    def __init__(self):
        self.calls = []

    def pprint(self, obj, *a, **k):
        self.calls.append(obj)


def reset_modes(fmt, **over):
    flags = dict(DEFAULTS)
    flags.update(over)
    for name, val in flags.items():
        setattr(fmt.Format, name, val)
    return flags


def parse_only(fmt, describe, **modes):
    """Reset MODE, parse, return the generated source printed by DEBUG=True."""
    reset_modes(fmt, **dict(modes, DEBUG=True))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fmt.Format.parse("DSS", describe)
        except BaseException:
            pass
    return buf.getvalue()


def strip_lineno(src):
    """DEBUG=True prints the generated source as `%-5d` + line -> make it executable."""
    return "\n".join(line[5:] if len(line) > 5 and line[:5].rstrip().isdigit() else line
                     for line in src.splitlines())


def describe_datas(ns):
    """Recover (describe, datas) from an executed example namespace as plain data."""
    import ast

    out = []
    for key in ("describe", "datas"):
        val = ns.get(key)
        if val is None:
            out.append(None)
            continue
        try:
            out.append(ast.literal_eval(repr(val)))
        except Exception:
            out.append(val)
    return out


def run_one(n):
    """Execute example `n` in THIS process (which must be a fresh subprocess)."""
    from _v1bridge import load_v1_engine

    _lines, chunks = uncomment_block()
    chunk = chunks[n - 1]
    text = "\n".join(chunk["text"])
    fmt = load_v1_engine()

    rec = {
        "index": n,
        "format_py_lines": [chunk["start"], chunk["end"]],
        "modes_set_by_example": {},
        "describe": None,
        "datas": None,
        "stdout": "",
        "pprint_value": None,
        "pprint_n_calls": 0,
        "exception": None,
        "traceback": None,
        "trye_off": None,
        "generated_source": None,
    }
    for m in re.finditer(r"Format\.(\w+)\s*=\s*([^\n]+)", text):
        rec["modes_set_by_example"][m.group(1)] = m.group(2).strip()

    ns = {"Format": fmt.Format, "__name__": f"v1example{n}", "__builtins__": __builtins__}
    fake = _FakePprint()
    buf = io.StringIO()
    real_pprint = sys.modules.get("pprint")
    reset_modes(fmt)
    try:
        sys.modules["pprint"] = fake
        with contextlib.redirect_stdout(buf):
            exec(compile(text, f"format.py#example{n}", "exec"), ns)
    except BaseException:
        rec["traceback"] = traceback.format_exc()
        rec["exception"] = rec["traceback"].strip().splitlines()[-1]
    finally:
        if real_pprint is not None:
            sys.modules["pprint"] = real_pprint
        else:
            sys.modules.pop("pprint", None)

    rec["stdout"] = buf.getvalue()
    rec["describe"] = repr(ns.get("describe"))
    rec["datas"] = repr(ns.get("datas"))
    rec["pprint_value"] = repr(fake.calls[0]) if fake.calls else None
    rec["pprint_n_calls"] = len(fake.calls)

    describe, datas = describe_datas(ns)
    # the flags THIS example set (FREE_MODE / SAFE_MODE / ... are part of the example)
    ex_modes = {k: eval(v, {"__builtins__": {}}, {})  # noqa: S307 - literal True/False
                for k, v in rec["modes_set_by_example"].items()}
    if describe is not None:
        rec["generated_source"] = parse_only(fmt, describe, CYTHON_MODE=False)
        rec["generated_source_as_written"] = parse_only(
            fmt, describe, **dict(ex_modes, CYTHON_MODE=False))
    if describe is not None and datas is not None:
        # 2-factor experiment.  The example's own flags are the baseline; each variant
        # flips EXACTLY ONE flag, so a difference is attributable to that flag alone.
        # (An earlier revision forced CYTHON_MODE=False in the TRYE variant, which
        # silently measured a configuration no example ever ran and produced a
        # false "TRYE changes codegen" conclusion.)
        rec["variants"] = {}
        for label, over in (
            ("trye_off", {"TRYE_MODE": False}),
            ("cython_off", {"CYTHON_MODE": False}),
            ("cython_off_trye_off", {"CYTHON_MODE": False, "TRYE_MODE": False}),
        ):
            diag = {"modes": reset_modes(fmt, **dict(ex_modes, DEBUG=True, **over))}
            dbuf = io.StringIO()
            try:
                with contextlib.redirect_stdout(dbuf):
                    _i, handler = fmt.Format.parse("DSS", describe)
                    diag["result"] = repr(handler(datas))
            except BaseException:
                diag["traceback"] = traceback.format_exc()
                diag["error"] = diag["traceback"].strip().splitlines()[-1]
            rec["variants"][label] = diag
    return rec
    # TRYE_MODE turns every row failure into a silent append to the error bucket, so
    # a wrong-looking result carries no explanation.  Re-run the AS-WRITTEN source
    # with the handler instrumented to record what it swallowed (source-level patch
    # only: the `except` body gains one append, nothing else changes).
    as_written = rec.get("generated_source_as_written")
    if describe is not None and datas is not None and as_written:
        patched = re.sub(
            r"(?m)^([ \t]*)except Exception as e:[ \t]*$",
            lambda m: m.group(0) + "\n" + m.group(1)
            + "    _ERRS.append(type(e).__name__ + ': ' + str(e))",
            strip_lineno(as_written))
        ns2 = {"_ERRS": []}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(patched, f"<generated_ex{n}_as_written>", "exec"), ns2)
                fn = [v for k, v in ns2.items() if k.startswith("_inline_call_")][0]
                result = fn(datas)
            rec["trye_on_errors"] = {
                "n": len(ns2["_ERRS"]),
                "unique": sorted(set(ns2["_ERRS"]))[:5],
                "result": repr(result),
            }
        except BaseException:
            rec["trye_on_errors"] = {"traceback": traceback.format_exc()}
    return rec


def _run_isolated(n):
    """Run example `n` in a fresh subprocess; return its record."""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.abspath(__file__), "--only", str(n)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout.splitlines():
        if line.startswith(JSON_TAG):
            return json.loads(line[len(JSON_TAG):])
    raise RuntimeError(
        f"example {n}: isolated subprocess produced no record\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")


def main():
    if "--only" in sys.argv:
        n = int(sys.argv[sys.argv.index("--only") + 1])
        print(JSON_TAG + json.dumps(run_one(n), ensure_ascii=False))
        return

    _lines, chunks = uncomment_block()
    print("=" * 78)
    print("v1 EXAMPLES PROBE -- format.py:577-816 un-commented and executed for real")
    print("=" * 78)
    print(f"format.py lines {FIRST}..{LAST}: {len(_lines)} comment lines, "
          f"{len(chunks)} example chunk(s) split by '#####' separators")
    print(f"python: {sys.version}")
    print("isolation: one subprocess per example (v1 state leaks between examples)")
    print()

    records = []
    for n in range(1, len(chunks) + 1):
        rec = _run_isolated(n)
        records.append(rec)
        print("-" * 78)
        print(f"EXAMPLE {n}  (format.py:{rec['format_py_lines'][0]}-{rec['format_py_lines'][1]})")
        print("-" * 78)
        print("modes set: " + (", ".join(f"{k}={v}" for k, v in rec["modes_set_by_example"].items())
                               or "(none -> defaults)"))
        print("describe : " + (rec["describe"] or "<unbound>"))
        print("datas    : " + (rec["datas"] or "<unbound>")[:300])
        if rec["exception"]:
            print("EXCEPTION: " + rec["exception"])
            print(rec["traceback"])
        else:
            print(f"pprint calls: {rec['pprint_n_calls']}")
            print("pprint value: " + (rec["pprint_value"] or "<none>")[:600])
        d = rec["trye_off"]
        if d:
            if d.get("error"):
                print("TRYE-OFF CALL ERROR: " + d["error"])
            else:
                print("TRYE-OFF result: " + d["result"][:300])
        if rec["stdout"].strip():
            print("--- stdout (first 20 lines) ---")
            print("\n".join(rec["stdout"].strip().splitlines()[:20]))
        print()

    outdir = os.path.join(ROOT, "evidence", "examples")
    os.makedirs(outdir, exist_ok=True)
    frozen = os.path.join(outdir, "v1_examples_frozen.json")
    with io.open(frozen, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "format.py:577-816",
            "method": "un-comment verbatim; one subprocess per example; MODE reset to dcg.py:35-40 defaults",
            "python": sys.version,
            "n_chunks": len(chunks),
            "chunks": records,
        }, fh, ensure_ascii=False, indent=1)

    buf = io.StringIO()
    print("=" * 78, file=buf)
    print("v1 EXAMPLES PROBE transcript (per-example subprocess isolation)", file=buf)
    print("=" * 78, file=buf)
    for rec in records:
        print(f"\n### EXAMPLE {rec['index']} (format.py:{rec['format_py_lines'][0]}-"
              f"{rec['format_py_lines'][1]})", file=buf)
        print("modes set:", rec["modes_set_by_example"], file=buf)
        print("describe:", rec["describe"], file=buf)
        print("datas:", rec["datas"], file=buf)
        if rec["exception"]:
            print("EXCEPTION:", rec["exception"], file=buf)
            print(rec["traceback"], file=buf)
        else:
            print("pprint value:", rec["pprint_value"], file=buf)
        d = rec["trye_off"]
        if d:
            print("TRYE-OFF:", d.get("error") or d.get("result"), file=buf)
        if rec["stdout"].strip():
            print("--- stdout ---", file=buf)
            print(rec["stdout"].strip(), file=buf)
        if rec["generated_source"]:
            print("--- generated source (DEBUG=True) ---", file=buf)
            print(rec["generated_source"].strip(), file=buf)
    txt = os.path.join(outdir, "v1_examples_raw.txt")
    with io.open(txt, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    print("=" * 78)
    print("frozen:", frozen)
    print("frozen:", txt)
    n_fail = sum(1 for r in records if r["exception"])
    print(f"SUMMARY: {len(records)} chunks, {len(records) - n_fail} ran, {n_fail} raised")
    for r in records:
        d = r["trye_off"] or {}
        print(f"  ex{r['index']}: as-written={'RAISED' if r['exception'] else 'ok'}"
              f"  TRYE-off={d.get('error') or 'ok'}")


if __name__ == "__main__":
    main()
