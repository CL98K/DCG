"""M0: hand-written Cython ceiling for the same workload (200k x 8 fields).

This is the realistic upper bound for a generated+Cython'ed kernel.
Build+run: python bench/cy_ceiling.py [N]
"""
import sys, time, gc, os

import numpy as np
from Cython.Build import cythonize
from setuptools import Extension
import pyximport
pyximport.install(setup_args={"include_dirs": np.get_include()}, language_level=3)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200000

PYX = r"""
# cython: boundscheck=False, wraparound=False, initializedcheck=False, cdivision=True, language_level=3

def run(list rows):
    cdef Py_ssize_t n = len(rows), i
    cdef list out = [None] * n
    cdef object r, payload
    cdef int hA, hB, c
    cdef str s
    for i in range(n):
        r = rows[i]
        payload = r[5]["a"]
        hA = r[5]["a"][1]
        hB = r[5]["a"][2]
        c = r[2]
        s = str(hA) + str(hB)
        out[i] = [payload, hA, hB, c, s, hA + hB, hB + hA, hB + 123]
    return out


def run_opt(list rows):
    cdef Py_ssize_t n = len(rows), i
    cdef list out = [None] * n
    cdef object r, payload, pl
    cdef int hA, hB, c
    cdef str s
    for i in range(n):
        r = rows[i]
        pl = r[5]
        payload = pl["a"]
        hA = payload[1]
        hB = payload[2]
        c = r[2]
        s = str(hA) + str(hB)
        out[i] = [payload, hA, hB, c, s, hA + hB, hB + hA, hB + 123]
    return out
"""

with open("_cy_ceiling.pyx", "w") as f:
    f.write(PYX)

import _cy_ceiling  # pyximport compiles on import
import importlib
importlib.reload(_cy_ceiling)


def make_rows(n):
    return [(f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
             {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}) for i in range(n)]


ROWS = make_rows(N)


def py_ref(rows):
    out = []
    ap = out.append
    for r in rows:
        a = r[5]["a"]
        hA, hB = a[1], a[2]
        ap([a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA, a[2] + 123])
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


a_py = py_ref(ROWS)
a_cy = _cy_ceiling.run(ROWS)
a_cy2 = _cy_ceiling.run_opt(ROWS)
ok = len(a_py) == len(a_cy) == len(a_cy2) and a_py[0] == a_cy[0] == a_cy2[0] and a_py[-1] == a_cy2[-1]
print(f"correctness: cy == py -> {ok}")
if not ok:
    print("  py[0] =", a_py[0])
    print("  cy[0] =", a_cy[0])

for label, fn in (("pure python hand-written", lambda: py_ref(ROWS)),
                  ("cython hand-written", lambda: _cy_ceiling.run(ROWS)),
                  ("cython bind-once", lambda: _cy_ceiling.run_opt(ROWS))):
    d = bench(fn)
    print(f"{label:28s} {d*1000:8.1f} ms  {d/N*1e6:7.3f} us/row  {N/d:11.0f} rows/s")
