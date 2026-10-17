"""把 v2 与手写参照的差距归因到「契约强制的 helper 调用」上（PROVISIONAL）。

背景：当前 v2 在 CAP 负载上实测约 3.26× 手写参照（CONTRACT §7 承诺 ≤1.05×）。
CAP 产物形态（codegen sha1 记录在 evidence 里）：

    from dcg.runtime import *
    def run(_in):
     _o=[]
     for R in _in:
      if R:
       _a=R[5]['a']; _a1=_a[1]; _a2=_a[2]
       _o.append([_a1,_a2,_S(_a1)+_S(_a2),_ad(_a1,_a2)])
     return _o

三个变体做同负载对照，把差距拆成「helper 开销」与「结构性开销」：
  inline  : 与产物同结构，但用内建 `str`/`+`（= 手写参照的上界）
  helpers : 与产物同结构，但用 runtime 的 `_S`/`_ad`（= 产物实际形态）
  product : dcg.generate 出来的真实产物
usage: python -X utf8 bench/probe_helper_overhead.py [n]
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import _workload as W  # noqa: E402
from dcg.runtime import _S, _ad  # noqa: E402


def run_inline(rows):
    out = []
    ap = out.append
    for r in rows:
        if r:
            a = r[5]["a"]
            a1 = a[1]
            a2 = a[2]
            ap([a1, a2, str(a1) + str(a2), a1 + a2])
    return out


def run_helpers(rows):
    out = []
    ap = out.append
    for r in rows:
        if r:
            a = r[5]["a"]
            a1 = a[1]
            a2 = a[2]
            ap([a1, a2, _S(a1) + _S(a2), _ad(a1, a2)])
    return out


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    print("PROVISIONAL (concurrent load) -- 非静默窗口数字")
    print(f"时间: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    for f in ("dcg/codegen.py", "dcg/runtime.py"):
        p = os.path.join(ROOT, *f.split("/"))
        with open(p, "rb") as fh:
            print(f"{f} sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")

    run, src = W.load_v2(W.V2_CAP)
    rows = W.make_rows(n)
    # 正确性前置：三者必须产出相同结果（否则计时无意义）
    a, b, c = run(rows[:1000]), run_inline(rows[:1000]), run_helpers(rows[:1000])
    print(f"正确性: product==inline {a == b}   helpers==inline {c == b}")

    res = {}
    for name, fn in (("inline", run_inline), ("helpers", run_helpers), ("product", run)):
        arr = W.bench_reps(lambda fn=fn: fn(rows))
        lo = min(arr)
        res[name] = W.us_per_row(lo, n)
        print(f"{name:8s} min={res[name]:.3f} us/row  "
              f"reps={['%.3f' % W.us_per_row(x, n) for x in arr]}")

    base = res["inline"]
    print(f"\nhelpers / inline = {res['helpers'] / base:.2f}x   <- 契约强制的 helper 开销")
    print(f"product / inline = {res['product'] / base:.2f}x   <- 产物 vs 最优手写上界")
    print(f"product / helpers= {res['product'] / res['helpers']:.2f}x   <- codegen 结构开销")
    return 0


if __name__ == "__main__":
    sys.exit(main())
