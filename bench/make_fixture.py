"""Generate the frozen conformance fixture: 50 cases + official expected values.

Run once (requires the jsonata probe package); writes bench/frozen/conformance.json.
After this, tests need NO jsonata package -- expected values are baked in (CONTRACT D4/D10).
"""
import json, sys, os

sys.path.insert(0, r"C:\Users\black\Desktop\DCG")
from jsonata import Jsonata
from jsonata_poc import CORE, EDGES, REFUSED, UNDEF

UNDEF_TAG = {"__undef__": True}
ERR_TAG = {"__err__": True}


def normalize(v):
    if v is UNDEF:
        return UNDEF_TAG
    return v


def expected(expr, ctx):
    try:
        v = Jsonata(expr).evaluate(ctx)
    except Exception as e:
        return {"error": type(e).__name__}
    return {"value": normalize(v)}


out = []
for group, cases in (("core", CORE), ("edge", EDGES)):
    for expr, ctx in cases:
        out.append({"group": group, "expr": expr, "ctx": ctx, "expect": expected(expr, ctx)})

refused = [{"expr": e} for e in REFUSED]

doc = {
    "note": "Frozen from jsonata-python 0.7.0. Do not regenerate in CI; this is the baseline.",
    "undef_marker": "__undef__",
    "cases": out,
    "refused": refused,
}

path = r"C:\Users\black\Desktop\DCG\bench\frozen\conformance.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False, indent=1)

n_undef = sum(1 for c in out if c["expect"].get("value") == UNDEF_TAG)
n_err = sum(1 for c in out if "error" in c["expect"])
print(f"wrote {path}")
print(f"  cases={len(out)} (core={len(CORE)}, edge={len(EDGES)}) refused={len(refused)}")
print(f"  undefined={n_undef} error-cases={n_err}")
print("\nsample:")
for c in out[:3] + out[26:29]:
    print(f"  [{c['group']}] {c['expr']!r:30s} ctx={c['ctx']!r:24s} -> {c['expect']}")
