# -*- coding: utf-8 -*-
"""README §4.1（`before` / `after` 数据级钩子）的回归守护。

README 里贴了**每一种写法**的完整产物与实跑结果，改 codegen 时很容易让它悄悄
漂移。这里把 README 逐字引用的那些行钉死 —— 文档与实现不同步会在这里立刻变红。

覆盖（对应用户 m00292 的要求「before 需要接收函数表达式和 py{}」）：
  1. JSONata 表达式形态的 before（平铺 / 高阶函数排序）
  2. py{} 代码块形态的 before（多语句 + let 局部）
  3. 反引号段形态的 before（单表达式）
  4. after（结果组装后改写返回值）
  5. before + after 共存
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg                                    # noqa: E402

_FIELDS = {"v": {"type": "int64", "value": "$.0"}}


def _build(name, output, feed, fields=None):
    schema = {"name": name, "output": output,
              "out": {"type": "object", "fields": fields or _FIELDS}}
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<readme-hooks>", "exec"), ns)
    return src, ns["run"](feed)


# --------------------------------------------------------------- JSONata 形态
def test_before_jsonata_path_is_documented():
    """`before` 的裸 `$` 是**全量数据本身**，形参名是 `D` 不是 `R`。"""
    src, got = _build("b1", {"shape": "list", "before": "= $.rows"},
                      {"rows": [[9, 8], [7, 6]]})
    assert " _d=(lambda D:_ix_get(D,'rows'))(_d)" in src, src
    assert got == [[9], [7]]


def test_before_jsonata_higher_order():
    """before 里可以用高阶函数（比较器返回 true 表示交换）。"""
    src, got = _build(
        "b3", {"shape": "list",
               "before": "= $sort($.rows, function($a,$b){ $a[0] > $b[0] })"},
        {"rows": [[3, 0], [1, 0], [2, 0]]})
    assert "_d=(lambda D:_so(_ix_get(D,'rows'),lambda a,b:_gt(a[0],b[0])))(_d)" \
        in src, src
    assert got == [[1], [2], [3]]


# ------------------------------------------------------------------ py{} 形态
def test_before_pyblock_multi_statement():
    """py{} 形态的 before：模块级 helper + `$.链` 变成形参。"""
    src, got = _build(
        "b2", {"shape": "list",
               "before": "= py{ let rows = $.rows; "
                         "let keep = [r for r in rows if r[0] > 1]; "
                         "return keep }"},
        {"rows": [[1, 0], [2, 0], [3, 0]]})
    # helper 定义在 `run()` 之外，且 `$.rows` 落成形参
    assert "def _py0(_p0):" in src, src
    assert src.index("def _py0(") < src.index("def run("), \
        "py{} 辅助函数必须在 run() 之前定义:\n" + src
    assert " _d=(lambda D:_py0(D['rows']))(_d)" in src, src
    assert got == [[2], [3]]


def test_before_pyblock_can_use_bare_dollar():
    """py{} 里的裸 `$` 就是**全量数据本身**（task-13）。

    `_pyN` 是模块级函数，看不到 `run()` 里的 `D`，所以裸 `$` 必须走形参：
    数据级块多接一个「全量数据」形参，调用处传 `D`。
    """
    src, got = _build(
        "b2d", {"shape": "list",
                "before": "= py{ let keep = [r for r in $ if r[0] != 3]; "
                          "return keep }"},
        [[1, 2], [3, 4], [5, 6]])
    assert "def _py0(_p0):" in src, src
    assert "for r in _p0 if r[0] != 3" in src, src
    assert " _d=(lambda D:_py0(D))(_d)" in src, src
    assert got == [[1], [5]]


def test_before_pyblock_dollar_inside_backticks():
    """块内的反引号段也支持：`` py{ let keep = `[... for r in $ ...]`; ... } ``。"""
    src, got = _build(
        "b2t", {"shape": "list",
                "before": "= py{ let keep = `[r for r in $ if r[0] != 3]`; "
                          "return keep }"},
        [[1, 2], [3, 4], [5, 6]])
    assert "for r in _p0 if r[0] != 3" in src, src
    assert got == [[1], [5]]


def test_before_backtick_single_expression():
    src, got = _build("b4", {"shape": "list",
                             "before": "= `[r for r in $ if r[0] > 1]`"},
                      [[1], [2], [3]])
    assert " _d=(lambda D:[r for r in D if r[0] > 1])(_d)" in src, src
    assert got == [[2], [3]]


# ------------------------------------------------------------------ after
def test_after_rewrites_the_result():
    """after 拿到的是**形状已套用**的全量结果。"""
    src, got = _build("a1", {"shape": "list",
                             "after": "= {'n': $count($), 'rows': $}"},
                      [[1], [2], [3]])
    assert " return (lambda D:_ob([('n',_ct(D)),('rows',D)]))(_o)" in src, src
    assert got == {"n": 3, "rows": [[1], [2], [3]]}


# ------------------------------------------------------------------ 两者共存
def test_before_and_after_together():
    src, got = _build("ab", {"shape": "list",
                             "before": "= `[r for r in $ if r[0] > 1]`",
                             "after": "= `{'n': len($)}`"},
                      [[1], [2], [3]])
    # before 在 `_o=[]` 之前，after 在 return 上
    assert " _d=(lambda D:[r for r in D if r[0] > 1])(_d)" in src
    assert src.index("_d=(lambda D:") < src.index("_o=[]"), src
    assert " return (lambda D:{'n': len(D)})(_o)" in src, src
    assert got == {"n": 2}


# ------------------------------------------------------------- 不写就不付费
def test_no_hooks_generate_no_hook_calls():
    src, _ = _build("plain", {"shape": "list"}, [[1]])
    assert "(lambda D:" not in src, "不写钩子不得生成钩子调用:\n" + src
    assert "_d=(lambda" not in src


# --------------------------------------------------- py{} 在钩子里无需声明字段
def test_pyblock_hook_does_not_require_declared_fields():
    """行内 py{} 的 `$.链` 首段必须是已声明字段；数据级钩子里不是——`$.rows`
    只是全量数据的一个键（用户 2026-10-22 m00292 的钩子语义）。"""
    src, got = _build(
        "b5", {"shape": "list",
               "before": "= py{ return $.rows }"},
        {"rows": [[7, 6], [5, 4]]})
    assert "_py0(D['rows'])" in src, src
    assert got == [[7], [5]]
