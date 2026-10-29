"""DCG v2 运行期 helper —— undefined 语义的**唯一实现处**。

产物的引用方式（Lead 裁决 2026-10-17，方案 ii）：生成码顶部写
``from dcg.runtime import*``，**不内联** helper 体。本模块因此是
① 产物 helper 的真相源 ② 单元测试的直接被测对象；本文件里的 `SOURCE` 字典
（内联形态的等价函数体）保留给 snapshot 测试做自洽性校验，不再是产物的一部分。
两条理由把内联方案钉死了：产物要短（原契约 §7 的字符预算，已按用户裁决取消，
全量计数）容不下 300+ 字符的 helper 前导；且内联的 `$number`/`$floor` 必然带
`int(`/`float(` 与大量 `is _U`，直接违反规则 6（type 是声明不是转换）与规则 7
（只有 optional 字段带 undefined 守卫）。

硬约束（DCG 接口实现细节文档.md §4.4）：
- 每个运算符一个**专用函数**，禁止 dict-of-lambda 分派表（M0 实测派发脚手架占守卫成本 89%）
- undefined 用单例 `UNDEF` 身份比较，不用 None；短名 `_U` 是同一个对象（P0-1）
- 六条语义逐条保留（见 tests/test_codegen_snapshot.py::test_runtime_undefined_semantics）

命名取两字符（`_ad`/`_cc`/`_eq`/`_lt`...）：产物里的每个引用约占 4 字符，
两字符名是"每运算符专用函数"与产物长度之间的折中。
"""

import base64
import functools
import inspect
import json
import math
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal as _DEC

# 模块级局部别名：helper 里反复用到的 `math.sqrt` / `math.pow` 经别名访问省一层
# 属性查找。函数体内的 `import math as _m` 每次执行要 ~40 ns（模块查找 + 绑定），
# 放到模块级一次绑定（实测 `_sq` 186 -> 73 ns/op）。
_M = math
# `±inf` 字面量：`_S`/`_N` 要判非有限数，但**不能**写成 `_M.isinf`/`math.isinf`
# —— `SOURCE_NEEDS_MATH` 是扫源码里 `math.`/`_M.` 派生的，多一处就会让产物多一条
# 无用 `import math`（契约 §4.4：只有 `_fl`/`_ce`/`_sq` 需要 math）。
_INF = float("inf")
_NINF = -_INF



class _Undef:
    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "undefined"

    def get(self, key, default=None):
        """让 `value` 路径的 `dict.get(k, _U)` 链能穿过哨兵。

        `optional` + 类型可判定的字段取值发的是 `R.get('a',_U).get('b',_U)`：
        首段缺键时 `R.get('a',_U)` 给出哨兵，若哨兵没有 `get`，下一段就会抛
        `AttributeError` —— 而「写了 optional 就要兜底」要求缺键落成 undefined。
        返回 `self` 而不是 `default`：链式逐层传播 undefined，最终由生成器的
        `is_u(...)` 守卫统一转成 None。
        """
        return self


UNDEF = _Undef()

#: 产物里的短名（`from dcg.runtime import*` 一并导出）；与 `UNDEF` 是同一对象。
_U = UNDEF


def is_u(v):
    return v is UNDEF


# ---------------------------------------------------------------- 辅助谓词
def _num(v):
    """JSONata 数值谓词：bool 不是 number，None(JSON null) 不是合法算术操作数。"""
    c = type(v)
    return c is int or c is float


def _tru(v):
    """JSONata truthiness：undefined/null/false/''/0/[]/{} 为假。

    EAFP 排布：数值与字符串走两条最廉价的比较；容器与非容器合并成一次
    `len()`（`list`/`dict`/`tuple`/`str` 都能 `len`），只有不可 `len` 的
    标量才落 `except` 返回 True —— 那 1% 的路径不影响 99% 的计时。
    """
    if v is UNDEF or v is None or v is False:
        return False
    if v is True:
        return True
    c = type(v)
    if c is int or c is float:
        return v != 0
    if c is str:
        return v != ""
    try:
        return len(v) > 0
    except TypeError:
        return True


# ---------------------------------------------------------------- 二元算术
#
# 六个算术算子共用一套纯 EAFP 骨架：**先算，算出来就直接返回**；类型判定、
# undefined 传播、除零处理全部只出现在失败路径上。成功路径上没有任何前置
# 判断，只有一次 `a op b`。
#
# 「Python 能算出来就算成功」是显式语义：`True + 2 == 3`、`'a' + 'b' == 'ab'`、
# `[1] + [2] == [1, 2]`、`True < 2` 都直接产出对应结果。只有 Python 自己抛
# `TypeError` 的组合（例如 `'a' + 1`、`[] + 5`）才报 `$add: 操作数必须是数值`；
# 失败路径里再分三种结局：含 undefined → `undefined`；除零 → `undefined`；
# 其余 → 带算子名的 `TypeError`。


#: 算术失败文案。EAFP 骨架把类型判定推到失败路径，文案常量必须有名字 ——
#: 原来按 `op` 函数做 dict 查表，但 dict 查表本身要一次 hash（实测 5 ns/
#: 失败调用），且成功路径完全用不到它；改成模块级常量后成功路径零成本。
_ADD_MSG = "$add: 操作数必须是数值"
_SUB_MSG = "$subtract: 操作数必须是数值"
_MUL_MSG = "$multiply: 操作数必须是数值"
_DIV_MSG = "$divide: 除数是 0"
_MOD_MSG = "$modulo: 除数是 0"
_POW_MSG = "$power: 操作数必须是数值"


def _ad(a, b):
    """`+`（JSONata 加法）。纯 EAFP：`a + b` 不抛异常就直接返回。

    Python 的 `+` 对 `bool`/`str`/`list` 是合法运算（`True + 2 == 3`、
    `'a' + 'b' == 'ab'`、`[1] + [2] == [1, 2]`），这些结果一律照收；只有
    Python 自己抛 `TypeError`（`'a' + 1`、`[] + 5`）才报错，且先查 undefined。
    """
    try:
        return a + b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_ADD_MSG) from None


def _sb(a, b):
    """`-`。其余同 `_ad`。"""
    try:
        return a - b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_SUB_MSG) from None


def _ml(a, b):
    """`*`。其余同 `_ad`。"""
    try:
        return a * b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_MUL_MSG) from None


def _dv(a, b):
    """`/`。除数为 0 是 undefined（契约 §4.4），不是异常。"""
    try:
        return a / b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_DIV_MSG) from None
    except ZeroDivisionError:
        return UNDEF


def _md(a, b):
    """`%`。除数为 0 是 undefined。"""
    try:
        return a % b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_MOD_MSG) from None
    except ZeroDivisionError:
        return UNDEF


def _pg(a, b):
    """`^`。非数值与负底数的非整数次幂都是 undefined（不是异常）。"""
    try:
        return a ** b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_POW_MSG) from None
    except (ZeroDivisionError, OverflowError, ValueError):
        return UNDEF


# ---------------------------------------------------------------- 拼接 / 逻辑
def _cc(a, b):
    """`&`：undefined **不传播**，当空串；`null` 按 1.8 的 `$string` 转成 `'null'`。

    EAFP：正常路径（两侧都是字符串）就是一次 `a + b` + 一次结果类型确认。
    失败有两条：`str + 非 str` 抛 `TypeError`；`1 + 2` **不抛**但结果不是字符串
    （Python 的 `+` 对数值合法），两种都落到底部的转换分支 —— 转换是 1% 的
    特例，不进 99% 的正常路径。
    """
    try:
        r = a + b
    except TypeError:
        pass
    else:
        if type(r) is str:
            return r
    sa = _S(a)
    sb = _S(b)
    # `_S` 对 NaN/±Infinity 返回 undefined（1.8 抛 D3001），拼接时按空串处理
    return ("" if sa is UNDEF else sa) + ("" if sb is UNDEF else sb)


