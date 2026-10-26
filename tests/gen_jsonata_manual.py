# -*- coding: utf-8 -*-
"""**回填工具**（不是唯一真相源）：把分类示例与语料统计渲染进 `JSONata 语法.md`。

`JSONata 语法.md` 现在是**手写正文**，可以直接编辑；本脚本只在需要批量补示例/统计时运行。
`tests/test_jsonata_manual.py` 已不再做「磁盘文档 == 本脚本输出」的逐字节比对（那会把文档
每一句话绑死在下面的硬编码文案上），只保留内容守卫（覆盖、计数、reject 行为）。

原始说明：所有示例先在真实引擎上执行/断言，再写入文档。

文档与语料同源（防漂移）：
- 分类示例（§2–§11）在本文件里手写，生成前逐条在真实引擎上执行并断言结果；
- §12.7 / §14 / §15 从 `examples/jx/*.jx` 语料（238 条）解析并真实执行得到；
- `tests/test_jsonata_manual.py` 断言磁盘文档与本渲染结果**逐字节相等**，文档不会与语料漂移。

回填：`python tests/gen_jsonata_manual.py`
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS = pathlib.Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import dcg  # noqa: E402
import dcg.codegen as codegen  # noqa: E402
from dcg.jx import Registry, Unsupported, compile_expr  # noqa: E402
from dcg.jx import policy  # noqa: E402
from dcg.jx.policy import BARE_CALL_ALIASES  # noqa: E402
from dcg.runtime import HELPERS, UNDEF, UNDEFINED_SEMANTICS  # noqa: E402

from jx_corpus import ALL, observe, parse_file, sample_files  # noqa: E402

OUT = ROOT / "JSONata 语法.md"
FAILS = []


# ---------------------------------------------------------------- 执行管线
def infer_type(v):
    """按示例行的值推断声明类型：数值/字符串/布尔可静态证明，其余保持 any。"""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int64"
    if isinstance(v, float):
        return "float64"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    return "any"


def build_src(expr, row, fields_extra=None, out_type="any"):
    """构造示例 schema。

    只有表达式真的引用**裸名**时，才把示例行的键补成声明字段（裸名必须是
    已声明字段）；纯 `$.路径` 表达式不需要这些字段，否则会把「行字段自己的
    `_ix_get` 取值」混进产物形态标注，导致路径表达式被误标成 helper。
    """
    def gen(fields):
        f = dict(fields)
        f["v"] = {"type": out_type, "expr": expr}
        schema = {"name": "ex",
                  "output": {"shape": "list"},
                  "out": {"type": "object", "fields": f}}
        return dcg.generate(schema)

    try:
        return gen(fields_extra or {})
    except dcg.SchemaError as e:
        if "裸名" not in str(e) or not isinstance(row, dict):
            raise
    fields = dict(fields_extra or {})
    # 只补表达式真的按裸名引用的字段：逐次从 SchemaError 里取缺失的裸名。
    for _ in range(12):
        try:
            return gen(fields)
        except dcg.SchemaError as e:
            m = re.search(r"裸名 '([^']+)'", str(e))
            if (not m or not isinstance(row, dict) or m.group(1) not in row
                    or m.group(1) in fields):
                raise
            name = m.group(1)
            fields[name] = {"type": infer_type(row[name]), "value": "$." + name}
    return gen(fields)


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


def _manual_double(x):
    """Registry 示例用的模块级函数（lambda 的 qualname 不可导入，build 期会被拒绝）。"""
    return x * 2


_reg_lambda = Registry()
_reg_lambda.fn("$double2", lambda x: x * 2)


def check_reject(expr, registry=None):
    try:
        compile_expr(expr, registry=registry)
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
    except dcg.SchemaError as e:
        # 生成器层的第二类拒绝：语义不成立（如 `$x` 变量在逐行模式下没有作用域）。
        # 两类都只在 build 期抛出，见契约 §7.3。
        return str(e)
    except Exception as e:  # noqa: BLE001
        FAILS.append(("codegen-reject", expr, "抛了非 Unsupported/SchemaError: %s: %s" % (type(e).__name__, e)))
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


HELPER_NAMES = set(HELPERS)


def result_type(v):
    """按示例期望结果推断输出字段声明类型（决定是否发裸代码）。"""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int64"
    if isinstance(v, float):
        return "float64"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    return "any"


# helper 调用识别：`_xx(` 形式的调用。名字必须真在 `runtime.HELPERS` 里 ——
# 早期版本用「产物里有没有 `_xx(`」当判据，但裸链 `R['a']['b']` 里的下标、
# 以及 `R[5]['a']` 这类位置式路径都不含 helper，却被误标成 helper；
# 反过来 `_ix_get(...)` 是真 helper 又要标出来。判据统一为「查 HELPERS 表」。
_CALL_RE = re.compile(r"\b(_[A-Za-z]\w*)\(")


def _shape_bits(src):
    """从产物源码里分拣出形态标签。

    * `helpers`: 真的调用到的 runtime helper 名（查 HELPERS 表，不靠猜）
    * 只有裸代码：没有任何 helper 调用 -> 「内联（裸代码）」
    """
    called = {m for m in _CALL_RE.findall(src) if m in HELPER_NAMES}
    return sorted(called)


def product_shape(expr, row=None, fields_extra=None, expect=None):
    """生成产物形态：内联（裸代码）/ helper（...）/ 内联 Python（反引号段）/ 辅助函数（`py{}`）。

    与 README/接口文档同口径：只有**真的调用了 helper** 才算 helper 形态；
    `R['a']['b']` 这种裸链虽然含下标但零 helper 调用，算「内联（裸代码）」。
    反引号段标「内联 Python」（原样嵌入一个 Python 表达式）；`py{}` 不是内联——
    build 期把它发成 run() 之外的**模块级辅助函数** `_pyN`（每次 build 只定义一次），
    调用处写作 `_pyN(...)`，所以单独标「辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`）」。
    两者可同时出现 helper：先标内联形态，再追加 helper 清单。
    """
    try:
        src = build_src(expr, row, fields_extra, result_type(expect))
    except Exception:  # noqa: BLE001  拒绝类：由 row_err 标
        return "—（拒绝）"
    helpers = _shape_bits(src)
    py_block = "py{" in expr
    inline_py = py_block or "`" in expr
    parts = []
    if py_block:
        parts.append("辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`）")
    elif inline_py:
        parts.append("内联 Python")
    if helpers:
        shown = "、".join("`%s`" % h for h in helpers[:3])
        if len(helpers) > 3:
            shown += "…"
        parts.append("helper（%s）" % shown)
    elif not inline_py:
        parts.append("内联（裸代码）")
    return " + ".join(parts)


