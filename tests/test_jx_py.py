# -*- coding: utf-8 -*-
"""jx 反引号内联 Python 段（契约 §1 ``py`` 节点 / §4.3）。

断言的是**编译期校验**，不是运行期：build 期只 ``ast.parse``，绝不 exec（契约 §7 第 3 条）。
"""
from __future__ import annotations

import pytest

from dcg.jx import (
    FIELD_REF,
    Unsupported,
    analyze_py_segment,
    chain_ir,
    compile_expr,
    parse_chain,
    parse_path,
    parse_path_chain,
)

SAFE_BUILTINS = ["int($.a)", "abs($.a) + 1", "len($.a)", "min($.a, 1)", "sorted($.a)"]
SAFE_MODULES = ["math.floor($.a)", "json.dumps($.a)", "re.sub('a', 'b', $.a)"]


def test_py_node_shape():
    """（"py", source_str, [free_field_names...], loc）——契约 §1。"""
    ir = compile_expr("`$.a * 2`")
    assert ir[0] == "py"
    assert ir[1] == "$.a * 2"          # source_str 原样保留
    assert isinstance(ir[2], list)     # 必须是 list，不是 set
    assert ir[2] == ["a"]
    assert ir[3] == (1, 1)


def test_py_node_keeps_its_own_column():
    ir = compile_expr("$.a + `int($.b)`")
    assert ir[0] == "bin" and ir[1] == "+"
    assert ir[2][0] in ("field", "index")   # 左侧 `$.a` 的形态由路径策略决定
    py = ir[3]
    assert py[0] == "py" and py[1] == "int($.b)"
    assert py[2] == ["b"] and py[3] == (1, 7)


def test_free_field_names_order_and_dedupe():
    src, names = analyze_py_segment("$.b + $.a * $.b")
    assert src == "$.b + $.a * $.b"
    assert names == ["b", "a"]          # 首次出现顺序，且去重


def test_free_field_names_empty_when_no_fields():
    assert analyze_py_segment("1 + 2")[1] == []


def test_py_bare_name_is_a_field_reference():
    """契约 §6.2：段内不是 Python 全局的裸名**也是字段引用**。

    依据是真实工作负载 ``bench/_workload.py`` 的显式 ext
    ``str($.5.a.1) + str(hourB)``：``str`` 是 Python 内置名（保持全局），
    ``hourB`` 是本 schema 的字段。jx 看不到 schema，只登记名字；
    "该名字是否已声明"由 codegen 在 **build 期**报 ``SchemaError`` 并列出可用字段名。
    """
    src, names = analyze_py_segment("str($.5.a.1) + str(hourB)", (1, 1))
    assert src == "str($.5.a.1) + str(hourB)"
    assert names == ["5.a.1", "hourB"]      # 链按源码序在前，裸名随后


def test_py_cap_workload_ext_compiles():
    """§3.7 最小测试：CAP 的 ext 是 M0/CAP 的命脉，必须编得过。"""
    ir = compile_expr("`str($.5.a.1) + str(hourB)`")
    assert ir[0] == "py"
    assert ir[1] == "str($.5.a.1) + str(hourB)"
    assert ir[2] == ["5.a.1", "hourB"]
    assert ir[3][0] == 1


def test_py_only_non_global_bare_names_are_fields():
    """Python 内置名/安全模块名不得被误登记成字段名。"""
    for src in SAFE_BUILTINS + SAFE_MODULES:
        assert analyze_py_segment(src, (1, 1))[1] == ["a"], src
    assert analyze_py_segment("foo + bar", (1, 1))[1] == ["foo", "bar"]


def test_py_allows_builtins_and_safe_modules():
    for src in SAFE_BUILTINS + SAFE_MODULES:
        _, names = analyze_py_segment(src, (1, 1))
        assert names == ["a"], src


def test_py_rejects_non_expression():
    """赋值/多语句/声明/import 在 build 期就必须失败，不能留到运行期 SyntaxError。"""
    for bad in ["x = 1", "1; 2", "def f(): pass", "import os", "return 1"]:
        with pytest.raises(Unsupported) as ei:
            analyze_py_segment(bad, (1, 1))
        assert ei.value.feature == "反引号段 Python 语法错误", bad
        assert ei.value.loc == (1, 1)


def test_py_syntax_error_carries_loc_and_message():
    with pytest.raises(Unsupported) as ei:
        analyze_py_segment("1 +", (2, 3))
    assert ei.value.loc == (2, 3)
    assert "Python 语法错误" in str(ei.value)
    assert "段内偏移" in str(ei.value)


def test_py_rejects_internal_placeholder_prefix():
    with pytest.raises(Unsupported) as ei:
        analyze_py_segment("__jx_f0 + 1", (1, 1))
    assert "保留前缀" in str(ei.value)


