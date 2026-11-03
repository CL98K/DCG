# -*- coding: utf-8 -*-
"""JSONata 2.2.2 对齐回归：`?:`/`??`、`$number` 进制、9 个新函数、`$formatNumber` picture。

这些用例的期望值全部来自 `jsonata@2.2.2` 实测（仓库外的 node 探针现取），
不是凭文档猜的。DCG 与官方的 7 处已知模型差异（见 `JSONata 语法.md` §1.4.2）
在本文件里**显式**写成 DCG 侧的期望值，避免「照着官方改回塌缩」。

文末的 `AUDIT_A` 是「全面复盘校验」的 A/B 档回归（用户 m06297 → m06592）：
`$number` 前导零、`$pad` 循环填充、`$trim` 内部空白、`$spread` 语义、
`$fromMillis` picture 全分量、`$formatNumber` 图样族与 `options`。
"""
import pytest

import dcg
from dcg import Unsupported, generate
from dcg.jx import compile_expr
import dcg.runtime as rt


def run(expr, data=({},)):
    """把表达式编成单字段 schema、执行，返回该字段的取值。"""
    schema = {
        "name": "p322_" + str(abs(hash(expr)) % 1000000),
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"v": {"type": "object", "expr": expr}}},
    }
    src = generate(schema)
    ns = {}
    exec(compile(src, "<p322>", "exec"), ns)
    # output.shape=list 的行就是字段值列表，单字段时 row[0] 即该字段的值
    return [row[0] for row in ns["run"](list(data))]


# --------------------------------------------------------------- ?: / ??
ELVIS = [
    ("1 ?: 2", 1),
    ("0 ?: 2", 2),
    ("false ?: 2", 2),
    ("true ?: 2", True),
    ("null ?: 2", 2),
    ("'' ?: 2", 2),
    ("[] ?: 2", 2),
    ("{} ?: 2", 2),
    ("0 ?: 1 ?? 2", 1),        # 同级左优先、右结合
    ("0 ?? 1 ?: 7", 0),
    ("0 ?: 1 + 1", 2),         # 结合力低于 +
    ("1 ?: 1 + 1", 1),
    ("0 ?? 1 + 1", 0),
    ("0 ?? 1 ? 2 : 3", 0),     # 结合力低于三元
    ("0 ?: 1 ? 2 : 3", 2),
    ("1 ?: $sqrt(-1)", 1),     # 右侧惰性
    ("1 ?? $sqrt(-1)", 1),
]

COALESCE = [
    ("1 ?? 2", 1),
    ("0 ?? 2", 0),
    ("false ?? 2", False),
    ("null ?? 2", None),       # null 不算缺失
    ("'' ?? 2", ""),
    ("[] ?? 2", []),
    ("{} ?? 2", {}),
    ("'x' ?? 2", "x"),
]


@pytest.mark.parametrize("expr,want", ELVIS)
def test_elvis_operator(expr, want):
    assert run(expr)[0] == want


@pytest.mark.parametrize("expr,want", COALESCE)
def test_coalesce_operator(expr, want):
    assert run(expr)[0] == want


def test_coalesce_right_side_is_lazy():
    """右值只能在需要时求值 —— 否则 `1 ?: $sqrt(-1)` 会拿到 undefined。"""
    assert run("1 ?: $sqrt(-1)")[0] == 1
    assert run("1 ?? $sqrt(-1)")[0] == 1


def test_coalesce_ir_and_binops():
    from dcg import ir

    assert "?:" in ir.BIN_OPS and "??" in ir.BIN_OPS
    node = compile_expr("1 ?: 2")
    assert node[0] == "bin" and node[1] == "?:"
    node = compile_expr("1 ?? 2")
    assert node[0] == "bin" and node[1] == "??"


# --------------------------------------------------------------- $number 进制
@pytest.mark.parametrize("text,want", [
    ("0x1F", 31), ("0X1F", 31), ("0x1f", 31),
    ("0b101", 5), ("0B101", 5),
    ("0o17", 15), ("0O17", 15),
    ("0x0", 0), ("0b0", 0), ("0o0", 0),
])
def test_number_accepts_radix_prefixes(text, want):
    assert rt._N(text) == want


