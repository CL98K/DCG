"""M0: manual micro-decomposition of the v1 baseline cost.

cProfile is useless here (all time is inside one giant generated function), so instead
build hand-written variants that progressively reproduce what DCG generates, and compare.
Run: python bench/decompose.py [N]
"""
import sys, time, gc

sys.path.insert(0, r"C:\Users\black\Desktop\DCG")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200000


def make_rows(n):
    rows = []
    for i in range(n):
        rows.append((f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
                     {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}))
    return rows


ROWS = make_rows(N)
DATAS = {"index": ROWS}

# --- A: exactly what v1 must do, but hand-written tight -----------------------
def ref_tight(datas):
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        h, hA, hB = a, a[1], a[2]
        ap([h, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123])
    return {"doc": out}


# --- B: same but re-does the payload lookup per field (v1 likely does this) ----
def ref_relookup(datas):
    out = []
    ap = out.append
    for r in datas["index"]:
        ap([r[5]["a"], r[5]["a"][1], r[5]["a"][2], r[2],
            str(r[5]["a"][1]) + str(r[5]["a"][2]),
            r[5]["a"][1] + r[5]["a"][2], r[5]["a"][2] + r[5]["a"][1],
            r[5]["a"][2] + 123])
    return {"doc": out}


# --- C: no output object at all (isolates payload+arith cost) -----------------
def ref_nooutput(datas):
    n = 0
    for r in datas["index"]:
        a = r[5]["a"]
        hA, hB = a[1], a[2]
        n += hA + hB + (hB + hA) + (a[2] + 123) + len(str(hA) + str(hB)) + r[2]
    return n


# --- D: output construction only (values precomputed) -------------------------
def ref_outonly(datas):
    out = []
    ap = out.append
    for r in datas["index"]:
        ap([r[5]["a"], 1, 2, r[2], "12", 3, 3, 125])
    return {"doc": out}


# --- E: preallocated list + index assign -------------------------------------
def ref_prealloc(datas):
    rows = datas["index"]
    out = [None] * len(rows)
    i = 0
    for r in rows:
        a = r[5]["a"]
        hA, hB = a[1], a[2]
        out[i] = [a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123]
        i += 1
    return {"doc": out}


# --- F: tuple rows instead of list rows in output -----------------------------
def ref_tupleout(datas):
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        hA, hB = a[1], a[2]
        ap((a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123))
    return {"doc": out}


def bench(fn, reps=3):
    best = None
    for _ in range(reps):
        gc.collect()
        t = time.perf_counter()
        fn(DATAS)
        d = time.perf_counter() - t
        best = d if best is None else min(best, d)
    return best


VARIANTS = [
    ("A ref_tight (unpack once)", ref_tight),
    ("B ref_relookup (per-field idx)", ref_relookup),
    ("C ref_nooutput (arith only)", ref_nooutput),
    ("D ref_outonly (output only)", ref_outonly),
    ("E ref_prealloc (no append)", ref_prealloc),
    ("F ref_tupleout (tuple rows)", ref_tupleout),
]

print(f"N = {N}")
base = None
for label, fn in VARIANTS:
    d = bench(fn)
    us = d / N * 1e6
    if base is None:
        base = us
    print(f"{label:32s} {d*1000:8.1f} ms  {us:7.3f} us/row  {N/d:11.0f} rows/s  {us/base:5.2f}x")
