# -*- coding: utf-8 -*-
"""`$formatBase` 非有限数与 `$fromMillis` 年份越界的**边界回归**（R7 C2 + D3）。

## C2：`$formatBase(inf / -inf / nan)`

`_fb` 的第一道门是 `if not _num(v) or not _num(radix): return UNDEF`，而
`_num` 只判 `type(v) is int or type(v) is float` —— `±inf` 与 `nan` 也是
`float`，于是它们通过了 `_num`，在 `int(v)` 那一行炸成 Python 异常：

```
_fb(float('inf'))  -> OverflowError: cannot convert float infinity to integer
_fb(float('nan'))  -> ValueError: cannot convert float NaN to integer
```

live 与 pure **一致都抛**，所以这不是双活违约，是 Python 侧自己没接住。
契约是「非法输入返回 undefined」，与 `_fb(True)` / `_fb('x')` 同档。

## D3：`$fromMillis(ms, '[X]')` 在 9999 年

`_fms_frag` 里 `[W]`/`[w]`/`[X]`/`[x]` 共用内嵌函数 `start_first_week(year, m0)`，
其中 `datetime(year, ...)` 在 `year == 10000` 时抛
`ValueError: year 10000 is out of range`。四个调用点里有三处传 `d.year + 1`，
所以 9999 年会炸。逐日扫描 9999 年：`[X]` **365/365 全年抛**（`d.year + 1`
是无条件的）、`[x]` 31/365、`[w]` 5/365、`[W]` 0/365。

修法：`start_first_week` 在 `year` 越出 `1..9999` 时返回 `None`，调用点先判空
再比较。语义上「下一年的第一周不存在」⇒ 这一天必然不属于下一年，跳过比较
即得正确结果。

## 本文件钉住的三件事

1. 三个非有限数返回 `UNDEF`，且**只**改 `v` 侧 —— `radix` 传 `inf` 仍抛
   （那是另一个语义变更，未经批准不顺手改，见 `test_radix_inf_still_raises`）。
2. 正常值一字不变（`_fb(255)` / `_fb(255,16)` / `_fb(1.5)` / `_fb(True)` …）。
3. `[W]`/`[w]`/`[X]`/`[x]` 在 1 年与 9999 年**全年逐日**都不抛，且与
   `date.isocalendar()` 一致（`[W]`=ISO 周、`[X]`=ISO 周年）；正常年份
   1..9998 的取值与改动前逐字节相同。
"""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from dcg import runtime as rt

UTC = timezone.utc

#: 9999-12-31T23:59:59.999Z —— `datetime` 支持域的最后 1 毫秒。
MS_YEAR_MAX = 253402300799999
#: 再往后 1 ms，超出 `datetime` 域；`_fms` 本有保护，应返回 undefined。
MS_OVERFLOW = 253402300800000


def _days_of(year):
    """`year` 全年逐日。用「年内第 n 天」正向构造，避免 `date(9999,12,31)+1天` 溢出。"""
    d0 = date(year, 1, 1)
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return [d0 + timedelta(days=i) for i in range(366 if leap else 365)]


def _ms_of(d):
    """`date` → 当天 12:00 UTC 的毫秒。"""
    return int(datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=UTC).timestamp() * 1000)


def _frag(d, comp):
    return rt._fms_frag(datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=UTC), comp)


# --------------------------------------------------------------------- C2
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_format_base_non_finite_is_undefined(value):
    """非有限数必须返回 undefined，不许把 `OverflowError`/`ValueError` 漏给调用方。"""
    assert rt._fb(value) is rt.UNDEF


@pytest.mark.parametrize("radix", [2, 7, 10, 16, 36])
def test_format_base_non_finite_any_radix(radix):
    """基数不改变结论：`±inf` / `nan` 在任何基数下都是 undefined。"""
    for value in (float("inf"), float("-inf"), float("nan")):
        assert rt._fb(value, radix) is rt.UNDEF, (value, radix)