@pytest.mark.parametrize("text", [
    "0x", "0b", "0o", "0b2", "0o8", "-0x10", "+0x10", " 0x1F", "Infinity", "+1",
])
def test_number_rejects_bad_radix(text):
    assert rt._N(text) is rt.UNDEF


# --------------------------------------------------------------- $contains
def test_contains_undefined_propagates():
    assert rt._cn("abc", rt.UNDEF) is rt.UNDEF
    assert rt._cn(rt.UNDEF, "a") is rt.UNDEF
    assert rt._cn("abc", "b") is True


# --------------------------------------------------------------- $match
MATCH = [
    ("$match('ababbabbcc', /a(b+)/)",
     [{"match": "ab", "index": 0, "groups": ["b"]},
      {"match": "abb", "index": 2, "groups": ["bb"]},
      {"match": "abb", "index": 5, "groups": ["bb"]}]),
    # DCG 无序列塌缩：单条命中是单元素数组，官方是裸对象（§1.4.3 第 2 条）
    ("$match('abc', /b/)", [{"match": "b", "index": 1, "groups": []}]),
    ("$match('abc', /z/)", "undefined"),
    ("$match('a1b2', /\\d/)",
     [{"match": "1", "index": 1, "groups": []},
      {"match": "2", "index": 3, "groups": []}]),
    ("$match('abc', /b/, 1)", [{"match": "b", "index": 1, "groups": []}]),
    ("$match('abc', /b/, 0)", "undefined"),
    ("$match('ABC', /abc/i)", [{"match": "ABC", "index": 0, "groups": []}]),
    # 未参与的可选组给 null（与官方一致）
    ("$match('a', /(a)(z)?/)", [{"match": "a", "index": 0, "groups": ["a", None]}]),
]


@pytest.mark.parametrize("expr,want", MATCH)
def test_match(expr, want):
    got = run(expr)[0]
    if want == "undefined":
        assert repr(got) == "undefined"
    else:
        assert got == want


@pytest.mark.parametrize("expr", [
    "$match('abc', 'b')",      # 第二实参必须是正则字面量
    "$match('abc')",
    "$match('abc', /b/, 1, 2)",
    "$match('abc', /b/g)",     # 不支持 g 标志
    "$match('abc', //)",       # 空正则在 2.2.2 是语法错误
])
def test_match_rejects_bad_forms(expr):
    with pytest.raises((Unsupported, Exception)):
        run(expr)


def test_bare_regex_still_rejected():
    """正则字面量只在 `$match` 第二实参位置有意义，独立使用仍结构性拒绝。"""
    with pytest.raises(Unsupported):
        compile_expr("/ab/")


# --------------------------------------------------------------- 其他新函数
NEW_FUNCS = [
    ("$zip([1,2],[3,4],[5,6])", [[1, 3, 5], [2, 4, 6]]),
    ("$zip([1,2],[3])", [[1, 3]]),
    ("$clone({'a':[1,2],'b':{'c':3}})", {"a": [1, 2], "b": {"c": 3}}),
    ("$encodeUrl('a b?c=1&d=2#f')", "a%20b?c=1&d=2#f"),
    ("$decodeUrl('a%20b?c=1&d=2')", "a b?c=1&d=2"),
    ("$decodeUrl('a%2Fb')", "a%2Fb"),
    ("$formatInteger(1234,'0000')", "1234"),
    ("$formatInteger(-5,'000')", "-005"),
    ("$formatInteger(1990,'I')", "MCMXC"),
    ("$formatInteger(1234567,'#,##0')", "1,234,567"),
    ("$parseInteger('005','000')", 5),
    ("$parseInteger('IV','I')", 4),
    ("$parseInteger('1,234','#,##0')", 1234),
    ("$fromMillis(0)", "1970-01-01T00:00:00.000Z"),
    ("$fromMillis(0,'[Y0001]-[M01]-[D01]')", "1970-01-01"),
    ("$toMillis('2024-01-01T00:00:00Z')", 1704067200000),
    ("$toMillis('2024-01-01T00:00:00+08:00')", 1704038400000),
]