def _and(a, b):
    if _tru(a) and _tru(b):
        return True
    if a is UNDEF or b is UNDEF:
        return UNDEF
    return False


def _or(a, b):
    if _tru(a):
        return True
    if _tru(b):
        return True
    if a is UNDEF or b is UNDEF:
        return UNDEF
    return False


def _nt(a):
    if a is UNDEF:
        return UNDEF
    return not _tru(a)


def _neg(a):
    """一元 `-`：EAFP —— 上来就取负，只有 Python 抛 `TypeError` 才落 undefined。"""
    try:
        return -a
    except TypeError:
        return UNDEF


def _co(c, a, b):
    """`c ? a : b`（JSONata）：c 为 undefined -> undefined，不用 `_tru` 折叠成 false。

    这条对 `optional: true` 有意义：缺输入的字段要保持 undefined，才能被守卫
    察觉（否则会凭空造出 else 分支的值，例如缺 score 时硬给一个 'B'）。
    """
    if c is UNDEF:
        return UNDEF
    return a if _tru(c) else b


def _pos(a):
    """一元 `+`：EAFP —— 上来就取正，只有 Python 抛 `TypeError` 才落 undefined。"""
    try:
        return +a
    except TypeError:
        return UNDEF


# ---------------------------------------------------------------- 比较
def _eq(a, b):
    """`=` / `in`：任一侧 undefined -> False（JSONata 1.8）。

    性能：`isinstance(x, bool)`/`_num(x)` 是三次函数调用，而这里每次比较都要走
    —— 换成 `__class__` 比较后本机 172 -> 116 ns/op。行为逐项等价：
    * `isinstance(a, bool) != isinstance(b, bool)`：bool 的 `__class__` 恒为
      `bool`（不会有子类实例进入这里，值来自 JSON），所以等价的判据是
      `(a.__class__ is bool) != (b.__class__ is bool)`；
    * `_num(a) and _num(b)`：`_num` 就是 `c is int or c is float`，直接内联；
    * 末两个 `a == b` 分支结果相同，合并成一个（`isinstance(str,str)` 之后
      落到同一个 `a == b`，无需分开写）。
    """
    if a is UNDEF or b is UNDEF:
        return False
    ca, cb = type(a), type(b)
    if (ca is bool) != (cb is bool):
        return False
    return a == b


def _ne(a, b):
    return not _eq(a, b)


def _cmp_ok(a, b):
    """`< <= > >=` 的操作数是否同域（同为数值或同为字符串）。

    与 `_eq` 同样的内联理由；这里只回答「可比」，不回答「是否相等」。
    保留这个独立函数只为对外的语义文档与旧调用方，热路径的四个算子
    不再经它中转（见 `_lt` 起的三行实现说明）。
    """
    if a is UNDEF or b is UNDEF:
        return False
    ca, cb = type(a), type(b)
    if (ca is int or ca is float) and (cb is int or cb is float):
        return True
    return ca is str and cb is str


#: 四个比较算子的**同域判定内联版**。
#:
#: 为什么不写成 `return a > b if _cmp_ok(a, b) else UNDEF`：那是一次额外
#: 的 Python 函数调用，实测 `_gt` 123 -> 79 ns/op（快 36%），而语义完全
#: 等价 —— 正常路径（两个数值）只做两次 `type()`，其余一律 undefined。
#: EAFP 在这里没有额外空间：`<` 对 `bool`/`str` 混比**不抛异常**
#: （`True < 2`、`'a' < 1` 都会抛，但 `'a' < 'b'` 合法、`True < True`
#: 也合法），所以必须先判同域，不能靠 try 兜。


def _lt(a, b):
    """`<`。EAFP：能比较就直接返回，`TypeError`（类型不可比/含 undefined）落 undefined。"""
    try:
        return a < b
    except TypeError:
        return UNDEF


def _le(a, b):
    """`<=`。EAFP：能比较就直接返回，`TypeError`（类型不可比/含 undefined）落 undefined。"""
    try:
        return a <= b
    except TypeError:
        return UNDEF


def _gt(a, b):
    """`>`。EAFP：能比较就直接返回，`TypeError`（类型不可比/含 undefined）落 undefined。"""
    try:
        return a > b
    except TypeError:
        return UNDEF


def _ge(a, b):
    """`>=`。EAFP：能比较就直接返回，`TypeError`（类型不可比/含 undefined）落 undefined。"""
    try:
        return a >= b
    except TypeError:
        return UNDEF


def _in(a, b):
    """`in`：任一侧 undefined -> False。与 JSONata 1.8 的
    `evaluateIncludesExpression` 对齐 —— **不是** Python 的 `in`。

    1.8 的实现是：右侧**不是数组**时先包成单元素数组，再逐元素用严格相等比。
    所以 `'a' in 'abc'` 是 **False**（比的是 "abc" 这个整体，不是子串），
    `1 in [1,2]` 是 True，`true in [1]` 是 False（bool 与数值不相等）。

    成员判定走 `_eq`（JSONata 的 `=` 语义：bool 与数值不相等、undefined 不相等），
    不能用 Python 的 `in` —— `1 in [True]` 在 Python 里是 True，在 JSONata 里是 False。
    """
    if a is UNDEF or b is UNDEF:
        return False
    c = type(b)
    if c is list or c is tuple:
        return any(_eq(a, x) for x in b)
    return _eq(a, b)


# ---------------------------------------------------------------- 字符串函数
def _S(v):
    """`$string`：与 JSONata 1.8 的 `JSON.stringify` 结果对齐。

    1.8 的实现是 `JSON.stringify(arg, replacer, 0)`，replacer 对数值做
    `Number(val.toPrecision(15))`，所以：

    - `null` -> `'null'`，`undefined` -> `''`（1.8 里 undefined 走 `if (typeof arg === 'undefined') return undefined` 后由调用点转空串）
    - `2.0` -> `'2'`（整值浮点数按整数写，JSON 没有小数尾巴）
    - `0.30000000000000004` -> `'0.3'`（`toPrecision(15)` 抹掉二进制尾巴）
    - `1e21` -> `'1e+21'`、`1e-7` -> `'1e-7'`（JS 的指数写法，去零填充）
    - 数组/对象 -> JSON 文本，**紧凑无空格**（`'[1,2]'`、`'{"a":1}'`），这里不能
      用 Python 默认的 `str([1, 2])`（带空格、单引号、且会写出 `None`/`True`）
    - `NaN`/`Infinity` -> undefined（1.8 抛 D3001，DCG 按"非法值 -> undefined"处理，
      与 §1.4 附录的 P4 宽严口径一致）

    整值浮点走 `int(v)` 分支；非整值先 `float(f'{v:.15g}')` 归一，再由 Python 的
    `repr` 给出最短往返表示（`0.3`，不是 `0.30000000000000004`）。
    """
    if v is UNDEF:
        return ""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    c = type(v)
    if c is str:
        return v
    if c is int:
        return str(v)
    if c is float:
        if v != v or v in (_INF, _NINF):
            return UNDEF
        av = abs(v)
        if av >= 1e21 or (av != 0.0 and av < 1e-6):
            # JS 的 Number#toString 在这两个区间用指数写法：`1e+21` / `1e-7`
            # （Python 的 `repr` 阈值不同，但这两个区间它同样给指数形式）
            m, _, e = repr(v).partition("e")
            if e:
                n = int(e)
                if m.endswith(".0"):
                    m = m[:-2]
                return "%se%s%d" % (m, "+" if n > 0 else "-", abs(n))
        if v.is_integer():
            return str(int(v))
        s = repr(float(f"{v:.15g}"))
        if "e" in s:
            # `1e-05` 这类 JS 写成 `0.00001`：用 Decimal 转定点展开
            s = format(_DEC(s), "f")
        return s
    if c is list or c is tuple or c is dict:
        try:
            return json.dumps(v, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False, default=_S)
        except (TypeError, ValueError):
            return UNDEF
    return str(v)


