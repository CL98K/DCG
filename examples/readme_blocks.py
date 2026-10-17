# -*- coding: utf-8 -*-
"""README.md 里每个 Python 代码块的实跑副本：按 README 的节顺序执行并打印真实输出。

用途：证明 README 的代码块不是手写想象，而是逐块跑过的。运行方式：
    cd <repo>; python -X utf8 examples/readme_blocks.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(tempfile.mkdtemp(prefix="dcg_readme_cwd_"))  # 让 build/ 产物落在临时目录里
import dcg  # noqa: E402

BT = chr(96)  # backtick，README 里写作 `...`


def hr(title):
    print("\n" + "=" * 72)
    print("## " + title)
    print("=" * 72)


def show(label, value):
    print("%-22s %r" % (label + ":", value))


def gen_run(schema, rows):
    code = dcg.generate(schema)
    ns = {}
    exec(compile(code, "<gen>", "exec"), ns)
    return code, ns["run"](rows)


# ---------------------------------------------------------------- 1 快速开始
hr("1 快速开始")
schema = {
    "name": "order",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "sku": {"type": "str", "value": "$.0"},
        "qty": {"type": "int64", "value": "$.1"},
        "price": {"type": "float64", "value": "$.2"},
        "amount": {"type": "float64", "expr": "qty * price"},
    }},
}
build_dir = "build"
manifest = dcg.build(schema, build_dir, overwrite=True)
code, out = gen_run(schema, [["A-1", 2, 3.5], ["B-2", 1, 10.0]])
print(code)
show("run(rows)", out)
show("manifest", manifest)

# ------------------------------------------------------------- 2 schema 规格
hr("2 schema 规格：取值三键 value/const/expr")
three_keys = {
    "name": "three_keys",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "const": 42},
        "c": {"type": "int64", "expr": "a + b"},
    }},
}
_, out = gen_run(three_keys, [[1], [2]])
show("run([[1],[2]])", out)

print("\n-- 三键互斥的错误 --")
try:
    dcg.validate({"name": "x", "output": {}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0", "const": 1}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)
try:
    dcg.validate({"name": "x", "output": {}, "out": {"type": "object", "fields": {
        "a": {"type": "int64"}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)

print("\n-- type 是声明不是转换 --")
no_coerce = {
    "name": "no_coerce",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "s": {"type": "str", "value": "$.0"},
        "n": {"type": "int64", "value": "$.1"},
    }},
}
code, out = gen_run(no_coerce, [[123, "abc"]])
print(code)
show("run([[123,'abc']])", out)

print("\n-- 嵌套 fields / items --")
nested = {
    "name": "nested",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "who": {"type": "object", "fields": {
            "name": {"type": "str", "value": "$.0.name"},
            "age": {"type": "int64", "value": "$.0.age"},
        }},
        "tags": {"type": "list", "value": "$.1"},
    }},
}
code, out = gen_run(nested, [[{"name": "n1", "age": 7}, ["x", "y"]]])
print(code)
show("run(...)", out)

print("\n-- type=list 的两种写法（items 实测语义）--")
items_only = {
    "name": "items_demo",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "f": {"type": "list", "items": {"type": "int64", "value": "$.0"}},
    }},
}
code, out = gen_run(items_only, [[7, 8]])
print(code)
show("run([[7,8]])", out)

print("\n-- output 是必填键；output:{} 会被规范化 --")
try:
    dcg.validate({"name": "x", "out": {"type": "object", "fields": {
        "a": {"type": "int64", "const": 1}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)
show("output:{} 规范化后", dcg.validate({"name": "x", "output": {}, "out": {
    "type": "object", "fields": {"a": {"type": "int64", "const": 1}}}})["output"])

print("\n-- optional=true 是唯一会生成 undefined 守卫的开关 --")
opt = {
    "name": "opt",
    "output": {"shape": "list", "filter": False},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0", "optional": True},
        "b": {"type": "int64", "value": "$.0"},
    }},
}
code, out = gen_run(opt, [[7]])
print(code)
show("run([[7]])", out)
try:
    ns = {}
    exec(compile(code, "<gen>", "exec"), ns)
    ns["run"]([[]])
except Exception as e:
    show("run([[]])", "%s: %s" % (type(e).__name__, e))

print("\n-- 旧版字段/describe 形状的报错会提示迁移工具 --")
try:
    dcg.validate({"name": "x", "output": {}, "out": {"type": "object", "fields": {
        "a": {"name": "a", "ext": "1"}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)

# --------------------------------------------------------- 3 表达式语法
hr("3 表达式语法")
exprs = {
    "name": "exprs",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "up": {"type": "str", "expr": "$uppercase($.0)"},
        "tag": {"type": "str", "expr": "$.1 & '-' & $string($.2)"},
        "twice": {"type": "int64", "expr": BT + "$.2 * 2 + len($.1)" + BT},
    }},
}
code, out = gen_run(exprs, [["abc", "xy", 21]])
print(code)
show("run([['abc','xy',21]])", out)

print("\n-- compile_expr（编译期 IR）--")
for src in ["$.hourA + hourB", "$uppercase($.sku)", BT + "str($.5.a.1) + str(hourB)" + BT]:
    try:
        show(repr(src), dcg.compile_expr(src))
    except dcg.Unsupported as e:
        show(repr(src), "Unsupported: %s" % e)

# --------------------------------------------------------- 4 输出形态
hr("4 输出形态")
base_fields = {"sku": {"type": "str", "value": "$.0"}, "qty": {"type": "int64", "value": "$.1"}}
rows2 = [["A-1", 2], ["B-2", 1]]
for shape in ("list", "dict", "index"):
    sch = {"name": "shapes", "output": {"shape": shape}, "out": {"type": "object", "fields": base_fields}}
    _, out = gen_run(sch, rows2)
    show("shape=%s" % shape, out)
sch = {"name": "shapes", "output": {"shape": "list", "single": True},
       "out": {"type": "object", "fields": base_fields}}
_, out = gen_run(sch, rows2)
show("shape=list single=true", out)
try:
    dcg.validate({"name": "s", "output": {"shape": "dict", "single": True},
                  "out": {"type": "object", "fields": base_fields}})
except dcg.SchemaError as e:
    print("SchemaError:", e)

print("\n-- filter --")
fsch = {"name": "f", "output": {"shape": "list", "filter": True},
        "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
fsch_off = {"name": "f", "output": {"shape": "list", "filter": False},
            "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
frows = [[], [1, 2], [], [3]]
_, out = gen_run(fsch, frows)
show("filter=true", out)
_, out = gen_run(fsch_off, frows)
show("filter=false", out)
print(dcg.generate(fsch_off))

print("\n-- post / trace --")
psch = {"name": "p", "output": {"shape": "list", "post": "pkg.mod.fn"},
        "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
print("post='pkg.mod.fn' 产物 == 无 post 产物:",
      dcg.generate(psch) == dcg.generate(fsch))
try:
    dcg.validate({"name": "p", "output": {"post": "f"},
                  "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)
tsch = {"name": "t", "output": {"shape": "list", "trace": True},
        "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
print("trace=true 产物 == 无 trace 产物:", dcg.generate(tsch) == dcg.generate(fsch))

print("\n-- input.source=index --")
isch = {"name": "idx", "input": {"source": "index"}, "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"k": {"type": "str", "value": "$.0"}}}}
code, out = gen_run(isch, {"index": [["a"], ["b"]]})
print(code)
show("run({'index':...})", out)

# --------------------------------------------------------- 5 能力边界
hr("5 能力边界")
refusals = [
    "$.a.b.c",
    "$sum(hour)",
    "$now()",
    "$map(hour, function($v) { $v })",
    "#.hour.3.2",
    "@$x",
    "1 ~> $string",
    BT + "1 +" + BT,
]
for src in refusals:
    try:
        show(repr(src), "accepted: %r" % (dcg.compile_expr(src),))
    except dcg.Unsupported as e:
        show(repr(src), "Unsupported: %s" % e)
print("backtick 里的任意 Python（build 期只 ast.parse，不执行）:")
show("compile_expr(" + repr(BT + '__import__("os").getcwd()' + BT) + ")",
     dcg.compile_expr(BT + '__import__("os").getcwd()' + BT))

print("\n-- value 侧：深路径被接受（与 expr 侧不同）--")
deep = {"name": "deep", "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"x": {"type": "int64", "value": "$.a.b.c"}}}}
code, out = gen_run(deep, [{"a": {"b": {"c": 42}}}])
print(code)
show("run", out)
