"""Capture v1 generated source for BOTH canonical workloads (CAP 4-field, M0 8-field)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _v1bridge import load_v1_engine  # noqa: E402

fmt = load_v1_engine()

CAP = {"datas": "$.index", "mode": "", "protocol": True, "single": False, "filter": "",
       "_field": {},
       "struct": {"doc": [
           {"name": "hourA", "alias": "", "type": "int", "value": "$.5.a.1"},
           {"name": "hourB", "alias": "", "type": "int", "value": "$.5.a.2"},
           {"name": "hourD", "alias": "", "type": "str", "value": "", "ext": "str($.5.a.1) + str(hourB)"},
           {"name": "hourE", "alias": "", "type": "int", "value": "", "ext": "hourA + hourB"}]}}

M0 = {"datas": "$.index", "mode": "", "protocol": True, "single": False, "filter": "",
      "_field": {},
      "struct": {"doc": [
          {"name": "hour",  "alias": "", "type": "list", "value": "$.5.a"},
          {"name": "hourA", "alias": "", "type": "int",  "value": "$.5.a.1"},
          {"name": "hourB", "alias": "", "type": "int",  "value": "$.5.a.2"},
          {"name": "hourC", "alias": "", "type": "int",  "value": "$.2"},
          {"name": "hourD", "alias": "", "type": "str",  "value": "", "ext": "str($.5.a.1) + str(hourB)"},
          {"name": "hourE", "alias": "", "type": "int",  "value": "", "ext": "hourA + hourB"},
          {"name": "hourF", "alias": "", "type": "int",  "value": "", "ext": "$.5.a.2 + $.5.a.1"},
          {"name": "hourG", "alias": "", "type": "int",  "value": "", "ext": "hour[2] + 123"}]}}


def capture(label, desc):
    fmt.Format.OPT_MODE = True
    fmt.Format.CYTHON_MODE = False
    grabbed = []
    orig = __builtins__.compile

    def spy(src, *a, **k):
        if isinstance(src, str) and len(src) > 60:
            grabbed.append(src)
        return orig(src, *a, **k)

    __builtins__.compile = spy
    try:
        fmt.Format.parse(label, desc)
    finally:
        __builtins__.compile = orig

    real = max(grabbed, key=len)
    print(f"=== {label}: {len(desc['struct']['doc'])} fields, "
          f"{len(grabbed)} compile() calls, real source {len(real)} chars "
          f"(stub sizes: {sorted(len(g) for g in grabbed)}) ===")
    print(real)
    print()
    return len(real), [len(g) for g in grabbed]


cap_chars, cap_all = capture("CAP", CAP)
m0_chars, m0_all = capture("M0", M0)
print(f"SUMMARY  CAP(4 fields)={cap_chars} chars  compiles={len(cap_all)}")
print(f"SUMMARY  M0 (8 fields)={m0_chars} chars  compiles={len(m0_all)}")