def _up(s):
    """`$uppercase`：EAFP —— 直接调用 `.upper()`，非字符串才会抛 `AttributeError`。

    非字符串（数字/布尔/list/object）一律 undefined，这是**失败路径**，
    用 `except AttributeError` 兜住即可；正常路径只有一次方法查找，实测
    比 `isinstance(s, str)` 前置判断快 32%（115 -> 79 ns/op）。
    """
    try:
        return s.upper()
    except AttributeError:
        return UNDEF


def _lo(s):
    """`$lowercase`：EAFP，理由同 `_up`。"""
    try:
        return s.lower()
    except AttributeError:
        return UNDEF


def _tm(s):
    """`$trim`：EAFP，理由同 `_up`。"""
    try:
        return s.strip()
    except AttributeError:
        return UNDEF


def _ln(s):
    """`$length`：字符串长度；数组 / 对象长度。

    EAFP：`len()` 对 str/list/tuple/dict 都直接可用，只有数字/布尔/None
    会抛 `TypeError`。实测比两次 `isinstance` 判定快 21%（65 -> 51 ns/op）。
    """
    try:
        return len(s)
    except TypeError:
        return UNDEF


def _ct(a):
    """`$count`：数组返回元素个数；**其它任何值（含字符串/对象/标量）= 1**。

    JSONata 1.8 `$count()`：把不是数组的参数当成**单个元素**，所以
    `$count('abc') == 1`、`$count({'k':1}) == 1`、`$count(7) == 1`。
    只有 undefined 返回 undefined（没有值可数）。
    """
    if a is UNDEF:
        return UNDEF
    c = type(a)
    if c is list or c is tuple:
        return len(a)
    return 1


def _sp(s, sep, limit=UNDEF):
    """`$split(str, separator[, limit])`，与 JSONata 1.8 的 `str.split(sep, limit)` 对齐。

    `limit` 是**结果元素个数上限**（JS `String.prototype.split` 的第二个参数）：
    省略 = 不限；`0` = 空结果；`n > 0` = 只保留前 n 个元素（**纯截断，不做余段合并**）。
    1.8 对 `limit < 0` 抛 D3020，DCG 按"非法入参 -> undefined"处理（§1.4 附录 P4）。

    旧实现把 limit 当"段数"（`maxsplit=limit-1` 且把余段拼回去），`$split('a,b,c',',',2)`
    会得到 `['a','b,c']`，与标准的 `['a','b']` 不同。
    """
    if s is UNDEF or s is None:
        return UNDEF
    if sep is UNDEF or sep is None:
        sep = ""
    if type(s) is not str or type(sep) is not str:
        return UNDEF
    if limit is UNDEF:
        n = -1                           # 不限
    elif type(limit) is bool or not _num(limit):
        return UNDEF                     # 非数值 limit：1.8 报签名错
    else:
        n = int(limit)
        if n < 0:
            return UNDEF                 # 1.8：D3020 limit 必须是正数
    parts = list(s) if not sep else s.split(sep)   # 空分隔符 = 按字符切
    return parts if n < 0 else parts[:n]


def _jn(a, sep):
    if a is UNDEF or a is None:
        return UNDEF
    if sep is UNDEF or sep is None:
        sep = ""
    if type(a) is not list and type(a) is not tuple:
        a = [a]
    parts = []
    ap = parts.append
    ex = parts.extend
    for x in a:
        if x is UNDEF or x is None:
            continue
        c = type(x)
        if c is list or c is tuple:
            ex(_S(y) for y in x if y is not UNDEF and y is not None)
        else:
            t = _S(x)
            # `_S` 对 NaN/Infinity 返回 undefined（1.8 抛 D3001）；拼接时按空串处理
            ap("" if t is UNDEF else t)
    return sep.join(parts)


def _ss(s, start, length=UNDEF):
    """`$substring(str, start[, length])`，逐行对齐 1.8 的 `substring`。

    1.8 的算法（jsonata.js:1502-1524）：

    1. `strLength + start < 0` 时把 `start` 夹到 0（`$substring('hello',-10,2)` -> 'he'）
    2. `length` 一旦给出且 `<= 0` 就直接返回 `''`（**不是** Python 的负切片）
    3. `end = start >= 0 ? start + length : strLength + start + length`
    4. 就是 `strArray.slice(start, end).join('')`

    旧实现把长度直接丢给 `s[start:start + length]`：`$substring('hello',1,-2)` 得到
    'ell'（Python 把 -1 当尾部下标）、`$substring('hello',1.5)` 抛 TypeError 被吞成
    undefined。这里改成显式整数化的下标运算，不再依赖 Python 的切片边界语义。
    """
    if type(s) is not str:
        return UNDEF                     # 1.8 对非字符串报 T0410
    try:
        st = int(start)                  # JS 的 ToIntegerOrInfinity：向零截断
    except (TypeError, ValueError):
        return UNDEF
    n = len(s)
    if n + st < 0:
        st = 0
    if length is UNDEF:
        return s[st:] if st >= 0 else s[n + st:]
    try:
        ln = int(length)
    except (TypeError, ValueError):
        return UNDEF
    if ln <= 0:
        return ""
    end = st + ln if st >= 0 else n + st + ln
    return s[st:end]


def _ix(s, sub):
    """`$indexOf`（JSONata 1.8 第 9 条）。

    返回 `sub` 在 `s` 里第一次出现的**字符位置**（从 0 开始）；找不到返回 -1。
    `s` 不是字符串，或任一侧是 undefined / null → `undefined`。
    `sub` 不是字符串时按 JSONata 的 `$string()` 语义转换（数字、布尔都行）。

    EAFP：正常路径只有一次 `_S()` 转换 + 一次 `str.find`；非字符串抛
    `AttributeError`，落成 undefined。实测比 `isinstance` 前置快 37%
    （119 -> 75 ns/op）。

    注意 `_ix`（= `$indexOf`）与 `_ix_get`（= 安全下标）**差一字符、语义完全不同**：
    `_ix` 找子串位置、`_ix_get` 取下标值。动态下标必须走 `_ix_get`，绝不能路由到这里。
    """
    if sub is UNDEF or sub is None:
        return UNDEF
    try:
        return s.find(_S(sub))
    except AttributeError:
        return UNDEF


def _cn(a, b):
    """`$contains`：EAFP —— 字符串走 `in`（一次 C 级查找），容器走 `_eq` 线性扫描。

    正常路径不做类型判定：先试 `b in a`，失败（`a` 不可 `in`）再走异常分支
    判容器 / undefined / 标量。
    """
    try:
        return b in a
    except TypeError:
        pass
    if type(a) is list or type(a) is tuple:
        return any(_eq(b, x) for x in a)
    return False


# ---------------------------------------------------------------- 数值函数
_NUM_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$")


