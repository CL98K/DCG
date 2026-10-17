"""One-shot verification gate for DCG v2 (verifier-dev, task-3).

Why Python and not PowerShell: this box has a script execution policy that refuses
unsigned .ps1 files (`无法对文件 ... 进行数字签名`), so a .ps1 runner is not runnable
here.  A Python runner needs no policy change and keeps the evidence in UTF-8.

Usage:
    python -X utf8 bench/run_verify.py            # full: gates + codegen review + timing
    python -X utf8 bench/run_verify.py --fast     # skip the 200k-row timing section

Order (Lead m00414 section 5):
    0. readiness gate  -> if v2 is not ready: STOP, write no v2 numbers
    0.5 Lead's own gate `evidence/accept.py`  -> frozen to evidence/accept_by_verifier.txt,
        and I paste that output back to the Lead verbatim BEFORE running my own gates
    1. pytest tests/test_e2e_flight.py
    2. pytest tests/test_jx_conformance.py
    3. bench/bench_codegen.py   (chars / compile count / determinism / §4.2 rules)
    4. bench/bench_row.py       (three-way timing, min-of-3 + spread + settle rounds)

Every artefact lands in evidence/ as UTF-8; nothing under dcg/ is touched.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(ROOT, "evidence")
PY = sys.executable

READINESS_PROBE = r'''
import sys
sys.path.insert(0, r"%s")
try:
    import dcg
except Exception as e:
    print("IMPORT-FAIL", type(e).__name__, e); sys.exit(3)
missing = [n for n in ("validate","SchemaError","generate","build","gen_name",
                       "compile_expr","Unsupported","Registry") if not hasattr(dcg, n)]
print("missing public surface:", missing)
import inspect
try:
    import dcg.codegen as cg
    print("codegen NotImplementedError lines:",
          [i + 1 for i, l in enumerate(inspect.getsource(cg).splitlines())
           if "NotImplementedError" in l])
except Exception as e:
    print("codegen introspection failed:", type(e).__name__, e)
sys.exit(0 if not missing else 4)
''' % ROOT


def machine_load_note() -> str:
    """Absolute us/row is load-sensitive: parser-dev/jx-dev/lead run on this same box.

    The ratio verdict (Lead m00414) is robust to this, but the load at measurement
    time is frozen alongside the numbers so nobody compares a quiet run with a busy one.
    Lead m00521: provisional numbers must carry the process table **and a timestamp**.
    """
    import datetime
    lines = ["# --- machine load at measurement time (absolute us/row is load-sensitive) ---",
             "# timestamp: %s" % datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
             "# PROVISIONAL (concurrent load): parser-dev / jx-dev / lead / verifier-dev "
             "share this box; final numbers require a quiet window decided by the Lead (m00521)"]
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                             capture_output=True, text=True, errors="replace", timeout=30)
        body = [l for l in out.stdout.splitlines() if l.strip()]
        lines.append("# python processes: %d" % max(0, len(body) - 1))
        lines.extend(body)
    except Exception as e:
        lines.append("# tasklist unavailable: %s: %s" % (type(e).__name__, e))
    return "\n".join(lines) + "\n"


def run(out_rel: str, args: list, append_header: str = "") -> int:
    path = os.path.join(ROOT, out_rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print("=== %s  <=  %s" % (out_rel, " ".join(args)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# cmd: %s %s\n" % (PY, " ".join(args)))
        if append_header:
            fh.write(append_header)
        p = subprocess.run([PY, "-X", "utf8"] + args, cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fh.write(p.stdout.decode("utf-8", "replace"))
        fh.write("# exit=%d\n" % p.returncode)
    print("    exit=%d" % p.returncode)
    return p.returncode


def main() -> int:
    fast = "--fast" in sys.argv
    os.makedirs(EVIDENCE, exist_ok=True)

    print("--- 0. readiness gate ---")
    probe = subprocess.run([PY, "-X", "utf8", "-c", READINESS_PROBE], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = probe.stdout.decode("utf-8", "replace")
    print(text.rstrip())
    if probe.returncode != 0:
        print("v2 NOT READY (probe exit=%d) -- STOPPING per Lead instruction; "
              "no v2 number written." % probe.returncode)
        return probe.returncode

    print("--- 0.5 Lead's own gate (evidence/accept.py) ---")
    if os.path.exists(os.path.join(EVIDENCE, "accept.py")):
        run("evidence/accept_by_verifier.txt", ["evidence/accept.py"])
        print(">> paste evidence/accept_by_verifier.txt verbatim back to lead (Lead m00414)")
    else:
        print("evidence/accept.py not found -- skipped")

    print("--- 1. e2e flight ---")
    run("evidence/pytest_e2e_v2.txt",
        ["-m", "pytest", "tests/test_e2e_flight.py", "-q", "-rA", "--no-header"])
    print("--- 2. jx conformance ---")
    run("evidence/pytest_jx_conformance_v2.txt",
        ["-m", "pytest", "tests/test_jx_conformance.py", "-q", "-rA", "--no-header"])
    print("--- 3. codegen review ---")
    run("evidence/v2_generator.txt", ["bench/bench_codegen.py", "--reps", "3"])

    if fast:
        print("--- 4. timing SKIPPED (--fast) ---")
    else:
        print("--- 4. three-way timing ---")
        run("evidence/bench_row_v2.txt",
            ["bench/bench_row.py", "--reps", "3", "--n", "200000"],
            append_header=machine_load_note())

    print("\n--- evidence summary (PASS / FAIL / SKIP must stay distinct) ---")
    for name in sorted(os.listdir(EVIDENCE)):
        if not name.endswith(".txt") or "v2" not in name:
            continue
        with open(os.path.join(EVIDENCE, name), encoding="utf-8", errors="replace") as fh:
            t = fh.read()
        print("%-34s PASS=%-4d FAIL=%-4d SKIP=%-4d" % (
            name, t.count("PASS"), t.count("FAIL"), t.count("SKIP") + t.lower().count("skipped")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
