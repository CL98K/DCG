"""Inspect evidence/examples/v1_examples_frozen.json.

`summary`            one line per example: as-written outcome + the 2-factor
                     (CYTHON_MODE / TRYE_MODE) variant outcomes.
`<n>`                full generated source of example n (both variants).
`<n> <substr>`       only generated-source lines containing <substr>.

Usage:
  python -X utf8 evidence/examples/_inspect_v1_codegen.py summary
  python -X utf8 evidence/examples/_inspect_v1_codegen.py 2
  python -X utf8 evidence/examples/_inspect_v1_codegen.py 6 index_
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = os.path.join(HERE, "v1_examples_frozen.json")


def load():
    return json.load(io.open(FROZEN, encoding="utf-8"))


def short(txt, n=110):
    txt = (txt or "").replace("\n", " ")
    return txt[:n]


def summary():
    data = load()
    print("python", data["python"].split()[0], "| chunks", data["n_chunks"])
    for rec in data["chunks"]:
        print("=" * 100)
        print("EX %d  format.py:%d-%d  modes=%s  exception=%s  pprint_calls=%d"
              % (rec["index"], rec["format_py_lines"][0], rec["format_py_lines"][1],
                 rec["modes_set_by_example"], rec["exception"], rec["pprint_n_calls"]))
        print("  as-written: %s" % short(rec["pprint_value"]))
        te = rec.get("trye_on_errors") or {}
        if te:
            print("  swallowed : n=%s unique=%s" % (te.get("n"), te.get("unique")))
        for label, v in (rec.get("variants") or {}).items():
            print("  %-20s -> %s" % (label, v.get("error") or short(v.get("result"))))
            if v.get("error"):
                for line in v["traceback"].strip().splitlines()[-4:]:
                    print("        | " + line.strip())


def detail(argv):
    idx = int(argv[1])
    needle = argv[2] if len(argv) > 2 else None
    rec = [c for c in load()["chunks"] if c["index"] == idx][0]
    print("modes_set_by_example:", rec["modes_set_by_example"])
    print("exception:", rec["exception"], "| pprint_calls:", rec["pprint_n_calls"])
    print("as-written pprint_value:", short(rec["pprint_value"], 400))
    for label, v in (rec.get("variants") or {}).items():
        print("=" * 80)
        print("VARIANT", label, "modes", v.get("modes"))
        print("error:", v.get("error"))
        if v.get("traceback"):
            print(v["traceback"].strip())
        if v.get("result"):
            print("result:", short(v["result"], 250))
    for name in ("generated_source", "generated_source_as_written"):
        src = rec.get(name)
        print("=" * 80)
        print("%s (%d chars)" % (name, len(src) if src else -1))
        for line in (src or "").splitlines():
            if needle is None or needle in line:
                print("   " + line[:400])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        summary()
    else:
        detail(sys.argv if len(sys.argv) > 1 else ["", "2"])
