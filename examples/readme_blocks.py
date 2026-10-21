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

print("\n-- type:'list' + fields 把对象输出成位置行 --")
row_demo = {
    "name": "rowdemo",
    "input": {"source": "$.index"},
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        "doc": {"type": "list", "fields": {
            "hour": {"type": "list", "value": "$.5.a"},
            "hourA": {"type": "int64", "value": "$.5.a.1"},
        }},
    }},
}
code, out = gen_run(row_demo, {"index": [[0, 0, 0, 0, 0, {"a": [0, 1, 2, [1, 2, 3]]}]]})
print(code)
show("run(...)['doc'][0]", out["doc"][0])

print("\n-- type=list 的两种写法（标量取值 vs 数组字面量包一层）--")
items_only = {
    "name": "items_demo",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "f": {"type": "list", "expr": "[$.0]"},
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
print("\n-- 字段级 default：undefined 换成默认值（README §2.3）--")
dft = {"name": "dft", "output": {"shape": "list", "filter": False},
       "out": {"type": "object", "fields": {
           "n": {"type": "int64", "value": "$.n", "default": 0},
           "x": {"type": "int64", "value": "$.x"}}}}
code, out = gen_run(dft, [{"x": 8}])
print(code)
show("run([{'x':8}])", out)
_ns = {}
exec(compile(code, "<gen>", "exec"), _ns)
show("run([{'n':5,'x':9}])", _ns["run"]([{"n": 5, "x": 9}]))

print("\n-- 前向引用：计算按依赖序，输出按声明序（README §2.7）--")
fwd = {"name": "fwd", "output": {"shape": "list"},
       "out": {"type": "object", "fields": {
           "b": {"type": "int64", "expr": "a * 2"},
           "a": {"type": "int64", "value": "$.x"}}}}
code, out = gen_run(fwd, [{"x": 3}])
print(code)
show("run([{'x':3}])", out)
try:
    dcg.generate({"name": "cyc", "output": {"shape": "list"},
                  "out": {"type": "object", "fields": {
                      "a": {"type": "int64", "expr": "b + 1"},
                      "b": {"type": "int64", "expr": "a + 1"}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)


print("\n-- 八种 type 同一份 schema（README §2.8）--")
types8 = {"name": "types8", "output": {"shape": "rows"}, "out": {"type": "object", "fields": {
    "i64":  {"type": "int64",  "value": "$.n"},
    "i32":  {"type": "int32",  "value": "$.small"},
    "f64":  {"type": "float64", "expr": "$.f * 2"},
    "code": {"type": "str",    "value": "$.s"},
    "txt":  {"type": "str",    "expr": "$uppercase(code)"},
    "flag": {"type": "bool",   "expr": "$.n > 3"},
    "xs":   {"type": "list",   "value": "$.xs"},
    "meta": {"type": "object", "fields": {
        "city": {"type": "str", "value": "$.meta.city"},
        "tag":  {"type": "str", "const": "ok"}}},
    "miss": {"type": "any",    "value": "$.zz"},
}}}
code, out = gen_run(types8, [{"n": 7, "small": 3, "f": 1.5, "s": "ab",
                              "xs": [1, 2], "meta": {"city": "URC"}}])
print(code)
show("run(...)", out)

print("\n-- type 可证 vs 不可证：同一个表达式两种产物（README §2.8）--")
diff_fields = {
    "i64":  {"type": "int64", "value": "$.n"},
    "miss": {"type": "any",   "value": "$.zz"},
    "add_bare":   {"type": "int64", "expr": "i64 + 1"},
    "add_helper": {"type": "int64", "expr": "miss + 1"},
    "str_bare":   {"type": "str",   "expr": "$string(i64)"},
    "str_helper": {"type": "str",   "expr": "$string(miss)"},
}
code, out = gen_run({"name": "diff", "output": {"shape": "list"},
                     "out": {"type": "object", "fields": diff_fields}}, [{"n": 7}])
print(code)
show("run([{'n':7}])", out)

print("\n-- value 缺数据：声明的具体类型抛错，any 得 undefined（README §2.8）--")
miss_fields = {
    "typed":  {"type": "int64", "value": "$.n"},
    "anyone": {"type": "any",   "value": "$.n"},
}
miss_code = dcg.generate({"name": "miss", "output": {"shape": "list"},
                          "out": {"type": "object", "fields": miss_fields}})
print(miss_code)
try:
    ns = {}
    exec(compile(miss_code, "<gen>", "exec"), ns)
    ns["run"]([{"q": 1}])
except Exception as e:
    show("run([{'q':1}])", "%s: %s" % (type(e).__name__, e))
ns = {}
exec(compile(miss_code, "<gen>", "exec"), ns)
show("run([{'n':3}])", ns["run"]([{"n": 3}]))


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
print("\n-- Registry 自定义函数（README §3.2）--")
reg = dcg.Registry()
reg.fn("$double", abs)
show("compile_expr('$double($.0)', registry=reg)",
     dcg.compile_expr("$double($.0)", registry=reg))

print("\n-- py{} 块：函数体是 Python 语句（README §3.3）--")
pyb = {"name": "pyb", "output": {"shape": "list"},
       "out": {"type": "object", "fields": {
           "n": {"type": "int64", "expr": "py{ let t = 2; return t * 3 }"}}}}
code, out = gen_run(pyb, [{"x": 1}])
print(code)
show("run([{'x':1}])", out)


print("\n-- 批量 / 高阶函数（A/B 类）：编译期编成 lambda，运行期无解释器 --")
agg = {
    "name": "agg",
    "output": {"shape": "rows"},
    "out": {"type": "object", "fields": {
        "total": {"type": "float64", "expr": "$sum($.xs)"},
        "doubled": {"type": "any", "expr": "$map($.xs, function($x){ $x * 2 })"},
        "big": {"type": "any", "expr": "$filter($.xs, function($x){ $x > 2 })"},
        "top": {"type": "int64", "expr": "$max($.xs)"},
        "label": {"type": "str", "expr": "$join($map($.xs, function($x){ $string($x) }), '+')"},
    }},
}
code, out = gen_run(agg, [{"xs": [1, 2, 3]}])
print(code)
show("run", out)

print("\n-- 带比较器的 $sort / $position() --")
sortc = {
    "name": "sortc",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "desc": {"type": "any", "expr": "$sort([3, 1, 2], function($a, $b){ $a > $b })"},
        "pos": {"type": "any", "expr": "$map([10, 20], function($x){ $position() })"},
    }},
}
_, out = gen_run(sortc, [[0]])
show("run", out)
print("\n-- 动态下标：required 裸下标 / optional 走 _ix_get（README §3.8）--")
dyn = {"name": "dyn", "output": {"shape": "list"},
       "out": {"type": "object", "fields": {
           "v": {"type": "int64", "expr": "$.xs[i]"},
           "i": {"type": "int64", "value": "$.i"},
           "o": {"type": "int64", "expr": "$.xs[i]", "optional": True}}}}
code, out = gen_run(dyn, [{"xs": [10, 20, 30], "i": 1}])
print(code)
show("run([{'xs':[10,20,30],'i':1}])", out)

print("\n-- 行对象 $ / $exists / undefined（README §3.8）--")
rowd = {"name": "rowd", "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            "raw": {"type": "any", "value": "$"},
            "has": {"type": "bool", "expr": "$exists($.zz)"},
            "miss": {"type": "any", "value": "$.zz"}}}}