def row_ex(expr, row=None, expect=None, note="", fields_extra=None):
    got = check_value(expr, expect, row, fields_extra)
    if got is None:
        got = "（校验失败）"
    shape = product_shape(expr, row, fields_extra, expect)
    return "| %s | %s | %s | %s | %s |" % (
        cell(code(expr)), cell(row_txt(row)), cell(code(got)), cell(shape), cell(note))


def row_err(expr, exc_name, row=None, note=""):
    got = check_error(expr, exc_name, row)
    shape = product_shape(expr, row)
    if shape != "—（拒绝）":
        shape += "；运行期 `%s`" % (got or "?")
    return "| %s | %s | `%s` | %s | %s |" % (
        cell(code(expr)), cell(row_txt(row)), got or "?", cell(shape), cell(note))


def row_codegen_err(expr, row=None, note=""):
    """生成器层拒绝（Unsupported 或 SchemaError）的表格行：结果是编译期报错文案。"""
    got = check_codegen_reject(expr, row)
    return "| %s | %s | `编译期拒绝` | —（build 期拒绝） | %s |" % (
        cell(code(expr)), cell(row_txt(row)), cell(note + ("" if got else "（校验失败）")))


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

S.append(("3. 路径与取值", "路径段可以引用本行其它字段的值：`$.a[hour].v` 里的 `hour` 可以是另一个字段加工出的结果，用来做动态键/下标。字段之间按依赖关系求值，声明顺序只决定输出结构（前向引用合法）；依赖成环或引用不存在的字段在 build 期报 `SchemaError`。", [
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
    row_codegen_err("$.a[$i]", {'a': [10, 20], 'i': 1},
                    "`$i` 是变量形态，**不会**因为 schema 里声明了字段 `i` 就降级成字段引用（见 §1）；"
                    "生成器层 `SchemaError`，文案「行模式没有变量作用域：$i 不是任何函数字面量的形参」。"
                    "要按下标取字段请写裸名：`$.a[i]`（`i` 已声明时取到 `20`）"),
    row_ex("$.a[$.b]", {'a': [10, 20], 'b': 0}, 10, "路径作下标"),
    row_ex("$.a[b]", {'a': [10, 20], 'b': 1}, 20, "下标里的裸名也是字段引用"),
    row_ex("$.a[hour].v", {'a': {'x': {'v': 42}}}, 42, "跨字段取值：`hour`（`expr` 算出 `'x'`）作动态键。**前提：schema 声明了字段 `hour`**", fields_extra={"hour": {"type": "str", "expr": "'x'"}}),
    row_codegen_err("$.a[$hour].v", {'a': {'x': {'v': 42}}}, "`$hour` 是变量形态 → **与 `$x + 1` 是同一条规则、同一个报错**：行模式无变量作用域 → 生成器层 `SchemaError`（文案「行模式没有变量作用域：$hour 不是任何函数字面量的形参」）。**位置无关、也没有同名降级**——`$name` 在下标位置和算术位置走完全相同的判定；schema 里声明了 `hour` 字段也不改判（对比上一条：裸名 `hour` 才是字段引用）。写法建议：`$.a[hour].v`（裸名）或 `$.a.$.hour.v`"),
    row_ex("$.a[hour]", {'a': [10, 20, 30]}, 20, "整数下标：`hour = 1` 时取第 2 个元素", fields_extra={"hour": {"type": "int64", "expr": "1"}}),
    row_ex("$.a['b']", {'a': {'b': 7}}, 7, "带引号 = 常量键"),
    row_err("$.a.{hour}.v", "Unsupported", {'a': {'x': {'v': 42}}}, "`{...}` 是对象构造/分组语义，不支持；动态键请用 `[...]`"),
    row_ex("$", {'a': 1}, {'a': 1}, "整行（根）"),
    row_codegen_err("$i + 1", {'i': 1},
                    "`$i` 是变量形态，**不会**因为声明了字段 `i` 就降级为字段引用（见 §1）；"
                    "生成器层 `SchemaError`，与 `$x + 1` 同一条规则。要取本行字段写裸名 `i + 1` 或 `$.i + 1`"),
    row_codegen_err("$x + 1", {'i': 1}, "这种才是真实报错形态：`$x` 没有同名已声明字段，逐行模式也没有变量作用域 → 生成器层 `SchemaError`，文案指引「写裸名 x 或 $.x」"),
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
    row_ex("2 + 3 * 4", None, 14, "常量也按正常优先级求值，可发裸代码"),
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
    row_ex("$round(n)", {'n': 3.5}, 4, "四舍五入：half away from zero（半值一律远离零，故 `$round(2.5)` = 3）"),
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
    row_ex("$count('abc')", None, 1, "计数（标量按 1 计，与 `$length` 口径一致）"),
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
    ("Reduce 路径阶段 `{k:v}`", "$.items.{ 'k': v }",
     "整批分组：`{...}` 紧跟在 `.` 后就是**路径阶段的 Reduce**（与「独立表达式的分组/聚合」"
     "是同一语法的两个位置）——`第 1 列: Reduce 路径阶段 {k:v} 不可逐行编译（契约 §3.5 要求拒绝）`",
     ),
    ("不支持的路径形态", "('s').x",
     "路径必须**锚在 `$` 上**、段只能是标识符或常量下标：把分组/字面量/调用的结果再接路径段"
     "（`('s').x`、`($.a).b`）、或把 `$`/`$$` 当路径下标（`$.a[$$]`）都会被拒绝——"
     "`不支持的路径 $x 不可逐行编译（路径只支持 $ 起头、由标识符段与常量下标段组成（长度不限））`",
     ),
    ("当前行 `$$`", "$$", "`$` 已表示当前行，没有整篇文档的作用域（`第 1 列: $$ 文档根 不可逐行编译`）；"
                      "`$x` 的处置**不在此表**，见 §1.2"),
    ("`$eval`", "$eval(x)", "运行期求值任意表达式 = 运行期解释器入口 + 注入面"),
]
assert len([1 for _k, _e, _w in CONSTRUCTS if _e == "$$"]) == 1, "§12.5 里 $$ 必须恰好一条（$x 不在其中）"
construct_rows = []
for cat, expr, why in CONSTRUCTS:
    check_reject(expr)
    construct_rows.append("| %s | `{}` | `Unsupported` | —（build 期拒绝） | %s（%s） |"
                          % (cell(code(expr)), why, cat))

# 生成器层（dcg.generate）再拒绝的两类：compile_expr 已通过，build 才报错
CODEGEN_REJECTS = [
    ("变量引用 $x（SchemaError）", "$x + 1", {"i": 1},
     "`$x` 不在 `function($x){...}` 的形参位置上 → 生成器层 `SchemaError`。"
     "**与 `$.a[$hour]` 这条完全同源**：`$name` 一律编成 `var`，**不看 `$name` 出现在下标位置还是算术位置**。"
     "取本行字段写裸名 `i + 1` 或 `$.i + 1`。"
     "**注意**：`$i + 1` 在 `i` 已声明时**同样报错**（没有同名降级），见 §1"),
    ("非函数值冒充调用", "('s'.x)('a')", {},
     "`(…)` 只是分组（**不是**官方 JSONata 的序列/数组构造），分组结果不可调用——"
     "`第 8 列: lit[x]() 不可逐行编译（只有 $ 函数可以被调用）`；`(*.x)('a')` 这类官方序列写法在词法层就是 "
     "`不支持的起始记号 '*'`"),
    ("非函数值冒充调用（路径）", "($.a)($.b)", {"a": 1, "b": 2},
     "路径结果同样不可调用：`第 6 列: $[a]() 不可逐行编译（只有 $ 函数可以被调用）`"),
    ("非函数值冒充调用（lambda）", "(function($a){$a})('a')", {},
     "lambda 不是可调用表达式的头：`第 19 列: lambda() 不可逐行编译（只有 $ 函数可以被调用）`；"
     "`function(...)` 只能作 `$map` 等高阶函数的实参（§9）"),
]
codegen_rows = []
for cat, expr, row, why in CODEGEN_REJECTS:
    got = check_codegen_reject(expr, row)
    codegen_rows.append("| %s | `%s` | `%s` | —（build 期报错） | %s（%s） |"
                        % (cell(code(expr)), cell(row_txt(row)), "编译期拒绝" if got else "?",
                           cell(why), cell(cat)))

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
#: `dcg/jx/policy.py::STRUCTURAL` 的**种类**划分（10 个键 → 9 类）。
#: 键数不等于构造个数：`regex` 一个键覆盖 `/ab/` 与 `/ab/i`；`transform` 覆盖 `a ~> b` 与 `a | b | c`。
STRUCTURAL = set(policy.STRUCTURAL)
#: 按**固定阅读顺序**列举 `STRUCTURAL` 的全部键（集合无序，直接迭代会让产物漂移）。
STRUCTURAL_KINDS = ("eval", "range", "regex", "bind_pos", "bind_join", "sort", "transform",
                    "group", "path", "reduce")
assert set(STRUCTURAL_KINDS) == STRUCTURAL, "STRUCTURAL_KINDS 与 policy.STRUCTURAL 不同步"
CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
          11: "十一", 12: "十二"}
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
        "$merge", "$keys", "$values", "$lookup", "$spread", "$single", "$indexOf"})),
    ("B 类｜高阶", sorted(HO)),
    ("C 类｜非确定", sorted(NONDET)),
]