def _N(v):
    """`$number`：不可转 -> undefined。

    字符串走 1.8 的 `stringToNumber` 口径：**只认 JSON 数字字面量**
    （可选负号、整数/小数、可选指数），所以 `'  42  '`、`'0x1F'`、`'+1'`、
    `'Infinity'`、`'nan'` 一律 undefined（旧实现直接 `float(v)`，全都能转成功，
    其中 `'Infinity'` 还会把 `inf` 带进产物、`json.dumps` 写出非法 JSON 字面量
    `Infinity`）。整值浮点收成 `int`（`'1e3'` -> 1000）。
    """
    # 性能：`__class__` 判定提到 isinstance 链之前，数值输入一步返回（334 -> 284 ns/op）。
    c = type(v)
    if c is int:
        return v
    if c is float:
        return UNDEF if (v != v or v in (_INF, _NINF)) else v
    if v is UNDEF:
        return UNDEF
    if v is None:
        return UNDEF                  # 1.8：T0410，DCG 按"非法入参 -> undefined"
    if c is bool:
        return 1 if v else 0
    if c is str:
        if not _NUM_RE.match(v):
            return UNDEF
        f = float(v)
        return int(f) if f.is_integer() else f
    return UNDEF


def _B(v):
    """`$boolean`：JSONata 1.8 真值表（与 `_tru` 同口径）；undefined 原样传播。

    与 `_tru` 的差别只有一处：`undefined` 在 `$boolean` 里是「没有值」，原样
    返回 undefined（产物出口再转 None），而不是当作 false。字符串**不做**
    'true'/'false' 解析——非空串一律为真（`$boolean('false')` 为 True）。
    生成器只在实参真值域可证（布尔/数值/字符串）时内联 `bool()`，这三类在
    `bool()` 与本函数下结果一致，所以两条路径不会分叉。
    """
    if v is UNDEF:
        return UNDEF
    return _tru(v)


def _ab(v):
    try:
        return abs(v)
    except TypeError:
        return UNDEF


def _fl(v):
    try:
        return _M.floor(v)
    except TypeError:
        return UNDEF


def _ce(v):
    try:
        return _M.ceil(v)
    except TypeError:
        return UNDEF


def _ro(v, p=0):
    """`$round`：**银行家舍入（round half to even）**，与 JSONata 1.8 一致。

    JSONata 1.8 的 `$round` 实现是「先 `Math.round`，若恰好落在 .5 上且结果
    为奇数则减 1」——等价于 round half to even：`$round(2.5) == 2`、
    `$round(1.5) == 2`、`$round(-2.5) == -2`、`$round(0.5) == 0`。
    Python 内建 `round()` 恰好也是 half to even，但它作用在二进制浮点上，
    大数或已带误差的小数会抖动；这里用 `decimal.Decimal(str(v)).quantize(..., ROUND_HALF_EVEN)`：
    先 `str(v)` 再构造 Decimal，避免二进制浮点的表示误差影响 .5 边界判断。

    `p` 是保留的小数位（可正可负）。有 `p` 时 JSONata 走十进制移位再移位回来
    （避免乘 10 幂引入误差）；Decimal 的 quantize 本身就是十进制运算，等价。
    """
    if v is UNDEF:
        return UNDEF
    try:
        p = int(p)
    except (TypeError, ValueError):
        return UNDEF
    try:
        import decimal as _d
        q = _d.Decimal(1).scaleb(-p)  # 1e-p：p=0 -> 1, p=2 -> 0.01
        r = _d.Decimal(str(v)).quantize(q, rounding=_d.ROUND_HALF_EVEN)
        f = float(r)
        return f if p > 0 else int(f)
    except (TypeError, ValueError, ArithmeticError):
        return UNDEF


def _sq(v):
    # 性能：数值走窄异常（只捕 ValueError），省掉 math.sqrt 的类型检查开销；
    # 负数仍正确返回 undefined。非数值才落下面的慢分支。
    # `_M` 是模块级局部别名：函数体里的 `import math as _m` 每次执行约 40 ns
    # 的模块查找 + 绑定开销（实测 186 -> 73 ns/op），降到模块级一次性绑定。
    c = type(v)
    if c is int or c is float:
        try:
            return _M.sqrt(v)
        except ValueError:
            return UNDEF
    if c is bool or v is UNDEF:
        return UNDEF
    try:
        return _M.sqrt(v)
    except (TypeError, ValueError):
        return UNDEF


def _pw(a, b):
    try:
        return _M.pow(a, b)
    except TypeError:
        return UNDEF
    except (OverflowError, ValueError):
        return UNDEF


# ---------------------------------------------------------------- 数组 / 下标
def _ar(*items):
    """数组字面量：**跳过** undefined（语义 5）。

    v2.1 起 codegen 只为 JSONata 数组字面量产出 `_ar(...)`；schema 侧的嵌套
    数组走 `fields`（`_ob`/`_rl`），不会再生成 `_ar(...)`。保留供既有产物导入。

    签名刻意保持 `*items`：与 `_ob`（`_ob(pairs)` 单参）不同，数组字面量的
    实参个数在编译期就固定，`_ar(a, b, c)` 由调用方直接铺开，比构造字面量
    再进去更省一次分配（实测 3/2/4 元素 129/136/133 ns vs 147/132/171 ns）。
    """
    return [x for x in items if x is not UNDEF]


def _ob(pairs):
    """对象字面量：**丢弃** undefined 键（语义 6）。

    参数是 `[(键, 值), ...]`（键可以是任意字符串，产物用这种形态，避免为
    非法标识符的键生成 kwargs）。签名刻意保持单参、无 `*args/**kw`：
    `_ob` 只服务「字段值可能 undefined」的慢路径，可证非 undefined 的字段
    codegen 直接发字典字面量；少掉打包/解包与 `len/isinstance` 判断后，
    2/3/4 键分别从 403/448/493 ns 降到 157/182/252 ns（见 §13 扩展规范）。
    """
    out = {}
    for k, v in pairs:
        if v is not UNDEF:
            out[k] = v
    return out


def _rl(vals):
    """位置行（`type:"list"` + `fields`）：按声明顺序输出，undefined 落成 None 以保住位置。

    `_ob` 丢弃 undefined 键，对象不丢信息；位置列表没有键，丢一个元素会让后面
    所有位置左移，所以这里显式换成 None。
    """
    return [None if v is UNDEF else v for v in vals]


def _ix_get(a, i):
    """`a[i]`：容器按下标取；越界/缺键 -> UNDEF；标量对任何下标都返回自身（`a[0] == a`）。

    热路径不做 `isinstance` 分派：直接 `a[i]`，只有抛异常时才判容器（异常路径不影响
    正常路径性能）。`null` 是**值**不是「没有值」：`null[0] == null`，所以 `None`
    取下标 -> `None`（契约 §4.4 附加条目 3）；数字/布尔/字符串等标量取下标 -> 返回自身。
    """
    try:
        return a[i]
    except (IndexError, KeyError):
        return UNDEF
    except TypeError:
        # `a` 是 None（null）时 `None[i]` 抛 TypeError：null 是值，按标量规则返回自身。
        if a is None:
            return None
        # 容器但下标类型不合法（如 list 用字符串下标）-> UNDEF；标量 -> 自身
        return UNDEF if isinstance(a, (list, tuple, dict, str)) else a


# ---------------------------------------------------- 字符串补充（A 类）
def _pd(v, n, c=" "):
    """`$pad(str, width[, char])`：width > 0 右补，< 0 左补。"""
    if v is UNDEF or n is UNDEF or not _num(n):
        return UNDEF
    s = v if type(v) is str else _S(v)
    fill = c[0] if type(c) is str and c else " "
    n = int(n)
    return s.ljust(n, fill) if n >= 0 else s.rjust(-n, fill)


