# -*- coding: utf-8 -*-
"""README「§1.1 一个复杂例子」的真实来源：跑通、打印产物与结果。

跑：python -X utf8 examples/readme_complex.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dcg  # noqa: E402

SCHEMA = {
    "name": "flight_pack",
    "input": {"source": "$.index"},
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        # ── 每行算一遍的叶子 ─────────────────────────────────────────
        "flight_id": {"type": "int64", "value": "$.fid"},
        "route":     {"type": "str",   "expr": "`$.dep + '-' + $.arr`"},
        "hours":     {"type": "list",  "value": "$.hours"},
        "total":     {"type": "int64", "expr": "$sum(hours)"},
        "delayed":   {"type": "bool",  "expr": "total > 4"},

        # ── 具名对象：每行一份，不标 aggregate ──────────────────────
        "leg": {"type": "object", "fields": {
            "from": {"type": "str",   "value": "$.dep"},
            "to":   {"type": "str",   "value": "$.arr"},
            "span": {"type": "int64", "expr": "total"},
        }},

        # ── 列容器：逐层 aggregate，各子键各成一列 ──────────────────
        "stats": {"type": "object", "aggregate": True, "fields": {
            "count": {"type": "int64", "const": 1},
            "span":  {"type": "int64", "expr": "total"},
            "deep":  {"type": "object", "aggregate": True, "fields": {
                "ids":    {"type": "list", "expr": "`[$.fid, $.fid + 1]`"},
                "routes": {"type": "list", "expr": "`[$.dep, $.arr]`"},
            }},
        }},

        # ── 位置行：按声明序出行，undefined 落 None 保位 ─────────────
        "pos": {"type": "list", "fields": {
            "id":    {"type": "int64", "value": "$.fid"},
            "span":  {"type": "int64", "expr": "total"},
            "extra": {"type": "any", "value": "$.maybe", "optional": True},
        }},
    }},
}

ROWS = [
    {"fid": 101, "dep": "URC", "arr": "PEK", "hours": [1, 2, 3]},
    {"fid": 202, "dep": "KMG", "arr": "SHA", "hours": [3, 3]},
    {"fid": 303, "dep": "HGH", "arr": "CTU", "hours": [1], "maybe": "x"},
]


def main():
    dcg.validate(SCHEMA)
    src = dcg.generate(SCHEMA)
    print("---- src / %d chars ----" % len(src))
    print(src)
    ns = {}
    exec(compile(src, "<gen>", "exec"), ns)
    got = ns["run"]({"index": ROWS})
    print("---- run ----")
    print(json.dumps(got, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
