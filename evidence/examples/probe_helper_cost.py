"""Attribute the v2 per-example overhead to specific dcg.runtime helpers (read-only).

The example benchmark shows v2 slower than v1 on exactly the examples whose rows
contain arithmetic / bracket-index expressions.  This probe measures the helpers
those expressions compile to, against the inline Python they replace, so the
overhead is a number rather than a guess.
"""
import gc
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dcg import runtime as rt  # noqa: E402

N = 2_000_000
REPS = 3


def timeit(fn, n=N, reps=REPS):
    fn()
    out = []
    for _ in range(reps):
        gc.collect()
        t = time.perf_counter()
        for _ in range(n):
            fn()
        out.append(time.perf_counter() - t)
    return min(out) / n * 1e9  # nanoseconds per call


A, B = 7, 11
LST = [0, 1, 2, 3]


def report(label, helper, inline):
    h = timeit(helper)
    i = timeit(inline)
    print("%-34s helper %7.1f ns   inline %7.1f ns   overhead %7.1f ns  (%.2fx)"
          % (label, h, i, h - i, h / i))


print("dcg.runtime helpers vs the inline Python they compile to  (min of %d x %d calls)"
      % (REPS, N))
print("helper names are the ones dcg/codegen.py actually emits.")
print("-" * 96)
report("_ad(a,b)        vs a+b", lambda: rt._ad(A, B), lambda: A + B)
report("_sb(a,b)        vs a-b", lambda: rt._sb(A, B), lambda: A - B)
report("_ml(a,b)        vs a*b", lambda: rt._ml(A, B), lambda: A * B)
report("_cc(a,b)        vs a & b", lambda: rt._cc(A, B), lambda: A & B)
report("_lt(a,b)        vs a<b", lambda: rt._lt(A, B), lambda: A < B)
report("_eq(a,b)        vs a==b", lambda: rt._eq(A, B), lambda: A == B)
report("_nt(a)          vs not a", lambda: rt._nt(A), lambda: not A)
report("_neg(a)         vs -a", lambda: rt._neg(A), lambda: -A)
report("_ix_get(l,2)    vs l[2]", lambda: rt._ix_get(LST, 2), lambda: LST[2])
report("_ix_get(l,9) miss vs l[9] ok", lambda: rt._ix_get(LST, 9), lambda: 0)
report("_ar(1,2,3)      vs [1,2,3]", lambda: rt._ar(1, 2, 3), lambda: [1, 2, 3])
report("_S(a)           vs str(a)", lambda: rt._S(A), lambda: str(A))
report("_N(a)           vs int(a)", lambda: rt._N(A), lambda: int(A))
print("-" * 96)
print("sum-of-helpers cost for the EX1 row (3x_ad + 4x_ix_get):")
h_ad = timeit(lambda: rt._ad(A, B))
h_ix = timeit(lambda: rt._ix_get(LST, 2))
print("  3*%.1f + 4*%.1f = %.0f ns/row  (EX1 measured v2-v1 gap: ~1640 ns/row)"
      % (h_ad, h_ix, 3 * h_ad + 4 * h_ix))