def _sbb(v, t):
    """`$substringBefore(str, chars)`：找不到分隔符时返回**整个字符串**。

    EAFP —— `str.find` 对非字符串抛 `AttributeError`。1.8 的实现是
    `pos <= -1 ? str : str.substr(0, pos)`（jsonata.js:1532-1544），旧实现
    返回 `''` 是错的（`$substringBefore('ab','-')` 标准是 `'ab'`）。
    """
    try:
        i = v.find(t)
    except (AttributeError, TypeError):
        return UNDEF
    return v if i < 0 else v[:i]


def _sba(v, t):
    """`$substringAfter(str, chars)`：找不到分隔符时返回**整个字符串**，理由同 `_sbb`。"""
    try:
        i = v.find(t)
    except (AttributeError, TypeError):
        return UNDEF
    return v if i < 0 else v[i + len(t):]


def _rp(v, pat, rep, limit=UNDEF):
    """`$replace(str, pattern, replacement[, limit])`：字符串模式，从左替换（EAFP）。"""
    try:
        if not pat:
            return v
        repl = rep if type(rep) is str else ("" if rep in (UNDEF, None) else _S(rep))
        return v.replace(pat, repl, int(limit) if _num(limit) else -1)
    except (AttributeError, TypeError):
        return UNDEF


def _b64e(v):
    """`$base64encode`：EAFP —— 直接编码，非字符串落异常返回 undefined。"""
    try:
        return base64.b64encode(v.encode("utf-8")).decode("ascii")
    except (AttributeError, UnicodeError):
        return UNDEF


def _b64d(v):
    """`$base64decode`：1.8 用宽松解码（`Buffer.from(v,'base64')` 忽略非法字符）。

    `$base64decode('!!!')` 标准是 `''`（解出空字节串），旧实现用
    `validate=True` 直接抛错落成 undefined。
    """
    try:
        return base64.b64decode(v.encode("ascii"), validate=False).decode(
            "utf-8", "replace")
    except (AttributeError, ValueError, UnicodeError):
        return UNDEF


def _ue(v):
    """`$encodeUrlComponent`：EAFP —— 直接 quote，非字符串落异常返回 undefined。"""
    try:
        return urllib.parse.quote(v, safe="")
    except (AttributeError, TypeError):
        return UNDEF


def _ud(v):
    """`$decodeUrlComponent`：EAFP —— 直接 unquote，非法输入落异常返回 undefined。"""
    try:
        return urllib.parse.unquote(v, errors="strict")
    except (AttributeError, TypeError, UnicodeError):
        return UNDEF


def _fb(v, radix=10):
    """`$formatBase(value[, radix])`：整数按基数 2..36。"""
    if not _num(v) or not _num(radix):
        return UNDEF
    n, r = int(v), int(radix)
    if not 2 <= r <= 36:
        return UNDEF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    neg, n, out = n < 0, abs(n), ""
    while n:
        n, m = divmod(n, r)
        out = digits[m] + out
    return ("-" if neg else "") + (out or "0")


def _fnm(v, picture, options=UNDEF):
    """`$formatNumber(value, picture[, options])`：**简化实现**（分组 + 小数位 + 百分号 + 整数位补零）。

    不支持 1.8 picture 的完整语法（正/负子图、指数图 `e`、条件 `?`、缩写 `K/M`、
    字面前缀/后缀）。整数位全为 `0` 的 picture 会按宽度补零
    （`$formatNumber(1234,'00000')` -> `'01234'`）。
    """
    if type(v) is bool or type(picture) is not str:
        return UNDEF
    if not _num(v):
        return UNDEF
    head, _, frac = picture.partition(".")
    dec = sum(1 for ch in frac if ch in "0#")
    grp = "," in head
    val = float(v) * (100 if "%" in picture else 1)
    try:
        out = format(val, ("," if grp else "") + "." + str(dec) + "f")
    except (ValueError, TypeError):
        return UNDEF
    ipic = head.replace(",", "")
    if not grp and ipic and not ipic.strip("0") and len(out.lstrip("-")) < len(ipic):
        neg = out[0] == "-"
        out = ("-" if neg else "") + out.lstrip("-").zfill(len(ipic))
    return out + ("%" if "%" in picture else "")


# ---------------------------------------------------- 数值/数组（A 类）
def _sum_type_msg(a):
    """`$sum` 的失败文案。

    默认文案与旧实现逐字一致（``$sum: 元素必须是数值``）。只在一个形态上
    额外给出指向性提示：整份实参是 **dict**。`dict` 在 Python 里可迭代（迭代
    出的是键），所以 ``sum({'v':[1,2,3]})`` 会进逐元素相加并报"元素必须是数值"
    —— 用户看到这句完全想不到问题出在"把整个对象传给了 $sum"。

    这正是 ``output.after`` 在 `dict`/`index` 形状下最容易踩的一处：那里 `$`
    是**列字典**（``{'v':[..]}``），写 ``= $sum($)`` 就会走到这里（README §4.1
    里 `$` 语义表附近有说明）。
    """
    if type(a) is dict:
        return ("$sum: 元素必须是数值（实参是整个对象，不是数组 —— 想对某一列求和请写 "
                "$sum($.列名)；`output.after` 里 `$` 是列字典时尤其如此）")
    return "$sum: 元素必须是数值"


def _sm(a):
    """`$sum(array)`：数值求和；undefined 跳过，不可加值报错。

    **纯 EAFP**（用户裁决 m03683 / m04706 / m04715）：成功路径就是一句
    `sum(xs)`，**没有任何 `is UNDEF` 或类型前置判断** —— 标量实参、整份
    `undefined`、数组里含 `undefined`、混入不可加值，全部落到 except 里处理，
    这些都是小概率事件。`xs` 的取法靠 `try/except TypeError` 而非 `type(a)` 判断。

    用户裁决（m04644）：不对 bool 做语义检查，按 Python 自身语义走
    （`True + 1 == 2` 合法），所以成功路径不需要任何逐元素扫描。

    失败路径语义：数组里的 `undefined` 跳过、其余不可加值报
    ``TypeError: $sum: 元素必须是数值``（文案与旧实现逐字一致）；整份实参是
    `undefined` 时返回 `undefined`；标量实参按单元素处理（`$sum(3)` -> 3）。

    性能（10 万元素，µs/次）：旧逐元素实现 int 4411 / float 5022；
    本实现对标的裸 `sum()` 是 int 1249 / float 1233，实测 int 1200 / float 1217
    —— **与裸 `sum()` 同速，比旧实现快约 3.7 倍**。
    """
    # `dict` 必须在**任何 try 之前**拦下（DEFECT-002 复核发现的静默错误）：
    # `sum({0: [...], 1: [...]})` 迭代出的是**键**，若键恰好是整数
    # （`index` 形状的 `$` 就是 `{0: [...], 1: [...]}`），`sum` 不抛异常，
    # 直接返回 `0 + 1 = 1` —— 用户拿到一个「看起来合法」的错误结果。
    #
    # 注意**不能**放进下面的 `try` 里：那层 `except TypeError` 会把这个
    # 人为 `raise` 吞掉，转去走「不可迭代」分支，`iter({...})` 对 dict 是
    # 成功的，结果仍然是对键求和 —— 守卫会静默失效（这是踩过的坑）。
    # 成本实测：10 万元素 -0.28%（噪声内），3 元素小数组 +42 ns。
    if type(a) is dict:
        raise TypeError(_sum_type_msg(a))
    try:
        xs = a
        try:
            return sum(xs)
        except TypeError:
            raise
    except TypeError:
        # 小概率路径：a 不可迭代（标量 / undefined / None）。
        try:
            if a is UNDEF:
                return UNDEF
            it = iter(a)
        except TypeError:
            # 标量实参：按单元素处理，加不上去就是类型错误。
            try:
                return a + 0
            except TypeError:
                raise TypeError("$sum: 元素必须是数值") from None
        total = 0
        for v in it:
            if v is UNDEF:
                continue
            try:
                total += v
            except TypeError:
                raise TypeError(_sum_type_msg(a)) from None
        return total

