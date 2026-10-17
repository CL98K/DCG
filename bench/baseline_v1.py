"""M0 baseline: measure v1 (dcg.py + format.py) at the real runtime shape.

Schema mirrors format.py:589-603 (the only complete v1 example in-repo), but uses the
flat field-name form and a shared local binding so 8 fields come from one row.
Run: python baseline_v1.py [N]
"""
import sys, os, time, statistics, gc, cProfile, pstats, io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _v1bridge import load_v1_engine  # noqa: E402

fmt = load_v1_engine()

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200000

# One row: (code, a, b, c, d, payload)  -- payload bound once, indexed cheaply.
def make_rows(n):
    rows = []
    for i in range(n):
        rows.append((f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
                     {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]}))
    return rows

ROWS = make_rows(N)

DESCRIBE = {
    "datas": "$.index",
    "mode": "",
    "protocol": True,
    "single": False,
    "filter": "",
    "_field": {},
    "struct": {
        "doc": [
            {"name": "hour",  "alias": "", "type": "list", "value": "$.5.a"},
            {"name": "hourA", "alias": "", "type": "int",  "value": "$.5.a.1"},
            {"name": "hourB", "alias": "", "type": "int",  "value": "$.5.a.2"},
            {"name": "hourC", "alias": "", "type": "int",  "value": "$.2"},
            {"name": "hourD", "alias": "", "type": "str",  "value": "", "ext": "str($.5.a.1) + str(hourB)"},
            {"name": "hourE", "alias": "", "type": "int",  "value": "", "ext": "hourA + hourB"},
            {"name": "hourF", "alias": "", "type": "int",  "value": "", "ext": "$.5.a.2 + $.5.a.1"},
            {"name": "hourG", "alias": "", "type": "int",  "value": "", "ext": "hour[2] + 123"},
        ]
    },
}

fmt.Format.OPT_MODE = True
fmt.Format.CYTHON_MODE = False
fmt.Format.SAFE_MODE = False
fmt.Format.DEBUG = False

import time as _t
t0 = _t.perf_counter()
info, handler = fmt.Format.parse("BASE", DESCRIBE)
t_build = _t.perf_counter() - t0

DATAS = {"index": ROWS}

# correctness spot-check
res = handler({"index": ROWS[:3]})
rows_out = res["doc"] if isinstance(res, dict) else res
assert len(rows_out) == 3, res
print("output shape:", type(res).__name__, "->", list(res) if isinstance(res, dict) else "list")
print("sample row0:", rows_out[0])
print(f"build(parse+compile) = {t_build*1000:.2f} ms")

def bench(fn, reps=3):
    best = None
    for _ in range(reps):
        gc.collect()
        t = _t.perf_counter()
        fn()
        d = _t.perf_counter() - t
        best = d if best is None else min(best, d)
    return best

for label, data in (("datas=dict{'index':rows}", {"index": ROWS}), ("datas=list[rows]", ROWS)):
    try:
        handler(data)
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__}: {e}")
        continue
    d = bench(lambda: handler(data))
    print(f"{label:28s} {d*1000:9.1f} ms  {d/N*1e6:7.3f} us/row  {N/d:12.0f} rows/s")

# --- profiling: where does the time go? ---
pr = cProfile.Profile()
pr.enable()
handler({"index": ROWS})
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("tottime")
ps.print_stats(18)
print(s.getvalue())
