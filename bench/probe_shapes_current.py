"""当前 revision 的产物形态速查：shape=list/dict/index/single + 行容器 + 过滤。

只读 dcg/，输出可直接作为缺陷最小复现（verifier-dev，task-3）。
用法：python -X utf8 bench/probe_shapes_current.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import dcg  # noqa: E402
from dcg import runtime  # noqa: E402


def mk(name, shape="list", single=False, filt=True, fields=None):
    return {
        "name": name,
        "input": {"source": "rows"},
        "output": {"shape": shape, "single": single, "filter": filt},
        "out": {"type": "object", "fields": fields or {
            "a": {"type": "int64", "value": "$.1"},
            "b": {"type": "int64", "value": "$.2"},
            "s": {"type": "int64", "expr": "$.1 + $.2"},
        }},
    }


def runner(schema):
    src = dcg.generate(schema)
    g = dict(runtime.SOURCE)
    exec(compile(src, "<probe>", "exec"), g)
    return g["run"], src


ROWS = [(0, 1, 2), (0, 3, 4)]


def show(title, schema, rows):
    print("=" * 78)
    print(f"## {title}")
    try:
        run, src = runner(schema)
    except Exception as exc:  # noqa: BLE001
        print(f"  generate 失败: {type(exc).__name__}: {exc}")
        return
    print(f"  schema.output = {schema['output']}")
    for line in src.splitlines():
        print(f"  | {line}")
    try:
        out = run(rows)
    except Exception as exc:  # noqa: BLE001
        print(f"  run 失败: {type(exc).__name__}: {exc}")
        return
    print(f"  run({rows}) -> {out!r}")
    if isinstance(out, list) and out:
        print(f"  第 0 个元素: {out[0]!r} (type={type(out[0]).__name__})")
    elif isinstance(out, dict):
        print(f"  dict 键: {list(out)[:4]} 值: {[v for _, v in list(out.items())[:2]]!r}")


def main() -> int:
    print(f"dcg.__file__ = {dcg.__file__}")
    show("shape=list, single=False", mk("p_list"), ROWS)
    show("shape=list, single=True", mk("p_single", single=True), ROWS[:1])
    show("shape=dict", mk("p_dict", shape="dict"), ROWS)
    show("shape=index", mk("p_index", shape="index"), ROWS)
    show("shape=list, filter=True, 含 None 行", mk("p_filter"), [(0, 1, 2), None, (0, 3, 4)])

    print("=" * 78)
    print("## FLIGHT（与 tests/test_e2e_flight.py 同 schema）")
    flight = {
        "name": "flight",
        "input": {"source": "rows"},
        "output": {"shape": "list", "single": False, "filter": True},
        "out": {"type": "object", "fields": {
            "id": {"type": "int64", "value": "$.ID"},
            "sum": {"type": "int64", "expr": "$.a + $.b"},
            "grade": {"type": "str", "optional": True,
                      "expr": "$.score >= 90 ? 'A' : 'B'"},
            "tag": {"type": "str", "const": "flight"},
        }},
    }
    frows = [
        {"ID": 10, "a": 3, "b": 4, "score": 95},
        {"ID": 11, "a": 1, "b": 2, "score": 50},
        {"ID": 12, "a": 0, "b": 0, "score": 91},
    ]
    run, src = runner(flight)
    for line in src.splitlines():
        print(f"  | {line}")
    print(f"  run(3 行) -> {run(frows)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