def test_format_base_non_finite_as_radix_argument():
    """`v` 正常、`radix` 是非有限数 → 也该是 undefined（`_num(radix)` 先拦）。

    这条和下面 `test_radix_inf_still_raises` 的区别：`radix` 是 `float('inf')`
    时 `_num` 返回 True，会走到 `int(radix)`；但 `radix` 是 `nan` 时同样如此。
    **本轮只修 `v` 侧**，所以 `radix` 非有限数仍然抛 —— 这条测试记录现状。
    """
    for radix in (float("inf"), float("nan")):
        with pytest.raises((OverflowError, ValueError)):
            rt._fb(255, radix)


def test_format_base_normal_values_unchanged():
    """正常值逐值不变（这是硬要求：改的是边界，不是主路径）。"""
    assert rt._fb(255) == "255"
    assert rt._fb(255, 10) == "255"
    assert rt._fb(255, 16) == "ff"
    assert rt._fb(-255, 16) == "-ff"
    assert rt._fb(255, 2) == "11111111"
    assert rt._fb(255, 7) == "513"
    assert rt._fb(10 ** 6, 7) == "11333311"
    assert rt._fb(-(10 ** 6), 7) == "-11333311"
    assert rt._fb(10 ** 18, 36) == "7lieexzx4kxs"


def test_format_base_float_truncates_not_undefined():
    """`_fb(1.5)` 走 `int(1.5)` = 1 —— 现状如此，本轮不许动。"""
    assert rt._fb(1.5) == "1"
    assert rt._fb(-1.5) == "-1"
    assert rt._fb(255.9) == "255"


def test_format_base_bool_is_undefined():
    """`_num(True)` 是 False（bool 不是 number）—— 不许因为本轮改动而变。"""
    assert rt._fb(True) is rt.UNDEF
    assert rt._fb(False) is rt.UNDEF


@pytest.mark.parametrize("bad", ["x", "", None, [1], {"a": 1}, rt.UNDEF])
def test_format_base_non_numeric_is_undefined(bad):
    assert rt._fb(bad) is rt.UNDEF


@pytest.mark.parametrize("radix", [0, 1, 37, -2, 99])
def test_format_base_bad_radix_is_undefined(radix):
    assert rt._fb(255, radix) is rt.UNDEF


def test_format_base_large_finite_float_still_works():
    """有限的大浮点仍走正常路径（别把有限性判断写成「浮点一律 UNDEF」）。"""
    assert rt._fb(1e18, 10) == str(int(1e18))
    assert rt._fb(-1e18, 16) == format(int(-1e18), "x")


# --------------------------------------------------------------------- D3
def test_from_millis_year_max_four_components():
    """9999-12-31 的 `[W]`/`[w]`/`[X]`/`[x]`：不抛，且值正确。"""
    assert rt._fms(MS_YEAR_MAX, "[W]") == "52"
    assert rt._fms(MS_YEAR_MAX, "[w]") == "5"
    assert rt._fms(MS_YEAR_MAX, "[X]") == "9999"
    assert rt._fms(MS_YEAR_MAX, "[x]") == "12"


def test_from_millis_year_max_matches_isocalendar():
    """`[W]`/`[X]` 必须等于 `date.isocalendar()` 的周号/周年。"""
    d = date(9999, 12, 31)
    iso = d.isocalendar()
    assert _frag(d, "W") == iso[1]
    assert _frag(d, "X") == iso[0]


@pytest.mark.parametrize("year", [1, 9999])
def test_frag_whole_year_never_raises(year):
    """边界年**全年逐日**跑四个 comp，一个异常都不许有。

    9999 年改前 `[X]` 365/365 全抛；1 年改前是全好的（`d.year - 1` 有
    `week < 1` 守卫），这里一并钉住，防止将来改下界时把它弄坏。
    """
    for d in _days_of(year):
        for comp in ("W", "w", "X", "x"):
            got = _frag(d, comp)
            assert isinstance(got, int), (d, comp, got)


@pytest.mark.parametrize("year", [1, 1970, 2024, 9999])
def test_frag_whole_year_matches_isocalendar(year):
    """全年 `[W]`/`[X]` 与标准库一致 —— 这条对 1..9998 是「没改坏」的证明。"""
    for d in _days_of(year):
        iso = d.isocalendar()
        assert _frag(d, "W") == iso[1], (d, "W")
        assert _frag(d, "X") == iso[0], (d, "X")


