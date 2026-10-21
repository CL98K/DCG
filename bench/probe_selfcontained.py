"""产物自足性审计：`from dcg.runtime import *` 是否真的把产物引用的每个名字带进来。

Lead 的契约要求产物落盘成 gen/<name>.py 后可独立 import；若 `import *` 漏掉
下划线开头的 helper（`import *` 默认只导公开名），产物会在独立 import 时 NameError，
而"在注入 SOURCE 的 ns 里跑"会把这个缺陷掩盖掉——所以这里用**空 globals** exec。

用法：python -X utf8 bench/probe_selfcontained.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import dcg  # noqa: E402
import dcg.runtime as runtime  # noqa: E402
import _workload as W  # noqa: E402

HELPER_RE = re.compile(r"(?<![\w.])(_[A-Za-z]\w*)")


def star_namespace():
    """模拟产物第一行的 `from dcg.runtime import *` 到底导入了什么。"""
    ns: dict = {}
    exec("from dcg.runtime import *", ns)  # noqa: S102
    return {k for k in ns if k != "__builtins__"}


def main() -> int:
    print("=" * 78)
    print("产物自足性审计（空 globals exec）")
    print(f"时间: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"dcg.__file__ = {dcg.__file__}")
    cg = os.path.join(ROOT, "dcg", "codegen.py")
    if os.path.exists(cg):
        with open(cg, "rb") as fh:
            print(f"dcg/codegen.py sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")
        print(f"dcg/codegen.py mtime = {datetime.fromtimestamp(os.path.getmtime(cg)).astimezone().isoformat(timespec='seconds')}")
    print(f"runtime 公开名数 = {len(star_namespace())}")
    print(f"runtime.__all__ 存在 = {hasattr(runtime, '__all__')}"
          + (f", 共 {len(runtime.__all__)} 项" if hasattr(runtime, "__all__") else ""))
    for probe in ("_U", "_ad", "_eq", "_S", "_up", "_sub", "_ix_get", "_num"):
        print(f"  runtime.{probe:8s} 存在={hasattr(runtime, probe)}  "
              f"被 import* 带入={probe in star_namespace()}")

    rows8 = W.make_rows(3)
    cases = [
        ("FLIGHT", {
            "name": "flight_probe",
            "input": {},
            "output": {"shape": "list", "single": False, "filter": True},
            "out": {"type": "object", "fields": {
                "id": {"type": "int64", "value": "$.ID"},
                "sum": {"type": "int64", "expr": "$.a + $.b"},
                "grade": {"type": "str", "optional": True, "expr": "$.score >= 90 ? 'A' : 'B'"},
                "tag": {"type": "str", "const": "flight"},
            }},
        }, [{"ID": 1, "a": 0, "b": 5, "score": 95}]),
        ("CAP", W.V2_CAP, rows8),
        ("M0", W.V2_SCHEMA, rows8),
    ]

    for label, schema, rows in cases:
        print("-" * 78)
        print(f"## {label}")
        try:
            src = dcg.generate(schema)
        except Exception as exc:  # noqa: BLE001
            print(f"  generate 失败: {type(exc).__name__}: {exc}")
            continue
        used = sorted(set(HELPER_RE.findall(src)))
        missing = [n for n in used if n not in star_namespace()]
        print(f"  产物引用的下划线名: {used}")
        print(f"  其中 `import *` 未带入: {missing or '无'}")
        ns: dict = {}
        try:
            exec(compile(src, f"<gen:{label}>", "exec"), ns)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            print(f"  空 globals exec 失败: {type(exc).__name__}: {exc}")
            continue
        try:
            out = ns["run"](rows)
        except Exception as exc:  # noqa: BLE001
            print(f"  空 globals exec 成功，但 run() 失败: {type(exc).__name__}: {exc}")
            continue
        print(f"  空 globals exec + run 成功: {str(out)[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
