# -*- coding: utf-8 -*-
"""task-7：`:py{}` 多语句代码块（契约 §3.7 第 1–7 条 / IR 节点 `("pyblock", stmts, free, loc)`）。

分两层断言：
1. **IR 层**（本文件主体）：节点形状、块内局部命名、自由字段链、位置、拒绝清单 —— 全绿。
2. **端到端**：codegen 把 `pyblock` 落成**模块级命名辅助函数** `def _pyN(f1, f2, ...)`（契约 §4.3 指定
   形状，不是 lambda/IIFE），本文件断言真实运行值。若将来 codegen 回归到"不支持的 IR 节点"，
   这里以**具名 FAIL** 暴露（不 skip、不 xfail），便于立刻定位。

块语法（与 `CONTRACT.md` §3.7 一致）：`py{ let NAME = EXPR; ... ; return EXPR }`
"""
from __future__ import annotations

import pytest

import dcg
from dcg.jx import Unsupported, compile_expr, compile_py_block

BLOCK = "pyblock"


# ---------------------------------------------------------------------------
# IR 层：节点形状
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "src, expect",
    [
        ("py{ return int($.a) }", (BLOCK, "return int($.a)", ["a"], (1, 4))),
        (
            "py{ let t = $.a * 1.2; return int(t) }",
            (BLOCK, "__jx_0_t = $.a * 1.2\nreturn int(__jx_0_t)", ["a"], (1, 4)),
        ),
        ("py{ return '}' }", (BLOCK, "return '}'", [], (1, 4))),
        ('py{ return "# }" }', (BLOCK, 'return "# }"', [], (1, 4))),
        ("py{ return 'a;b' }", (BLOCK, "return 'a;b'", [], (1, 4))),
        ("py{ return 'a`b' }", (BLOCK, "return 'a`b'", [], (1, 4))),
        ("py{ return $.5.a.1 }", (BLOCK, "return $.5.a.1", ["5.a.1"], (1, 4))),
        ("py{ return len($.a) }", (BLOCK, "return len($.a)", ["a"], (1, 4))),
    ],
)
def test_block_node_shape(src, expect):
    assert compile_expr(src) == expect


def test_block_loc_is_absolute_and_loc_base_shifts_it():
    assert compile_expr("  py{ return $.a }")[3] == (1, 6)
    node = compile_expr("py{ let t = $.a; return t }", loc_base=(3, 5))
    assert node[0] == BLOCK
    assert node[3] == (3, 8)                      # 5 + len("py{") = 8
    assert node[1] == "__jx_0_t = $.a\nreturn __jx_0_t"


def test_blocks_are_numbered_independently():
    node = compile_expr("py{ return 1 } + py{ let z = 2; return z }")
    assert node == (
        "bin",
        "+",
        (BLOCK, "return 1", [], (1, 4)),
        (BLOCK, "__jx_1_z = 2\nreturn __jx_1_z", [], (1, 21)),
        (1, 16),
    )


def test_single_return_block_matches_inline_py_segment():
    """`py{ return EXPR }` 与 `` `EXPR` `` 同源、同自由字段，只差容器标签。"""
    block = compile_expr("py{ return int($.a) }")
    inline = compile_expr("`int($.a)`")
    assert block[0] == BLOCK and inline[0] == "py"
    # 块源码是语句序列（以 return 收尾），内联段是裸表达式
    assert block[1] == "return " + inline[1]
    assert block[2] == inline[2]


def test_block_locals_are_namespaced_per_block():
    expr = "py{ let t = 1; return t } & py{ let t = 2; return t }"
    node = compile_expr(expr)
    left, right = node[2], node[3]
    assert left[1] == "__jx_0_t = 1\nreturn __jx_0_t"
    assert right[1] == "__jx_1_t = 2\nreturn __jx_1_t"


def test_block_comments_do_not_break_statement_split():
    """`;` 之后紧跟注释时，注释不能把该语句判成"不是合法语句"。"""
    node = compile_expr("py{ let t = 1; # }\n return t }")
    assert node == (BLOCK, "__jx_0_t = 1\nreturn __jx_0_t", [], (1, 4))


def test_block_keeps_ordinary_python_indexing():
    node = compile_expr("py{ return $.a[0] }")
    assert node[0] == BLOCK
    assert node[2] == ["a"]
    assert "$.a" in node[1]


def test_compile_py_block_is_exported_and_pure():
    src, free = compile_py_block(" let t = $.a + 1; return t ")
    assert free == ["a"]
    assert src == "__jx_0_t = $.a + 1\nreturn __jx_0_t"
    # 纯函数：同名块重复调用结果一致（无隐藏计数状态）
    assert compile_py_block(" let t = $.a + 1; return t ") == (src, free)
    assert compile_py_block(" return 1 ", block_index=7)[0] == "return 1"