def test_jx_reserves_only_the_dunder_jx_namespace():
    """jx 只保留 ``__jx`` 前缀（codegen 别名/占位符可放心用它）。

    ``_y0`` 这类名字**用户可写**（且会被当作字段引用），所以 codegen 用
    ``re.sub(r"\\b_y0\\b", ...)`` 回填路径别名的做法会**静默撞车** —— 已告知 Lead。
    """
    assert analyze_py_segment("_y0 + $.a")[1] == ["a", "_y0"]
    with pytest.raises(Unsupported):
        analyze_py_segment("__jx_p0 + 1")


def test_py_loc_base_offsets():
    ir = compile_expr("`$.a`", loc_base=(3, 5))
    assert ir[0] == "py" and ir[3] == (3, 5)


def test_py_escaped_backtick_inside_segment():
    """段内容捕获到下一个**未转义**反引号；``\\``` 是转义（契约 §3.6）。"""
    ir = compile_expr("`'a\\`b'`")
    assert ir[0] == "py"
    assert ir[1] == "'a`b'"


def test_single_dollar_is_not_a_field_inside_backtick():
    with pytest.raises(Unsupported):
        compile_expr("`$`")


def test_multi_statement_brace_form_is_rejected():
    """契约只定义反引号段；`py{...}` 这类花括号形式不是逐行可编译结构。"""
    with pytest.raises(Unsupported):
        compile_expr("py{ $.a }")


def test_backtick_inside_string_literal_is_a_boundary():
    """JSONata 从不使用反引号，因此它零歧义地开启 Python 段（契约 §3.6）。"""
    ir = compile_expr("'a' & `str(1)`")
    assert ir[0] == "bin" and ir[3][0] == "py"


# ---------------------------------------------------------------------------
# §3.7 字段链 + §3.6 动态下标 + §6.1 深路径（链解析唯一权威 = dcg/jx/paths.py）
# ---------------------------------------------------------------------------
def _strip_loc(x):
    """去掉 IR 里的 (line, col)，只比较结构（chain_ir 与路径编译的 loc 基准不同）。"""
    if isinstance(x, list):
        return [_strip_loc(i) for i in x]
    if isinstance(x, tuple):
        if (
            x
            and isinstance(x[-1], tuple)
            and len(x[-1]) == 2
            and all(isinstance(i, int) for i in x[-1])
        ):
            return tuple(_strip_loc(i) for i in x[:-1])
        return tuple(_strip_loc(i) for i in x)
    return x


def test_paths_parse_path_is_the_single_authority():
    """paths.parse_path：只解析、只认 $. 起头的合法链；深路径判定不在这里。"""
    for src, want in [
        ("$.a", ["a"]),
        ("$.5", [5]),
        ("$.5.a", [5, "a"]),
        ("$.5.a.1", [5, "a", 1]),
        ("$.5.a.1.b", [5, "a", 1, "b"]),   # 解析**成功**（拒绝发生在路径编译层）
        ("$.a.b", ["a", "b"]),
    ]:
        assert parse_path(src) == want, src
    for bad in ["bare", "$", "$.", "$.a..b", "$.a.", "$.-1", "$.a b", "$.a[0]", 5, None]:
        assert parse_path(bad) is None, bad


def test_paths_single_extraction_regex():
    """切链的正则也只有一处（paths.FIELD_REF），且必须整体吃掉整条链。"""
    assert FIELD_REF.findall("str($.5.a.1) + $.b * $.5") == ["5.a.1", "b", "5"]
    src, names = analyze_py_segment("$.a.b + 1")     # 段内允许多符号段：由调用方判 §6.1
    assert names == ["a.b"] and src == "$.a.b + 1"


def test_chain_ir_shares_implementation_with_path_compile():
    """§3.7 第 3 条：``parse_path_chain("5.a.1")`` 与 ``compile_expr("$.5.a.1")`` **== 相等**。

    这是"同一个解析结果"的强证据（不只是"两侧长得像"）。默认 ``loc=(1,1)`` 时
    ``$`` 在 (1,1)、链节点在 (1,2)，与路径编译逐位一致。
    """
    assert parse_path_chain("5.a.1") == compile_expr("$.5.a.1")
    assert parse_path_chain("a") == compile_expr("$.a")
    assert parse_path_chain("5.a") == compile_expr("$.5.a")
    assert parse_path_chain("5") == compile_expr("$.5")
    # loc 基准同样对齐
    assert parse_path_chain("5.a.1", (3, 5)) == compile_expr("$.5.a.1", loc_base=(3, 5))


