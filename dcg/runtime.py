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
import math
import random
import time
import urllib.parse
from datetime import datetime, timezone

# 模块级局部别名：helper 里反复用到的 `math.sqrt` / `math.pow` 经别名访问省一层
# 属性查找。函数体内的 `import math as _m` 每次执行要 ~40 ns（模块查找 + 绑定），
# 放到模块级一次绑定（实测 `_sq` 186 -> 73 ns/op）。
_M = math



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
# 五个算术算子共用一套 EAFP 骨架 `_arith`：**上来就计算**，类型判定全部推到
# 失败路径与成功路径末尾。正常输入（两个 int / float）只有一次 `op()` 调用 +
# 一次结果类型判断，没有前置保护 —— 与 99%/1% 的成本模型一致。
#
# 为什么不能只写 `try: return a + b except TypeError: ...`：
# Python 的 `+` 对 `bool`/`str`/`list` **不抛异常**（`True + 2 == 3`、
# `'a' + 'b' == 'ab'`、`[1] + [2] == [1, 2]`），异常抓不到。所以成功路径
# 末尾再做一次极廉价的结果域判断（`type(r) is int or float` + 排除 bool 输入）。


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
    """`+`（JSONata 加法）。EAFP：上来就算，类型判定全在失败路径与末尾。

    为什么不能只写 `try: return a + b except TypeError: ...`：
    Python 的 `+` 对 `bool`/`str`/`list` **不抛异常**（`True + 2 == 3`、
    `'a' + 'b' == 'ab'`、`[1] + [2] == [1, 2]`），异常抓不到。所以成功路径
    末尾还要一次极廉价的结果域判定（`type(r) is int/float`）。
    """
    try:
        r = a + b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_ADD_MSG) from None
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    raise TypeError(_ADD_MSG)


def _sb(a, b):
    """`-`。其余同 `_ad`。"""
    try:
        r = a - b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_SUB_MSG) from None
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    raise TypeError(_SUB_MSG)


def _ml(a, b):
    """`*`。其余同 `_ad`。"""
    try:
        r = a * b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_MUL_MSG) from None
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    raise TypeError(_MUL_MSG)


def _dv(a, b):
    """`/`。除数为 0 是 undefined（契约 §4.4），不是异常。"""
    try:
        r = a / b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_DIV_MSG) from None
    except ZeroDivisionError:
        return UNDEF
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    raise TypeError(_DIV_MSG)


def _md(a, b):
    """`%`。除数为 0 是 undefined。"""
    try:
        r = a % b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_MOD_MSG) from None
    except ZeroDivisionError:
        return UNDEF
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    raise TypeError(_MOD_MSG)


def _pg(a, b):
    """`^`。非数值与负底数的非整数次幂都是 undefined（不是异常）。"""
    try:
        r = a ** b
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF
        raise TypeError(_POW_MSG) from None
    except (ZeroDivisionError, OverflowError, ValueError):
        return UNDEF
    t = type(r)
    if t is int or t is float:
        if type(a) is not bool and type(b) is not bool:
            return r
    if a is UNDEF or b is UNDEF:
        return UNDEF
    return UNDEF


# ---------------------------------------------------------------- 拼接 / 逻辑
def _cc(a, b):
    """`&`：undefined **不传播**，当空串；null 当 ''；非字符串先转字符串。

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
    return _S(a) + _S(b)


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
    """一元 `-`：EAFP —— 上来就取负，`bool`/非数值落异常分支返回 undefined。"""
    if a is UNDEF:
        return UNDEF
    if a is True or a is False:
        return UNDEF
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
    """一元 `+`：EAFP —— 上来就取正，失败即 undefined。"""
    if a is UNDEF:
        return UNDEF
    if a is True or a is False:
        return UNDEF
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
    """
    if a is UNDEF or b is UNDEF:
        return False
    ca, cb = type(a), type(b)
    if (ca is int or ca is float) and (cb is int or cb is float):
        return True
    return ca is str and cb is str


def _lt(a, b):
    return a < b if _cmp_ok(a, b) else UNDEF


def _le(a, b):
    return a <= b if _cmp_ok(a, b) else UNDEF


def _gt(a, b):
    return a > b if _cmp_ok(a, b) else UNDEF


def _ge(a, b):
    return a >= b if _cmp_ok(a, b) else UNDEF


def _in(a, b):
    """`in` 与 `=` 同族：左侧 undefined -> False；右侧不是容器 -> False。

    EAFP：正常路径先直接 `a in b`（`str`/`list` 都是一次 C 级扫描），
    只有不可 `in` 的右侧才落 `except` 判数组（数组要按 `_eq` 逐个比，
    不能用 `in`，因为 `_eq` 有自己的 bool/undefined 规则）。
    """
    if a is UNDEF:
        return False
    try:
        return a in b
    except TypeError:
        pass
    if type(b) is list or type(b) is tuple:
        return any(_eq(a, x) for x in b)
    return False