@pytest.mark.parametrize("expr,want", NEW_FUNCS)
def test_new_functions(expr, want):
    assert run(expr)[0] == want


def test_clone_is_deep_copy():
    assert rt._cl(1) is rt.UNDEF
    assert rt._cl(None) is rt.UNDEF
    src = {"a": [1, 2], "b": {"c": 3}}
    out = rt._cl(src)
    assert out == src and out is not src and out["a"] is not src["a"]


ROMAN_REF = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
             (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
             (4, "IV"), (1, "I"))


def _roman_slow(n):
    """逐档 `while` 追加的参考实现 —— 只用来对拍，量级受限于本测试的入参。"""
    if n <= 0:
        return ""
    out = []
    for val, sym in ROMAN_REF:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def test_roman_num_matches_the_stepwise_definition():
    """`_roman_num` 的「整段相乘」写法必须与逐档追加的定义等价。"""
    for n in list(range(-3, 4001)) + [4999, 9999, 12345, 100000, 1000000]:
        assert rt._roman_num(n) == _roman_slow(n), n
    assert rt._roman_num(1994) == "MCMXCIV"
    assert rt._roman_num(4000) == "MMMM"
    assert rt._roman_num(1000000) == "M" * 1000


def test_format_integer_roman_does_not_abort_on_huge_input():
    """`$formatInteger(10**21, "I")` 必须抛可捕获的 `MemoryError`，不能让进程 abort。

    旧写法 `while n >= val: out.append(sym)` 会逐个追加大约 10**18 个 `M`：
    列表一路涨到 32 GB，本机实测被系统直接杀掉（`memory allocation of 34359738368
    bytes failed`，退出码 -1073740791），连 `try/except` 都来不及。改成
    `sym * k` 之后代价只与输出长度成正比，于是同一个实参干净地抛 `MemoryError`。
    参考实现 `jsonata@2.2.2` 在同一量级抛的是 `RecursionError`，两边都拿不到结果，
    但「抛异常」和「进程消失」不是一回事。
    """
    with pytest.raises(MemoryError):
        rt._fgi(10 ** 21, "I")


def test_zip_rejects_non_sequence():
    assert rt._zp([1], 2) is rt.UNDEF
    assert rt._zp() is rt.UNDEF


def test_decode_url_rejects_malformed():
    assert rt._du("%") is rt.UNDEF
    assert rt._du("a%2") is rt.UNDEF
    assert rt._du("a%GGb") is rt.UNDEF


def test_to_millis_rejects_bad_input():
    assert rt._tms("bogus") is rt.UNDEF
    assert rt._tms(123) is rt.UNDEF


# --------------------------------------------------------------- $formatNumber picture
FNM = [
    (1234.5678, "#,##0.00", "1,234.57"),
    (1234.5678, "0", "1235"),
    (1234, "0,000", "1,234"),
    (12, "000", "012"),
    (1234, "00000", "01234"),
    (123456789, "#,##0", "123,456,789"),
    (0.5, "0%", "50%"),
    (0.5, "0.0%", "50.0%"),
    (0.5, "0\u2030", "500\u2030"),
    (-1234.5, "#,##0.00;(#,##0.00)", "(1,234.50)"),
    (-5, "0;(0)", "(5)"),
    (5, "0;(0)", "5"),
    (0, "0;(0)", "0"),
    (1234, "0.0e0", "1.2e3"),
    (0, "0e0", "0e0"),
    (12345, "0.00e0", "1.23e4"),
    (0.00012, "0.0e0", "1.2e-4"),
    (1234, "$#,##0.00", "$1,234.00"),
    (1234, "#,##0.00 USD", "1,234.00 USD"),
    (0.25, "#.##", ".25"),
    (1.5, "#.##", "1.5"),
    (0, "#.##", ".0"),
    (0, "#", "0"),
    (-0.4, "#", "-0"),
    (1234, ".##", "1234.0"),
    (1234, "#,##0.", "1,234"),
    (1.005, "0.00", "1.00"),
    (2.675, "0.00", "2.68"),
    (1.25, "0.0", "1.2"),
    (1.35, "0.0", "1.4"),
    (0.015, "0%", "2%"),
    (999.995, "0.00", "1000.00"),
    (-0.001, "0.00", "-0.00"),
    (1e21, "0", "1e+21"),
    (1e-7, "0.00", "0.00"),
    (0, "0.0", "0.0"),
]


