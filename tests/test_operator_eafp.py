# -*- coding: utf-8 -*-
"""算子的纯 EAFP 语义（用户裁决 2026-10-26）。

判据只有一条：**Python 自己能算出来，就必须原样返回**；只有 Python 自己
抛异常的组合才进入失败路径 —— 那里再分三种结局：含 undefined → `undefined`；
除零 → `undefined`；其余 → 带算子名的 `TypeError`。

这条口径同时废掉了两种历史写法：
* 早期「成功路径末尾再判一次结果域」—— 会把 `True+1` / `'a'+'b'` / `[1]+[2]` 判死；
* 「`try` 之前先查 undefined」—— undefined 判断被推到了成功路径上，与 99%/1%
  的成本模型相反。

直接对 `dcg.runtime` 的 helper 做断言（不经过产物），这样失败信息指向具体算子，
也避免 codegen 的类型内联把 helper 换成裸运算、让测试失去覆盖。
"""

import pytest

from dcg.runtime import UNDEF as U
import dcg.runtime as rt


# ---------------------------------------------------------------- 算术：能算就算
@pytest.mark.parametrize(
    "fn, a, b, want",
    [
        (rt._ad, True, 1, 2),          # bool 是 int 子类 → Python 直接算
        (rt._ad, True, True, 2),
        (rt._ad, 1, 2, 3),
        (rt._ad, 1.5, 2, 3.5),
        (rt._ad, "a", "b", "ab"),      # str 拼接
        (rt._ad, [1], [2], [1, 2]),    # list 拼接
        (rt._sb, True, 1, 0),
        (rt._sb, True, False, 1),
        (rt._sb, 5, 3, 2),
        (rt._ml, True, 5, 5),
        (rt._ml, "ab", 2, "abab"),     # str * int
        (rt._ml, [0], 3, [0, 0, 0]),   # list * int
        (rt._dv, True, 2, 0.5),
        (rt._dv, 7, 2, 3.5),
        (rt._md, 7, 3, 1),
        (rt._md, True, 2, 1),
        (rt._pg, 2, 10, 1024),
        (rt._pg, True, 3, 1),          # 1 ** 3
    ],
)
def test_arithmetic_follows_python_on_success(fn, a, b, want):
    """Python 算得出来的组合，算子必须原样返回结果。"""
    got = fn(a, b)
    assert got == want and type(got) is type(want), f"{fn.__name__}({a!r}, {b!r}) → {got!r}"


@pytest.mark.parametrize(
    "fn, msg",
    [
        (rt._ad, "$add"),
        (rt._sb, "$subtract"),
        (rt._ml, "$multiply"),
        (rt._pg, "$power"),
    ],
)
def test_arithmetic_type_error_is_named(fn, msg):
    """Python 抛 TypeError 的组合：冒泡成带 JSONata 函数名的 TypeError。"""
    # `'a' * 1` 在 Python 里合法（str 重复），要真正触发 TypeError 得用
    # 两个都不支持该运算的组合：str 与 dict 相乘/相除、str 与 str 相减。
    with pytest.raises(TypeError) as ei:
        fn("a", {"k": 1})
    assert msg in str(ei.value)


def test_divide_by_zero_is_undefined_not_error():
    """除零仍是 undefined（契约 §4.4），不因放宽类型检查而变成异常。"""
    assert rt._dv(1, 0) is U
    assert rt._md(1, 0) is U


@pytest.mark.parametrize("fn", [rt._ad, rt._sb, rt._ml, rt._dv, rt._md, rt._pg])
def test_arithmetic_propagates_undefined(fn):
    """含 undefined 的运算返回 undefined —— 判断在失败路径里，结果不变。"""
    assert fn(U, 1) is U
    assert fn(1, U) is U
    assert fn(U, U) is U


@pytest.mark.parametrize("fn", [rt._ad, rt._sb, rt._ml, rt._dv, rt._md, rt._pg])
def test_undefined_check_is_not_on_success_path(fn):
    """源码形态守卫：`try:` 之前不得出现 `is UNDEF` 判断（用户要求）。

    这是形状断言而不是行为断言 —— 行为上「先判 undefined」与「失败后再判」
    对 undefined 输入等价，区别只在成功路径的成本与代码可读性。
    """
    import inspect

    src = inspect.getsource(fn)
    head = src.split("try:", 1)[0]
    assert "UNDEF" not in head, f"{fn.__name__} 的 try 之前仍有 UNDEF 判断：\n{head}"


# ---------------------------------------------------------------- 一元与比较
@pytest.mark.parametrize(
    "fn, a, want",
    [(rt._neg, True, -1), (rt._neg, 5, -5), (rt._pos, True, 1), (rt._pos, 5, 5)],
)
def test_unary_follows_python(fn, a, want):
    assert fn(a) == want


