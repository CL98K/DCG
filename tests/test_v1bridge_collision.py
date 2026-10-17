"""Regression test for the v1/v2 module-name collision (CONTRACT.md §5b).

Background: v1's engine is the *module* ``dcg.py`` and ``format.py`` does
``from dcg import ERROR, MODE, SYMBOL, STRUC, Template, Engine``.  v2's engine is the
*package* ``dcg/``.  A regular package shadows a same-named module, so as soon as
``dcg/__init__.py`` exists, plain ``import format`` fails with

    ImportError: cannot import name 'ERROR' from 'dcg'

These tests pin the ruling: v1 is reachable in-process through ``bench/_v1bridge.py``,
and both engines remain usable in one process without contaminating the v2 public surface.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")

if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

_v1bridge = pytest.importorskip("_v1bridge")


def test_v1_engine_loads_and_v2_package_is_restored():
    fmt = _v1bridge.load_v1_engine()
    assert os.path.basename(fmt.__file__) == "format.py"

    # after loading v1, ``dcg`` must resolve back to the v2 package
    import dcg

    assert dcg.__file__ is not None
    assert os.path.basename(os.path.dirname(dcg.__file__)) == "dcg"
    assert dcg.__file__.endswith(os.path.join("dcg", "__init__.py"))


def test_no_v1_symbols_leak_into_v2_public_surface():
    """CONTRACT §5: the v2 ``__all__`` must not contain v1's symbols."""
    import dcg

    exported = set(getattr(dcg, "__all__", ()) or ())
    for v1_only in ("ERROR", "MODE", "SYMBOL", "STRUC", "Template", "Engine"):
        assert v1_only not in exported, f"v1 symbol {v1_only} leaked into dcg.__all__"


def test_v1_and_v2_usable_in_same_process():
    """CONTRACT §7 requires same-process three-way comparison."""
    fmt = _v1bridge.load_v1_engine()
    import dcg

    assert fmt is not None
    assert dcg is not None
    # v1 and v2 are genuinely different modules, not aliases of one another
    assert os.path.abspath(fmt.__file__) != os.path.abspath(dcg.__file__)
