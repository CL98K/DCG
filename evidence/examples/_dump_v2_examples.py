"""Print the v2 generated source for each migrated example (read-only probe)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "tests", "examples")):
    if p not in sys.path:
        sys.path.insert(0, p)

from v1_parity import schemas  # noqa: E402

import dcg  # noqa: E402

for key in ("ex1", "ex2_docs", "ex3", "ex4"):
    s = schemas.by_name()[key]
    src = dcg.generate(s)
    print("=" * 70)
    print("%s  (%d chars)" % (key, len(src)))
    print("=" * 70)
    for i, line in enumerate(src.splitlines(), 1):
        print("%3d| %s" % (i, line))
