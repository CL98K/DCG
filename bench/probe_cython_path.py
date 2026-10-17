"""Cython 路径独立验证：`generate(cython=True)` 产物必须①能被 Cython 编译
②编译后语义与纯 Python 产物**完全一致**（CONTRACT §4.2 第 ⑨ 条「同源加类型标注，
不改变语义」+ §7 Cython row-object 目标）。

纯 Python 侧的产品是 `from dcg.runtime import *` + cdef 标注；cdef 不是合法 Python，
所以只能真编译。用 pyximport（临时 build 目录，不污染仓库）。

usage: python -X utf8 bench/probe_cython_path.py [n]
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import _workload as W  # noqa: E402
import dcg  # noqa: E402


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    print("=" * 78)
    print("Cython 路径验证（PROVISIONAL）")
    print(f"时间: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    for f in ("dcg/codegen.py", "dcg/runtime.py"):
        p = os.path.join(ROOT, *f.split("/"))
        with open(p, "rb") as fh:
            print(f"{f} sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")

    src_py = dcg.generate(W.V2_CAP)
    src_cy = dcg.generate(W.V2_CAP, cython=True)
    print(f"\n--- cython=True 产物（{len(src_cy)} 字符）---")
    for line in src_cy.splitlines():
        print(f"  | {line}")

    rows = W.make_rows(n)
    py_run = W.load_v2(W.V2_CAP)[0]
    expect = py_run(rows[:2000])

    tmp = tempfile.mkdtemp(prefix="dcgcy_")
    modname = "dcgcap_probe"
    pyx = os.path.join(tmp, modname + ".pyx")
    with open(pyx, "w", encoding="utf-8") as fh:
        fh.write(src_cy)
    print(f"\n临时 .pyx: {pyx}")

    import pyximport  # noqa: E402
    pyximport.install(language_level=3, build_dir=tmp, inplace=False)
    sys.path.insert(0, tmp)
    t0 = time.perf_counter()
    try:
        mod = importlib.import_module(modname)
    except Exception as exc:  # noqa: BLE001
        print(f"Cython 编译/导入失败: {type(exc).__name__}: {exc}")
        return 1
    print(f"编译+导入耗时 {time.perf_counter() - t0:.2f} s")

    run = getattr(mod, "run", None)
    if run is None:
        print("编译产物里没有 run()")
        return 1
    got = run(rows[:2000])
    same = got == expect
    print(f"语义一致性（前 2000 行 vs 纯 Python 产物）: {same}")
    if not same:
        for i, (g, e) in enumerate(zip(got, expect)):
            if g != e:
                print(f"  首个差异 行{i}: cython={g!r}  python={e!r}")
                break
    # 计时（min of 3，与 §7 口径一致）
    arr = W.bench_reps(lambda: run(rows))
    cy_us = W.us_per_row(min(arr), n)
    print(f"cython 产物: min={cy_us:.3f} us/row  reps={['%.3f' % W.us_per_row(x, n) for x in arr]}")
    return 0 if same else 2


if __name__ == "__main__":
    sys.exit(main())
