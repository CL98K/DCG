# -*- coding: utf-8 -*-
"""生成 `JSONata 语法.md`：所有示例先在真实引擎上执行/断言，再写入文档。

文档与语料同源（防漂移）：
- 分类示例（§2–§11）在本文件里手写，生成前逐条在真实引擎上执行并断言结果；
- §12.7 / §13 / §14 从 `examples/jx/*.jx` 语料（215 条）解析并真实执行得到；
- `tests/test_jsonata_manual.py` 断言磁盘文档与本渲染结果**逐字节相等**，文档不会与语料漂移。

重新生成：`python tests/gen_jsonata_manual.py`
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS = pathlib.Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import dcg  # noqa: E402
import dcg.codegen as codegen  # noqa: E402
from dcg.jx import Unsupported, compile_expr  # noqa: E402
from dcg.jx import policy  # noqa: E402
from dcg.runtime import UNDEF  # noqa: E402

from jx_corpus import ALL, observe, parse_file, sample_files  # noqa: E402

OUT = ROOT / "JSONata 语法.md"
FAILS = []


# ---------------------------------------------------------------- 执行管线
def build_src(expr, row, fields_extra=None):
    fields = {}
    if isinstance(row, dict):
        for k in row:
            if isinstance(k, str) and k.isidentifier() and k.isascii():
                fields[k] = {"type": "any", "value": "$." + k}
    fields.update(fields_extra or {})
    fields["v"] = {"type": "any", "expr": expr}
    schema = {"name": "ex", "output": {"shape": "list", "single": False, "filter": False},
              "out": {"type": "object", "fields": fields}}
    return dcg.generate(schema)


def run_value(expr, row=None, fields_extra=None):
    src = build_src(expr, row, fields_extra)
    ns = {}
    exec(compile(src, "<doc-verify>", "exec"), ns)
    row = {} if row is None else row
    return ns["run"]([row])[0][-1]


def fmt(v):
    if v is UNDEF:
        return "undefined"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, float) and v.is_integer():
        return repr(v)
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join("%r: %s" % (k, fmt(x)) for k, x in v.items()) + "}"
    return repr(v)


def code(expr):
    if "`" in expr:
        return "`` " + expr + " ``"
    return "`" + expr + "`"


def cell(s):
    return s.replace("|", "\\|")


def row_txt(row):
    return "—" if row is None else "`" + fmt(row) + "`"


# ---------------------------------------------------------------- 示例校验
def check_value(expr, expect, row=None, fields_extra=None):
    try:
        got = run_value(expr, row, fields_extra)
    except Exception as e:  # noqa: BLE001
        FAILS.append(("value", expr, "运行异常 %s: %s" % (type(e).__name__, e)))
        return None
    if (got is UNDEF) != (expect is UNDEF) or (got is not UNDEF and got != expect):
        FAILS.append(("value", expr, "期望 %r 实际 %r" % (expect, got)))
    return fmt(got)


def check_reject(expr):
    try:
        compile_expr(expr)
    except Unsupported as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        FAILS.append(("reject", expr, "抛了非 Unsupported: %s: %s" % (type(e).__name__, e)))
        return None
    FAILS.append(("reject", expr, "没有被拒绝"))
    return None


def check_codegen_reject(expr, row=None):
    try:
        build_src(expr, row or {})
    except Unsupported as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        FAILS.append(("codegen-reject", expr, "抛了非 Unsupported: %s: %s" % (type(e).__name__, e)))
        return None
    FAILS.append(("codegen-reject", expr, "没有被拒绝"))
    return None


def check_error(expr, exc_name, row=None):
    try:
        run_value(expr, row)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ != exc_name:
            FAILS.append(("error", expr, "期望 %s 实际 %s: %s" % (exc_name, type(e).__name__, e)))
        return type(e).__name__
    FAILS.append(("error", expr, "没有抛异常"))
    return None


def row_ex(expr, row=None, expect=None, note="", fields_extra=None):
    got = check_value(expr, expect, row, fields_extra)
    if got is None:
        got = "（校验失败）"
    return "| %s | %s | %s | %s |" % (cell(code(expr)), cell(row_txt(row)), cell(code(got)), cell(note))


def row_err(expr, exc_name, row=None, note=""):
    got = check_error(expr, exc_name, row)
    return "| %s | %s | `%s` | %s |" % (cell(code(expr)), cell(row_txt(row)), got or "?", cell(note))


S = []  # (title, prose, table_rows)

S.append(("2. 字面量", "", [
    row_ex("42", None, 42, "整数"),
    row_ex("3.5", None, 3.5, "小数"),
    row_ex("'hi'", None, 'hi', "单引号字符串"),
    row_ex('"hi"', None, 'hi', "双引号字符串，与单引号等价"),
    row_ex("true", None, True, "布尔真（不是 1）"),
    row_ex("false", None, False, "布尔假（不是 0）"),
    row_ex("null", None, None, "空值，是**值**，与「取不到」不同"),
    row_ex("[1, 2, 3]", None, [1, 2, 3], "数组字面量"),
    row_ex("[[1,2],[3]]", None, [[1, 2], [3]], "嵌套数组"),
    row_ex("[]", None, [], "空数组"),
    row_ex("{}", None, {}, "空对象"),
    row_ex("{'k': 1}", None, {'k': 1}, "对象字面量"),
    row_ex("{'k': a, 'j': b}", {'a': 1, 'b': 2}, {'k': 1, 'j': 2}, "对象值可以是表达式"),
]))

S.append(("3. 路径与取值", "路径段可以引用本行**其它字段**的值：`$.a[hour].v` 里的 `hour` 可以是另一个字段加工出的结果，用来做动态键/下标。字段之间按**依赖关系**求值，声明顺序只决定输出结构（前向引用合法）；依赖成环或引用不存在的字段在 build 期报 `SchemaError`。", [
    row_ex("$.name", {'name': 'n'}, 'n', "根路径取字段"),
    row_ex("name", {'name': 'n'}, 'n', "裸名 = 本行已声明字段"),
    row_ex("$.5", [10, 20, 30, 40, 50, 60], 60, "数值下标（行是 list）"),
    row_ex("$.5.a", [0, 0, 0, 0, 0, {'a': [10, 20]}], [10, 20], "下标后面可以继续接键"),
    row_ex("$.5.a.1", [0, 0, 0, 0, 0, {'a': [10, 20]}], 20, "数值段与符号段可混用，长度不限"),
    row_ex("$.5.a.1.b", [0, 0, 0, 0, 0, {'a': [10, {'b': 20}]}], 20, "任意深度"),
    row_ex("$.a.b", {'a': {'b': 7}}, 7, "两层标识符路径"),
    row_ex("$.a.b.c", {'a': {'b': {'c': 'x'}}}, 'x', "三层标识符路径"),
    row_ex("$.a[0]", {'a': [7, 8]}, 7, "常量下标"),
    row_ex("a[0]", {'a': [7, 8]}, 7, "裸名 + 常量下标"),
    row_ex("$.a[$i]", {'a': [10, 20], 'i': 1}, 20, "变量写法取本行字段 i 作下标"),
    row_ex("$.a[$.b]", {'a': [10, 20], 'b': 0}, 10, "路径作下标"),
    row_ex("$.a[b]", {'a': [10, 20], 'b': 1}, 20, "**下标里的裸名也是字段引用**"),
    row_ex("$.a[hour].v", {'a': {'x': {'v': 42}}}, 42, "**跨字段取值**：`hour`（`expr` 算出 `'x'`）作动态键", fields_extra={"hour": {"type": "str", "expr": "'x'"}}),
    row_ex("$.a[$hour].v", {'a': {'x': {'v': 42}}}, 42, "`$hour` 变量写法等价（`hour` 须是本 schema 的字段；前向引用合法）", fields_extra={"hour": {"type": "str", "expr": "'x'"}}),
    row_ex("$.a[hour]", {'a': [10, 20, 30]}, 20, "整数下标：`hour = 1` 时取第 2 个元素", fields_extra={"hour": {"type": "int64", "expr": "1"}}),
    row_ex("$.a['b']", {'a': {'b': 7}}, 7, "带引号 = 常量键"),
    row_err("$.a.{hour}.v", "Unsupported", {'a': {'x': {'v': 42}}}, "`{...}` 是对象构造/分组语义，不支持；动态键请用 `[...]`"),
    row_ex("$", {'a': 1}, {'a': 1}, "整行（根）"),
    row_ex("$i + 1", {'i': 1}, 2, "变量写法 $i 在本行内等价于字段 i"),
]))

# §4 算术与连接
S.append(("4. 算术与字符串连接", "", [
    row_ex("a + b", {'a': 1, 'b': 2}, 3, "加"),
    row_ex("a - b", {'a': 7, 'b': 2}, 5, "减"),
    row_ex("a * b", {'a': 3, 'b': 4}, 12, "乘"),
    row_ex("a / b", {'a': 7, 'b': 2}, 3.5, "除（结果是浮点）"),
    row_ex("a % b", {'a': 7, 'b': 2}, 1, "取模"),
    row_ex("-a", {'a': 5}, -5, "一元负号"),
    row_ex("+a", {'a': 5}, 5, "一元正号"),
    row_ex("a + b * 2", {'a': 1, 'b': 2}, 5, "乘法优先于加法"),
    row_ex("(a + b) * 2", {'a': 1, 'b': 2}, 6, "括号"),
    row_ex("2 + 3 * 4", None, 14, "常量在编译期折叠"),
    row_ex("'a' & 'b'", None, 'ab', "`&` 是字符串连接"),
    row_ex("'n=' & a", {'a': 5}, 'n=5', "连接时数字自动转字符串"),
    row_ex("'y' & a & 'z'", {'a': 1}, 'y1z', "三段连接"),
    row_ex("'x' & $.m", {'m': None}, 'x', "null 当空串"),
    row_ex("$.m & 'x'", {'m': None}, 'x', "左侧 null 也当空串"),
    row_err("$.m + 1", "TypeError", {'m': None}, "null 是值：参与算术报错（不是 undefined）"),
]))

# §5 比较、逻辑与条件
S.append(("5. 比较、逻辑与条件", "", [
    row_ex("a = b", {'a': 1, 'b': 1}, True, "相等"),
    row_ex("a != b", {'a': 1, 'b': 2}, True, "不等"),
    row_ex("a < b", {'a': 1, 'b': 2}, True, "小于"),
    row_ex("a <= b", {'a': 2, 'b': 2}, True, "小于等于"),
    row_ex("a > b", {'a': 3, 'b': 2}, True, "大于"),
    row_ex("a >= b", {'a': 1, 'b': 2}, False, "大于等于"),
    row_ex("'a' < 'b'", None, True, "字符串按字典序比较"),
    row_ex("a in [1, 2, 3]", {'a': 2}, True, "`in`：元素是否在数组里"),
    row_ex("a in [1, 2, 3]", {'a': 9}, False, "`in` 反例"),
    row_ex("a and b", {'a': True, 'b': False}, False, "逻辑与"),
    row_ex("a or b", {'a': False, 'b': True}, True, "逻辑或"),
    row_ex("not a", {'a': False}, True, "逻辑非"),
    row_ex("not a", {'a': True}, False, "逻辑非（真）"),
    row_ex("(a or b) and not c", {'a': False, 'b': True, 'c': False}, True, "嵌套逻辑"),
    row_ex("a > 1 ? 'big' : 'small'", {'a': 5}, 'big', "条件表达式，真分支"),
    row_ex("a > 1 ? 'big' : 'small'", {'a': 0}, 'small', "条件表达式，假分支"),
    row_ex("a ? 'y' : 'n'", {'a': 5}, 'y', "真值条件（真）"),
    row_ex("a ? 'y' : 'n'", {'a': 0}, 'n', "真值条件（假）：0 为假"),
    row_ex("(a > 1 ? 'big' : 'small') & '!'", {'a': 5}, 'big!', "条件嵌在连接里"),
    row_ex("1 = true", None, False, "布尔与数值不是同一个值"),
]))

# §6 字符串函数
S.append(("6. 字符串函数", "", [
    row_ex("$string(a)", {'a': 5}, '5', "数字转字符串"),
    row_ex("$string(true)", None, 'true', "布尔转字符串"),
    row_ex("$uppercase(s)", {'s': 'ab'}, 'AB', "大写"),
    row_ex("$lowercase(s)", {'s': 'AB'}, 'ab', "小写"),
    row_ex("$trim(s)", {'s': '  x  '}, 'x', "去首尾空白"),
    row_ex("$length(s)", {'s': 'abc'}, 3, "长度"),
    row_ex("$substring(s, 1, 2)", {'s': 'abcde'}, 'bc', "子串：起点 + 长度"),
    row_ex("$substring(s, 2)", {'s': 'abcde'}, 'cde', "子串：只有起点，取到末尾"),
    row_ex("$split(s, ',')", {'s': 'a,b,c'}, ['a', 'b', 'c'], "切分"),
    row_ex("$join(xs, '-')", {'xs': ['a', 'b']}, 'a-b', "连接数组"),
    row_ex("$contains(s, 'b')", {'s': 'abc'}, True, "包含（真）"),
    row_ex("$contains(s, 'z')", {'s': 'abc'}, False, "包含（假）"),
]))

# §7 数值函数
S.append(("7. 数值函数", "", [
    row_ex("$number(s)", {'s': '42'}, 42, "字符串转数字"),
    row_ex("$abs(n)", {'n': -3}, 3, "绝对值"),
    row_ex("$floor(n)", {'n': 3.7}, 3, "下取整"),
    row_ex("$ceil(n)", {'n': 3.2}, 4, "上取整"),
    row_ex("$round(n)", {'n': 3.5}, 4, "四舍五入（注意 $round(2.5) = 2，见下方说明）"),
    row_ex("$sqrt(n)", {'n': 9}, 3.0, "平方根"),
    row_ex("$power(a, b)", {'a': 2, 'b': 10}, 1024, "幂"),
]))

# §8 类型、存在性与计数
S.append(("8. 类型、存在性与计数", "", [
    row_ex("$type(a)", {'a': 1}, 'number', "数字"),
    row_ex("$type(a)", {'a': 's'}, 'string', "字符串"),
    row_ex("$type(a)", {'a': [1]}, 'array', "数组"),
    row_ex("$type(a)", {'a': {'k': 1}}, 'object', "对象"),
    row_ex("$type($.m)", {'m': None}, 'null', "null"),
    row_ex("$type(true)", None, 'boolean', "布尔"),
    row_ex("$boolean(a)", {'a': 1}, True, "布尔化"),
    row_ex("$boolean(a)", {'a': 0}, False, "布尔化：0 为假"),
    row_ex("$not(a)", {'a': 1}, False, "逻辑非函数"),
    row_ex("$not(true)", None, False, "逻辑非函数（布尔）"),
    row_ex("$exists(a)", {'a': 1}, True, "存在性：有值即真"),
    row_ex("$count([1, 2, 3])", None, 3, "计数（字面量数组）"),
    row_ex("$count(xs)", {'xs': [1, 2, 3]}, 3, "计数（字段数组）"),
]))

# §9 数组与对象构造
S.append(("9. 数组与对象构造", "", [
    row_ex("[a, b]", {'a': 1, 'b': 2}, [1, 2], "引用字段构造数组"),
    row_ex("[a, b, a + b]", {'a': 1, 'b': 2}, [1, 2, 3], "数组内可写表达式"),
    row_ex("[$.m, a]", {'m': None, 'a': 1}, [None, 1], "null 是值，保留在数组里"),
    row_ex("{'k': a}", {'a': 1}, {'k': 1}, "对象字段"),
    row_ex("{'sum': a + b}", {'a': 1, 'b': 2}, {'sum': 3}, "对象值可以是表达式"),
]))

# §10 反引号
S.append(("10. 反引号内联 Python", "反引号段内是逐字嵌入的 Python **单表达式**（build 期只做语法校验，不执行）；段外仍是 JSONata。段内的 `$.路径` 与裸名都是字段引用。", [
    row_ex("`int($.a) + 1`", {'a': '3'}, 4, "段内 Python 算术"),
    row_ex("`$.a * 2`", {'a': 4}, 8, "原样 Python 表达式"),
    row_ex("`len($.a)`", {'a': [1, 2, 3]}, 3, "段内可用 Python 内置函数"),
    row_ex("`$.a` + `$.b`", {'a': 1, 'b': 2}, 3, "两个反引号段参与同一表达式"),
    row_ex("'p' & str($.a)", {'a': 7}, 'p7', "段外 JSONata + 段内 Python 混用"),
    row_ex("`$.a + '_s'`", {'a': '1'}, '1_s', "段内是纯 Python（字符串拼接用 +）"),
    row_err("`$.a + '_s'`", "TypeError", {'a': 1}, "段内不做 JSONata 的宽容拼接：数字 + 字符串报错"),
]))

# §11 py{}
S.append(("11. py{} 多语句代码块", "块内规则：只能 `let NAME = EXPR;` 加结尾唯一一条 `return EXPR`，中间可用 Python 表达式（含推导式、方法调用、内置函数）；不能写循环/条件语句/`try`/`def`/`class`/`lambda`/`import`，也不能直接用裸字段名（字段写 `$.名称`）。`py{}` 不是沙箱，只做语法约束。`py{}` 也可以写在函数字面量体内（如 `$map([1, 2], function($x){ py{ return 7 } })`）：块每元素执行一次，但**块内不能引用回调参数**（`$x`），只能读原始行 `$.字段链`，详见 §12.3。", [
    row_ex("py{ return int($.a) }", {'a': '7'}, 7, "最小形态：一条 return"),
    row_ex("py{ let t = $.a * 1.2; return int(t) }", {'a': 10}, 12, "let 绑定 + return"),
    row_ex("py{ let s = $.a; let t = s * 2; return t + 1 }", {'a': 5}, 11, "多条 let 链式依赖"),
    row_ex("py{ return len($.a) }", {'a': [1, 2, 3]}, 3, "块内调用内置函数"),
    row_ex("py{ return [x * 2 for x in $.a] }", {'a': [1, 2]}, [2, 4], "推导式"),
    row_ex("py{ let t = $.a; return t.upper() }", {'a': 'ab'}, 'AB', "对字段值调用方法"),
    row_ex("py{ return $.a + 1 } / 2", {'a': 5}, 3.0, "块可以嵌在更大的表达式里"),
    row_ex("$map([1, 2], function($x){ py{ return $.a + 1 } })", {'a': 10},
           [11, 11], "写在函数字面量体内：块内只读原始行 `$.a`，每元素执行一次"),
]))

# ---------------------------------------------------------------- 支持面 / 拒绝面校验
def _find_fn_table():
    for name in dir(codegen):
        val = getattr(codegen, name)
        if isinstance(val, dict) and "$uppercase" in val:
            return val
    raise AssertionError("找不到 codegen 的函数发射表")


FN_TABLE = _find_fn_table()
ROW = set(policy.ROW_FUNCS)
HO = set(policy.HIGHER_ORDER)
NONDET = set(policy.NONDETERMINISTIC)
SUPPORTED_FUNCS = set(FN_TABLE) | HO


def check_accept(expr):
    try:
        compile_expr(expr)
    except Unsupported as e:  # noqa: F821
        FAILS.append(("accept", expr, "被拒绝: %s" % e))
    except Exception as e:  # noqa: BLE001
        FAILS.append(("accept", expr, "异常 %s: %s" % (type(e).__name__, e)))


if set(FN_TABLE) - (ROW | HO | NONDET):
    FAILS.append(("emit-extra", sorted(set(FN_TABLE) - (ROW | HO | NONDET)),
                  "codegen 发射表里出现表外函数"))
for _f in sorted(ROW | NONDET):
    check_accept(_f + "(v)")
HO_ACCEPT = {
    "$map": "$map(v, function($x){ $x })",
    "$filter": "$filter(v, function($x){ $x })",
    "$each": "$each(v, function($x){ $x })",
    "$sift": "$sift(v, function($x){ $x })",
    "$reduce": "$reduce(v, function($a, $b){ $a + $b })",
    "$sort": "$sort(v)",
}
for _f in sorted(HO):
    check_accept(HO_ACCEPT[_f])

# 高阶函数必须拒绝非函数字面量实参
for _f in sorted(HO):
    try:
        compile_expr("%s(v, $string)" % _f)
    except Unsupported:  # noqa: F821
        pass
    else:
        FAILS.append(("higher-arg", _f, "非函数字面量实参没有被拒绝"))

CONSTRUCTS = [
    ("区间", "1..5", "整批序列语义"),
    ("正则字面量", "/ab/", "正则需序列匹配"),
    ("正则字面量（带 flags）", "/ab/i", "正则需序列匹配"),
    ("位置绑定 `#`", "a#$i", "隐式整批序号"),
    ("join 绑定 `@`", "a@$j", "整批序列语义"),
    ("变换操作符 `~>`", "a ~> b", "整批管道语义"),
    ("变换操作符 `|...|`", "a | b | c", "整批管道语义"),
    ("排序 `^`", "a^.b", "整批排序"),
    ("分组/聚合 `{...}`", "a{ 'k': v }", "整批分组"),
    ("当前行 `$$`", "$$", "`$` 已表示当前行，`$$` 不支持"),
    ("`$eval`", "$eval(x)", "运行期求值任意表达式 = 运行期解释器入口 + 注入面"),
]
construct_rows = []
for cat, expr, why in CONSTRUCTS:
    check_reject(expr)
    construct_rows.append("| %s | `{}` | `Unsupported` | %s（%s） |"
                          % (cell(code(expr)), why, cat))

# 反引号 / py{} 限制校验
BACKTICK_REJECTS = ["`x = 1`", "`import os`", "`1 +`"]
for e in BACKTICK_REJECTS:
    check_reject(e)
BLOCK_REJECTS = [
    "py{ $.a }", "py{}", "py{ let t = 1 }", "py{ return 1; return 2 }",
    "py{ return 1; let t = 2 }", "py{ x = 1; return x }", "py{ let t = 1; t += 1; return t }",
    "py{ import os; return 1 }", "py{ from os import path; return 1 }",
    "py{ for x in $.a: return x }", "py{ while True: return 1 }", "py{ if $.a: return 1 }",
    "py{ try: return 1 }", "py{ def f(): return 1 }", "py{ class C: pass }",
    "py{ return lambda: 1 }", "py{ return hourB }", "py{ return __import__('os') }",
    "py{ return $x }", "py{ return $$ }", "py{ return __jx_p0 }", "py{ let t = 1; return t",
]
for e in BLOCK_REJECTS:
    check_reject(e)

# ---------------------------------------------------------------- §12 函数总览
FUNCTION_GROUPS = [
    ("A 类｜字符串", sorted(f for f in ROW if f in {
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase"})),
    ("A 类｜数值", sorted(f for f in ROW if f in {
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max"})),
    ("A 类｜布尔", sorted(f for f in ROW if f in {"$boolean", "$not", "$exists", "$type"})),
    ("A 类｜数组与对象", sorted(f for f in ROW if f in {
        "$count", "$append", "$average", "$reverse", "$sort", "$distinct",
        "$merge", "$keys", "$values", "$lookup", "$spread", "$single"})),
    ("B 类｜高阶", sorted(HO)),
    ("C 类｜非确定", sorted(NONDET)),
]

#: §12.2 数组与对象函数示例（逐条真实执行）
ARRAY_ROWS = [
    row_ex("$sum([1, 2, 3])", None, 6, "数组求和"),
    row_ex("$sum($.xs)", {'xs': [1, 2, 3]}, 6, "行内数组值（不要求字面量）"),
    row_ex("$sum([])", None, 0, "空数组求和为 0"),
    row_ex("$average([1, 2, 3])", None, 2.0, "均值"),
    row_ex("$min([3, 1, 2])", None, 1, "最小值"),
    row_ex("$max([3, 1, 2])", None, 3, "最大值"),
    row_ex("$reverse([1, 2, 3])", None, [3, 2, 1], "反转"),
    row_ex("$sort([3, 1, 2])", None, [1, 2, 3], "升序排序"),
    row_ex("$sort(['b', 'a', 'c'])", None, ['a', 'b', 'c'], "字符串默认升序"),
    row_ex("$distinct([1, 1, 2])", None, [1, 2], "去重、保持首次出现顺序"),
    row_ex("$append([1], [2])", None, [1, 2], "拼接两个数组"),
    row_ex("$keys({'a': 1, 'b': 2})", None, ['a', 'b'], "对象键列表"),
    row_ex("$values({'a': 1})", None, [1], "对象值列表"),
    row_ex("$merge([{'a': 1}, {'b': 2}])", None, {'a': 1, 'b': 2}, "合并对象"),
    row_ex("$lookup({'a': 1}, 'a')", None, 1, "按键取值；键不存在得到 undefined"),
    row_ex("$spread({'a': [1, 2]})", None, [{'a': [1, 2]}], "对象拆成对象数组"),
    row_ex("$single([1])", None, 1, "取唯一元素；长度不为 1 得到 undefined"),
    row_ex("$count($.xs)", {'xs': [1, 2, 3]}, 3, "数组长度"),
]

#: §12.3 高阶函数示例（逐条真实执行）
HO_ROWS = [
    row_ex("$map([1, 2, 3], function($x){ $x * 2 })", None, [2, 4, 6], "逐元素映射"),
    row_ex("$map($.xs, function($x){ $x + 1 })", {'xs': [1, 2]}, [2, 3],
           "实参可以是行内数组值"),
    row_ex("$map([10, 20], function($x){ $position() })", None, [0, 1],
           "`$position()` 是回调内的下标，只在函数体内可用"),
    row_ex("$map([1, 2], function($x, $i){ $x + $i })", None, [1, 3],
           "回调第二个参数就是下标"),
    row_ex("$filter([1, 2, 3], function($x){ $x > 1 })", None, [2, 3], "保留函数体为真的元素"),
    row_ex("$reduce([1, 2, 3], function($a, $b){ $a + $b }, 0)", None, 6, "带初值折叠"),
    row_ex("$each({'a': 1, 'b': 2}, function($v, $k){ $k & '=' & $v })", None,
           ['a=1', 'b=2'], "按键值对回调"),
    row_ex("$sift({'a': 1, 'b': 2}, function($v){ $v > 1 })", None, {'b': 2}, "过滤对象键"),
    row_ex("$sort([3, 1, 2], function($a, $b){ $a < $b })", None, [3, 2, 1],
           "比较器返回 true 表示交换：`$a < $b` 得到降序"),
    row_ex("$sort([3, 1, 2], function($a, $b){ $a > $b })", None, [1, 2, 3],
           "`$a > $b` 得到升序（与 JSONata 1.8 一致）"),
    row_ex("$map([1, 2], function($x){ py{ return 7 } })", None, [7, 7],
           "函数体内写 `py{}`：每元素执行一次"),
    row_ex("$map([10, 20], function($x){ py{ let t = 2; return t * 3 } })",
           None, [6, 6], "块内可用 `let` 多语句（不能引用参数 `$x`）"),
    row_ex("$map([10, 20], function($x){ py{ return $.base + 1 } })",
           {'base': 100}, [101, 101],
           "块内读原始行：写 `$.base`（该字段须在 schema 声明），不是 `$x`"),
    row_err("$map([1], function($x){ py{ return $x } })", "Unsupported",
            None, "块内不能引用回调参数：`$x` 只存在于 JSONata 表达式层"),
]

NONDET_ROWS = [
    ("`$now()`", "—", "每次不同", "当前 UTC 时间（ISO 8601，毫秒精度）"),
    ("`$millis()`", "—", "每次不同", "当前 epoch 毫秒（整数）"),
    ("`$random()`", "—", "每次不同", "`[0, 1)` 浮点"),
    ("`$shuffle($.xs)`", "`{'xs': [1, 2, 3]}`", "某个排列", "随机洗牌"),
]


def _states():
    counts = {"ok": 0, "gap": 0, "defect": 0, "fixed": 0}
    for _p, _m, _i, case in ALL:
        counts[case["state"]] = counts.get(case["state"], 0) + 1
    return counts


def _observed(item):
    _path, _meta, _i, case = item
    obs = observe(case)
    if obs.kind in ("unsupported", "codegen-unsupported"):
        return cell(str(obs.exc))
    if obs.kind == "undefined":
        return "undefined"
    if obs.kind == "raise":
        return cell("%s: %s" % (type(obs.exc).__name__, obs.exc))
    if obs.kind == "not-rejected":
        return "（未被拒绝，测试会失败）"
    return cell(fmt(obs.value))


def render():
    if FAILS:
        raise AssertionError("示例校验失败：" + repr(FAILS[:5]))
    counts = _states()
    supported_count = len(ROW | HO | NONDET)
    md = []
    add = md.append

    add("# JSONata 语法（DCG 支持范围与示例）")
    add("")
    add("> **读者**：使用 DCG 编写 schema 的开发者")
    add("> **定位**：本文是 DCG 支持的 JSONata 子集**唯一权威清单**：每条给出写法、输入行与结果，"
        "全部实跑校验；实现必须与本文一致。")
    add("> **范围**：只讲表达式层的支持范围与示例；schema 结构、产物形态、字符预算见 "
        "`DCG 整体设计方案与架构文档.md`，接口细节见 `DCG 接口实现细节文档.md`。")
    add("")

    add("## 0. 一页速览")
    add("")
    add("| 问题 | 答案 |")
    add("|---|---|")
    add("| 本文是什么 | DCG 支持的 JSONata 子集：按类别列出写法、输入行与结果，全部实跑校验 |")
    add("| 支持范围 | %d 个函数：A 类行内 %d / B 类高阶 %d / C 类非确定 %d"
        "（`$sort` 同属 A/B）；永久不支持的构造见 §12.5 |"
        % (supported_count, len(ROW), len(HO), len(NONDET)))
    add("| 用在哪里 | schema 的 `expr` 表达式、`value` 取值路径、反引号段，以及 `py{}` 块内部的表达式层 |")
    add("| 求值模型 | 按**行**求值：表达式在 build 期编译进生成码，运行期无解释器；"
        "字段按依赖关系求值、前向引用合法 |")
    add("| 怎么验证 | 语料 %d 条例样逐条 `dcg.generate` → `exec` → `run`；"
        "本文与渲染结果逐字节一致 |" % len(ALL))
    add("| 从哪看起 | §1 阅读前须知 → §§2–11 逐类语法 → §12 支持面与永久拒绝 → "
        "§13 覆盖统计 → §14 缺陷与回归 |")
    add("")

    add("## 1. 阅读前须知")
    add("")
    add("**undefined 与 null 不同**：")
    add("- `null` 是一个**值**：可以输出、可以放进数组；参与算术会报错。")
    add("- undefined 表示「没有值」，是表达式层的哨兵：例如 `$count(标量)` 这类场景会得到 undefined；"
        "它出现在输出里时原样显示为 `undefined`，`&` 连接时当空串，比较规则见 §5。")
    add("- **取值缺失不是 undefined**：非 optional 字段直接按键取值，行里缺键会在运行期抛 `KeyError`"
        "（`list` 下标越界抛 `IndexError`）；只有声明 `optional: true` 的字段/表达式缺值时才输出 `null`，不抛异常。")
    add("- `$foo` 必须是本行字段名，否则 build 期报 `SchemaError`。`$.missing` 这类数据键 build 期不检查，"
        "运行期缺键即 `KeyError`——所以 `$exists($.a)` 也无法探测缺失键（取键本身就会抛）；"
        "需要容错时请在数据准备侧保证键存在，或用 optional 字段。")
    add("")
    add("**字段按依赖关系求值**：字段在 schema 里的声明顺序只决定输出结构；计算顺序由字段之间的引用关系决定，"
        "前向引用（引用后声明的字段）合法。依赖成环或引用不存在的字段在 build 期报 `SchemaError`。")
    add("")
    add("**裸名即字段**：`hourB`、`$.hourB` 读的都是本行字段 `hourB`；`$i` 不是真正的变量绑定，"
        "在本行内等价于字段 `i`。**下标里的裸名同样是字段**：`$.a[b]` 等价于 `$.a[$.b]`；"
        "要按字符串键取值请写引号：`$.a['b']`。")
    add("")

    for title, prose, rows in S:
        add("## %s" % title)
        add("")
        if prose:
            add(prose)
            add("")
        add("| 写法 | 输入行 | 结果 | 说明 |")
        add("|---|---|---|---|")
        md.extend(rows)
        add("")

    add("## 12. 函数能力总览与边界")
    add("")
    add("### 12.1 支持的函数（共 %d 个）" % len(SUPPORTED_FUNCS))
    add("")
    add("A 类在行内直接求值（可接收字面量数组或行内数组值）；B 类的回调写成 "
        "`function($v){...}`，函数体是单条表达式、可以内含 `py{}`；C 类结果不可复现，"
        "但产物代码仍可复现（见 §12.4）。")
    add("")
    add("| 类别 | 函数 | 个数 |")
    add("|---|---|---|")
    for group, funcs in FUNCTION_GROUPS:
        add("| %s | %s | %d |" % (group, "、".join("`%s`" % f for f in funcs), len(funcs)))
    add("| **合计** | — | **%d** |" % len(SUPPORTED_FUNCS))
    add("")
    add("### 12.2 数组与对象函数")
    add("")
    add("| 写法 | 输入行 | 结果 | 说明 |")
    add("|---|---|---|---|")
    md.extend(ARRAY_ROWS)
    add("")
    add("### 12.3 高阶函数")
    add("")
    add("回调最多接收两个位置参数；`$position()` 只在函数体内可用，表示当前下标。"
        "`$sort` 带比较器时，比较器返回 `true` 表示第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。函数体内**可以写 `py{}`**：块编译成 run() 之外的辅助函数、每元素调用一次；块内**不能引用回调参数**（`$x`），只能读原始行的 `$.字段链`。")
    add("")
    add("| 写法 | 输入行 | 结果 | 说明 |")
    add("|---|---|---|---|")
    md.extend(HO_ROWS)
    add("")
    add("### 12.4 非确定函数")
    add("")
    add("C 类函数每次调用的结果不同：**产物代码字节可复现，运行结果不可复现**。"
        "复跑证据时把 `dcg.runtime` 的时钟 / 随机源换成固定实现即可得到确定值，"
        "不需要改产物、也不通过环境变量开关。")
    add("")
    add("```python")
    add("import dcg.runtime as rt")
    add("")
    add("class FixedClock:")
    add("    def now_ms(self):")
    add("        return 1700000000000")
    add("")
    add("class FixedRandom:")
    add("    def random(self):")
    add("        return 0.25")
    add("")
    add("    def shuffle(self, xs):")
    add("        return list(reversed(xs))")
    add("")
    add("rt.CLOCK = FixedClock()")
    add("rt.RANDOM = FixedRandom()")
    add("```")
    add("")
    add("| 写法 | 输入行 | 结果 | 说明 |")
    add("|---|---|---|---|")
    for e, r, v, n in NONDET_ROWS:
        add("| %s | %s | %s | %s |" % (e, r, v, n))
    add("")
    add("### 12.5 永久不支持的语法构造")
    add("")
    add("| 写法 | 输入行 | 结果 | 说明 |")
    add("|---|---|---|---|")
    md.extend(construct_rows)
    add("")
    add("### 12.6 反引号段的限制")
    add("")
    add("反引号段内只能是**一个 Python 表达式**：不能出现语句、赋值、`import`，也不能多语句拼接。"
        "以下写法均被拒绝：")
    add("")
    for e in BACKTICK_REJECTS:
        add("- %s" % code(e))
    add("")
    add("### 12.7 py{} 块的限制")
    add("")
    add("块内只允许 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR`；以下写法均被拒绝：")
    add("")
    for e in BLOCK_REJECTS:
        add("- %s" % code(e))
    add("")
    add("## 13. 覆盖统计")
    add("")
    add("| 文件 | 主题 | 例数 |")
    add("|---|---|---|")
    for path in sample_files():
        meta, cases = parse_file(path)
        add("| %s | %s | %d |" % (cell(meta["file"]), cell(meta["title"]), len(cases)))
    add("| **合计** | 正常 %d ｜ 未实现(gap) %d ｜ 仍存缺陷(defect) %d ｜ 已修复(fixed) %d | **%d** |"
        % (counts["ok"], counts["gap"], counts["defect"], counts["fixed"], len(ALL)))
    add("")

    add("## 14. 已知缺陷与回归保护")
    add("")
    add("### 14.1 仍未修复（`state: defect`）")
    add("")
    add("这些条目**故意断言当前的错误行为**，修好后对应测试就会变红：")
    add("")
    add("| 表达式 | row | 实测（错误行为） | 正确语义 |")
    add("|---|---|---|---|")
    for path, meta, i, case in ALL:
        if case["state"] != "defect":
            continue
        note = (case["note"] or "").replace("**应当", "应当")
        add("| %s | %s | %s | %s |" % (cell(case["expr"]), cell(str(case["row"] or "{}")),
                                      _observed((path, meta, i, case)), cell(note)))
    add("")
    add("### 14.2 已修复（`state: fixed`，回归保护）")
    add("")
    add("共 %d 条，断言的是**正确值**（源：`14_fixed_regressions.jx`）；曾经是错误行为，"
        "修复后重新冻结，再次退化就会变红。" % counts["fixed"])
    add("")
    add("| 表达式 | row | 实测（正确值） | 修复前 / 说明 |")
    add("|---|---|---|---|")
    for path, meta, i, case in ALL:
        if case["state"] != "fixed":
            continue
        note = (case["note"] or "").replace("原缺陷：", "")
        add("| %s | %s | %s | %s |" % (cell(case["expr"]), cell(str(case["row"] or "{}")),
                                      _observed((path, meta, i, case)), cell(note)))
    add("")

    add("## 附录：易错点速查")
    add("")
    add("| 事项 | 说明 |")
    add("|---|---|")
    tips = [
        ("裸名", "`hourB` = 本行已声明字段，不是字符串键、也不是未定义变量"),
        ("下标里的裸名", "`$.a[b]` 里的 `b` 是字段引用；字符串键要写 `$.a['b']`"),
        ("`$i` 不是变量", "`$i` 等价于字段 `i`；DCG 没有变量绑定"),
        ("undefined vs null", "`null` 是值，会输出、参与构造；undefined 是「没有值」的哨兵，出现在输出里时原样显示为 `undefined`"),
        ("取值缺失", "非 optional 字段缺键 → 运行期 `KeyError`；`optional: true` 的字段/表达式缺值 → 输出 `null`"),
        ("`$foo` 未声明", "必须是本行已声明字段名，否则 build 期 `SchemaError`"),
        ("`$exists` 不能探测缺失键", "取键本身会抛 `KeyError`；它判断的是取到的值（`null` 也算 exists）"),
        ("`&` 与 `+`", "`&` 连接（undefined/null 当空串）；`+` 是算术（null 参与报错）"),
        ("比较遇 undefined", "`=`/`!=` 返回 false；大小比较返回 undefined"),
        ("布尔", "`true`/`false` 是布尔值，不是 1/0；`1 = true` 为 false"),
        ("`$round`", "当前按 Python 银行家舍入：`$round(2.5)` = 2、`$round(3.5)` = 4（与 JSONata 的 half-up 不同，属已知差异）"),
        ("`$count` 对标量", "当前对非数组标量返回 undefined（已知差异）"),
        ("py{} 不是沙箱", "只做语法约束，不做安全隔离"),
    ]
    for a, b in tips:
        add("| %s | %s |" % (a, b))
    add("")

    return "\n".join(md) + "\n"


def main():
    text = render()
    OUT.write_text(text, encoding="utf-8", newline="\n")
    n = sum(len(rows) for _title, _prose, rows in S)
    print("wrote %s (%d 行, 分类示例 %d 条, 语料 %d 条)"
          % (OUT, len(text.splitlines()), n, len(ALL)))


if __name__ == "__main__":
    main()