def _mn(a):
    # 性能：单遍扫描，不再先过滤出临时列表再 min()（350 -> 210 ns/op）。
    # EAFP：直接比较，不可比的值落 except 跳过 —— 省掉每元素的类型判定。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升：1.8 的 validateArguments 对数组型形参把标量包成单元素数组
        # （`$min(1)` -> 1、`$max(1)` -> 1、`$average(2)` -> 2）
        if a is UNDEF:
            return UNDEF
        a = (a,)
    best = None
    found = False
    for v in a:
        if v is True or v is False or v is UNDEF:
            continue
        try:
            if not found or v < best:
                best = v
                found = True
        except TypeError:
            continue
    return best if found else UNDEF


def _mx(a):
    # 性能：单遍扫描，不再先过滤出临时列表再 max()（350 -> 202 ns/op）。
    # EAFP：直接比较，不可比的值落 except 跳过。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升，理由同 `_mn`
        if a is UNDEF:
            return UNDEF
        a = (a,)
    best = None
    found = False
    for v in a:
        if v is True or v is False or v is UNDEF:
            continue
        try:
            if not found or v > best:
                best = v
                found = True
        except TypeError:
            continue
    return best if found else UNDEF


def _av(a):
    # 性能：单遍累加，不再「过滤列表 + all() 校验 + sum()」三遍（656 -> 231 ns/op）。
    # EAFP：直接累加，非数值落 except 返回 undefined（不作猜测性跳过）。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升，理由同 `_mn`
        if a is UNDEF:
            return UNDEF
        a = (a,)
    n = 0
    total = 0
    for v in a:
        if v is UNDEF:
            continue
        try:
            total += v
        except TypeError:
            return UNDEF
        else:
            tc = type(v)
            if tc is int or tc is float:
                n += 1
            else:
                return UNDEF
    return total / n if n else UNDEF


def _ap(a, b):
    """`$append`：两侧各自「是数组就当数组、否则当单元素」，undefined 当空。

    EAFP：`list(x)` 对不可迭代的标量抛 `TypeError`，落 `except` 再包成
    单元素列表 —— 正常路径（两侧都是数组）只有两次 `list()` + 一次拼接。
    """
    return _as_list(a) + _as_list(b)


def _as_list(v):
    """把「不保证是数组」的实参提升成数组（JSONata 1.8 的数组型形参语义）。

    快路径：已经是 `list` 就**原样返回**（不复制）——两个调用方（`_ap` 的拼接、
    生成码里的推导式）都只读不修改；`tuple` 与其它可迭代物仍走 `list()` 复制。
    """
    if v is UNDEF:
        return []
    if type(v) is list:
        return v
    try:
        return list(v)
    except TypeError:
        return [v]


def _rv(a):
    """`$reverse`：**字符串是单元素序列**，不是字符序列。

    1.8 的 `$reverse("abc")` 是 `["abc"]`（数组型形参把标量提升成一个元素的数组，
    再对数组反转）；旧实现 `list(reversed(a))` 把字符串按字符反转成
    `["c","b","a"]`，是错的。
    """
    c = type(a)
    if c is list or c is tuple:
        return list(reversed(a))
    if a is UNDEF:
        return UNDEF
    return [a]


def _sort_merge(left, right, comp):
    """`$sort` 比较器路径的归并一步（1.8 `merge` 的逐行移植）。

    提成模块级函数是为了让**无比较器**的快路径不付闭包创建的钱：
    闭包写在 `_so` 里时，每次调用都会新建 `merge`/`msort` 两个函数对象，
    实测让 `$sort([i,i+1])` 从 0.15 µs 涨到 0.24 µs。
    """
    out = []
    ap = out.append
    i = j = 0
    nl = len(left)
    nr = len(right)
    while i < nl and j < nr:
        # 关键：比较器返回真就取右元素。JS 真值判定意味着负数也是真，
        # 故 `$a-$b` 得 `[2,1,3]` 而不是 `[1,2,3]`。
        if comp(left[i], right[j]):
            ap(right[j])
            j += 1
        else:
            ap(left[i])
            i += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out


def _sort_msort(xs, comp):
    """二分递归归并（1.8 `msort` 的逐行移植）；`len <= 1` 原样返回。"""
    n = len(xs)
    if n <= 1:
        return xs
    m = n // 2
    return _sort_merge(_sort_msort(xs[:m], comp), _sort_msort(xs[m:], comp), comp)


def _so(a, f=UNDEF):
    """`$sort(array[, comparator])`：1.8 的**归并排序**逐行移植（jsonata.js:3251-3317）。

    比较器语义不是"三值比较"，而是「**返回真就交换**」：`merge` 每步看
    `if comp(left[0], right[0])` 决定取右还是取左。JS 的真值判定意味着
    `$sort([3,1,2], $a-$b)` 里负数也是真 —— 结果 `[2,1,3]` 而不是 `[1,2,3]`。
    旧实现用 `cmp_to_key` + 三值化把这种比较器当成了符号函数，因此算错。

    无比较器时注入 1.8 的默认 `a > b`；标量入参按单元素数组处理
    （`$sort(1)` -> `[1]`，`arr.length <= 1` 时数组原样返回，不塌缩）。
    """
    c = type(a)
    if c is not list and c is not tuple:
        if a is UNDEF:
            return UNDEF
        a = (a,)
    if len(a) <= 1:
        return list(a)
    try:
        if f is UNDEF:
            return sorted(a)
        return _sort_msort(list(a), f)
    except TypeError:
        return UNDEF


def _ds(a):
    """`$distinct(array)`：保序去重（等价判定走 `_eq`）。

    标量入参按 1.8 的单值提升处理：`$distinct(1)` -> `1`（单元素数组去重后
    塌缩回单值），所以这里标量原样返回而不是 undefined。
    """
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF if a is UNDEF else a
    out = []
    ap = out.append
    for v in a:
        for w in out:
            if _eq(v, w) is True:
                break
        else:
            ap(v)
    return out


def _mg(objs):
    """`$merge`：对象数组合并；任一元素不是对象 -> undefined。

    标量入参按 1.8 的口径处理：`$merge({'a':1})` -> `{'a':1}`（单元素提升后
    只有一个对象，合并即它本身）；非对象的标量（如 `$merge(1)`）仍落 undefined。
    EAFP：正常路径就是 `out.update(o)`，`update` 对非对象直接抛
    `TypeError`/`ValueError`，落 `except` 再判 undefined 与非法元素。
    """
    c = type(objs)
    if c is not list and c is not tuple:
        if objs is UNDEF:
            return UNDEF
        objs = (objs,)
    out = {}
    upd = out.update
    for o in objs:
        if o is UNDEF:
            continue
        try:
            upd(o)
        except (TypeError, ValueError):
            return UNDEF
    return out


def _ky(o):
    """`$keys`：对象取键；**数组则逐元素取键后按出现顺序去重**（1.8 语义）。

    1.8 的 `$keys([{'a':1},{'b':2}])` 是 `['a','b']`（`keys` 对数组输入逐个
    递归再合并）。旧实现直接 `o.keys()`，数组抛 `AttributeError` 落成 undefined。
    """
    if o is UNDEF:
        return UNDEF
    c = type(o)
    if c is dict:
        # 空对象 -> 空序列 -> undefined（`$keys({})` 标准就是 undefined，
        # 与 P1 附录里"序列塌缩"同源，这条单点对齐成本极低）
        return list(o.keys()) or UNDEF
    if c is list or c is tuple:
        out = []
        ap = out.append
        seen = set()
        for x in o:
            k = _ky(x)
            if k is UNDEF:
                continue
            for name in k:
                if name not in seen:
                    seen.add(name)
                    ap(name)
        return out or UNDEF
    return UNDEF


