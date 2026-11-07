# -*- coding: utf-8 -*-
"""`dcg.runtime._ro`（`$round`）的**逐值等价**回归。

背景：`_ro` 原来是 `Decimal(str(v)).quantize(1e-p, ROUND_HALF_EVEN)`，每次调用
都要建两个 `Decimal` 对象（实测 ~2.2 µs/call）。改成「十进制数字串舍入」快路径后
需要保证：**对任何 (v, p) 组合，输出与旧实现逐值相同（含类型）**。

本文件把旧实现完整内联为 `_ro_reference`，并用两类输入对拍：
  * 边界集：0.5 半值、负数半值、`-0.0`、`inf`/`-inf`/`nan`、2^53、1e20 上下、
    Decimal 上下文 28 位精度边界，精度 −30…40 全覆盖；
  * 随机集：10 万组 (v, p)，含均匀浮点、大整数、半值附近、次正规数、极大极小值。

比较口径与 `test_codegen_snapshot._call` 一致：`UNDEF` 比身份，异常只比类型。
"""

import math
import random
from decimal import Decimal as _DEC, ROUND_HALF_EVEN

import pytest

from dcg import runtime as rt


# --------------------------------------------------------------- 旧实现（对照）
def _ro_reference(v, p=0):
    """改动前的 `dcg.runtime._ro` 原样内联：`Decimal` 量化 + `ROUND_HALF_EVEN`。"""
    if v is rt.UNDEF:
        return rt.UNDEF
    try:
        p = int(p)
    except (TypeError, ValueError):
        return rt.UNDEF
    try:
        q = _DEC(1).scaleb(-p)
        r = _DEC(str(v)).quantize(q, rounding=ROUND_HALF_EVEN)
        f = float(r)
        return f if p > 0 else int(f)
    except (TypeError, ValueError, ArithmeticError):
        return rt.UNDEF


def _norm(v):
    """结果归一：`UNDEF` 比身份，NaN 单独归一，其余比类型 + 值。"""
    if v is rt.UNDEF:
        return ("undef",)
    if isinstance(v, float) and math.isnan(v):
        return ("nan",)
    return (type(v).__name__, v)


def _compare(v, p):
    try:
        want = _norm(_ro_reference(v, p))
    except BaseException as exc:  # noqa: BLE001 - 对拍就是要比异常
        want = ("raise", type(exc).__name__)
    try:
        got = _norm(rt._ro(v, p))
    except BaseException as exc:  # noqa: BLE001
        got = ("raise", type(exc).__name__)
    assert got == want, f"$round({v!r}, {p!r}): 新实现 {got!r} != 旧实现 {want!r}"


# --------------------------------------------------------------- 边界集
_EDGE_VALUES = [
    0, 1, -1, 2, 5, 10, 15, 25, 45, 50, 55, 95, 99, 105, 125, 135, 145,
    245, 250, 255, 265, 275, 475,
    # 恰好一半
    0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 3.5, -3.5, 4.5, -4.5,
    0.05, -0.05, 0.15, -0.15, 0.25, -0.25, 0.45, -0.45, 0.55, -0.55,
    0.005, -0.005, 0.0005, -0.0005, 0.0015, -0.0015, 0.0025, -0.0025,
    # 二进制表示不精确的经典值（`round(2.675, 2)` 会错的那些）
    2.675, -2.675, 1.005, 1.015, 8.835, 9.995, 99.95, 0.999,
    0.35, 0.65, 1.15, -1.15, 0.049999999999999996, 0.14999999999999999,
    # 零与符号零
    0.0, -0.0, -0,
    # 非有限
    float("inf"), float("-inf"), float("nan"),
    # 大整数与 2^53 / i64 边界
    2 ** 62, -(2 ** 63), 2 ** 53, 2 ** 53 + 1,
    10 ** 20, 10 ** 26, 10 ** 27, 10 ** 28, 10 ** 29, 10 ** 30, 10 ** 40,
    123456789012345678901234567890, -123456789012345678901234567890,
    # 浮点大值（1e20 是快路径上界）
    1e19, 9.999999999999999e19, 1e20, 1e26, 1e27, 1e28, 1e300, -1e300,
    # 浮点小值 / 次正规
    1e-5, 1e-6, 1e-7, 1e-20, 1e-300, 5e-324, 1.7976931348623157e-300,
    1.7976931348623157e308,
    # 精度边界
    1e15 + 0.5, 1e16 + 2.5, 1234567890123456.5, 9999999999999998.0,
    9007199254740993.0, 4503599627370496.5, 123456789.123456789,
    0.12345678901234567, 1.0000000000000002, 2.0000000000000004,
    9500000000000000.0,
    # 非数值
    "2.5", "abc", "", True, False, None, rt.UNDEF, 3.0, 2.0,
]


