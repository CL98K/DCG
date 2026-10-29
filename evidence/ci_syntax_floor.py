"""Syntax floor: parse every source file with the 3.8 grammar.

Why this exists
---------------
``pyproject.toml`` declares ``requires-python = ">=3.8"`` (deployment floor),
while the interpreter that runs the suite is whatever the CI matrix provides.
Grammar drift is silent on a modern interpreter: ``dict | dict``, ``match``,
``str.removeprefix`` all run fine on 3.13 and blow up only on the oldest
supported version -- exactly the version nobody runs locally.

This script is the cheap guard.  It needs no interpreter of the target version:
``ast.parse`` takes ``feature_version``, so a 3.13 interpreter can parse with
the 3.8 grammar and report precisely which file and line went too new.

It catches *syntax* only.  A new standard-library API (e.g. ``zoneinfo``) is
invisible here and is covered by the matrix job instead -- the two guards are
complementary, neither replaces the other.

Run: python evidence/ci_syntax_floor.py
Exit code 0 = every file parses under the floor grammar.
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The oldest Python the project claims to support.  Keep in sync with
#: ``requires-python`` in ``pyproject.toml`` and the CI matrix floor.
FLOOR = (3, 8)

#: Directories scanned for sources.  Ordered for stable output.
SOURCE_DIRS = ("dcg", "tests", "evidence")

#: Directories skipped wherever they appear.  ``pytest_gate_trap`` holds
#: deliberately broken fixtures (a module-level ``pytest.skip`` that must never
#: be counted as a pass); they are data for a gate, not library source, and one
#: of them carries a UTF-8 BOM that ``ast.parse`` refuses when handed a string.
SKIP_DIRS = frozenset({"__pycache__", "pytest_gate_trap"})


def iter_sources():
    """Yield every ``.py`` path under the scanned directories, repo-relative."""
    for d in SOURCE_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for cur, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for fn in sorted(files):
                if fn.endswith(".py"):
                    yield os.path.join(cur, fn)


def main() -> int:
    floor_txt = "%d.%d" % FLOOR
    failures = []
    seen = 0
    for path in iter_sources():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        try:
            src = io.open(path, encoding="utf-8").read()
        except OSError as e:
            failures.append((rel, 0, "cannot read: %s" % e))
            continue
        seen += 1
        try:
            ast.parse(src, filename=rel, feature_version=FLOOR)
        except SyntaxError as e:
            failures.append((rel, e.lineno or 0, e.msg))

    print("syntax floor: python %s grammar, %d file(s) scanned" % (floor_txt, seen))
    if not failures:
        print("RESULT: OK -- every file parses under %s" % floor_txt)
        return 0

    print("RESULT: FAIL -- %d file(s) use syntax newer than %s:" % (len(failures), floor_txt))
    for rel, line, msg in failures:
        print("  %s:%d: %s" % (rel, line, msg))
    print()
    print("Fix: rewrite the construct to %s, or raise FLOOR here *and*"
          % floor_txt)
    print("     requires-python in pyproject.toml *and* the CI matrix floor.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