@pytest.mark.parametrize("fn", [rt._neg, rt._pos])
def test_unary_undefined_and_typeerror(fn):
    assert fn(U) is U
    assert fn("abc") is U  # Python 对 str 取负/取正抛 TypeError


@pytest.mark.parametrize(
    "fn, a, b, want",
    [
        (rt._lt, 1, 2, True),
        (rt._lt, True, 2, True),        # bool 与 int 可比（Python 语义）
        (rt._lt, "a", "b", True),
        (rt._le, 2, 2, True),
        (rt._gt, 3, 1, True),
        (rt._ge, 3, 3, True),
    ],
)
def test_comparison_follows_python(fn, a, b, want):
    assert fn(a, b) is want


@pytest.mark.parametrize("fn", [rt._lt, rt._le, rt._gt, rt._ge])
def test_comparison_typeerror_and_undefined(fn):
    assert fn("a", 1) is U   # Python 不自洽的混比 → TypeError → undefined
    assert fn(U, 1) is U
    assert fn(1, U) is U


# ---------------------------------------------------------------- 数值单参函数
@pytest.mark.parametrize(
    "fn, a, want",
    [
        (rt._ab, True, 1),      # abs(True) == 1
        (rt._ab, -5, 5),
        (rt._fl, True, 1),      # math.floor(True) == 1
        (rt._fl, 2.7, 2),
        (rt._ce, True, 1),
        (rt._ce, 2.1, 3),
        (rt._pw, True, 1),
    ],
)
def test_numeric_helpers_follow_python(fn, a, want):
    got = fn(a) if fn is not rt._pw else fn(a, 2)
    assert got == want


@pytest.mark.parametrize("fn", [rt._ab, rt._fl, rt._ce])
def test_numeric_helpers_undefined_and_typeerror(fn):
    assert fn(U) is U
    assert fn("abc") is U


# ---------------------------------------------------------------- 端到端
def _run(schema, rows):
    import dcg

    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<eafp>", "exec"), ns)
    return ns["run"](rows), src


def _schema(expr):
    return {
        "name": "eafp",
        "input": {"source": "$"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"v": {"type": "any", "expr": expr}}},
    }


@pytest.mark.parametrize(
    "expr, a, want",
    [
        ("$.a + 1", True, 2),
        ("$.a + $.a", True, 2),
        ("$.a < 2", True, True),
    ],
)
def test_end_to_end_bool_operand(expr, a, want):
    """端到端：`bool` 参与算术/比较不再报错（用户可观测的行为变化）。"""
    out, _ = _run(_schema(expr), [{"a": a}])
    assert out[0][0] == want


def test_end_to_end_str_add_still_type_error():
    """端到端：`'a' + 1` 仍然响亮失败（Python 自己抛 TypeError）。"""
    with pytest.raises(TypeError):
        _run(_schema("$.a + 1"), [{"a": "x"}])


# ---------------------------------------------------------------- $boolean（D-BOOL-01）
def test_boolean_helper_follows_jsonata_truthiness():
    """`_B` 必须是 JSONata 1.8 真值表，而不是 `bool()` 或 'true'/'false' 解析器。

    修复 D-BOOL-01 前：`_B('abc')` 是 undefined、`_B('false')` 是 False、
    `_B(None)` 是 None、`_B([])` 是 undefined，与内联 `bool()` 的结果分裂。
    """
    cases = [
        (True, True), (False, False),
        (1, True), (0, False), (1.5, True), (0.0, False),
        ("", False), ("abc", True),
        # 字符串**不解析** 'true'/'false'：非空即真
        ("false", True), ("true", True),
        (None, False), ([], False), ([0], True), ({}, False), ({"a": 1}, True),
    ]
    for value, want in cases:
        got = rt._B(value)
        assert got is want, f"_B({value!r}) → {got!r}，期望 {want!r}"
    # undefined 是「没有值」，原样传播（出口再转 None），不是 false
    assert rt._B(U) is U


def test_boolean_inline_and_helper_agree():
    """同一表达式「可证 → 内联 bool()」与「不可证 → _B」两条路径结果必须一致。"""
    schema = {
        "name": "bool2",
        "input": {"source": "$"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            # 字面量：真值域可证 → 内联 bool()
            "inline_str": {"type": "bool", "expr": "$boolean('abc')"},
            "inline_empty": {"type": "bool", "expr": "$boolean('')"},
            "inline_num": {"type": "bool", "expr": "$boolean(1)"},
            # `$.x` 未声明类型 → 走 helper `_B`
            "helper": {"type": "bool", "expr": "$boolean($.x)"},
        }},
    }
    out, src = _run(schema, [{"x": "abc"}])
    assert "bool(" in src and "_B(" in src, src
    # 内联三条是常量：分别对 'abc' / '' / 1 的 JSONata 真值
    assert out[0][0] is True and out[0][1] is False and out[0][2] is True
    # helper 对同一批标量值必须给出 JSONata 真值
    for value, want in [("abc", True), ("false", True), ("", False),
                        (1, True), (0, False), (True, True), (False, False)]:
        got, _ = _run(schema, [{"x": value}])
        assert got[0][3] is want, f"_B({value!r}) → {got[0][3]!r}，期望 {want!r}"


