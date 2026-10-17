# -*- coding: utf-8 -*-
"""jx 政策层：契约 §3.4 逐行白名单 / §3.5 整批与非确定黑名单 / D4 无 jsonata 依赖。

两个方向都要断言：**白名单里的必须能编译**、**黑名单里的必须拒绝**。
"""
from __future__ import annotations

import pathlib
import re

import pytest

import dcg
from dcg.jx import ROW_FUNCS, Registry, Unsupported, compile_expr, default_registry
from dcg.jx.policy import (
    BARE_CALL_ALIASES,
    BATCH_ONLY,
    LITERAL_ARRAY_ONLY,
    NONDETERMINISTIC,
    STRUCTURAL,
    Unsupported as PolicyUnsupported,
    is_row_func,
    rejection_reason,
)

#: 手抄自 CONTRACT.md §3.4 —— 独立于 policy.py 的第二份清单，用来发现漂移。
CONTRACT_3_4 = frozenset(
    {
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase",
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max",
        "$boolean", "$not", "$exists", "$type",
        "$count", "$append", "$reverse", "$sort", "$distinct",
    }
)


def test_whitelist_matches_contract_3_4():
    assert ROW_FUNCS == CONTRACT_3_4
    assert all(n.startswith("$") for n in ROW_FUNCS)


def test_unsupported_is_the_public_exception():
    assert PolicyUnsupported is Unsupported
    e = Unsupported("$sum", (1, 12), "需整批数据求值")
    assert str(e) == "第 12 列: $sum 不可逐行编译（需整批数据求值）"   # 契约 §3.1 的样例
    assert e.feature == "$sum" and e.loc == (1, 12) and e.col == 12
    assert str(Unsupported("区间 a..b", None, "需要序列语义")).startswith("区间 a..b")


@pytest.mark.parametrize("name", sorted(ROW_FUNCS))
def test_every_whitelisted_function_compiles(name):
    expr = "$append([1], [2])" if name == "$append" else (
        f"{name}([1, 2])" if name in LITERAL_ARRAY_ONLY else f"{name}(v)"
    )
    ir = compile_expr(expr)
    assert ir[0] == "call" and ir[1] == name, expr
    assert isinstance(ir[-1], tuple) and len(ir[-1]) == 2, expr


@pytest.mark.parametrize("name", sorted(BATCH_ONLY))
def test_batch_only_functions_are_rejected(name):
    with pytest.raises(Unsupported) as ei:
        compile_expr(f"{name}(xs)")
    assert name in str(ei.value)
    assert ei.value.loc is not None and f"第 {ei.value.loc[1]} 列" in str(ei.value)


@pytest.mark.parametrize("name", sorted(LITERAL_ARRAY_ONLY))
def test_literal_array_functions_accept_literal_arrays(name):
    expr = "$append([1], [2])" if name == "$append" else f"{name}([1, 2])"
    assert compile_expr(expr)[1] == name


@pytest.mark.parametrize("name", sorted(LITERAL_ARRAY_ONLY - {"$append"}))
def test_literal_array_functions_reject_non_literal_args(name):
    with pytest.raises(Unsupported) as ei:
        compile_expr(f"{name}(xs)")
    assert "字面量数组" in str(ei.value)


@pytest.mark.parametrize("name", sorted(NONDETERMINISTIC))
def test_nondeterministic_functions_are_rejected(name):
    with pytest.raises(Unsupported) as ei:
        compile_expr(f"{name}()")
    assert name in str(ei.value)
    assert "非确定" in str(ei.value)


def test_overlap_between_white_and_black_list_is_intentional():
    """§3.4 与 §3.5 故意重叠；处置是"只接受字面量数组"。"""
    assert LITERAL_ARRAY_ONLY <= ROW_FUNCS
    # `$append` 只在 §3.4 里带"仅限字面量数组"注解；§3.5 的黑名单没有它（它不是整批函数），
    # 但实参形状检查照样生效 —— 见 test_literal_array_functions_reject_non_literal_args。
    assert (LITERAL_ARRAY_ONLY - {"$append"}) <= BATCH_ONLY
    assert "$count" in ROW_FUNCS and "$count" not in BATCH_ONLY  # 运行期数组，逐行可编译
    assert rejection_reason("$sum") is None
    assert rejection_reason("$map") == "需整批数据求值"
    assert is_row_func("sum") and is_row_func("$sum") and not is_row_func("$map")