def _vl(o):
    """`$values`：EAFP —— 非对象没有 `values()`，`AttributeError` 即 undefined。"""
    try:
        return list(o.values())
    except AttributeError:
        return UNDEF


def _lk(o, k):
    """`$lookup`：EAFP —— 直接 `get`，非对象落 `AttributeError` 返回 undefined。

    与 JSONata 官方语义对齐：第一实参为数组时逐个元素递归求值，**只收集非
    undefined 的命中值** —— 空数组或全未命中得到 `[]`（不是 undefined）；
    数组里的标量元素按非对象处理、静默跳过，不报错。
    """
    try:
        return o.get(k, UNDEF)
    except AttributeError:
        pass
    c = type(o)
    if c is list or c is tuple:
        out = []
        ap = out.append
        for x in o:
            v = _lk(x, k)
            if v is not UNDEF:
                ap(v)
        return out
    return UNDEF


def _sr(o):
    """`$spread`：对象 -> 单键对象数组；数组 -> 原样；标量 -> undefined。"""
    c = type(o)
    if c is dict:
        return [{k: v} for k, v in o.items()]
    if c is list or c is tuple:
        return list(o)
    return UNDEF


def _sg(a, f=UNDEF):
    """`$single(array[, predicate])`：恰好一个元素时返回它，否则 undefined。

    标量入参按 1.8 的单值提升处理：`$single(1)` -> `1`、`$single('a')` -> `'a'`。
    """
    c = type(a)
    if c is not list and c is not tuple:
        if a is UNDEF:
            return UNDEF
        a = (a,)
    xs = [v for v in a if v is not UNDEF]
    if f is not UNDEF:
        xs = [v for i, v in enumerate(xs) if _tru(f(v, i))]
    return xs[0] if len(xs) == 1 else UNDEF


# ---------------------------------------------------- 高阶函数（B 类）
def _mp(a, f):
    """`$map(array, function($v,$i){...})`：结果跳过 undefined。

    EAFP：`len()` 成功即容器，失败即标量（包成单元素）—— 省掉 `isinstance`。
    """
    if a is UNDEF:
        return UNDEF
    try:
        len(a)
    except TypeError:
        a = (a,)
    out = []
    ap = out.append
    for i, v in enumerate(a):
        r = f(v, i)
        if r is not UNDEF:
            ap(r)
    return out


def _fi(a, f):
    """`$filter`：先做数组型形参的单值提升，再 EAFP 交给推导式。

    提升规则与 `$map` 一致（1.8 的 `validateArguments`）：`undefined` 原样返回，
    不可迭代的标量包成单元素 —— 故 `$filter(1, $v > 0)` 得 `[1]`（旧实现落
    `TypeError` 返回 undefined，与 `$map` 不一致）。
    """
    if a is UNDEF:
        return UNDEF
    try:
        len(a)
    except TypeError:
        a = (a,)
    try:
        return [v for i, v in enumerate(a) if _tru(f(v, i))]
    except TypeError:
        return UNDEF


def _rd(a, f, init=UNDEF):
    """`$reduce(array, function($acc,$v){...}[, init])`：逐元素折叠。"""
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF
    n = len(a)
    if not n:
        return init
    if init is UNDEF:
        acc, start = a[0], 1
    else:
        acc, start = init, 0
    for i in range(start, n):
        acc = f(acc, a[i])
    return acc


def _ea(o, f):
    """`$each(obj, function($v,$k){...})`：按键值对回调。"""
    c = type(o)
    if c is dict:
        items = list(o.items())
    elif c is list or c is tuple:
        items = list(enumerate(o))
    else:
        return UNDEF
    out = []
    for k, v in items:
        r = f(v, k)
        if r is not UNDEF:
            out.append(r)
    return out


def _sf(o, f):
    """`$sift(obj, function($v,$k){...})`：按谓词过滤对象键。

    EAFP：直接 `items()`，非对象抛 `AttributeError` 返回 undefined。
    """
    try:
        return {k: v for k, v in o.items() if _tru(f(v, k))}
    except AttributeError:
        return UNDEF


# ---------------------------------------------------- 非确定函数（C 类）
class _Clock:
    """可替换时钟：``dcg.runtime.CLOCK.now_ms()`` 返回 epoch 毫秒。"""

    def now_ms(self):
        return int(time.time() * 1000)


class _RandomSource:
    """可替换随机源：``dcg.runtime.RANDOM``（注入固定源即可复跑证据）。"""

    def __init__(self):
        self._r = random.Random()

    def random(self):
        return self._r.random()

    def shuffle(self, xs):
        out = list(xs)
        self._r.shuffle(out)
        return out


CLOCK = _Clock()
RANDOM = _RandomSource()


def _nw():
    """`$now()`：ISO 8601 UTC 毫秒时间戳。"""
    ms = CLOCK.now_ms()
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _ms():
    """`$millis()`：epoch 毫秒。"""
    return CLOCK.now_ms()


def _rn():
    """`$random()`：`[0,1)` 浮点。"""
    return RANDOM.random()


def _sh(a):
    """`$shuffle(array)`：EAFP —— 直接交给随机源洗牌，标量落异常返回 undefined。"""
    try:
        return RANDOM.shuffle(a)
    except (TypeError, AttributeError):
        return UNDEF


def _RW(x):
    """`input.source="index"` 解包后的行容器归一化：dict -> values（保序）。

    EAFP：先当作 dict 取 `values()`，`AttributeError` 说明本来就是序列，原样返回。
    """
    try:
        return list(x.values())
    except AttributeError:
        return x


# ------------------------------------------------------------ 数据级钩子守卫
# `before` / `after` 是「整体数据 → 新数据」的转换钩子（§4.1），返回值直接替换
# 行集合 / 最终结果。没有守卫时两类错都会**静默产出脏结果**（Lead 实测）：
#   * `before` 返回 undefined → `for R in UNDEF` 抛 TypeError: '_Undef' object
#     is not iterable —— 文案完全看不出是 before 造成的；
#   * `after` 返回 undefined → 函数返回 undefined 哨兵，不是合法 JSON；
# `after` 返回标量则是合法覆盖（「结果结构随你定」），只拦哨兵。
_HOOK_HINT = "钩子必须返回数据本身（裸 `$` 就是它）；要取字段请写 $.路径"


def _hb(v):
    """`before` 的返回值守卫：必须是可迭代的行集合（也容忍外部函数返回 dict）。"""
    if v is UNDEF:
        raise TypeError("output.before: 返回了 undefined（没有值）—— " + _HOOK_HINT)
    if isinstance(v, (list, tuple, dict)):
        return v
    raise TypeError(
        "output.before: 必须返回数组（行集合），得到 %s —— %s" % (_ty(v), _HOOK_HINT))


def _ha(v):
    """`after` 的返回值守卫：拦 undefined 哨兵（它会原样泄进输出，不是合法 JSON）。"""
    if v is UNDEF:
        raise TypeError("output.after: 返回了 undefined（没有值）—— " + _HOOK_HINT)
    return v



# ---------------------------------------------------------------- 类型 / 存在
_TY_MAP = {int: "number", float: "number", str: "string", bool: "boolean",
           list: "array", tuple: "array", dict: "object", type(None): "null"}