code, out = gen_run(rowd, [{"a": 1}])
print(code)
show("run([{'a':1}])", out)


print("\n-- 非确定函数：注入固定时钟 / 随机源后结果可复跑 --")
import dcg.runtime as _rt


class _FixedClock:
    now_ms = staticmethod(lambda: 1700000000000)


class _FixedRandom:
    random = staticmethod(lambda: 0.5)
    shuffle = staticmethod(lambda xs: list(reversed(xs)))


_rt.CLOCK = _FixedClock()
_rt.RANDOM = _FixedRandom()
nondet = {
    "name": "nondet",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "now": {"type": "int64", "expr": "$millis()"},
        "coin": {"type": "float64", "expr": "$random()"},
        "rev": {"type": "any", "expr": "$shuffle([1, 2, 3])"},
    }},
}
_, out = gen_run(nondet, [[0]])
show("run", out)
show("产物字节可复现", dcg.generate(nondet) == dcg.generate(nondet))

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

print("\n-- post：组装完成后恰好调用一次（按需 import，不进逐行循环）--")
psch = {"name": "p", "output": {"shape": "list", "post": "json.dumps"},
        "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
pcode, out = gen_run(psch, [[0]])
print(pcode)
show("run", out)
show("type", type(out).__name__)
try:
    dcg.validate({"name": "p", "output": {"post": "f"},
                  "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}})
except dcg.SchemaError as e:
    print("SchemaError:", e)

print("\n-- trace：额外生成独立调试产物，正式产物字节不变 --")
tsch = {"name": "t", "output": {"shape": "list", "trace": True},
        "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
mani = dcg.build(tsch, "out", overwrite=True)
show("manifest", mani)
trace_src = open(mani["trace_artifact"], encoding="utf-8").read()
print(trace_src)
_tns = {}
exec(compile(trace_src, "<trace>", "exec"), _tns)
show("run", _tns["run"]([["A-1"], ["B-2"]]))
show("TRACE", _tns["TRACE"])

print("\n-- input.source=index --")
isch = {"name": "idx", "input": {"source": "$.index"}, "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"k": {"type": "str", "value": "$.0"}}}}
code, out = gen_run(isch, {"index": [["a"], ["b"]]})
print(code)
show("run({'index':...})", out)

print("\n-- output 组合矩阵：同一份订单 schema（README §4.5）--")
order_fields = {
    "sku":    {"type": "str",     "value": "$.sku"},
    "qty":    {"type": "int64",   "value": "$.qty"},
    "amount": {"type": "float64", "expr": "$.qty * $.price"},
    "tags":   {"type": "list",    "value": "$.tags"},
    "item":   {"type": "object",  "fields": {
        "sku": {"type": "str", "value": "$.sku"},
        "qty": {"type": "int64", "value": "$.qty"}}},
}
order_rows = [
    {"sku": "A-1", "qty": 2, "price": 3.5, "tags": ["new"]},
    {"sku": "B-2", "qty": 0, "price": 1.0, "tags": []},
]
order_base = {"name": "orders", "out": {"type": "object", "fields": order_fields}}
for cfg in ({"shape": "list"}, {"shape": "list", "single": True},
            {"shape": "rows"}, {"shape": "rows", "single": True},
            {"shape": "dict"}, {"shape": "index"}):
    sch = dict(order_base)
    sch["output"] = dict(cfg)
    _, out = gen_run(sch, order_rows)
    show("output=%s" % cfg, out)

print("\n-- 零行输入 --")
for shape in ("list", "rows", "dict", "index"):
    sch = dict(order_base)
    sch["output"] = {"shape": shape}
    _, out = gen_run(sch, [])
    show("shape=%s run([])" % shape, out)

print("\n-- filter x single --")
fsch = {"name": "f", "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}}
for cfg, rows in (({ "shape": "list", "filter": True}, [[], [1, 2], [], [3]]),
                  ({ "shape": "list", "filter": False}, [[], [1, 2], [], [3]]),
                  ({ "shape": "list", "filter": False, "single": True}, [[], [1, 2]]),
                  ({ "shape": "list", "filter": True, "single": True}, [[], [1, 2]]),
                  ({ "shape": "list", "filter": True, "single": True}, [[], []])):
    sch = {"name": "f", "output": dict(cfg), "out": fsch["out"]}
    _, out = gen_run(sch, rows)
    show("%s rows=%s" % (cfg, rows), out)

print("\n-- post 与 index 输入、trace 叠加 --")
post_sch = dict(order_base)
post_sch["output"] = {"shape": "rows", "post": "json.dumps"}
pcode, out = gen_run(post_sch, order_rows)
show("post run", out)
show("post type", type(out).__name__)
idx_sch = dict(order_base)
idx_sch["input"] = {"source": "$.index"}
idx_sch["output"] = {"shape": "rows"}
icode, iout = gen_run(idx_sch, {"index": order_rows})
show("input.source=index run", iout)
tr_sch = dict(order_base)
tr_sch["output"] = {"shape": "rows", "trace": True}
mani = dcg.build(tr_sch, "out", overwrite=True)
show("manifest", mani)
tsrc = open(mani["trace_artifact"], encoding="utf-8").read()
show("trace 比正式产物多的行", [l for l in tsrc.splitlines() if "TRACE" in l])
_tns = {}
exec(compile(tsrc, "<trace>", "exec"), _tns)
show("trace run", _tns["run"](order_rows))
show("TRACE", _tns["TRACE"])

print("\n-- 校验期拒绝的组合（README §4.5）--")
for bad in ({"shape": "dict", "single": True}, {"shape": "index", "single": True},
            {"shape": "table"}, {"shape": "list", "filter": "sku"},
            {"shape": "rows", "single": "yes"}, {"shape": "list", "post": "f"}):
    try:
        dcg.validate({"name": "x", "output": dict(bad),
                      "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}}})
        show(str(bad), "no error")
    except dcg.SchemaError as e:
        show(str(bad), "SchemaError: %s" % e)


# --------------------------------------------------------- 5 能力边界
hr("5 支持度与拒绝清单")
from dcg.jx import policy as _policy  # noqa: E402

show("A 类行内函数", len(_policy.ROW_FUNCS))
show("B 类高阶函数", sorted(_policy.HIGHER_ORDER))
show("C 类非确定函数", sorted(_policy.NONDETERMINISTIC))
show("合计（$sort 同属 A/B，去重）", len(_policy.ROW_FUNCS | _policy.HIGHER_ORDER
                                          | _policy.NONDETERMINISTIC))
show("永久拒绝构造", sorted(_policy.STRUCTURAL))

print("\n-- 永久拒绝（原样消息，带列号）--")
for src in ["$eval($.a)", "$map($.a, $string)", "1..5", "/ab/", "$position()",
            "#.hour.3.2", "@$x", "1 ~> $string", BT + "1 +" + BT]:
    try:
        show(repr(src), "accepted: %r" % (dcg.compile_expr(src),))
    except dcg.Unsupported as e:
        show(repr(src), "Unsupported: %s" % e)

print("\n-- 反引号里的任意 Python（build 期只 ast.parse，不执行）--")
show("compile_expr(" + repr(BT + '__import__("os").getcwd()' + BT) + ")",
     dcg.compile_expr(BT + '__import__("os").getcwd()' + BT))

print("\n-- 深路径：expr / 反引号 / value 三侧都支持任意深度 --")
deep = {"name": "deep", "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"x": {"type": "int64", "value": "$.a.b.c"}}}}
code, out = gen_run(deep, [{"a": {"b": {"c": 42}}}])
print(code)
show("run", out)