@pytest.mark.parametrize("p", list(range(-30, 41)))
def test_edge_values_match_reference(p):
    """边界集 × 精度 −30…40 全覆盖，逐值对拍。"""
    for v in _EDGE_VALUES:
        _compare(v, p)


def test_half_even_is_decimal_not_binary():
    """语义钉子：十进制半进偶，而不是二进制 `round()`。"""
    assert rt._ro(2.675, 2) == 2.68          # 内置 round 给 2.67
    assert rt._ro(0.5, 0) == 0               # 偶
    assert rt._ro(1.5, 0) == 2               # 偶
    assert rt._ro(2.5, 0) == 2               # 偶
    assert rt._ro(3.5, 0) == 4               # 偶
    assert rt._ro(-0.5, 0) == 0
    assert rt._ro(-1.5, 0) == -2
    assert rt._ro(-2.5, 0) == -2
    assert rt._ro(0.125, 2) == 0.12
    assert rt._ro(0.375, 2) == 0.38


def test_result_types_match_reference():
    """`p > 0` 给 float、`p == 0` 给 int —— 快路径不能把 float 退化成 int。"""
    assert type(rt._ro(2.0, 2)) is float
    assert type(rt._ro(2.0, 0)) is int
    assert type(rt._ro(2, 2)) is float
    assert type(rt._ro(2, 0)) is int
    assert type(rt._ro(2.5, 0)) is int
    assert rt._ro(5, 2) == 5.0


def test_undefined_and_non_numeric():
    assert rt._ro(rt.UNDEF) is rt.UNDEF
    assert rt._ro("abc", 2) is rt.UNDEF
    assert rt._ro(None, 2) is rt.UNDEF
    assert rt._ro(1.5, "x") is rt.UNDEF


def test_context_precision_boundary_is_undefined():
    """系数位数超过 Decimal 上下文精度（28）时旧实现给 undefined，必须保留。"""
    assert rt._ro(1000000000000000.5, 13) is rt.UNDEF
    assert _ro_reference(1000000000000000.5, 13) is rt.UNDEF


def test_random_values_match_reference():
    """10 万组随机 (v, p) 对拍。"""
    rng = random.Random(20261107)
    edge = _EDGE_VALUES
    for _ in range(100000):
        r = rng.random()
        p = rng.randint(-30, 40)
        if r < 0.22:
            v = rng.uniform(-1e9, 1e9)
        elif r < 0.38:
            v = rng.randint(-10 ** 18, 10 ** 18)
        elif r < 0.48:
            v = rng.randint(-10 ** 30, 10 ** 30)
        elif r < 0.66:
            # 半值附近：截断点正好落在 5 上或紧邻它
            k = rng.randint(-10 ** 6, 10 ** 6)
            v = k + rng.choice([0.5, -0.5, 0.05, -0.05, 0.005, -0.005,
                                0.25, -0.25, 0.75, -0.75, 0.0005, -0.0005])
        elif r < 0.76:
            v = rng.choice([1e-300, 1e300, 5e-324, 1.7976931348623157e308,
                            1e20, 1e19, 1e-20])
        elif r < 0.86:
            v = round(rng.uniform(-1e4, 1e4), rng.randint(0, 8))
        elif r < 0.94:
            v = rng.uniform(-1e16, 1e16)
        else:
            v = rng.choice(edge)
        _compare(v, p)


def test_repeated_quantum_is_cached_not_leaked():
    """量子缓存只增不减，但不该无限增长（p 超出 ±64 不入缓存）。"""
    before = len(rt._RO_Q)
    for p in (500, -500, 10 ** 6):
        rt._ro(1.5, p)
    assert len(rt._RO_Q) == before
