"""Lead 独立复核 CONTRACT.md §8 的 O-1 / O-2（临时探针，用完即删）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dcg

base = {
    "name": "o12", "input": {"source": "rows"},
    "output": {"shape": "list", "single": False, "filter": False},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.a"},
        "b": {"type": "int64", "expr": "$..a"},
    }},
}
print("=" * 20, "O-1  $..a")
try:
    dcg.validate(base)
    print("validate: OK (no error)")
except Exception as e:
    print(f"validate: {type(e).__name__}: {e}")
try:
    src = dcg.generate(base)
    print("generate: OK")
    print("product:")
    print(src)
    ns = {}
    exec(compile(src, "<p>", "exec"), ns)
    print("run([{'a': 7, 'b': 9}]) ->", ns["run"]([{"a": 7, "b": 9}]))
except Exception as e:
    print(f"generate/run: {type(e).__name__}: {e}")

print("=" * 20, "O-1b  $..a on the `value` side (not `expr`)")
vb = {
    "name": "o1b", "input": {"source": "rows"},
    "output": {"shape": "list", "single": False, "filter": False},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$..a"},
    }},
}
try:
    dcg.validate(vb)
    print("validate: OK (no error)")
    vsrc = dcg.generate(vb)
    print("generate: OK ->", vsrc.splitlines()[-2] if len(vsrc.splitlines()) > 1 else vsrc)
    ns = {}
    exec(compile(vsrc, "<p>", "exec"), ns)
    print("run([{'a': 7}]) ->", ns["run"]([{"a": 7}]))
except Exception as e:
    print(f"{type(e).__name__}: {e}")

for chain in ["$..a", "$.a..b", "$.*", "$.a.", "$...a"]:
    s = {
        "name": "c", "input": {"source": "rows"},
        "output": {"shape": "list", "single": False, "filter": False},
        "out": {"type": "object", "fields": {"x": {"type": "any", "value": chain}}},
    }
    try:
        src = dcg.generate(s)
        body = [l for l in src.splitlines() if "R[" in l or "_o.append" in l]
        print(f"value={chain!r}: ACCEPTED -> {body[-1].strip() if body else '?'}")
    except Exception as e:
        print(f"value={chain!r}: {type(e).__name__}: {str(e)[:90]}")

print("=" * 20, "O-2  post / trace")
def mk(extra):
    s = {
        "name": "o2", "input": {"source": "rows"},
        "output": {"shape": "list", "single": False, "filter": False},
        "out": {"type": "object", "fields": {"a": {"type": "int64", "value": "$.a"}}},
    }
    s["output"].update(extra)
    return s

try:
    none_src = dcg.generate(mk({}))
    post_src = dcg.generate(mk({"post": "os.path.basename"}))
    trace_src = dcg.generate(mk({"trace": True}))
    print("no-post  == post  product:", none_src == post_src)
    print("no-trace == trace product:", none_src == trace_src)
    print("chars:", len(none_src), len(post_src), len(trace_src))
except Exception as e:
    print(f"generate: {type(e).__name__}: {e}")

for bad in ["not a dotted name", "", 123]:
    try:
        dcg.generate(mk({"post": bad}))
        print(f"post={bad!r}: ACCEPTED (no error)")
    except Exception as e:
        print(f"post={bad!r}: {type(e).__name__}: {str(e)[:100]}")
