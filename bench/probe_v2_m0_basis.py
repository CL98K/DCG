"""V2_M0 基准对齐探针：把 Lead 门（evidence/accept.py）里的 M0 与我的 _workload.V2_SCHEMA
逐项对比，找出字符数差异（Lead 量 293 / 我量 210）。

只读 dcg/ 与 evidence/accept.py；输出到 stdout。
"""
from __future__ import annotations

import difflib
import hashlib
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import _workload as W  # noqa: E402
import dcg  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "evidence"))
import accept as A  # noqa: E402


def codes(schema):
    src = dcg.generate(schema)
    return src, len(src), len(src.splitlines())


def main() -> int:
    print("=" * 78)
    print("V2_M0 基准对齐探针  时间=" + datetime.now().astimezone().isoformat(timespec="seconds"))
    for f in ("dcg/codegen.py", "dcg/runtime.py"):
        with open(os.path.join(ROOT, *f.split("/")), "rb") as fh:
            print(f"{f} sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")

    pairs = [("accept.M0", A.M0), ("_workload.V2_SCHEMA", W.V2_SCHEMA),
             ("accept.CAP", A.CAP), ("_workload.V2_CAP", W.V2_CAP)]
    srcs = {}
    for label, schema in pairs:
        try:
            src, n, lines = codes(schema)
            srcs[label] = src
            print(f"\n### {label}: chars={n} lines={lines} name={schema.get('name')!r} "
                  f"input={schema.get('input')}")
            print(dict(schema))
            print("--- 产物 ---")
            for ln in src.splitlines():
                print(f"  | {ln}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {label}: generate 失败 {type(exc).__name__}: {exc}")

    for a, b in (("accept.M0", "_workload.V2_SCHEMA"), ("accept.CAP", "_workload.V2_CAP")):
        if a in srcs and b in srcs:
            print(f"\n=== diff {a}  vs  {b} ===")
            d = list(difflib.unified_diff(srcs[a].splitlines(), srcs[b].splitlines(),
                                          lineterm="", n=1))
            print("\n".join(d) if d else "  (逐行相同)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
