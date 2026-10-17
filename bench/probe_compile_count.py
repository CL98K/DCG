"""rule ⑥（每次 build 只 compile 一次）的**测量完整性**探针。

发现：`bench_codegen.py` 里当 CAP[ext] 是进程内第一次 generate 时，compile() 计数 = 5；
而同一进程后段的 jsonata schema 计数 = 1。两者 codegen 路径相同，差别只有「冷/热」。
假设：冷路径里的懒加载 import 会经 CPython 的源码编译器，自己也调用 compile()。

本探针把「冷 vs 热」分开量，并列出每次 compile() 的调用点（`sys._getframe` 回溯），
这样规则 ⑥ 的判定不会被 import 噪声污染。

usage: python -X utf8 bench/probe_compile_count.py
"""
from __future__ import annotations

import builtins
import hashlib
import os
import sys
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import _workload as W  # noqa: E402

_real_compile = builtins.compile


def report(title, calls):
    n_prod = sum(1 for c in calls if c[1] == "product-exec")
    n_ast = sum(1 for c in calls if c[1] == "ast-parse")
    print(f"\n--- {title}: {len(calls)} compile() call(s) "
          f"[product-exec={n_prod}, ast-parse={n_ast}] ---")
    for src, kind, fn, file, line in calls:
        head = (src if isinstance(src, str) else repr(src)).replace("\n", "\\n")[:60]
        print(f"  [{kind}] {file}:{line} in {fn}()  src={head!r}")


def main() -> int:
    print("=" * 78)
    print("rule 6 测量完整性探针  时间=" + datetime.now().astimezone().isoformat(timespec="seconds"))
    for f in ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py"):
        p = os.path.join(ROOT, *f.split("/"))
        with open(p, "rb") as fh:
            print(f"{f} sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")

    import dcg

    # (1) cold: very first generate() in this process
    calls = []

    def counter(*a, **k):
        fr = sys._getframe(1)
        kind = "ast-parse" if len(a) >= 4 or k.get("flags") else "product-exec"
        calls.append((a[0] if a else k.get("source", ""), kind, fr.f_code.co_name,
                      os.path.basename(fr.f_code.co_filename), fr.f_lineno))
        return _real_compile(*a, **k)

    builtins.compile = counter
    try:
        src_cold = dcg.generate(W.V2_CAP_EXT)
    finally:
        builtins.compile = _real_compile
    report("COLD first generate() in process", calls)

    # (2) warm: same schema, imports already resolved
    calls.clear()
    builtins.compile = counter
    try:
        src_warm = dcg.generate(W.V2_CAP_EXT)
    finally:
        builtins.compile = _real_compile
    report("WARM generate(), same schema", calls)
    print(f"  cold and warm source identical: {src_cold == src_warm}")

    # (3) build(): cold vs warm
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        calls.clear()
        builtins.compile = counter
        try:
            dcg.build({"name": "probe_cc", "input": {"source": "index"},
                       "output": {"shape": "list", "single": False, "filter": True},
                       "out": {"type": "object", "fields": {
                           "v": {"type": "int64", "value": "$.5.a.1"}}}}, td)
        finally:
            builtins.compile = _real_compile
        report("build() after warm-up", calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
