"""就绪探针（Hold 期使用）：只回答「generate 是否可调用 + 内联是否落地」，
不采集字符数/计时，避免在 codegen 变动期产出不可引用的数字。

用法: python -X utf8 bench/probe_ready.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _workload as W  # noqa: E402
import dcg  # noqa: E402

CASES = (("V2_M0", "V2_M0"), ("V2_CAP_EXT", "V2_CAP_EXT"), ("V2_SCHEMA", "V2_SCHEMA"))


def main() -> int:
    bad = 0
    for label, attr in CASES:
        schema = getattr(W, attr)
        try:
            src = dcg.generate(schema)
            compile(src, "<probe>", "exec")
            flags = []
            flat = src.replace(" ", "")
            if "_ix_get(hour,2)" in flat:
                flags.append("_ix_get(hour,2) STILL PRESENT (内联未落地)")
            if "is _U" in src:
                flags.append("has is _U guard")
            if "_ad(" in src:
                flags.append("has _ad(")
            print(f"{label}: OK  {'; '.join(flags) or 'no flags'}")
        except Exception as exc:  # noqa: BLE001 - probe must report everything
            bad += 1
            print(f"{label}: FAIL {type(exc).__name__}: {exc}")
    print(f"READY: {'no' if bad else 'yes'} (failures={bad})")
    return 4 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