@pytest.mark.parametrize("value,picture,want", FNM)
def test_format_number_picture(value, picture, want):
    assert rt._fnm(value, picture) == want


def test_format_number_rejects_bool_and_non_string_picture():
    assert rt._fnm(True, "0") is rt.UNDEF
    assert rt._fnm(1, 0) is rt.UNDEF


# ------------------------------------------------- 审计修复（A/B 档）
# 期望值全部由 `jsonata@2.2.2` 现取。`MISSING` 标记「官方返回 undefined 或抛错」，
# 与 JSON `null`（Python 的 `None`）区分开——两者在 DCG 侧结果不同：
# 前者是 `UNDEF`，后者就是 `None`。

MISSING = object()

AUDIT_A = [
    ('$number("00")', 0),
    ('$number("007")', 7),
    ('$number("00.5")', 0.5),
    ('$number("-00")', 0),
    ('$number("0000")', 0),
    ('$number("00e1")', 0),
    ('$number("0000000000000000001")', 1),
    ('$number("-01")', -1),
    ('$pad("abc", 6, "xy")', 'abcxyx'),
    ('$pad("abc", 7, "xy")', 'abcxyxy'),
    ('$pad("abc", -5, "xy")', 'xyabc'),
    ('$pad("abc", -6, "xy")', 'xyxabc'),
    ('$pad("abc", 6.99, "xy")', 'abcxyx'),
    ('$pad("abc", 5, "")', 'abc  '),
    ('$pad("abc", 2, "xy")', 'abc'),
    ('$pad("ab", 5, "-")', 'ab---'),
    ('$pad("\\u4f60\\u597d", 4, "\\u554a")', '你好啊啊'),
    ('$trim("  a  b  ")', 'a b'),
    ('$trim("a\\t\\tb")', 'a b'),
    ('$trim("  x  ")', 'x'),
    ('$trim("a\\n\\nb")', 'a b'),
    ('$trim("a \\t\\n\\r b")', 'a b'),
    ('$trim("\\u00a0a\\u00a0")', '\xa0a\xa0'),
    ('$trim("")', ''),
    ("$spread({'a': 1})", {'a': 1}),
    ("$spread({'a': [1, 2]})", {'a': [1, 2]}),
    ("$spread({'a': [1]})", {'a': [1]}),
    ("$spread({'a': []})", {'a': []}),
    ("$spread({'a': 1, 'b': 2})", [{'a': 1}, {'b': 2}]),
    ("$spread([{'a': 1}])", [{'a': 1}]),
    ("$spread([{'a': 1, 'b': 2}])", [{'a': 1}, {'b': 2}]),
    ('$spread([[1, 2]])', [1, 2]),
    ('$spread(1)', 1),
    ('$spread(null)', None),
    ('$spread({})', MISSING),
    ('$spread([])', MISSING),
    ('$fromMillis(0, "[Y]-[M]-[D]")', '1970-1-1'),
    ('$fromMillis(0, "[Y]")', '1970'),
    ('$fromMillis(0, "[Y01]")', '70'),
    ('$fromMillis(0, "[Y0001]")', '1970'),
    ('$fromMillis(0, "[Y99]")', '70'),
    ('$fromMillis(0, "[M]")', '1'),
    ('$fromMillis(0, "[M01]")', '01'),
    ('$fromMillis(0, "[D]")', '1'),
    ('$fromMillis(0, "[D01]")', '01'),
    ('$fromMillis(0, "[H]")', '0'),
    ('$fromMillis(0, "[H01]")', '00'),
    ('$fromMillis(0, "[h]")', '12'),
    ('$fromMillis(0, "[h01]")', '12'),
    ('$fromMillis(0, "[P]")', 'am'),
    ('$fromMillis(0, "[PN]")', 'AM'),
    ('$fromMillis(0, "[m]")', '00'),
    ('$fromMillis(0, "[m1]")', '0'),
    ('$fromMillis(0, "[s]")', '00'),
    ('$fromMillis(0, "[f]")', '0'),
    ('$fromMillis(0, "[f001]")', '000'),
    ('$fromMillis(0, "[f0001]")', '0000'),
    ('$fromMillis(0, "[d]")', '1'),
    ('$fromMillis(0, "[d01]")', '01'),
    ('$fromMillis(0, "[F]")', 'thursday'),
    ('$fromMillis(0, "[FN]")', 'THURSDAY'),
    ('$fromMillis(0, "[FNn]")', 'Thursday'),
    ('$fromMillis(0, "[MN]")', 'JANUARY'),
    ('$fromMillis(0, "[MNn]")', 'January'),
    ('$fromMillis(0, "[MNn,3-3]")', 'Jan'),
    ('$fromMillis(0, "[W]")', '1'),
    ('$fromMillis(0, "[W01]")', '01'),
    ('$fromMillis(0, "[w]")', '1'),
    ('$fromMillis(0, "[X]")', '1970'),
    ('$fromMillis(0, "[x]")', '1'),
    ('$fromMillis(0, "[Z]")', '+00:00'),
    ('$fromMillis(0, "[Z01:01t]")', 'Z'),
    ('$fromMillis(0, "[z]")', 'GMT+00:00'),
    ('$fromMillis(0, "[C]")', 'ISO'),
    ('$fromMillis(0, "[[Y]]")', '[Y]'),
    ('$fromMillis(0, "a[Y]b")', 'a1970b'),
    ('$fromMillis(1704067200123, "[Y]-[M01]-[D01]T[H01]:[m01]:[s01].[f001]")', '2024-01-01T00:00:00.123'),
    ('$fromMillis(1704067200123, "[F]")', 'monday'),
    ('$fromMillis(1704067200123, "[Y01]")', '24'),
    ('$fromMillis(-1)', '1969-12-31T23:59:59.999Z'),
    ('$fromMillis(-86400000)', '1969-12-31T00:00:00.000Z'),
    ('$fromMillis(-1000000000000)', '1938-04-24T22:13:20.000Z'),
    ('$formatNumber(1234, "0;0")', '1234'),
    ('$formatNumber(1234567, "#,##,##0")', '12,34,567'),
    ('$formatNumber(1234, "[>=1000]0.0K")', MISSING),  # 官方 D3086
    ('$formatNumber(1234, "0;0;0")', MISSING),  # 官方 D3080
    ('$formatNumber(1234, "0.0.0")', MISSING),  # 官方 D3081
    ('$formatNumber(1234, "0%%")', MISSING),  # 官方 D3082
    ('$formatNumber(1234, "0,,0")', MISSING),  # 官方 D3089
    ('$formatNumber(1234, "00#")', MISSING),  # 官方 D3090
    ('$formatNumber(1234, "0.0#0")', MISSING),  # 官方 D3091
    ('$formatNumber(1234, "0.0e")', MISSING),  # 官方 D3093
    ('$formatNumber(1234, "0.0e0%")', MISSING),  # 官方 D3092
    ('$formatNumber(1234, "abc")', MISSING),  # 官方 D3086
    ('$formatNumber(1234.5678, "#,##0.00", {"grouping-separator": " "})', MISSING),  # 官方 D3086
    ('$formatNumber(0.5, "0p", {"percent": "p"})', '50p'),
    ('$formatNumber(1234, "##0", {"digit": "@"})', '##1234'),
    ('$formatNumber(1234, "0,000", {"grouping-separator": "\'"})', MISSING),  # 官方 D3086
]


