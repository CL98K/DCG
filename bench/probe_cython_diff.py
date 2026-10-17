"""Diagnose the cython product structure (why strip-cdef != pure product).

Read-only probe; prints a unified diff of the two products for V2_M0 / V2_CAP
and confirms whether the ONLY difference is cdef annotations.
"""
from __future__ import annotations

import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _workload as W  # noqa: E402
import dcg  # noqa: E402


def main() -> int:
    for label, schema in (("V2_M0", W.V2_M0), ("V2_CAP_EXT", W.V2_CAP_EXT)):
        py = dcg.generate(schema)
        cy = dcg.generate(schema, cython=True)
        stripped = [ln for ln in cy.splitlines() if not ln.strip().startswith("cdef ")]
        print("=" * 70)
        print(f"{label}: pure={len(py)} chars/cython={len(cy)} chars")
        print(f"strip-cdef == pure ? {'\n'.join(stripped) == py}")
        print("--- cython lines ---")
        for ln in cy.splitlines():
            print(f"  |{ln}")
        print("--- unified diff (pure -> strip-cdef) ---")
        for ln in difflib.unified_diff(py.splitlines(), stripped,
                                       "pure", "strip-cdef", lineterm=""):
            print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
