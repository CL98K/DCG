# -*- coding: utf-8 -*-
"""jx 政策层：DCG 接口实现细节文档 §3.4 / §3.5 的四类函数表与永久拒绝。

两个方向都要断言：**能力表里的必须能编译**、**永久拒绝的必须拒绝且带列号**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

import dcg
from dcg.jx import HIGHER_ORDER, NONDETERMINISTIC, ROW_FUNCS, Unsupported, compile_expr
from dcg.jx.policy import (
    BARE_CALL_ALIASES,
    STRUCTURAL,
    Unsupported as PolicyUnsupported,
    is_higher_order,
    is_nondeterministic,
    is_row_func,
    rejection_reason,
)

#: 手抄自 DCG 接口实现细节文档.md §3.4 —— 独立于 policy.py 的第二份清单，用来发现漂移。
CONTRACT_3_4 = frozenset(
    {
        # 字符串（20）
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase",
        "$indexOf",
        # 数值（10）
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max",
        # 布尔（4）
        "$boolean", "$not", "$exists", "$type",
        # 数组/对象（12）
        "$count", "$append", "$average", "$reverse", "$sort", "$distinct",
        "$merge", "$keys", "$values", "$lookup", "$spread", "$single",
    }
)

#: 手抄自 §3.5「B 类高阶」（展开见 §3.6）。
CONTRACT_3_5_HO = frozenset({"$map", "$filter", "$reduce", "$each", "$sift", "$sort"})

#: 手抄自 §3.5「C 类非确定」（展开见 §3.7）。
CONTRACT_3_5_NONDET = frozenset({"$now", "$millis", "$random", "$shuffle"})


def test_whitelist_matches_contract_3_4():
    assert ROW_FUNCS == CONTRACT_3_4
    assert all(n.startswith("$") for n in ROW_FUNCS)


def test_higher_order_matches_contract_3_5_2():
    assert HIGHER_ORDER == CONTRACT_3_5_HO
    assert all(n.startswith("$") for n in HIGHER_ORDER)


def test_nondeterministic_matches_contract_3_5_3():
    assert NONDETERMINISTIC == CONTRACT_3_5_NONDET


def test_unsupported_is_the_public_exception():
    assert PolicyUnsupported is Unsupported
    e = Unsupported("$eval(...)", (1, 12), "运行期求值任意表达式")
    assert str(e) == "第 12 列: $eval(...) 不可逐行编译（运行期求值任意表达式）"
    assert e.feature == "$eval(...)" and e.loc == (1, 12) and e.col == 12
    assert str(Unsupported("区间 a..b", None, "需要序列语义")).startswith("区间 a..b")


@pytest.mark.parametrize("name", sorted(ROW_FUNCS))
def test_every_row_function_compiles(name):
    ir = compile_expr(f"{name}(v)")
    assert ir[0] == "call" and ir[1] == name, name
    assert isinstance(ir[-1], tuple) and len(ir[-1]) == 2, name


@pytest.mark.parametrize(
    "expr",
    [
        "$map(v, function($x){ $x + 1 })",
        "$filter(v, function($x){ $x > 1 })",
        "$reduce(v, function($a, $b){ $a + $b }, 0)",
        "$each(v, function($x, $k){ $k })",
        "$sift(v, function($x){ $x })",
        "$sort(v, function($a, $b){ $a > $b })",
        "$map(v, function($x, $i){ $x + $i })",
        "$map(v, function($x){ $position() })",
    ],
)
def test_higher_order_functions_compile_with_lambda(expr):
    assert compile_expr(expr)[0] == "call"


def test_sort_without_comparator_is_a_row_function():
    ir = compile_expr("$sort(v)")
    assert ir[0] == "call" and ir[1] == "$sort"
    assert ir[2] and ir[2][0][0] in ("field", "index")  # 实参按普通行内表达式发射


@pytest.mark.parametrize("name", sorted(CONTRACT_3_5_HO))
def test_higher_order_requires_function_literal(name):
    with pytest.raises(Unsupported) as ei:
        compile_expr(f"{name}(v, $string)")
    assert name in str(ei.value)
    assert "函数字面量" in str(ei.value)
    assert ei.value.loc is not None


@pytest.mark.parametrize("name", sorted(NONDETERMINISTIC))
def test_nondeterministic_functions_compile(name):
    expr = f"{name}(v)" if name == "$shuffle" else f"{name}()"
    ir = compile_expr(expr)
    assert ir[0] == "call" and ir[1] == name, name


def test_eval_is_permanently_rejected():
    with pytest.raises(Unsupported) as ei:
        compile_expr("$eval(x)")
    assert "运行期求值" in str(ei.value)
    assert rejection_reason("$eval") is not None


def test_position_only_inside_function_body():
    with pytest.raises(Unsupported):
        compile_expr("$position()")
    ir = compile_expr("$map(v, function($x){ $position() })")
    assert ir[0] == "call"


STRUCTURAL_EXPRS = {
    "eval": "$eval(x)",
    "range": "1..5",
    "regex": "/ab/",
    "bind_pos": "a#$i",
    "bind_join": "a@$j",
    "sort": "a^.b",
    "transform": "a ~> b",
    "reduce": "a{'k': v}",
    "group": "a[.b > 1]",
    "path": "$.a[$$]",
}


@pytest.mark.parametrize("key", sorted(STRUCTURAL))
def test_structural_constructs_are_rejected(key):
    with pytest.raises(Unsupported) as ei:
        compile_expr(STRUCTURAL_EXPRS[key])
    assert ei.value.loc is not None, key
    assert str(ei.value).startswith(f"第 {ei.value.loc[1]} 列: "), key


def test_structural_table_covers_every_rejected_feature():
    assert set(STRUCTURAL) == set(STRUCTURAL_EXPRS)


#: 分组构造 `{expr: expr}` 的各种写法都必须按 `group` 拒绝。
#: 回归：修前 `{$.k: $.v}` 被当成对象字面量，非 lit 键直接进 compiler，
#: 最终把 jx AST 元组当字典键泄漏进产物
#: （`_ob([(('index', ...), r['v'])])`）。
GROUP_KEYED_EXPRS = [
    "{$.k: $.v}",
    "{1+1: 'a'}",
    "{$.k: $.v, $.a: $.b}",
    "{'ok': 1, $.k: 2}",
]


@pytest.mark.parametrize("src", GROUP_KEYED_EXPRS)
def test_dynamic_object_keys_are_rejected_as_group(src):
    with pytest.raises(Unsupported) as ei:
        compile_expr(src)
    assert ei.value.feature == "分组/聚合 {..}", src
    assert ei.value.loc is not None, src


def test_string_literal_object_keys_still_compile():
    """对象字面量（键是字符串字面量）不受上面那条拒绝的影响。"""
    assert compile_expr("{}")[0] == "object"
    assert compile_expr("{'a': 1, 'b': 2}")[0] == "object"
    ir = compile_expr("{'k': v, 'j': w}")
    assert ir[0] == "object" and [k for k, _ in ir[1]] == ["k", "j"]


def test_overlap_between_tables_is_intentional():
    # `$sort` 既是 A 类（单实参升序）也是 B 类（带比较器排序）。
    assert "$sort" in ROW_FUNCS and "$sort" in HIGHER_ORDER
    assert not (ROW_FUNCS & NONDETERMINISTIC)
    assert rejection_reason("$sum") is None
    assert rejection_reason("$now") is None
    assert rejection_reason("$map") is None
    assert rejection_reason("$eval") is not None
    assert is_row_func("sum") and is_row_func("$sum") and not is_row_func("$map")
    assert is_higher_order("map") and is_higher_order("$map")
    assert is_nondeterministic("now") and is_nondeterministic("$now")


def test_bare_call_aliases_for_real_schemas():
    """真实 schema 写的是 str($.5.a.1) / len(x)。"""
    ir = compile_expr("str($.a)")
    assert ir[0] == "call" and ir[1] == "$string"
    assert compile_expr("int($.a)")[1] == "$number"
    assert set(BARE_CALL_ALIASES) == {"str", "int", "float", "bool", "len", "abs"}


def test_unknown_bare_name_call_is_rejected():
    with pytest.raises(Unsupported) as ei:
        compile_expr("hourB(1)")
    assert "hourB()" in str(ei.value)


def test_unknown_dollar_function_is_rejected():
    with pytest.raises(Unsupported) as ei:
        compile_expr("$frobnicate(1)")
    assert "$frobnicate" in str(ei.value)


def test_dollar_and_double_dollar_forms():
    assert compile_expr("$")[0] == "root"
    with pytest.raises(Unsupported):
        compile_expr("$$.a")
    with pytest.raises(Unsupported):
        compile_expr("$()")


def test_no_jsonata_package_dependency():
    """实现细节文档 §3.5（D10）：JSONata 语义全部自研，不 import 第三方 jsonata 包。"""
    for path in pathlib.Path(dcg.__file__).resolve().parent.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import jsonata" not in text, path
        assert "from jsonata" not in text, path
