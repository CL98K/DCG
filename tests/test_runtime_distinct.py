# -*- coding: utf-8 -*-
"""`dcg.runtime._ds`（`$distinct`）的**逐元素等价**与保序回归。

背景：容器分支（`list`/`tuple`/`dict`/`set`）原本对**全部已收下元素**做 `_eq`
深比较，是 O(n²)：n=1e4 含 30% 对象要 3.1 s。改成按 `_sig` 分桶后，桶内仍用
`_eq` 复核，正确性依据是 `_sig` 文档串里的单向不变式：

    `_eq(v, w) is True`  ⇒  `_sig(v) == _sig(w)`

本文件把旧实现完整内联为 `_ds_reference`，用混合类型数组对拍，并单独钉住
保序、bool/int 分离、NaN 逐个保留、深嵌套与「算不出签名就降级」这几种语义。
"""

import math
import random

import pytest

from dcg import runtime as rt


# --------------------------------------------------------------- 旧实现（对照）
def _ds_reference(a):
    """改动前的 `dcg.runtime._ds` 原样内联（不含 Rust 分支）。"""
    c = type(a)
    if c is not list and c is not tuple:
        return rt.UNDEF if a is rt.UNDEF else a
    out = []
    ap = out.append
    seen = set()
    bools = set()
    slow = []
    add = seen.add
    addb = bools.add
    for v in a:
        t = type(v)
        if t is bool:
            if v in bools:
                continue
            ap(v)
            addb(v)
            continue
        if t is int or t is float:
            if v != v:
                ap(v)
                slow.append(v)
                continue
        elif t is not str and v is not None:
            for w in out:
                if rt._eq(v, w) is True:
                    break
            else:
                ap(v)
                slow.append(v)
            continue
        if v in seen:
            continue
        if slow:
            for w in slow:
                if rt._eq(v, w) is True:
                    break
            else:
                ap(v)
                add(v)
            continue
        ap(v)
        add(v)
    return out


def _norm(xs):
    """归一化以便断言：`UNDEF` 比身份，NaN 用哨兵（每个 NaN 都要保留）。"""
    if xs is rt.UNDEF:
        return rt.UNDEF
    out = []
    for v in xs:
        if isinstance(v, float) and math.isnan(v):
            out.append("<nan>")
        elif v is rt.UNDEF:
            out.append("<undef>")
        else:
            out.append(v)
    return out


def _assert_same(a):
    """对拍：结果逐元素相同（值 + 类型 + 顺序）。"""
    got = _norm(rt._ds(a))
    want = _norm(_ds_reference(a))
    assert len(got) == len(want), f"长度不同：{len(got)} != {len(want)}  for {a!r}"
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w and type(g) is type(w), (
            f"第 {i} 个元素不同：新 {g!r} != 旧 {w!r}  for {a!r}")


# --------------------------------------------------------------- 定值用例
def test_order_is_preserved():
    """保序：首次出现的位置决定输出顺序。"""
    assert rt._ds([3, 1, 3, 2, 1, 2]) == [3, 1, 2]
    assert rt._ds([{"a": 1}, {"b": 2}, {"a": 1}]) == [{"a": 1}, {"b": 2}]
    assert rt._ds([[1], [2], [1]]) == [[1], [2]]


def test_bool_and_int_are_never_equal():
    """`_eq` 里 bool 与非 bool 永不相等 —— 1 与 True 必须都保留。"""
    assert rt._ds([1, True, 1, True]) == [1, True]
    assert rt._ds([True, 1, False, 0]) == [True, 1, False, 0]
    assert rt._ds([0, False, 0.0]) == [0, False]
    # 0 与 0.0 在 `_eq` 里相等（同属 number），只留第一个
    assert rt._ds([0, 0.0]) == [0]


def test_each_nan_is_kept():
    """NaN 与任何值（含自身）都不相等，所以每个 NaN 都要保留。"""
    nan = float("nan")
    got = rt._ds([nan, nan, nan])
    assert len(got) == 3
    assert all(isinstance(x, float) and math.isnan(x) for x in got)