STRUCTURAL_EXPRS = {
    "range": "1..5",
    "regex": "/ab/",
    "lambda": "function($a){$a}",
    "bind_pos": "a#$i",
    "bind_join": "a@$j",
    "sort": "a^.b",
    "transform": "a ~> b",
    "reduce": "a{'k': v}",
    "group": "a[.b > 1]",  # 分组/谓词阶段，非逐行可编译
    "path": "a.b.c",
}


@pytest.mark.parametrize("key", sorted(STRUCTURAL))
def test_structural_constructs_are_rejected(key):
    with pytest.raises(Unsupported) as ei:
        compile_expr(STRUCTURAL_EXPRS[key])
    assert ei.value.loc is not None, key
    assert str(ei.value).startswith(f"第 {ei.value.loc[1]} 列: "), key


def test_structural_table_covers_every_rejected_feature():
    assert set(STRUCTURAL) == set(STRUCTURAL_EXPRS)


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


def test_compile_expr_is_pure_and_stable():
    """纯函数：同输入同输出，不读全局 MODE（契约 §3.1）。"""
    src = "$uppercase(s) & `str($.n)`"
    assert compile_expr(src) == compile_expr(src)
    with pytest.raises(TypeError):
        compile_expr(123)


def test_d4_no_jsonata_dependency_in_dcg():
    """契约 D4：运行期绝不 import jsonata；jsonata 只允许出现在 tests/ 的 importorskip 里。"""
    root = pathlib.Path(dcg.__file__).resolve().parent
    hits = []
    for path in sorted(root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(import\s+jsonata|from\s+jsonata)", line):
                hits.append(f"{path}:{lineno}: {line.strip()}")
    assert not hits, "运行时代码不得依赖 jsonata：\n" + "\n".join(hits)


def test_jx_does_not_import_other_dcg_modules():
    """依赖方向：jx/* 只 import 同级模块（契约 §7 顶部注释）。"""
    root = pathlib.Path(dcg.__file__).resolve().parent / "jx"
    bad = []
    for path in sorted(root.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(from\s+dcg\.(?!jx)|import\s+dcg\.(?!jx))", line):
                bad.append(f"{path}:{lineno}: {line.strip()}")
    assert not bad, bad


# ---------------------------------------------------------------------------
# Registry（契约 §3.1）
# ---------------------------------------------------------------------------
def _double(x):
    return x * 2


def test_registry_fn_does_not_invoke_the_registered_function():
    """回归：形参名 `callable` 遮蔽内置函数，`callable(callable)` 会把被注册函数当校验器调用。"""
    r = Registry()
    calls = []
    r.fn("$f", lambda: calls.append(1))
    assert calls == []
    assert callable(r.get("$f"))


def test_registry_fn_and_get():
    r = Registry()
    r.fn("$double", _double)
    assert r.get("$double") is _double
    assert r.get("$nope") is None
    assert r.qualname("$double") == f"{_double.__module__}.{_double.__qualname__}"
    assert "$double" in r and r["$double"] is _double and len(r) == 1
    r.unregister("$double")
    assert "$double" not in r and len(r) == 0


def test_registry_rejects_bad_name_and_non_callable():
    r = Registry()
    with pytest.raises(ValueError):
        r.fn("double", _double)          # 必须带 "$"
    with pytest.raises(TypeError):
        r.fn("$x", 42)


def test_registry_is_not_implicit_global_state():
    """compile_expr 保持纯函数：只有显式 registry= 才认注册函数（契约 §3.1）。"""
    default_registry.fn("$double", _double)
    assert compile_expr("$double(a)", registry=default_registry)[0] == "rfunc"
    with pytest.raises(Unsupported):
        compile_expr("$double(a)")       # 不传 registry 就不认
    assert compile_expr("$double(a)", registry=default_registry) == compile_expr(
        "$double(a)", registry=default_registry
    )