def _ty(v):
    """`$type`：正常路径一次 `dict` 查表即返回；只有自定义子类才落 isinstance 链。"""
    if v is UNDEF:
        return UNDEF
    t = _TY_MAP.get(type(v))
    if t is not None:
        return t
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple)):
        return "array"
    return "object"


def _ex(v):
    return v is not UNDEF


def _b(v):
    return v


def _df(v, d):
    """字段级 default：值为 undefined 时替换成编译期常量 d（§2.3）。"""
    return d if v is UNDEF else v


HELPERS = {
    "_ad": _ad, "_sb": _sb, "_ml": _ml, "_dv": _dv, "_md": _md, "_pg": _pg,
    "_cc": _cc, "_and": _and, "_or": _or, "_nt": _nt, "_neg": _neg, "_pos": _pos,
    "_eq": _eq, "_ne": _ne, "_lt": _lt, "_le": _le, "_gt": _gt, "_ge": _ge,
    "_in": _in, "_S": _S, "_up": _up, "_lo": _lo, "_tm": _tm, "_ln": _ln,
    "_ct": _ct, "_sp": _sp, "_jn": _jn, "_ss": _ss, "_ix": _ix, "_cn": _cn,
    "_N": _N, "_B": _B, "_ab": _ab, "_fl": _fl, "_ce": _ce, "_ro": _ro,
    "_sq": _sq, "_pw": _pw, "_ar": _ar, "_ob": _ob, "_ix_get": _ix_get,
    "_ty": _ty, "_ex": _ex, "_b": _b, "_num": _num, "_tru": _tru,
    "_co": _co, "_df": _df, "_rl": _rl, "is_u": is_u,
    # 字符串补充（A 类）
    "_pd": _pd, "_sbb": _sbb, "_sba": _sba, "_rp": _rp, "_b64e": _b64e,
    "_b64d": _b64d, "_ue": _ue, "_ud": _ud, "_fb": _fb, "_fnm": _fnm,
    # 数值 / 数组（A 类）
    "_sm": _sm, "_mn": _mn, "_mx": _mx, "_av": _av, "_ap": _ap,
    "_as_list": _as_list, "_rv": _rv,
    "_so": _so, "_ds": _ds, "_mg": _mg, "_ky": _ky, "_vl": _vl, "_lk": _lk,
    "_sr": _sr, "_sg": _sg,
    # 高阶函数（B 类）
    "_mp": _mp, "_fi": _fi, "_rd": _rd, "_ea": _ea, "_sf": _sf,
    # 非确定函数（C 类）与输入归一化
    "_nw": _nw, "_ms": _ms, "_rn": _rn, "_sh": _sh, "_RW": _RW,
    # 数据级钩子守卫（§4.1）
    "_hb": _hb, "_ha": _ha,
}

UNDEFINED_SEMANTICS = (
    "& 不传播 undefined（当空串）",
    "or/and 的 undefined 参与运算",
    "= / != 遇 undefined 返回 False",
    "大小比较遇 undefined 返回 undefined",
    "数组字面量跳过 undefined",
    "对象字面量丢弃 undefined 键",
)


# ------------------------------------------------------ 助手源码文本（派生）
# 唯一实现是上面的 `HELPERS`（真实 callable，产物 `from dcg.runtime import *`
# 跑的就是它）。`SOURCE` 只是它的**源码视图**，由 `inspect.getsource` 反推 ——
# 手抄一份源码文本曾产出大量漂移（`_cn`/`_ix_get`/`_jn` 语义相反、`_N` 引用不存在
# 的 `_NNN`、`_fl/_ce/_sq` 缺 `_m`）。派生后漂移在构造上不可能发生；
# `tests/test_codegen_snapshot.py` 逐名对拍守住这条。
def _helper_source(name, fn):
    """`HELPERS[name]` -> 源码文本；取不到就报错，绝不静默留空。"""
    try:
        text = inspect.getsource(fn)
    except (OSError, TypeError) as exc:  # pragma: no cover - 打包/REPL 无源码时
        raise RuntimeError(f"无法取得 helper {name} 的源码: {exc}") from exc
    return text if text.endswith("\n") else text + "\n"


SOURCE = {name: _helper_source(name, fn) for name, fn in HELPERS.items()}


# 产物里需要 `import math` 的助手。产物发的是**裸** `import math`（不是
# `import math as _m`），判据是现扫产物文本里是否出现 `math.`（见
# `dcg/codegen.py` 的 `_RUNTIME_IMPORT` 附近）；产物里没有 `_m` 这个别名。
#: 源码里依赖 `math` 的助手 —— 内联它们时必须保证产物已 `import math`。
#:
#: 判定必须**同时**认两种拼法：
#:   * `math.xxx` —— 直接模块属性访问；
#:   * `_M.xxx`   —— 模块级别名（`_M = math`，见本文件 `_M` 的说明：函数体内的
#:     `import math as _m` 每次执行多 ~40 ns，所以热路径助手统一走模块级别名）。
#:
#: 只认 `"math" in t` 会漏掉全部 `_M.` 形态 —— 2026-10 的 `_fl`/`_ce` 别名化改动
#: 就把这里从 3 个名字静默缩成 1 个（`{"_sq"}`），内联后产物会 `NameError: _M`。
#: 契约口径（接口文档 §4.4 / 架构 §7.4）是 `{"_fl","_ce","_sq"}`：这三个是
#: **可被 codegen 内联**的函数。`_pw` 也走 `_M.`，但 `$power` 的内联由
#: `codegen._inline_text` 直接发 `a**b`、从不内联 helper 体，故它对**内联**无意义，
#: 这里显式排除，让「依赖集合」的语义保持单一：*会被内联的 math 依赖*。
#: **生成器不使用这个名字**（`dcg/codegen.py` 判 `import math` 时现扫产物文本里的
#: `math.` 字面量，不读任何清单）。它的唯一身份是**测试自检不变量**：钉住
#: "哪些 helper 会内联出 math 依赖"，由 `tests/test_codegen_snapshot.py:89`
#: （`:108` 钉死集合）与 `tests/test_docs_consistency.py:111` 覆盖。
#: 命名保留 `SOURCE_` 前缀是历史原因（早期生成器确实读它），改名会破坏上述测试
#: 与三份文档的引用，故只在此处显式声明语义边界。
SOURCE_NEEDS_MATH = frozenset(
    n for n, t in SOURCE.items() if "math" in t or "_M." in t) - {"_pw"}

#: 产物头部的前导常量。
#:
#: P0-1 裁决（Lead 2026-10-17）：undefined 哨兵**必须是自建单例对象**，禁止字面量
#: `_U=0` —— 后者让 `0`/`0.0`/`False` 与 undefined 在运行期不可区分，破坏语义 1/3。
#: 产物现在直接 `from dcg.runtime import*`（见 codegen 模块 docstring 的出处说明），
#: 所以哨兵只需要一份：本模块的 `UNDEF`，短名 `_U` 也在 `__all__` 里导出。
SOURCE_PRELUDE = ""

#: 产物里的短名哨兵（P0-1）：与 `UNDEF` **同一个**单例对象，绝不是字面量。
_U = UNDEF

#: `from dcg.runtime import*` 的导出面（产物就靠这一行拿到全部 helper）。
#: 显式列出是因为 `import *` 默认不导出下划线开头名字，而 helper 全是下划线名。
__all__ = sorted(
    set(SOURCE) | set(HELPERS) | {"_cmp_ok"} |
    {"UNDEF", "_U", "is_u", "HELPERS", "SOURCE", "SOURCE_NEEDS_MATH",
     "SOURCE_PRELUDE", "UNDEFINED_SEMANTICS", "CLOCK", "RANDOM"}
)