def test_container_semantics():
    assert rt._ds([[1, 2], [1, 2], (1, 2)]) == [[1, 2], (1, 2)]  # list != tuple
    assert rt._ds([{"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}]) == [{"a": [1, {"b": 2}]}]
    assert rt._ds([{"a": 1, "b": 2}, {"b": 2, "a": 1}]) == [{"a": 1, "b": 2}]
    assert rt._ds([{1, 2}, frozenset({2, 1})]) == [{1, 2}]  # set == frozenset
    assert rt._ds([None, None, "None"]) == [None, "None"]


def test_undefined_and_non_array():
    assert rt._ds(rt.UNDEF) is rt.UNDEF
    assert rt._ds(5) == 5
    assert rt._ds("ab") == "ab"


def test_deep_nesting_degrades_without_error():
    """嵌套超过 `_SIG_MAX_DEPTH` 时算不出签名，必须降级成线性扫描且结果正确。"""
    deep = []
    cur = deep
    for _ in range(200):
        nxt = []
        cur.append(nxt)
        cur = nxt
    got = rt._ds([deep, deep])
    assert len(got) == 1


def test_unhashable_custom_object_falls_back():
    """自定对象没有可信签名 —— 退回线性 `_eq`，结果与旧实现一致。"""
    class Box:
        def __init__(self, v):
            self.v = v

        def __eq__(self, other):
            return isinstance(other, Box) and self.v == other.v

        def __hash__(self):
            return hash(self.v)

    _assert_same([Box(1), Box(2), Box(1)])


def test_container_bucketed_but_scalar_still_scans_slow():
    """分桶后容器不再进 `slow`，标量那条 `if slow:` 反向扫描仍须等价。

    旧实现把每个容器都追加进 `slow`，于是后续**标量**会拿它做一次 `_eq`；
    新实现里正常容器只进 `buckets`。`_eq(标量, 容器)` 恒为 False（Python 的
    `1 == [1]` 为假），所以跳过这次比较不改变结果 —— 这里把它钉住。
    """
    _assert_same([[1], 1, [1], 1.0, (1,), 1, {"a": 1}, "1"])
    _assert_same([[1], [2], 1, [1], 2, [2], 1.0, 2.0])
    # 容器在前、标量在后，且标量恰好与容器「看起来」同形
    assert rt._ds([[1], 1]) == [[1], 1]
    assert rt._ds([{"a": 1}, "a", 1, [1], True]) == [{"a": 1}, "a", 1, [1], True]


def test_nan_inside_container_uses_python_identity_shortcut():
    """容器内 NaN 按 Python `==` 的「同对象即相等」语义 —— 与旧实现一致。

    `[nan] == [nan]` 在 CPython 里对**同一个** NaN 对象为真（列表比较先比身份），
    对两个不同的 NaN 对象为假。`_sig` 用元组承载，元组比较同样有身份短路，
    所以两侧语义天然对齐；这里钉住它，避免以后把 NaN 换成哨兵而悄悄改变行为。
    """
    nan = float("nan")
    a = [nan]
    assert rt._ds([a, a]) == [a]          # 同一对象 -> 去重
    _assert_same([a, a])
    _assert_same([[float("nan")], [float("nan")]])  # 不同对象 -> 都保留


def test_degraded_mode_still_dedups_against_bucketed():
    """降级后新容器走线性扫描，必须能命中**分桶期**已收下的元素。"""
    class Box:
        def __init__(self, v):
            self.v = v

        def __eq__(self, other):
            return isinstance(other, Box) and self.v == other.v

        def __hash__(self):
            return hash(self.v)

    # 先入桶（dict），再遇到算不出签名的 Box 触发降级，最后再放一个同值 dict
    a = [{"a": 1}, Box(1), {"a": 1}, {"a": 2}, {"a": 1}]
    _assert_same(a)
    assert rt._ds(a) == [{"a": 1}, Box(1), {"a": 2}]


def test_scalar_only_paths_unchanged():
    """纯标量输入不进桶路径，行为与旧实现逐值相同。"""
    _assert_same(list(range(100)))
    _assert_same([1, 1, 2, 2, 3] * 20)
    _assert_same(["a", "b", "a", None, None, "c"] * 10)
    _assert_same([1.5, 1.5, 2.5, True, False, 1] * 10)


# --------------------------------------------------------------- 随机对拍
def _gen_value(rng, depth=0):
    """生成一个混合类型的值；`depth` 控制嵌套。"""
    choices = ["int", "float", "str", "bool", "none"]
    if depth < 3:
        choices += ["list", "dict", "tuple"]
    kind = rng.choice(choices)
    if kind == "int":
        return rng.choice([rng.randint(-5, 5), rng.randint(-10 ** 6, 10 ** 6),
                           rng.randint(-2 ** 62, 2 ** 62)])
    if kind == "float":
        return rng.choice([round(rng.uniform(-5, 5), 2), rng.uniform(-1e6, 1e6),
                           float(rng.randint(-5, 5)), float("nan")])
    if kind == "str":
        return rng.choice(["a", "b", "", "ab", "1", "0"])
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "none":
        return None
    if kind == "list":
        return [_gen_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    if kind == "tuple":
        return tuple(_gen_value(rng, depth + 1) for _ in range(rng.randint(0, 3)))
    return {rng.choice(["a", "b", "c"]): _gen_value(rng, depth + 1)
            for _ in range(rng.randint(0, 3))}


def test_random_arrays_match_reference():
    """200 组随机混合数组（含 list/dict/嵌套/重复/NaN）逐元素对拍。"""
    rng = random.Random(20261107)
    for _ in range(200):
        n = rng.randint(0, 40)
        a = [_gen_value(rng) for _ in range(n)]
        _assert_same(a)


def test_random_with_high_duplicate_rate():
    """高重复率：桶内复核路径被反复走到的场景。"""
    rng = random.Random(4242)
    pool = [{"a": 1}, {"a": 1}, {"a": 2}, [1, 2], [1, 2], [1, 3],
            (1, 2), {1: "x"}, {1: "x"}, {"a": [1, {"b": 2}]},
            {"a": [1, {"b": 2}]}, None, "s", 1, 1.0, True]
    for _ in range(60):
        a = [rng.choice(pool) for _ in range(rng.randint(0, 60))]
        _assert_same(a)


def test_large_object_array_is_linear_not_quadratic():
    """回归护栏：n=2000 含 30% 对象必须在秒级以内（旧实现这里要约 240 s）。

    不断言具体毫秒数（机器差异），只断言它没退化成 O(n²) —— 旧的 3.1 s@1e4
    在这个规模上约 240 s，超时阈值 30 s 有 8 倍余量。
    """
    import time

    n = 2000
    a = []
    for i in range(n):
        if i % 10 < 3:
            a.append({"k": i // 3, "n": [i % 7]})
        else:
            a.append(i % 1000)
    t0 = time.perf_counter()
    out = rt._ds(a)
    dt = time.perf_counter() - t0
    assert dt < 30.0, f"$distinct n={n} 用了 {dt:.1f}s，疑似退化成 O(n²)"
    assert len(out) < len(a)
    _assert_same(a)
