"""Is `dcg.generate` deterministic, and is the row-loop name consistent with the
signature?  Repeated generation, in-process and across processes (read-only probe).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "tests", "examples")):
    if p not in sys.path:
        sys.path.insert(0, p)
import dcg  # noqa: E402
from v1_parity import schemas  # noqa: E402

MINI = {
    "name": "mini",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": True},
    "out": {"type": "object", "fields": {"h": {"type": "int64", "expr": "$.2 + 1"}}},
}

CASES = {"mini": MINI, "ex1": schemas.EX1, "ex2_docs": schemas.EX2_DOCS,
         "ex3": schemas.EX3, "ex4": schemas.EX4}

for name, schema in CASES.items():
    srcs = {dcg.generate(schema) for _ in range(5)}
    sig = [l for l in next(iter(srcs)).splitlines() if l.startswith("def run")]
    loop = [l for l in next(iter(srcs)).splitlines() if "for R in" in l]
    sig_name = sig[0].split("(")[1].split(")")[0] if sig else "?"
    loop_name = loop[0].split("for R in")[1].strip().rstrip(":") if loop else "?"
    print("%-9s distinct_sources=%d  param=%-5s loop=%-5s  consistent=%s"
          % (name, len(srcs), sig_name, loop_name, sig_name == loop_name))
