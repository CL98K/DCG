"""README 附录 A（逐项样例）的可执行来源。

每个 `sample_*` 函数可以单独运行，打印这份样例的：schema 输入、`dcg.validate()`
规范化结果、`dcg.generate()` 真实产物、`run()` 真实返回值。README 附录 A 里的
所有产物片段都来自这里，改了引擎就跑一遍对账：

    python -X utf8 examples/readme_samples.py          # 跑全部，带断言
    python -X utf8 examples/readme_samples.py --code   # 只打印产物源码

为了让输出稳定，模块把 `rt.CLOCK` / `rt.RANDOM` 换成了固定值（§3.7 的做法）。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dcg
from dcg import runtime as rt

ROW2 = [{"sku": "A-1", "n": 2}, {"sku": "B-2", "n": 1}]
TWO_FIELDS = {
    "sku": {"type": "str", "value": "$.sku"},
    "n": {"type": "int64", "value": "$.n"},
}


def load(schema):
    """规范 → 生成 → 执行，返回 (规范化 schema, 产物源码, run)。"""
    norm = dcg.validate(schema)
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<readme-sample>", "exec"), ns)
    return norm, src, ns["run"]


def report(title, schema, data, *, show_src=True):
    norm, src, run = load(schema)
    out = run(data)
    print("=" * 70)
    print(title)
    print("-" * 70)
    print("schema :", json.dumps(schema, ensure_ascii=False))
    print("norm   :", json.dumps(norm, ensure_ascii=False))
    if show_src:
        print("src    :", len(src), "chars")
        print(src.rstrip())
    print("run    :", out)
    return out


# --- A.2 input.source 的 5 种写法 -------------------------------------------------
def sample_input():
    base = {"output": {"shape": "list"},
            "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}}}
    plain = dict(base, name="in0")
    report("input 省略 / \"$\"（整份输入就是行集合）", plain, [[1], [2]])
    report("input.source = \"$.index\"", dict(base, name="in1", input={"source": "$.index"}),
           {"index": [[1], [2]]})
    report("input.source = \"$.data.0.rows\"（数字段是整数下标）",
           dict(base, name="in2", input={"source": "$.data.0.rows"}),
           {"data": [{"rows": [[1], [2]]}]})
    report("input.source = \"$['x y'].k\"（方括号 + 引号段）",
           dict(base, name="in3", input={"source": "$['x y'].k"}),
           {"x y": {"k": [[1], [2]]}})


# --- A.3 output.shape 的 4 个取值 + 缺省 ------------------------------------------
def sample_shape():
    base = {"name": "shape", "input": {"source": "$"}, "out": {"type": "object", "fields": TWO_FIELDS}}
    for shape in (None, "list", "dict", "index", "rows"):
        output = {} if shape is None else {"shape": shape}
        report(f"output.shape = {shape!r}", dict(base, output=output), ROW2)
    print("=" * 70)
    print("零行输入")
    for shape in ("list", "dict", "index", "rows"):
        _, _, run = load(dict(base, output={"shape": shape}))
        print(f"  {shape:6s} ->", run([]))


# --- A.4 output.before 的 4 种形态 -----------------------------------------------
def sample_before():
    one = {"type": "int64"}
    fields_n = {"n": dict(one, value="$.n")}
    fields_v = {"v": dict(one, value="$")}
    base = {"name": "b", "input": {"source": "$"}, "out": {"type": "object", "fields": fields_n}}

    report("before ① JSONata 路径", dict(base, output={"shape": "list", "before": "= $.rows"}),
           {"rows": [{"n": 1}, {"n": 2}]})

    report("before ② JSONata 高阶函数（比较器 true = 交换，`>` 即升序）",
           dict(base, output={"shape": "list", "before": "= $sort($, function($a,$b){ $a > $b })"},
                out={"type": "object", "fields": fields_v}),
           [3, 1, 2])

    report("before ③ 反引号段（整份列表推导）",
           dict(base, output={"shape": "list", "before": "= `[r for r in $ if r['n'] > 1]`"}),
           [{"n": 1}, {"n": 2}, {"n": 3}])

    report("before ④ py{} 多语句块，块内用 $.rows",
           dict(base, output={"shape": "list",
                              "before": "= py{ let rows = [r for r in $.rows if r[0] > 1]; return rows }"},
                out={"type": "object", "fields": {"v": dict(one, value="$.0")}}),
           {"rows": [[1], [2], [3]]})

    report("before ⑤ py{} 里写裸 $（= 全量数据本身）",
           dict(base, output={"shape": "list",
                              "before": "= py{ let rows = [r for r in $ if r[0] > 1]; return rows }"},
                out={"type": "object", "fields": {"v": dict(one, value="$.0")}}),
           [[1], [2], [3]])


# --- A.5 output.after 的 4 种形态 -------------------------------------------------
def sample_after():
    base = {"name": "a", "input": {"source": "$.rows"},
            "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
    data = {"rows": [{"n": 1}, {"n": 2}]}

    report("after ① JSONata 对象：包一层 dict", dict(base, output={"shape": "list",
           "after": "= {'n': $count($), 'rows': $}"}), data)

    report("after ② 反引号段：整份结果交给标准库",
           dict(base, output={"shape": "list", "after": "= `json.dumps($)`"}), data)

    report("after ③ py{}：逐行加工结果",
           dict(base, output={"shape": "list",
                              "after": "= py{ let col = [r[0] * 100 for r in $]; return col }"}), data)

    report("after ④ dict 形状下数行数要写 $count($.v)",
           {"name": "a4", "input": {"source": "$.batch.rows"}, "output": {
               "shape": "dict", "after": "= {'rows': $count($.v), 'col': $}"},
            "out": {"type": "object", "fields": {
                "v": {"type": "int64", "value": "$.0"},
                "w": {"type": "int64", "value": "$.1"}}}},
           {"batch": {"rows": [[3, 30], [1, 10], [2, 20]]}})


# --- A.6 before + after 共存 ------------------------------------------------------
def sample_both():
    report("before 先换数据，再行循环，最后 after 换结果",
           {"name": "ba", "input": {"source": "$"},
            "output": {"shape": "list",
                       "before": "= `[r for r in $ if r[0] > 1]`",
                       "after": "= {'n': $count($)}"},
            "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}},
           [[1], [2], [3]])


# --- A.7 output.trace 的 2 个取值 -------------------------------------------------
def sample_trace():
    schema = {"name": "t", "input": {"source": "$"}, "output": {"shape": "list", "trace": True},
              "out": {"type": "object", "fields": {"v": {"type": "int64", "const": 1}}}}
    off = dcg.generate(dict(schema, output={"shape": "list"}))
    on = dcg.generate(schema)
    print("=" * 70)
    print("trace=false / true 的 generate() 产物逐字节相同:", off == on, f"({len(off)} chars)")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_trace_build")
    m = dcg.build(schema, out_dir, overwrite=True)
    print("build() manifest:", json.dumps(m, ensure_ascii=False))
    print("trace 产物:")
    print(_read(os.path.join(out_dir, "gen", "t.trace.py")).rstrip())


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --- A.8 out.fields 的七个键 ------------------------------------------------------
TYPES_ROW = {"v": 7, "small": 3, "f": 1.5, "s": "ab", "b": True,
             "xs": [1, 2], "meta": {"k": 1}, "x": 3}
TYPE_FIELDS = {
    "int64": {"type": "int64", "value": "$.v"},
    "int32": {"type": "int32", "value": "$.v"},
    "float64": {"type": "float64", "value": "$.f"},
    "str": {"type": "str", "value": "$.s"},
    "bool": {"type": "bool", "value": "$.b"},
    "list": {"type": "list", "value": "$.xs"},
    "object": {"type": "object", "fields": {"k": {"type": "int64", "value": "$.meta.k"}}},
    "any": {"type": "any", "value": "$.x"},
}


def sample_fields():
    for tname, f in TYPE_FIELDS.items():
        report(f"type = {tname!r}",
               {"name": "t_" + tname, "input": {"source": "$"}, "output": {"shape": "rows"},
                "out": {"type": "object", "fields": {"v": f}}},
               [TYPES_ROW])

    base = {"name": "opt", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {"a": {"type": "int64", "optional": True, "value": "$.a"}}}}
    report("optional: true（缺值 -> None）", base, [{"a": 1}, {}])
    report("optional: true + default: 0（缺值 -> 0，不再生成 None 分支）",
           {"name": "opt2", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "a": {"type": "int64", "optional": True, "default": 0, "value": "$.a"}}}},
           [{"a": 1}, {}])
    report("default: 7（不带 optional，缺值 -> 7）",
           {"name": "def7", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "v": {"type": "int64", "default": 7, "value": "$.zzz"}}}},
           [{}])
    report("const：编译期常量，零 runtime 依赖",
           {"name": "cst", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {"v": {"type": "str", "const": "ok"}}}},
           [{}])
    report("fields：object 嵌套 = 具名 dict",
           {"name": "nobj", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {"d": {"type": "object", "fields": {
                "x": {"type": "int64", "value": "$.a"},
                "y": {"type": "int64", "const": 1}}}}}},
           [{"a": 5}])
    report("fields：list 嵌套 = 位置行（undefined 落 None 保位）",
           {"name": "nlist", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {"pair": {"type": "list", "fields": {
                "s": {"type": "str", "value": "$.0"},
                "a": {"type": "int64", "optional": True, "value": "$.1"}}}}}},
           [["x", 1], ["y"]])
    report("expr：值被复用 + 多种内联形态",
           {"name": "expr_mix", "input": {"source": "$"}, "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "qty": {"type": "int64", "value": "$.qty"},
                "amount": {"type": "float64", "expr": "qty * $.price"},
                "big": {"type": "bool", "expr": "qty > 1"},
                "label": {"type": "str", "expr": "$string(qty) & '-item'"}}}},
           [{"qty": 2, "price": 3.5}])

    agg_base = {"input": {"source": "$"}, "output": {"shape": "dict"},
                "out": {"type": "object", "fields": {
                    "sku": {"type": "str", "value": "$.sku"},
                    "item": {"type": "object", "fields": {
                        "s": {"type": "str", "value": "$.sku"},
                        "n": {"type": "int64", "value": "$.n"}}}}}}
    report("aggregate 缺省：object 字段是一列具名行",
           dict(agg_base, name="agg_off"), [{"sku": "A", "n": 1}, {"sku": "B", "n": 2}])

    agg_on = {"name": "agg_on", "input": {"source": "$"}, "output": {"shape": "dict"},
              "out": {"type": "object", "fields": {
                  "sku": {"type": "str", "value": "$.sku"},
                  "stat": {"type": "object", "aggregate": True, "fields": {
                      "n": {"type": "int64", "value": "$.n"}}}}}}
    report("aggregate: true：逐子键拆成列", agg_on, [{"sku": "A", "n": 1}, {"sku": "B", "n": 2}])


# --- A.9 generate() 的两个关键字参数 ----------------------------------------------
def sample_generate_kwargs():
    schema = {"name": "g", "input": {"source": "$"}, "output": {"shape": "list"},
              "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
    print("=" * 70)
    print("cython=True")
    print(dcg.generate(schema, cython=True).rstrip())
    plain = dcg.generate(schema)
    cy = dcg.generate(schema, cython=True)
    stripped = "\n".join(ln for ln in cy.splitlines() if not ln.strip().startswith("cdef "))
    print("剥掉 cdef 行后与 cython=False 相同:", stripped == plain.rstrip())

    opt = {"name": "io", "input": {"source": "$"}, "output": {"shape": "list"},
           "out": {"type": "object", "fields": {"a": {"type": "int64", "optional": True, "value": "$.a"}}}}
    print("=" * 70)
    print("inline_optional=False")
    print(dcg.generate(opt, inline_optional=False).rstrip())


# --- A.10 校验期拒绝 --------------------------------------------------------------
REJECTS = [
    {"output": {"shape": "table"}},
    {"output": {"single": True}},
    {"output": {"filter": True}},
    {"output": {"post": None}},
    {"output": {"before": True}},
    {"output": {"before": "@f"}},
    {"output": {"after": "= "}},
    {"output": {"foo": 1}},
    {"input": {"datas": "$.index"}},
    {"input": {"source": "index"}},
]


def sample_rejects():
    base = {"name": "r", "output": {}, "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.n"}}}}
    print("=" * 70)
    print("校验期拒绝")
    for patch in REJECTS:
        schema = dict(base, **patch)
        if "output" in patch:
            schema["output"] = patch["output"]
        try:
            dcg.validate(schema)
        except dcg.SchemaError as exc:
            print(f"  {json.dumps(patch, ensure_ascii=False):42s} -> SchemaError: {exc}")
    cases = [
        ("字段名保留字", {"name": "x", "output": {}, "out": {"type": "object", "fields": {"rows": {"type": "int64", "value": "$.n"}}}}),
        ("字段名 items", {"name": "x", "output": {}, "out": {"type": "object", "fields": {"t": {"type": "list", "items": "x"}}}}),
        ("字段名 row", {"name": "x", "output": {}, "out": {"type": "object", "fields": {"d": {"type": "object", "row": 1}}}}),
        ("两个取值键", {"name": "x", "output": {}, "out": {"type": "object", "fields": {"a": {"type": "int64", "value": "$.n", "const": 1}}}}),
        ("一个都不给", {"name": "x", "output": {}, "out": {"type": "object", "fields": {"a": {"type": "int64"}}}}),
        ("default + fields", {"name": "x", "output": {}, "out": {"type": "object", "fields": {
            "o": {"type": "object", "default": {}, "fields": {"k": {"type": "int64", "const": 1}}}}}}),
        ("aggregate 用在 list", {"name": "x", "output": {}, "out": {"type": "object", "fields": {
            "o": {"type": "list", "aggregate": True, "fields": {"k": {"type": "int64", "const": 1}}}}}}),
        ("before 写裸名", {"name": "x", "output": {"shape": "list", "before": "= `sku`"},
                           "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.n"}}}}),
        ("名字非法", {"name": "bad name", "output": {}, "out": {"type": "object", "fields": {"v": {"type": "int64", "const": 1}}}}),
        ("缺 out", {"name": "x", "output": {}}),
    ]
    for title, schema in cases:
        try:
            if "before" in schema.get("output", {}):
                dcg.generate(schema)
            else:
                dcg.validate(schema)
        except (dcg.SchemaError, dcg.Unsupported) as exc:
            print(f"  {title:14s} -> {type(exc).__name__}: {exc}")


# --- A.11 组合样例 ----------------------------------------------------------------
def sample_combined():
    report("五个开关全开：input.source + shape + before + after + trace",
           {"name": "all5", "input": {"source": "$.batch.rows"},
            "output": {"shape": "dict",
                       "before": "= $sort($, function($a,$b){ $a[0] > $b[0] })",
                       "after": "= {'rows': $count($.v), 'col': $}",
                       "trace": True},
            "out": {"type": "object", "fields": {
                "v": {"type": "int64", "value": "$.0"},
                "w": {"type": "int64", "value": "$.1"}}}},
           {"batch": {"rows": [[3, 30], [1, 10], [2, 20]]}})

    report("rows 形状 + before 筛选 + after 加工",
           {"name": "combo2", "input": {"source": "$"},
            "output": {"shape": "rows",
                       "before": "= `[r for r in $ if r['qty'] > 0]`",
                       "after": "= {'count': $count($), 'items': $}"},
            "out": {"type": "object", "fields": {
                "sku": {"type": "str", "value": "$.sku"},
                "qty": {"type": "int64", "value": "$.qty"}}}},
           [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 0}])


SAMPLES = [
    sample_input, sample_shape, sample_before, sample_after, sample_both,
    sample_trace, sample_fields, sample_generate_kwargs, sample_rejects, sample_combined,
]


def main():
    rt.CLOCK.now_ms = lambda: 1700000000000
    rt.RANDOM.random = lambda: 0.5
    for fn in SAMPLES:
        fn()
    print("=" * 70)
    print("全部样例执行完成")


if __name__ == "__main__":
    main()