def test_boolean_and_not_share_truthiness_and_propagate_undefined():
    """`$boolean`（`_B`）与 `$not`（`_nt`）必须共用同一 `_tru` 真值口径。"""
    # undefined：两者都原样传播，不转成布尔
    assert rt._B(U) is U
    assert rt._nt(U) is U
    # 假值：`_B` 为 False，`_nt` 为 True（同一 `_tru` 判定）
    for value in (None, [], {}, "", 0, 0.0, False):
        assert rt._B(value) is False, f"_B({value!r})"
        assert rt._nt(value) is True, f"_nt({value!r})"
    # 真值：`_B` 为 True，`_nt` 为 False
    for value in ("x", "false", [0], {"a": 1}, 2, True):
        assert rt._B(value) is True, f"_B({value!r})"
        assert rt._nt(value) is False, f"_nt({value!r})"


# ---------------------------------------------------------------- $distinct（_ds 快路径）
def _ds_pairwise_reference(a):
    """`_ds` 的成对 `_eq` 参考实现（快路径改造前的原样逻辑）。

    改造后的 `_ds` 走「哈希键快路径 + 不可哈希兜底」，这条参考实现保证两者的
    去重结果逐位一致：凡是快路径能判定重复的，参考实现也必须判定重复。
    """
    c = type(a)
    if c is not list and c is not tuple:
        return U if a is U else a
    out = []
    for v in a:
        for w in out:
            if rt._eq(v, w) is True:
                break
        else:
            out.append(v)
    return out


def _same_seq(x, y):
    """逐值比较，NaN 视为相等（NaN 元素彼此独立出现，位置必须对上）。"""
    if len(x) != len(y):
        return False
    for a, b in zip(x, y):
        if type(a) is not type(b):
            return False
        if a != a and b != b:
            continue
        if a != b:
            return False
    return True


def test_distinct_keeps_eq_bool_rule():
    """`_eq` 的 bool 规则必须在快路径里保留：`True` 与 `1` 不是同一个值。

    直接换 `set()` 的实现会在这里静默出错 —— `True == 1` 且哈希相同，
    但 `_eq(True, 1)` 是 False（`dcg/runtime.py:289`）。
    """
    assert rt._ds([True, 1, 1.0, False, 0, 0.0]) == [True, 1, False, 0]
    assert rt._ds([1, True, 1.0, False]) == [1, True, False]
    # 同类内部仍按原生 `==` 合并：`1` 与 `1.0` 是同一个值
    assert rt._ds([1, 1.0, 2.0, 2]) == [1, 2]
    assert rt._ds([True, True, False, False]) == [True, False]


def test_distinct_handles_unhashable_and_nan():
    """不可哈希元素走兜底扫描；NaN 与自身不等，每个都独立保留。"""
    assert rt._ds([[1], [1], 1, "1", 1.0]) == [[1], 1, "1"]
    assert rt._ds([{"a": 1}, {"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]
    nan = float("nan")
    got = rt._ds([nan, nan, 1, 1.0])
    assert len(got) == 3 and got[0] != got[0] and got[1] != got[1] and got[2] == 1
    # undefined 在 `_eq` 下与任何值都不等，因此每次都独立保留（旧行为）
    assert rt._ds([None, None, U, U]) == [None, U, U]


def test_distinct_matches_pairwise_reference():
    """固定种子随机混合样本：快路径结果与成对 `_eq` 参考实现逐值一致。"""
    import random

    pool = [
        0, 1, -1, 2, 1.0, 2.0, 0.0, -0.0, True, False, None,
        "", "a", "b", "A", "1", U, float("nan"), 2 ** 63, 10 ** 20,
        [1], [1, 2], [], {"a": 1}, {}, (1,), (1, 2),
    ]
    rng = random.Random(20261031)
    for _ in range(400):
        arr = [rng.choice(pool) for _ in range(rng.randint(0, 8))]
        got = rt._ds(arr)
        want = _ds_pairwise_reference(arr)
        assert _same_seq(got, want), f"_ds({arr!r}) → {got!r}，参考实现 → {want!r}"