@pytest.mark.parametrize("expr,want", AUDIT_A)
def test_audit_a_and_b(expr, want):
    """A 档（`$number` 前导零 / `$pad` 循环填充 / `$trim` 内部空白 /
    `$spread` 语义）+ B 档（`$fromMillis` picture 分量 / `$formatNumber`
    图样族与 options）与 2.2.2 的逐值对拍。"""
    got = run(expr)[0]
    if want is MISSING:
        # 官方抛错 / 返回 undefined 的形态，DCG 侧统一是 UNDEF
        assert got is rt.UNDEF, (expr, got)
    else:
        assert got == want, (expr, got)


# --------------------------------------------- 双活回退臂（`_ACC` 关掉后的纯 Python 路径）
#
# 门控生效时 helper 体内 `_ACC` 分支之外的代码根本不执行，于是「Rust 能算对」
# 会掩盖纯 Python 路径的缺陷：`$toMillis` 的 `_ISO_RE` 曾被整段替换脚本误删，
# 全量测试全绿，直到关掉 `_ACC` 跑基准才炸出 `NameError`。下面这组用例**显式**
# 把 `_ACC` 置空，专测回退臂。
FALLBACK_CASES = [
    (rt._mn, ([3, 1, 2],), 1),
    (rt._mx, ([3, 1, 2],), 3),
    (rt._av, ([1, 2, 3, 4],), 2.5),
    (rt._ds, ([1, 2, 2, 3],), [1, 2, 3]),
    (rt._eu, ("a b",), "a%20b"),
    (rt._du, ("a%20b",), "a b"),
    (rt._tms, ("2024-01-01T00:00:00Z",), 1704067200000),
    (rt._tms, ("2024-01-01T00:00:00+08:00",), 1704038400000),
    (rt._tms, ("bogus",), rt.UNDEF),
    (rt._fms, (0,), "1970-01-01T00:00:00.000Z"),
    (rt._fms, (0, "[Y]-[M]-[D]"), "1970-1-1"),
    (rt._fnm, (1234.5678, "#,##0.00"), "1,234.57"),
    (rt._fnm, (1234, "[>=1000]0.0K"), rt.UNDEF),
    (rt._pgi, ("005", "000"), 5),
]