def test_chain_ir_rejects_illegal_chain_and_deep_path():
    with pytest.raises(Unsupported) as ei:
        chain_ir("a..b", (1, 1))
    assert "非法" in str(ei.value)
    with pytest.raises(Unsupported) as ei2:
        chain_ir("5.a.1.b", (1, 1))     # 能解析，但 ≥2 个符号段 -> §6.1 拒绝
    assert "深路径" in str(ei2.value)


def test_parse_path_rejects_bare_chain_without_dollar_prefix():
    """接口边界：parse_path 只认 `$.` 前缀；链文本要走 parse_chain（不许静默回退）。"""
    assert parse_path("3.x") is None          # 不是 `$.` 起头
    assert parse_chain("3.x") == [3, "x"]     # 链文本形态；`$.3.x` 是合法路径（1 个符号段）
    assert parse_chain("5.a.1.b") == [5, "a", 1, "b"]
    for bad in ["", "a..b", ".a", "a.", "-1", "a b", None, 5]:
        assert parse_chain(bad) is None, bad
    assert compile_expr("$.3.x")[0] == "index"
    assert parse_path_chain("3.x") == compile_expr("$.3.x")


def test_dynamic_index_is_index_node_not_ix_call():
    """§3.6：动态下标统一 ("index", base, idx, loc)，废弃的 $ix call 不得出现。"""
    ir = compile_expr("$.a[$i]")
    assert ir[0] == "index"
    assert ir[1][0] == "index" and ir[1][2][1] == "a"
    assert ir[2][0] == "var" and ir[2][1] == "i"
    assert "$ix" not in repr(ir)
    # 数值下标与动态下标并存
    ir2 = compile_expr("$.5.a[$i]")
    assert ir2[0] == "index" and ir2[1][0] == "index"
    assert "$ix" not in repr(ir2)


def test_deep_path_rejected_with_nested_fields_hint():
    """§6.1：从 root 起 ≥2 个**符号段**即拒绝，并提示用嵌套 fields。"""
    for bad in ["$.a.b", "$.a.b.c", "$.5.a.1.b", "$.a.b[0]"]:
        with pytest.raises(Unsupported) as ei:
            compile_expr(bad)
        assert "深路径" in str(ei.value), bad
        assert "fields" in str(ei.value), bad
        assert "第 " in str(ei.value) and ei.value.loc is not None, bad


def test_deep_path_allows_at_most_one_symbolic_segment():
    """§6.1 允许：$.name、纯数值 $.5、$.5.a.1（只有 a 一个符号段）、hour[2]。"""
    for good in ["$.a", "$.5", "$.5.a", "$.5.a.1", "$.index"]:
        assert compile_expr(good)[0] == "index", good
    assert compile_expr("hour[2] + 123")[0] == "bin"
    assert compile_expr("hourA + hourB")[0] == "bin"


def test_union_and_bitwise_or_are_not_silently_accepted():
    """§3.6：JSONata 的并集 | 未实现 -> 必须编译期拒绝，不能当位或悄悄放过。"""
    for bad in ["a | b", "$.a | $.b", "a | |a|"]:
        with pytest.raises(Unsupported):
            compile_expr(bad)


def test_bare_name_subscript_is_a_field_reference_not_a_deep_path():
    """§3.6 第 2 条 + 裸名=字段：`$.a[b]` 是动态下标，**不是** §6.1 深路径。

    修复前这里报「第 2 列: 深路径 [a,b]」——把裸名下标当成符号段，诊断也是错的。
    """
    ir = compile_expr("$.a[b]")
    assert ir[0] == "index"
    assert ir[1][0] == "index" and ir[1][2][1] == "a", ir   # base: r['a']
    assert ir[2][0] == "field" and ir[2][1] == "b", ir       # idx: 本行字段 b
    assert "$ix" not in repr(ir)
    assert ir[3] is not None, "必须带列号"


def test_bare_name_subscript_on_non_root_base_is_not_a_string_key():
    """非 `$` 基座：`a[b]` 必须编成动态下标（字段 b），**不得**静默变成字符串键 a["b"]。

    修复前 `a[b]` -> ('index', ('field','a'), ('lit','b'))，即 a["b"] —— 静默降级（§3.5）。
    """
    ir = compile_expr("a[b]")
    assert ir[0] == "index"
    assert ir[1][0] == "field" and ir[1][1] == "a", ir
    assert ir[2][0] == "field" and ir[2][1] == "b", ir
    assert not (ir[2][0] == "lit" and ir[2][1] == "b"), "裸名被编成了字符串键"
    # 带引号的字符串键仍然是常量键：裸名与引号名必须分离
    quoted = compile_expr("a['b']")
    assert quoted[2][0] == "lit" and quoted[2][1] == "b", quoted
    # 常量数值下标不受影响
    assert compile_expr("a[2]")[2][0] == "lit" and compile_expr("a[2]")[2][1] == 2
    assert compile_expr("$.a[$i]")[2][0] == "var"
