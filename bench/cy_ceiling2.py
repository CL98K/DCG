"""M0b: probe the actual Cython ceiling -- what dominates 0.41 us/row?

Variants: list rows vs tuple rows vs no-output, plus typed-array input path.
Run: python bench/cy_ceiling2.py [N]
"""
import sys, time, gc, os, atexit, shutil, tempfile

import numpy as np
import pyximport

# The .pyx source below is generated at run time; keep it (and the Cython build
# output) inside a temp dir so the repo root stays clean.  Set DCG_KEEP_CY=1 to
# keep the temp dir for inspection.  NOTE: bench/cy_ceiling2.py and its
# `_cy_ceiling2.pyx` must live and die together -- the module cannot outlive
# this script.
_TMPDIR = tempfile.mkdtemp(prefix="cy_ceiling2_")
if os.environ.get("DCG_KEEP_CY") != "1":
    atexit.register(shutil.rmtree, _TMPDIR, ignore_errors=True)
print(f"cython build dir: {_TMPDIR} (removed on exit unless DCG_KEEP_CY=1)")

pyximport.install(setup_args={"include_dirs": np.get_include()}, language_level=3,
                  build_dir=_TMPDIR)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200000

PYX = r"""
# cython: boundscheck=False, wraparound=False, initializedcheck=False, cdivision=True, language_level=3

def run_list(list rows):
    cdef Py_ssize_t n = len(rows), i
    cdef list out = [None] * n
    cdef object r, payload, pl
    cdef int hA, hB, c
    for i in range(n):
        r = rows[i]
        pl = r[5]
        payload = pl["a"]
        hA = payload[1]
        hB = payload[2]
        c = r[2]
        out[i] = [payload, hA, hB, c, str(hA) + str(hB), hA + hB, hB + hA, hB + 123]
    return out


def run_tuple(list rows):
    cdef Py_ssize_t n = len(rows), i
    cdef list out = [None] * n
    cdef object r, payload, pl
    cdef int hA, hB, c
    for i in range(n):
        r = rows[i]
        pl = r[5]
        payload = pl["a"]
        hA = payload[1]
        hB = payload[2]
        c = r[2]
        out[i] = (payload, hA, hB, c, str(hA) + str(hB), hA + hB, hB + hA, hB + 123)
    return out


def run_noout(list rows):
    cdef Py_ssize_t n = len(rows), i
    cdef long acc = 0
    cdef object r, payload, pl
    cdef int hA, hB
    for i in range(n):
        r = rows[i]
        pl = r[5]
        payload = pl["a"]
        hA = payload[1]
        hB = payload[2]
        acc += hA + hB + (hB + hA) + (hB + 123) + r[2]
    return acc


def run_typed(long[::1] col2, long[::1] colA, long[::1] colB):
    cdef Py_ssize_t n = col2.shape[0], i
    cdef list out = [None] * n
    cdef int hA, hB
    for i in range(n):
        hA = colA[i]
        hB = colB[i]
        out[i] = [hA, hB, col2[i], str(hA) + str(hB), hA + hB, hB + hA, hB + 123]
    return out
"""

with open(os.path.join(_TMPDIR, "_cy_ceiling2.pyx"), "w") as f:
    f.write(PYX)
sys.path.insert(0, _TMPDIR)
import _cy_ceiling2
import importlib
importlib.reload(_cy_ceiling2)


def make_rows(n):
    return [(f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
             {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}) for i in range(n)]


ROWS = make_rows(N)
COL2 = np.array([r[2] for r in ROWS], dtype=np.int32)
COLA = np.array([r[5]["a"][1] for r in ROWS], dtype=np.int32)
COLB = np.array([r[5]["a"][2] for r in ROWS], dtype=np.int32)


def py_ref(rows):
    out = []
    ap = out.append
    for r in rows:
        a = r[5]["a"]
        hA, hB = a[1], a[2]
        ap([a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123])
    return out


def py_typed(c2, ca, cb):
    out = []
    ap = out.append
    for i in range(len(c2)):
        hA, hB = int(ca[i]), int(cb[i])
        ap([hA, hB, int(c2[i]), str(hA) + str(hB), hA + hB, hB + hA, hB + 123])
    return out


def bench(fn, reps=3):
    best = None
    for _ in range(reps):
        gc.collect()
        t = time.perf_counter()
        fn()
        d = time.perf_counter() - t
        best = d if best is None else min(best, d)
    return best


# correctness
assert _cy_ceiling2.run_list(ROWS)[0] == py_ref(ROWS)[0]
assert _cy_ceiling2.run_typed(COL2, COLA, COLB)[0] == py_typed(COL2, COLA, COLB)[0]
print("correctness: all variants match python reference -> True")

VARIANTS = [
    ("py   row-object (7 fields)", lambda: py_ref(ROWS)),
    ("cy   row-object  list rows", lambda: _cy_ceiling2.run_list(ROWS)),
    ("cy   row-object tuple rows", lambda: _cy_ceiling2.run_tuple(ROWS)),
    ("cy   row-object NO output ", lambda: _cy_ceiling2.run_noout(ROWS)),
    ("py   typed columns         ", lambda: py_typed(COL2, COLA, COLB)),
    ("cy   typed columns         ", lambda: _cy_ceiling2.run_typed(COL2, COLA, COLB)),
]

print(f"N = {N}")
for label, fn in VARIANTS:
    d = bench(fn)
    print(f"{label:30s} {d*1000:8.1f} ms  {d/N*1e6:7.3f} us/row  {N/d:11.0f} rows/s")