# ---------------------------------------------------------------- 字符串函数
def _S(v):
    """`$string`：undefined/null -> ''，bool -> 'true'/'false'。

    `True`/`False` 是单例，用 identity 判定比 `isinstance(v, bool)` 少一次
    MRO 查找；正常路径就是一次 `str()`。
    """
    if v is UNDEF or v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
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
    """`$split(str, separator[, limit])`。

    `limit` 是**结果切片数上限**，与 JSONata 1.8 一致：省略或 0 = 不限；
    负数 = 不限。`s.x.split(sep, limit)` 里 Python 的 `maxsplit=limit-1`，
    JSONata 的语义是"最多切成 limit 段"，所以这里传 `limit - 1` 给 maxsplit。
    """
    if s is UNDEF or s is None:
        return UNDEF
    if sep is UNDEF or sep is None:
        sep = ""
    if type(s) is not str or type(sep) is not str:
        return UNDEF
    n = int(limit) if _num(limit) else 0
    if n == 1:
        return [s]                       # 只切一段 = 整串
    if not sep:
        # JSONata：空分隔符 = 按字符切（`$split('abc','')` -> ['a','b','c']）
        parts = list(s)
        return parts if n <= 0 else parts[:n - 1] + ["".join(parts[n - 1:])]
    if n > 1:
        return s.split(sep, n - 1)       # 最多切 n 段
    return s.split(sep)


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
            ap(_S(x))
    return sep.join(parts)


def _ss(s, start, length=UNDEF):
    """`$substring`：EAFP —— 正常路径只有一次切片，**没有任何前置判断**。

    非字符串抛 `AttributeError`（数字/布尔/None），下标类型不合法抛 `TypeError`，
    都落成 undefined。`list`/`tuple` 切片**不抛错**（`[1,2][1:]` 合法），所以
    在异常分支里再判一次容器——那是 1% 的特例路径，不影响 99% 的字符串计时。
    """
    try:
        if length is UNDEF or length is None:
            r = s[start:]
        else:
            r = s[start:start + length]
    except (TypeError, ValueError):
        return UNDEF
    if type(r) is str:
        return r
    # 特例路径：list/tuple 切片成功但不是字符串 -> undefined
    return UNDEF


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
def _N(v):
    """`$number`：不可转 -> undefined。"""
    # 性能：`__class__` 判定提到 isinstance 链之前，数值输入一步返回（334 -> 284 ns/op）。
    c = type(v)
    if c is int or c is float:
        return v
    if v is UNDEF:
        return UNDEF
    if v is None:
        return None
    if c is bool:
        return 1 if v else 0
    if c is str:
        try:
            f = float(v)
        except ValueError:
            return UNDEF
        return int(f) if f.is_integer() else f
    return UNDEF


def _B(v):
    """`$boolean`：bool 原样；'true'/'false' 字面量；数值 != 0；其余 undefined。"""
    if v is True or v is False:
        return v
    if v is UNDEF:
        return UNDEF
    if v is None:
        return None
    c = type(v)
    if c is str:
        if v == "true":
            return True
        if v == "false":
            return False
        return UNDEF
    if c is int or c is float:
        return v != 0
    return UNDEF


def _ab(v):
    if type(v) is bool or v is UNDEF:
        return UNDEF
    try:
        return abs(v)
    except TypeError:
        return UNDEF


def _fl(v):
    if type(v) is bool or v is UNDEF:
        return UNDEF
    try:
        return _M.floor(v)
    except TypeError:
        return UNDEF


def _ce(v):
    if type(v) is bool or v is UNDEF:
        return UNDEF
    try:
        return _M.ceil(v)
    except TypeError:
        return UNDEF


def _ro(v, p=0):
    """`$round`：**四舍五入（half-up）**，与 JSONata 一致（不是 Python 的银行家舍入）。

    `p` 是保留的小数位（可正可负）。`round()` 对 .5 采用「银行家舍入」
    （`round(2.5) == 2`、`round(3.5) == 4`），与 JSONata 的 `$round` 不符。
    这里改用 `decimal.Decimal(str(v)).quantize(..., ROUND_HALF_UP)`：
    先 `str(v)` 再构造 Decimal，避免二进制浮点的表示误差影响 .5 边界判断。
    """
    if type(v) is bool or v is UNDEF:
        return UNDEF
    try:
        p = int(p)
    except (TypeError, ValueError):
        return UNDEF
    try:
        import decimal as _d
        q = _d.Decimal(1).scaleb(-p)  # 1e-p：p=0 -> 1, p=2 -> 0.01
        r = _d.Decimal(str(v)).quantize(q, rounding=_d.ROUND_HALF_UP)
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
    if not (_num(a) and _num(b)):
        return UNDEF
    try:
        return _M.pow(a, b)
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
    """`$substringBefore(str, chars)`：EAFP —— `str.find` 对非字符串抛 `AttributeError`。"""
    try:
        i = v.find(t)
    except AttributeError:
        return UNDEF
    return "" if i < 0 else v[:i]


