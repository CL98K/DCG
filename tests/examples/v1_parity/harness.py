"""v1 -> v2 parity harness for the `format.py` examples (task-5).

Design rules this module enforces
---------------------------------
1. **The v1 side is never hand-copied.**  Expectations come from
   `evidence/examples/v1_examples_frozen.json`, which `probe_v1_examples.py`
   produces by really executing `format.py:577-816` (each example in its own
   subprocess) with the v1 engine loaded through the sanctioned bridge
   (`bench/_v1bridge.py` -> `load_v1_engine`).  If that file is missing we
   regenerate it rather than inventing expectations.
2. **The v1 defect is visible, not patched over.**  `golden(n).exception` and
   `golden(n).silent_loss` carry the verbatim v1 evidence, so a test that must
   fail can xfail with a strict, evidence-backed reason.
3. **v2 is driven only through the public surface** in `dcg/__init__.py`
   (`generate`), never by reaching into `dcg.codegen` internals.
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
EVIDENCE = os.path.join(ROOT, "evidence", "examples")
FROZEN = os.path.join(EVIDENCE, "v1_examples_frozen.json")
PROBE = os.path.join(EVIDENCE, "probe_v1_examples.py")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class NotReady(RuntimeError):
    """v2 cannot express what the test needs - not a pass, not a silent skip."""


def ensure_frozen():
    """Return the frozen v1 record, regenerating it if it is absent."""
    if not os.path.exists(FROZEN):
        subprocess.run([sys.executable, "-X", "utf8", PROBE],
                       cwd=ROOT, check=True, capture_output=True)
    with io.open(FROZEN, encoding="utf-8") as fh:
        return json.load(fh)


_DATA = None


def data():
    global _DATA
    if _DATA is None:
        _DATA = ensure_frozen()
    return _DATA


def _parse(text):
    """Parse a frozen repr back into Python objects.

    `ast.literal_eval` covers everything except example 5, whose payload holds
    `datetime.datetime` / `datetime.date`; that one falls back to an eval with a
    namespace containing only `datetime` and no builtins.
    """
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        import datetime
        return eval(text, {"datetime": datetime, "__builtins__": {}}, {})  # noqa: S307


def parse_repr(text):
    """Public alias for the frozen-repr parser used by the tests."""
    return _parse(text)


class Golden:
    """One frozen v1 example."""

    def __init__(self, rec):
        self.raw = rec
        self.index = rec["index"]
        self.format_py_lines = tuple(rec["format_py_lines"])
        self.modes = rec["modes_set_by_example"]
        self.exception = rec["exception"]
        self.traceback = rec["traceback"]
        self.pprint_n_calls = rec["pprint_n_calls"]
        self.value = _parse(rec["pprint_value"])
        self.datas = _parse(rec["datas"])
        self.describe = _parse(rec["describe"])
        self.variants = rec.get("variants") or {}
        self.swallowed = rec.get("trye_on_errors") or {}

    @property
    def ran(self):
        """True when the example actually executed its transform (not parse-only)."""
        return self.pprint_n_calls > 0

    @property
    def result(self):
        """The transform's own return value, without v1's (result, errors) wrapper.

        Provenance: this property first read ``isinstance(self.value, list)``, which is never
        true (``self.value`` is the whole ``(result, errors)`` tuple), so ``result`` silently
        returned the tuple and 9 parity tests failed. The check has to look at ``value[1]``
        (the error bucket). Kept as a comment because the failure looked like a v1/v2
        disagreement when it was purely a harness bug.
        """
        if self.value is None:
            return None
        if isinstance(self.value, tuple) and len(self.value) == 2 and isinstance(self.value[1], list):
            return self.value[0]
        return self.value

    @property
    def errors(self):
        """v1's per-row error bucket (only populated when TRYE_MODE is on)."""
        if isinstance(self.value, tuple) and len(self.value) == 2 and isinstance(self.value[1], list):
            return self.value[1]
        return []

    @property
    def silent_loss(self):
        """Rows v1 silently dropped: bucket holds rows while output has none of them."""
        if not self.errors:
            return []
        return self.errors


def golden(n):
    return Golden([c for c in data()["chunks"] if c["index"] == n][0])


def all_goldens():
    return [Golden(c) for c in data()["chunks"]]


def keys_of(obj):
    if isinstance(obj, dict):
        return sorted(obj.keys())


# --------------------------------------------------------------------------
# v2 side
# --------------------------------------------------------------------------
def v2_generate(schema):
    import dcg
    return dcg.generate(schema)


def v2_run(schema, payload):
    """Generate `schema`, exec it, and call `run(payload)`.  Returns (src, out)."""
    src = v2_generate(schema)
    ns = {}
    exec(compile(src, "<generated %s>" % schema.get("name", "?"), "exec"), ns)  # noqa: S102
    if "run" not in ns:
        raise NotReady("generated module has no `run`: %s" % keys_of(ns))
    return src, ns["run"](payload)