# ---------------------------------------------------------------------------
# IR 层：拒绝清单（逐条带列号，绝不静默降级）
# ---------------------------------------------------------------------------
REJECTS = [
    ("py{ $.a }", 5, "块内不是合法语句"),
    ("py{}", 4, "代码块缺少 return"),
    ("py{ let t = 1 }", 15, "代码块缺少 return"),
    ("py{ import os; return 1 }", 5, "块内禁止 import"),
    ("py{ from os import path; return 1 }", 5, "块内禁止 import"),
    ("py{ for x in $.a: return x }", 5, "块内不支持 for 循环"),
    ("py{ while True: return 1 }", 5, "块内不支持 while 循环"),
    ("py{ try: return 1 }", 5, "块内不支持 try/except"),
    ("py{ def f(): return 1 }", 5, "块内不支持 def"),
    ("py{ class C: pass }", 5, "块内不支持 class"),
    ("py{ return 1; return 2 }", 15, "代码块有多个 return"),
    ("py{ x = 1; return x }", 5, "赋值给非 let 变量 'x'"),
    ("py{ return hourB }", 12, "代码块里引用了未声明的名字 'hourB'"),
    ("py{ return $x }", 12, "代码块里的 $ 引用"),
    ("py{ return $$ }", 12, "代码块里的 $ 引用"),
    ("py{ return lambda: 1 }", 12, "块内不支持 lambda"),
    ("py{ return __import__('os') }", 12, "代码块里引用了未声明的名字 '__import__'"),
    ("py{ let t = 1; t += 1; return t }", 16, "赋值给非 let 变量 't'"),
    ("py{ return 1; let t = 2 }", 15, "return 之后还有语句"),
    ("py{ if $.a: return 1 }", 5, "块内不支持 if 语句"),
    ("py{ return $.a.b.c }", 12, "块内深路径 $.a.b.c"),
    ("py{ return __jx_p0 }", 4, "代码块含保留前缀 '__jx'"),
    ("py{ let t = 1; return t", 1, "语法错误"),
]


@pytest.mark.parametrize("src, col, feature", REJECTS, ids=[r[0] for r in REJECTS])
def test_block_rejections_carry_column(src, col, feature):
    with pytest.raises(Unsupported) as ei:
        compile_expr(src)
    assert ei.value.loc == (1, col), f"{src}: loc={ei.value.loc}"
    assert ei.value.feature == feature, f"{src}: feature={ei.value.feature!r}"
    assert str(ei.value).startswith(f"第 {col} 列: {feature} 不可逐行编译")


def test_block_deep_path_explains_the_workaround():
    with pytest.raises(Unsupported) as ei:
        compile_expr("py{ return $.d.items() }")
    assert "let x = $.名称" in str(ei.value), "深路径拒绝必须给出可操作的替代写法"


def test_block_form_only_accepts_brace_after_py():
    """`py` 后面不跟 `{` 时仍是普通字段名，不误判成代码块。"""
    assert compile_expr("py + 1")[0] == "bin"


# ---------------------------------------------------------------------------
# 端到端：codegen 接线前是具名 FAIL（不 skip / 不 xfail）
# ---------------------------------------------------------------------------
def _run_one(expr: str, row: dict):
    fields = {k: {"type": "any", "value": "$." + k} for k in row}
    fields["v"] = {"type": "any", "expr": expr}
    schema = {
        "name": "pyblock_probe",
        "output": {"shape": "list", "single": False, "filter": False},
        "out": {"type": "object", "fields": fields},
    }
    src = dcg.generate(schema)
    ns: dict = {}
    exec(compile(src, "<gen/pyblock_probe>", "exec"), ns)  # noqa: S102 - 生成码本来就是 exec 产物
    return ns["run"]([row])[0][list(fields).index("v")]


def test_end_to_end_codegen_supports_pyblock():
    expr = "py{ let t = $.a * 1.2; return int(t) }"
    try:
        got = _run_one(expr, {"a": 10})
    except Exception as exc:  # noqa: BLE001 - 把"未接线"与"接线后算错"区分开
        raise AssertionError(
            "pyblock 端到端执行失败（codegen 现在把 `pyblock` 落成模块级 `def _pyN(f...)` 辅助函数；"
            f"若它回归到不支持该节点，这里以具名 FAIL 暴露，不 skip/xfail）：{type(exc).__name__}: {exc}"
        ) from exc
    assert got == 12


def test_end_to_end_codegen_supports_pyblock_multi_statement():
    expr = "py{ let s = $.a; let t = s * 2; return t + 1 }"
    try:
        got = _run_one(expr, {"a": 5})
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            "pyblock 端到端执行失败（codegen 现在把 `pyblock` 落成模块级 `def _pyN(f...)` 辅助函数；"
            f"若它回归到不支持该节点，这里以具名 FAIL 暴露，不 skip/xfail）：{type(exc).__name__}: {exc}"
        ) from exc
    assert got == 11