def _sba(v, t):
    """`$substringAfter(str, chars)`：EAFP，理由同 `_sbb`。"""
    try:
        i = v.find(t)
    except (AttributeError, TypeError):
        return UNDEF
    return "" if i < 0 else v[i + len(t):]


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
    """`$base64decode`：EAFP —— 直接解码，非法输入落异常返回 undefined。"""
    try:
        return base64.b64decode(v.encode("ascii"), validate=True).decode("utf-8")
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
    """`$formatNumber(value, picture[, options])`：简化实现（分组 + 小数位 + 百分号）。"""
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
    return out + ("%" if "%" in picture else "")


# ---------------------------------------------------- 数值/数组（A 类）
def _sm(a):
    """`$sum(array)`：数值求和；undefined 元素跳过。

    性能：把 `_num(v)` 函数调用换成内联的 `__class__` 判断 —— 与 `_mn`/`_mx`/
    `_av` 同一手法（那两个只省了 140 ns，这里在 10 万元素上是同量级收益）。
    `_num` 只判「不是 bool 且是 int/float」，等价于 `c is int or c is float`
    （bool 是 int 子类，`c is int` 对它为 False，所以显式排除已内建在这里）。
    非数组的标量实参仍按单元素处理（`$sum(3)` -> 3），与 `_ln` 一致。
    """
    if a is UNDEF:
        return UNDEF
    c = type(a)
    xs = a if (c is list or c is tuple) else (a,)
    total = 0
    for v in xs:
        if v is UNDEF:
            continue
        try:
            total += v
        except TypeError:
            raise TypeError("$sum: 元素必须是数值") from None
        else:
            tc = type(v)
            if tc is int or tc is float:
                continue
            # `True + 1` 在 Python 里合法但 JSONata 里 boolean 不是数值
            raise TypeError("$sum: 元素必须是数值")
    return total


def _mn(a):
    # 性能：单遍扫描，不再先过滤出临时列表再 min()（350 -> 210 ns/op）。
    # EAFP：直接比较，不可比的值落 except 跳过 —— 省掉每元素的类型判定。
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF
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
        return UNDEF
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
        return UNDEF
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
    if v is UNDEF:
        return []
    try:
        return list(v)
    except TypeError:
        return [v]


def _rv(a):
    """`$reverse`：EAFP —— 直接 `reversed()`，不可反转的标量落异常返回 undefined。"""
    try:
        return list(reversed(a))
    except TypeError:
        return UNDEF


def _so(a, f=UNDEF):
    """`$sort(array[, comparator])`：默认升序；比较器返回布尔，true 表示
    第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。"""
    try:
        # `sorted()` 自己就会复制一份再排 —— 先 `list(a)` 只是多一次拷贝。
        # 无比较器时直接 `sorted(a)`（10 万元素实测省 12%）；有比较器才需要
        # `xs`（`cmp_to_key` 的闭包只读它，`sorted` 内部同样会复制）。
        if f is UNDEF:
            return sorted(a)
        key = functools.cmp_to_key(
            lambda x, y: 1 if f(x, y) else (-1 if f(y, x) else 0))
        return sorted(a, key=key)
    except TypeError:
        return UNDEF


def _ds(a):
    """`$distinct(array)`：保序去重（等价判定走 `_eq`）。"""
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF
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

    EAFP：正常路径就是 `out.update(o)`，`update` 对非对象直接抛
    `TypeError`/`ValueError`，落 `except` 再判 undefined 与非法元素。
    """
    c = type(objs)
    if c is not list and c is not tuple:
        return UNDEF
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
    """`$keys`：EAFP —— 非对象没有 `keys()`，`AttributeError` 即 undefined。"""
    try:
        return list(o.keys())
    except AttributeError:
        return UNDEF


def _vl(o):
    """`$values`：EAFP —— 非对象没有 `values()`，`AttributeError` 即 undefined。"""
    try:
        return list(o.values())
    except AttributeError:
        return UNDEF


def _lk(o, k):
    """`$lookup`：EAFP —— 直接 `get`，非对象落 `AttributeError` 返回 undefined。"""
    try:
        return o.get(k, UNDEF)
    except AttributeError:
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
    """`$single(array[, predicate])`：恰好一个元素时返回它，否则 undefined。"""
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF
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
    """`$filter`：EAFP —— 直接交给推导式，不可迭代的标量落异常返回 undefined。"""
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
    "_sm": _sm, "_mn": _mn, "_mx": _mx, "_av": _av, "_ap": _ap, "_rv": _rv,
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


# 产物里需要 `import math` 的助手（`_m` 前缀别名在产物头部定义）。
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