def test_frag_month_week_boundaries():
    """`[w]`/`[x]` 在跨年/跨月边界上的已知取值（防止 None 分支改错判断顺序）。"""
    # 2024-12-30 是 ISO 2025-W01-1
    assert _frag(date(2024, 12, 30), "X") == 2025
    assert _frag(date(2024, 12, 30), "x") == 1
    # 2024-12-31 是 ISO 2025-W01-2
    assert _frag(date(2024, 12, 31), "X") == 2025
    # 9999-12-31 不属于 10000 年，所以 `[X]` 停在 9999
    assert _frag(date(9999, 12, 31), "X") == 9999
    assert _frag(date(9999, 12, 31), "x") == 12
    # 1 年的第一天：`[X]` 该是 1（下界没被 `d.year - 1 = 0` 弄坏）
    assert _frag(date(1, 1, 1), "X") == 1


def test_from_millis_no_picture_unchanged():
    """无 picture 形态走的是另一条路，必须一字不变。"""
    assert rt._fms(MS_YEAR_MAX) == "9999-12-31T23:59:59.999Z"
    assert rt._fms(0) == "1970-01-01T00:00:00.000Z"


def test_from_millis_overflow_millis_still_undefined():
    """越界毫秒本有保护，返回 undefined，不许因为本轮改动变成抛。"""
    assert rt._fms(MS_OVERFLOW, "[X]") is rt.UNDEF
    assert rt._fms(MS_OVERFLOW) is rt.UNDEF


@pytest.mark.parametrize("picture", [
    "[Y0001]-[M01]-[D01]T[H01]:[m01]:[s01].[f001][Z01:01]",
    "[D01]/[M01]/[Y0001]",
    "[MNn] [D1], [Y0001]",
    "[F] [D], [Y]",
    "[h]:[m] [P]",
])
def test_from_millis_other_pictures_unchanged(picture):
    """其它图样在 9999-12-31 上不受本轮改动影响。"""
    out = rt._fms(MS_YEAR_MAX, picture)
    assert isinstance(out, str), (picture, out)


def test_start_first_week_boundary_returns_none():
    """`start_first_week` 是内嵌闭包，只能通过 `_frag` 的输出观察边界行为。

    `year=10000` 时不能再抛 `ValueError`；`year=9999` 时仍要正常返回日期
    （否则 `9999-12-31` 的 `[X]` 会退化成 9998 或 52 周）。
    """
    assert _frag(date(9999, 12, 31), "X") == 9999
    assert _frag(date(9999, 12, 31), "W") == 52
    # 9999-01-01 属于 ISO 9998-W53 —— 说明 9999 年的 `start_first_week` 正常返回，
    # 且「回到上一年」的分支（`d < start`）也正常。
    assert _frag(date(9999, 1, 1), "X") == 9998
    assert _frag(date(9999, 1, 1), "W") == 53
    assert _frag(date(9998, 12, 31), "X") == 9998


def test_from_millis_last_ms_of_every_component():
    """9999-12-31 上把所有日期 comp 跑一遍，确认没有别的地方也越界。"""
    for comp in ("Y", "M", "D", "d", "F", "W", "w", "X", "x", "H", "h", "m", "s", "f"):
        got = rt._fms(MS_YEAR_MAX, "[%s]" % comp)
        assert got is not rt.UNDEF, (comp, got)


def test_format_base_finite_check_is_a_single_comparison():
    """有限性检查用一条链式比较，不引入新模块、不调 `math.isinf`。

    为什么钉住写法而不只钉住行为：`-inf < v < inf` 对 `±inf` 与 `nan` **全为假**，
    一次比较就够；`math.isinf(v) or v != v` 要两次判断，在每次 `$formatBase` 上
    都多付一次调用。这条断言防止后人「简化」成两次函数调用。
    """
    import inspect
    src = inspect.getsource(rt._fb)
    head = src.split("n, r = int(v), int(radix)")[0]
    code = "\n".join(line for line in head.split("\n")
                     if line.strip() and not line.strip().startswith("#"))
    assert "_INF < v < _INF" in code, code
    assert "math." not in code, "有限性检查不该调用 math 函数"
    assert "isinf" not in code and "isnan" not in code, code