#: §12.2 数组与对象函数示例（逐条真实执行）
ARRAY_ROWS = [
    row_ex("$sum([1, 2, 3])", None, 6, "数组求和"),
    row_ex("$sum($.xs)", {'xs': [1, 2, 3]}, 6, "行内数组值（不要求字面量）"),
    row_ex("$sum([])", None, 0, "空数组求和为 0"),
    row_ex("$sum([true, 1])", None, 2, "**`$sum` 不对 `bool` 做语义检查**：成功路径就是一次 C 级 `sum()`，`True` 按 `1` 计（用户裁决）。算术算子 `+` 现在也是同一口径（`true + 1 == 2`），不再分叉"),
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
    row_ex("$indexOf('a234b', '23')", None, 1, "子串首次出现的位置（0 起）；找不到得 -1（字符串函数，见 §6）"),
    row_ex("$indexOf('a234b', 23)", None, 1, "子串按 `$string` 转换后再找"),
    row_ex("$indexOf(5, 'x')", None, UNDEF, "左侧不是字符串得到 undefined"),
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
            None, "块内不能引用回调参数：`$x` 只存在于 JSONata 表达式层。**替代方案**：把元素预先拆成 `$.字段` —— "
                  "在 `py{}` 里写 `$.field` 直接读原始行（该字段须在 schema 声明），"
                  "或用 JSONata 侧先 `$map` 出所需值再用 `py{}` 处理"),
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
    add("> **定位**：本文是 DCG 支持的 JSONata 子集**唯一权威清单**：核心分类给出写法/输入/结果；"
        "完整 55 函数清单见 §12.1，逐函数实测见验收报告 §4.2 与 examples/jx/12_funcs.jx；实现必须与本文一致。")
    add("> **范围**：只讲表达式层的支持范围与示例；schema 结构、产物形态、字符预算见 "
        "`DCG 整体设计方案与架构文档.md`，接口细节见 `DCG 接口实现细节文档.md`。")
    add("")

    add("## 0. 一页速览")
    add("")
    add("| 问题 | 答案 |")
    add("|---|---|")
    add("| 本文是什么 | DCG 支持的 JSONata 子集：核心分类给出写法/输入/结果；完整 55 函数清单见 §12.1，逐函数实测见验收报告 §4.2 与 examples/jx/12_funcs.jx |")
    add("| 支持范围 | %d 个函数：A 类行内 %d / B 类高阶 %d / C 类非确定 %d"
        "（`$sort` 同属 A/B）；永久不支持的构造见 §12.5；想扩展内置算子见 §13，想在不改引擎的前提下接入自己的函数见架构文档 §7.6 |"
        % (supported_count, len(ROW), len(HO), len(NONDET)))
    add("| 用在哪里 | schema 的 `expr` 表达式、`value` 取值路径、反引号段，以及 `py{}` 块内部的表达式层 |")
    add("| 求值模型 | 按行求值：表达式在 build 期编译进生成码，运行期无解释器；"
        "字段按依赖关系求值、前向引用合法 |")
    add("| 怎么验证 | 语料 %d 条例样逐条 `dcg.generate` → `exec` → `run`；"
        "正文手写维护（不做逐字节比对）；语料计数以 §14 覆盖统计的合计行为权威 |" % len(ALL))
    add("| 从哪看起 | §1 阅读前须知（1.1 undefined 与 null / 1.2 `$name` 与读值规则 / 1.3 裸名与产物形态 / 1.4 与 JSONata 1.8 的显式偏离）→ §2–§12 逐类语法与函数能力 → "
        "§13 怎么扩展内置算子 → §14 覆盖统计 → §15 缺陷与回归 |")
    add("")

    add("## 1. 阅读前须知")
    add("")
    add("**undefined 与 null 不同**：")
    add("- `null` 是一个值：可以输出、可以放进数组；参与算术会报错。")
    add("- undefined 表示「没有值」，是表达式层的哨兵：例如 `$not(缺失字段)` 得到 undefined；"
        "它出现在输出里时原样显示为 `undefined`，`&` 连接时当空串，比较规则见 §5。")
    add("- 取值缺失不是 undefined：非 optional 字段直接按键取值，行里缺键会在运行期抛 `KeyError`"
        "（`list` 下标越界抛 `IndexError`）；只有声明 `optional: true` 的字段/表达式缺值时才输出 `null`，不抛异常。")
    add("- **`$foo` 形式的变量引用**（不是函数调用）必须是本行已声明字段名，否则 build 期报 `SchemaError`；"
        "**`$string` / `$map` 这类是函数名调用，不受此限**（函数能力见 §13）。数据键在 build 期一律不检查，"
        "运行期行为**只看字段是否声明 `optional`**（这一条是唯一开关，`type` 声明不改变读法）："
        "**未声明 `optional`** 的路径一律发裸链，缺键抛 `KeyError`、下标越界抛 `IndexError`、"
        "对 `null` 取下标抛 `TypeError`——因此 `$exists($.missing)` 探不到 required 字段的缺失键"
        "（取键本身就抛了，`$exists` 根本没机会执行）；**声明 `optional: true`** 的整条链走守卫取值，"
        "缺键得 undefined（不抛），再按有无 `default` 落 `null` 或默认值，此时 `$exists($.missing)` 才返回 `false`。"
        "要容错请声明 `optional: true`，或在数据准备侧保证键存在。")
    add("")
    add("**字段按依赖关系求值**：字段在 schema 里的声明顺序只决定输出结构；计算顺序由字段之间的引用关系决定，"
        "前向引用（引用后声明的字段）合法。依赖成环或引用不存在的字段在 build 期报 `SchemaError`。")
    add("")
    add("**产物形态**：每条示例标注生成的 Python 产物形态——"
        "「内联（裸代码）」= 直接发 Python 表达式、不调 helper；"
        "「helper（`_ad`…）」= 调用 `dcg.runtime` 的专用函数（保留 undefined / 类型守卫）；"
        "「内联 Python（反引号段）」= 原样嵌入一个 Python 表达式；「`py{}`」= 发 `run()` 外的模块级辅助函数 `_pyN`（每次 build 只定义一次），调用处写作 `_pyN(...)`，不是内联。能否发裸代码只取决于操作数类型能否在 build 期证明："
        "字面量、required 且已声明类型的字段可以裸；`any`、optional、未声明类型必须走 helper。"
        "比较 / 逻辑 / 条件同理：两侧类型同类可证时发裸表达式（`(a<b)`、`(a and b)`、`(x if c else y)`），"
        "只有 JSONata 特例才留 helper——混合类型相等（`1 = true` 为 `false`）、可能 `undefined` 的操作数（undefined 传播语义）。"
        "有语义一致的 Python 内建等价的列函数也发内建表达式（`len()`、`abs()`、`in`、`split()`、`partition()`、`replace()`、`ljust()`、切片、`sum/max/min`、`bool`、`not`、`math.floor/ceil`、`a**b`、`math.sqrt`）—— **`$round` 不在内联表内**（Python `round()` 是银行家舍入，与 JSONata 的 half-away-from-zero 不同，恒定走 helper `_ro`）；"
        "`$number`、`$join`、`$type`、`$exists` 等无逐项等价或语义有差异的仍走 helper；`$sqrt` 只在可证非负时内联（负数走 helper 的 undefined 语义）。")
    add("")
    add("**裸代码 vs helper：判定顺序**（自上而下，命中即定）：")
    add("")
    add("| # | 情形 | 产物 |")
    add("|---|---|---|")
    add("| 1 | 字面量、常量链（`$.5.a.1`）、常量运算 | 裸代码 |")
    add("| 2 | 路径读值（`value` 与 `expr` 同规则）：**未声明 `optional`** | 裸链 `R['a']`、`R['a']['b']`、`R[5]['a'][1]`；缺键 = `KeyError` |")
    add("| 3 | 路径读值：**声明了 `optional`**（`value` 与 `expr` 同规则） | `_ix_get` 安全链；缺键 = `undefined` |")
    add("| 4 | 表达式里的**多段**路径 `$.a.b`、`$.5.a.1`（未声明 `optional`） | 裸链；同一路径被多处引用或作为前缀时行首绑定一次 |")
    add("| 5 | 运算/函数/字面量容器，操作数类型可证 | 裸运算符 / Python 内建 / 裸字面量 |")
    add("| 6 | 操作数类型不可证（`any`、路径值、函数结果、变量） | helper（`_ad`/`_co`/`_ar`…） |")
    add("| 7 | `optional` / `default` 字段的值 | `_ix_get` + 守卫 / `_df` |")
    add("| 8 | 动态下标 `base[idx]` | required → 裸下标；optional/default → `_ix_get` |")
    add("| 9 | 语义特例（`$number`、`$join`、`$type`、`$exists`、`$keys/$values/$lookup/$merge/$spread`、`$sqrt` 不可证非负、`$round` 2 参、混合类型相等、高阶/非确定函数） | helper |")
    add("")
    add("**表里没写的一律按 `optional` 判，不看 `type`**：`type` 声明不改变读法——"
        "`{\"type\":\"any\",\"value\":\"$.zz\"}` 与 `{\"type\":\"int64\",\"value\":\"$.zz\"}` 在未声明 `optional` 时产物完全同形"
        "（都是裸链，缺键 `KeyError`）；要让缺键变 `undefined` 只能写 `optional: true`。"
        "`type: \"any\"` 只影响后续运算走 helper 还是裸运算符（第 6 行）。")
    add("")
    add("「操作数类型可证」的来源：字面量；`optional` 非真且 `type` 为具体类型的字段；"
    "上述值的算术/比较/逻辑/条件/内建函数结果；字面量容器。")
    add("")
    add("**裸名即字段**：`hourB`、`$.hourB` 读的都是本行字段 `hourB`。**下标里的裸名同样是字段**："
        "`$.a[b]` 等价于 `$.a[$.b]`；要按字符串键取值请写引号：`$.a['b']`。")
    add("")
    add("**`$name` 只有一种命运：编成 `var`，生成期报 `SchemaError`（没有同名降级）**：")
    add("1. **`$name` 一律编成 `var` 节点**；它不在任何函数字面量的形参位置上 → **build 期 `SchemaError`**："
        "逐行模式没有变量作用域，`$name` 无处可绑。")
    add("2. **声明了同名字段也不改判**：`$i` 不是 `i`——`$.a[$i]` 不因为 schema 里声明了字段 `i` 就变成 `$.a[i]`，"
        "两者是不同的 token 形态。要按下标取字段请写裸名 `$.a[i]` / `$.a[hour]`。")
    add("")
    add("**唯一的例外是回调形参**：`$map(a, function($v){...})` 里的 `$v` 由函数字面量绑定，见 §12。"
        "**`$string` / `$map` 这类是函数名调用，不适用本节规则**（见 §12.1 能力表）。")
    add("")

    for title, prose, rows in S:
        add("## %s" % title)
        add("")
        if prose:
            add(prose)
            add("")
        add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
        add("|---|---|---|---|---|")
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
    add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
    add("|---|---|---|---|---|")
    md.extend(ARRAY_ROWS)
    add("")
    add("### 12.3 高阶函数")
    add("")
    add("回调最多接收两个位置参数；`$position()` 只在函数体内可用，表示当前下标。"
        "`$sort` 带比较器时，比较器返回 `true` 表示第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。函数体内**可以写 `py{}`**：块编译成 run() 之外的辅助函数、每元素调用一次；块内**不能引用回调参数**（`$x`），只能读原始行的 `$.字段链`。")
    add("")
    add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
    add("|---|---|---|---|---|")
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
    add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
    add("|---|---|---|---|---|")
    for e, r, v, n in NONDET_ROWS:
        shape = product_shape(e.strip("`"))
        add("| %s | %s | %s | %s | %s |" % (e, r, v, shape, n))
    add("")
    add("### 12.5 永久不支持的语法构造")
    add("")
    add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
    add("|---|---|---|---|---|")
    md.extend(construct_rows)
    add("")
    add("**生成器层（`dcg.generate()`）再拒绝的两类**——`compile_expr` 已通过，build 期才报错（契约 §7.3：两类错误都只在 build 期抛出）：")
    add("")
    add("| 写法 | 输入行 | 结果 | 产物形态 | 说明 |")
    add("|---|---|---|---|---|")
    md.extend(codegen_rows)
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
    add("## 13. 怎么扩展内置算子")
    add("")
    add("> 本章为**人工维护**（操作指引，含 15 个运算符级/单参 helper 的 EAFP 骨架表；另有 1 个旧/备用实现 `_pg`，生成器**不产出**）。")
    add("> 生成器**不再回填**本章：旧版「扩展内置算子的规范」13.1~13.10 已被评审重写，")
    add("> 若重新运行本脚本回填，会把旧章写回，故此处只留占位。详见 `JSONata 语法.md` §13。")
    add("")
    add("## 14. 覆盖统计")
    add("")
    add("| 文件 | 主题 | 例数 | 其中 gap / defect / fixed |")
    add("|---|---|---|---|")
    for path in sample_files():
        meta, cases = parse_file(path)
        _st = {}
        for case in cases:
            _st[case["state"]] = _st.get(case["state"], 0) + 1
        _tag = " / ".join("%s %d" % (k, _st[k]) for k in ("gap", "defect", "fixed") if _st.get(k))
        add("| %s | %s | %d | %s |" % (cell(meta["file"]), cell(meta["title"]), len(cases), _tag or ""))
    add("| **合计** | 正常 %d ｜ 未实现(gap) %d ｜ 仍存缺陷(defect) %d ｜ 已修复(fixed) %d | **%d** |"
        % (counts["ok"], counts["gap"], counts["defect"], counts["fixed"], len(ALL)))
    add("")
    _gap = [(path, meta, i, case) for path, meta, i, case in ALL if case["state"] == "gap"]
    if _gap:
        add("**`未实现(gap)` 的来源**（`state: gap`，共 %d 条，全部在 `examples/jx/%s`）："
            % (len(_gap), _gap[0][0].name))
        add("")
        add("| 表达式 | 现状 | 原因 |")
        add("|---|---|---|")
        for path, meta, i, case in _gap:
            add("| %s | %s | 编译期已拒绝，但拒在**生成器层**（`SchemaError`）而不是 jx 层（`Unsupported`）；"
                "jx 是纯函数、看不到 schema，把 `$x` 编成 `var` 节点是对的，逐行模式下无处求值。见 §13。 |"
                % (cell(case["expr"]), cell(str(observe(case).kind))))
        add("")
        add("这些用例断言的是**当前的拒绝行为**；如果将来引入变量绑定，它们会变红——这是有意留的复核点。")
        add("")

    add("## 15. 已知缺陷与回归保护")
    add("")
    if counts["defect"] == 0:
        add("### 15.1 未修缺陷：当前为空（`state: defect` 计数为 0）")
        add("")
        add("当前没有故意断言错误行为的用例：已知缺陷全部修复，且都迁到 §15.2 作为回归保护。"
            "再出现回归时，对应用例会坏而不是继续正确——不需要专门维护一份「当前错误行为」清单。")
    else:
        add("### 15.1 仍未修复（`state: defect`）")
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
    add("### 15.2 已修复（`state: fixed`，回归保护）")
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

    add("## 附录 A：易错点速查")
    add("")
    add("| 事项 | 说明 |")
    add("|---|---|")
    tips = [
        ("裸名", "`hourB` = 本行已声明字段，不是字符串键、也不是未定义变量"),
        ("下标里的裸名", "`$.a[b]` 里的 `b` 是字段引用；字符串键要写 `$.a['b']`"),
        ("`$name` 的唯一命运", "**一律编成 `var` 节点**（没有同名降级——`$i` 不是字段 `i`），"
                          "逐行模式没有变量作用域 → **生成器层**报 `SchemaError`。"
                          "`$$`（整篇文档根）则在 **jx 编译层**报 `Unsupported`——两者抛出层不同，但都在 build 期拒绝"),
        ("undefined vs null", "`null` 是值，会输出、参与构造；undefined 是「没有值」的哨兵，出现在输出里时原样显示为 `undefined`"),
        ("取值缺失", "非 optional 字段缺键 → 运行期 `KeyError`；声明 `optional: true` 的字段："
                    "`value` 形态缺值输出 `null`，`expr` 形态缺值保留 `undefined`（供 `$exists` 区分「没有值」与 `null`）；有 `default` 时落默认值"),
        ("`$foo` 未声明", "必须是本行已声明字段名，否则 build 期 `SchemaError`"),
        ("`$exists` 不能探测缺失键", "**未声明 `optional`** 的字段里 `$.missing` 是裸链 `R['missing']`，取键本身抛 `KeyError`，`$exists` 没机会执行；**声明了 `optional: true`** 时整链走守卫，缺键得 undefined，`$exists($.missing)` 返回 `false`。它对取到的值只判 undefined（`null` 也算存在）"),
        ("`&` 与 `+`", "`&` 连接（undefined/null 当空串）；`+` 是算术（null 参与报错）"),
        ("比较遇 undefined", "`=`/`!=` 返回 false；大小比较返回 undefined"),
        ("布尔", "`true`/`false` 是布尔值，不是 1/0；`1 = true` 为 false"),
        ("`$round`", "half away from zero（半值远离零）：`$round(2.5)` = 3、`$round(3.5)` = 4，与 JSONata 一致（不由 Python `round()` 承担）"),
        ("`$count` 对标量", "非容器标量按 1 计：`$count('abc')` = 1、`$count(5)` = 1，与 `$length` 对标量的口径一致；只有 undefined 得到 undefined"),
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
