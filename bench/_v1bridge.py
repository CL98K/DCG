"""Bridge for running the v1 reference engine alongside the v2 package.

PROBLEM (contract-level name collision, found by independent verification):
    v1 engine lives in the module ``dcg.py`` and ``format.py:13`` does
    ``from dcg import ERROR, MODE, SYMBOL, STRUC, Template, Engine``.
    v2 lives in the package ``dcg/``. A regular package **shadows** the module of the
    same name, so once ``dcg/__init__.py`` exists, ``import format`` fails with
    ``ImportError: cannot import name 'ERROR' from 'dcg'``.

RULING (Lead): do NOT modify dcg.py / format.py (they are the frozen v1 baseline), and do
NOT re-export v1 symbols from the v2 package (that would pollute the v2 public surface,
forbidden by CONTRACT.md §5). Instead pin ``sys.modules["dcg"]`` to the v1 module file for
the duration of the v1 import, then restore the v2 package.

Usage:
    from _v1bridge import load_v1_engine
    fmt = load_v1_engine()          # returns the v1 ``format`` module
"""
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_V1_DCG = os.path.join(_ROOT, "dcg.py")


def load_v1_engine():
    """Import and return the v1 ``format`` module, with ``dcg`` bound to v1's dcg.py."""
    if "format" in sys.modules:
        return sys.modules["format"]

    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

    v2_pkg = sys.modules.get("dcg")

    spec = importlib.util.spec_from_file_location("dcg", _V1_DCG)
    v1_mod = importlib.util.module_from_spec(spec)
    sys.modules["dcg"] = v1_mod
    try:
        spec.loader.exec_module(v1_mod)
        import format as fmt  # resolved against the v1 dcg module
    finally:
        # restore the v2 package so both engines stay usable in this process
        if v2_pkg is not None:
            sys.modules["dcg"] = v2_pkg
        else:
            del sys.modules["dcg"]
    return fmt


def v1_datas(rows):
    """v1 expects ``datas`` to be a mapping with the key named by ``describe['datas']``."""
    return {"index": rows}