@pytest.mark.parametrize("fn,args,want", FALLBACK_CASES)
def test_accel_fallback_arm_matches(fn, args, want):
    """关掉 `_ACC` 后，纯 Python 臂必须给出与开加速时相同的结果。"""
    saved = rt._ACC
    try:
        rt._ACC = None
        got = fn(*args)
    finally:
        rt._ACC = saved
    assert got == want, (fn.__name__, args, got)


def test_accel_fallback_arm_agrees_with_accel_arm():
    """同一批输入，`_ACC` 开 / 关两条臂逐值相同（含脏数据）。"""
    dirty = [
        [], [0], [1, 1], [True, 1], [1.5, 2], ["a", "b"], None, 5, "x",
        [2 ** 62, 3], [-1, 0, 1],
    ]
    saved = rt._ACC
    try:
        for fn in (rt._mn, rt._mx, rt._av, rt._ds):
            for arg in dirty:
                rt._ACC = saved
                on = fn(arg)
                rt._ACC = None
                off = fn(arg)
                assert repr(on) == repr(off), (fn.__name__, arg, on, off)
        for text in ("", "a b", "%", "a%2Fb", "'unclosed", "\u00e9"):
            for fn in (rt._eu, rt._du, rt._tms):
                rt._ACC = saved
                on = fn(text)
                rt._ACC = None
                off = fn(text)
                assert repr(on) == repr(off), (fn.__name__, text, on, off)
        for ms in (0, -1, -86400000, 1704067200123, 10 ** 12):
            for pic in (rt.UNDEF, "[Y]-[M01]-[D01]", "[FNn] [D01] [MNn] [Y0001]"):
                rt._ACC = saved
                on = rt._fms(ms, pic)
                rt._ACC = None
                off = rt._fms(ms, pic)
                assert repr(on) == repr(off), (ms, pic, on, off)
    finally:
        rt._ACC = saved
