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
import random
import time
import urllib.parse
from datetime import datetime, timezone



class _Undef:
    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "undefined"


UNDEF = _Undef()

#: 产物里的短名（`from dcg.runtime import*` 一并导出）；与 `UNDEF` 是同一对象。
_U = UNDEF


def is_u(v):
    return v is UNDEF


# ---------------------------------------------------------------- 辅助谓词
def _num(v):
    """JSONata 数值谓词：bool 不是 number，None(JSON null) 不是合法算术操作数。"""
    c = v.__class__
    return c is int or c is float


def _tru(v):
    """JSONata truthiness：undefined/null/false/''/0/[]/{} 为假。"""
    if v is UNDEF or v is None or v is False:
        return False
    if v is True:
        return True
    c = v.__class__
    if c is int or c is float:
        return v != 0
    if c is str:
        return v != ""
    if c is list or c is dict or c is tuple:
        return len(v) > 0
    return True


# ---------------------------------------------------------------- 二元算术
def _ad(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float):
        return a + b
    raise TypeError("$add: 操作数必须是数值")


def _sb(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float):
        return a - b
    raise TypeError("$subtract: 操作数必须是数值")


def _ml(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float):
        return a * b
    raise TypeError("$multiply: 操作数必须是数值")


def _dv(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float) and b != 0:
        return a / b
    return UNDEF


def _md(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float) and b != 0:
        return a % b
    return UNDEF


def _pg(a, b):
    # 性能：先判 __class__ 再算，省掉两次 `_num()` 调用（本机 182 -> 93 ns/op）。
    ca, cb = a.__class__, b.__class__
    if (ca is int or ca is float) and (cb is int or cb is float):
        return a ** b
    return UNDEF


# ---------------------------------------------------------------- 拼接 / 逻辑
def _cc(a, b):
    """`&`：undefined **不传播**，当空串；null 当 ''；非字符串先转字符串。"""
    if a.__class__ is str and b.__class__ is str:
        return a + b
    if a is UNDEF or a is None:
        a = ""
    elif not isinstance(a, str):
        a = repr(a) if isinstance(a, bool) else str(a)
    if b is UNDEF or b is None:
        b = ""
    elif not isinstance(b, str):
        b = repr(b) if isinstance(b, bool) else str(b)
    return a + b


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
    if a is UNDEF:
        return UNDEF
    if _num(a):
        return -a
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
    if a is UNDEF:
        return UNDEF
    if _num(a):
        return +a
    return UNDEF


# ---------------------------------------------------------------- 比较
def _eq(a, b):
    """`=` / `in`：任一侧 undefined -> False（JSONata 1.8）。"""
    if a is UNDEF or b is UNDEF:
        return False
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if _num(a) and _num(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a == b


def _ne(a, b):
    return not _eq(a, b)


def _cmp_ok(a, b):
    if a is UNDEF or b is UNDEF:
        return False
    if _num(a) and _num(b):
        return True
    return isinstance(a, str) and isinstance(b, str)


def _lt(a, b):
    return a < b if _cmp_ok(a, b) else UNDEF


def _le(a, b):
    return a <= b if _cmp_ok(a, b) else UNDEF


def _gt(a, b):
    return a > b if _cmp_ok(a, b) else UNDEF


def _ge(a, b):
    return a >= b if _cmp_ok(a, b) else UNDEF


def _in(a, b):
    """`in` 与 `=` 同族：左侧 undefined -> False。"""
    if a is UNDEF:
        return False
    if isinstance(b, (list, tuple)):
        return any(_eq(a, x) for x in b)
    if isinstance(b, str):
        return isinstance(a, str) and a in b
    return False


# ---------------------------------------------------------------- 字符串函数
def _S(v):
    """`$string`：undefined/null -> ''，bool -> 'true'/'false'。"""
    if v is UNDEF or v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _up(s):
    if s is UNDEF or s is None:
        return UNDEF
    return s.upper() if isinstance(s, str) else UNDEF


def _lo(s):
    if s is UNDEF or s is None:
        return UNDEF
    return s.lower() if isinstance(s, str) else UNDEF


def _tm(s):
    if s is UNDEF or s is None:
        return UNDEF
    return s.strip() if isinstance(s, str) else UNDEF


def _ln(s):
    """`$length`：字符串长度；数组长度。"""
    if isinstance(s, str):
        return len(s)
    if isinstance(s, (list, tuple, dict)):
        return len(s)
    return UNDEF


def _ct(a):
    """`$count`：只对数组有意义。"""
    if isinstance(a, (list, tuple)):
        return len(a)
    return UNDEF


def _sp(s, sep):
    if s is UNDEF or s is None:
        return UNDEF
    if sep is UNDEF or sep is None:
        sep = ""
    return s.split(sep) if isinstance(s, str) else UNDEF


def _jn(a, sep):
    if a is UNDEF or a is None:
        return UNDEF
    if sep is UNDEF:
        sep = ""
    if not isinstance(a, (list, tuple)):
        a = [a]
    parts = []
    for x in a:
        if x is UNDEF or x is None:
            continue
        if isinstance(x, (list, tuple)):
            parts.extend(_S(y) for y in x if y is not UNDEF and y is not None)
        else:
            parts.append(_S(x))
    return (sep or "").join(parts)


def _ss(s, start, length=UNDEF):
    """`$substring`。"""
    if s is UNDEF or s is None or not isinstance(s, str):
        return UNDEF
    if length is UNDEF or length is None:
        return s[start:]
    return s[start:start + length]


def _ix(s, sub):
    """`$indexOf`（JSONata 1.8 第 9 条）。

    返回 `sub` 在 `s` 里第一次出现的**字符位置**（从 0 开始）；找不到返回 -1。
    `s` 不是字符串，或任一侧是 undefined / null → `undefined`。
    `sub` 不是字符串时按 JSONata 的 `$string()` 语义转换（数字、布尔都行）。

    注意 `_ix`（= `$indexOf`）与 `_ix_get`（= 安全下标）**差一字符、语义完全不同**：
    `_ix` 找子串位置、`_ix_get` 取下标值。动态下标必须走 `_ix_get`，绝不能路由到这里。
    """
    if s is UNDEF or s is None or not isinstance(s, str):
        return UNDEF
    if sub is UNDEF or sub is None:
        return UNDEF
    return s.find(_S(sub))


def _cn(a, b):
    """`$contains`。"""
    # 性能：str / list / tuple 的精确类型快路径前置（118 -> 100 ns/op）；
    # 下面保留 isinstance 分支只为兼容子类，正常输入走不到。
    ca = a.__class__
    if ca is str:
        return b.__class__ is str and b in a
    if ca is list or ca is tuple:
        return any(_eq(b, x) for x in a)
    if a is UNDEF or a is None:
        return False
    if isinstance(a, str):
        return isinstance(b, str) and b in a
    if isinstance(a, (list, tuple)):
        return any(_eq(b, x) for x in a)
    return False


# ---------------------------------------------------------------- 数值函数
def _N(v):
    """`$number`：不可转 -> undefined。"""
    # 性能：`__class__` 判定提到 isinstance 链之前，数值输入一步返回（334 -> 284 ns/op）。
    c = v.__class__
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
    if v is UNDEF:
        return UNDEF
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v == "true":
            return True
        if v == "false":
            return False
        return UNDEF
    if _num(v):
        return v != 0
    return UNDEF


def _ab(v):
    if v.__class__ is bool or v is UNDEF:
        return UNDEF
    try:
        return abs(v)
    except TypeError:
        return UNDEF


def _fl(v):
    if v.__class__ is bool or v is UNDEF:
        return UNDEF
    import math as _m
    try:
        return _m.floor(v)
    except TypeError:
        return UNDEF


def _ce(v):
    if v.__class__ is bool or v is UNDEF:
        return UNDEF
    import math as _m
    try:
        return _m.ceil(v)
    except TypeError:
        return UNDEF


def _ro(v, p=0):
    if v.__class__ is bool or v is UNDEF:
        return UNDEF
    try:
        return round(v, p) if p else round(v)
    except TypeError:
        return UNDEF


def _sq(v):
    # 性能：数值走窄异常（只捕 ValueError），省掉 math.sqrt 的类型检查开销
    # （231 -> 199 ns/op；负数仍正确返回 undefined）。非数值才落下面的慢分支。
    c = v.__class__
    if c is int or c is float:
        import math as _m
        try:
            return _m.sqrt(v)
        except ValueError:
            return UNDEF
    if c is bool or v is UNDEF:
        return UNDEF
    import math as _m
    try:
        return _m.sqrt(v)
    except (TypeError, ValueError):
        return UNDEF


def _pw(a, b):
    if not (_num(a) and _num(b)):
        return UNDEF
    return a ** b


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
    正常路径性能）。`null`/数字等标量取下标 -> 返回自身（契约 §4.4 第 7 条）。
    """
    try:
        return a[i]
    except (IndexError, KeyError):
        return UNDEF
    except TypeError:
        # 容器但下标类型不合法（如 list 用字符串下标）-> UNDEF；标量 -> 自身
        return UNDEF if isinstance(a, (list, tuple, dict, str)) else a


# ---------------------------------------------------- 字符串补充（A 类）
def _pd(v, n, c=" "):
    """`$pad(str, width[, char])`：width > 0 右补，< 0 左补。"""
    if v is UNDEF or n is UNDEF or not _num(n):
        return UNDEF
    s = v if isinstance(v, str) else _S(v)
    fill = c[0] if isinstance(c, str) and c else " "
    n = int(n)
    return s.ljust(n, fill) if n >= 0 else s.rjust(-n, fill)


def _sbb(v, t):
    """`$substringBefore(str, chars)`。"""
    if not isinstance(v, str) or not isinstance(t, str):
        return UNDEF
    i = v.find(t)
    return "" if i < 0 else v[:i]


def _sba(v, t):
    """`$substringAfter(str, chars)`。"""
    if not isinstance(v, str) or not isinstance(t, str):
        return UNDEF
    i = v.find(t)
    return "" if i < 0 else v[i + len(t):]


def _rp(v, pat, rep, limit=UNDEF):
    """`$replace(str, pattern, replacement[, limit])`：字符串模式，从左替换。"""
    if not isinstance(v, str) or not isinstance(pat, str):
        return UNDEF
    repl = rep if isinstance(rep, str) else ("" if rep in (UNDEF, None) else _S(rep))
    cnt = int(limit) if _num(limit) else -1
    if not pat:
        return v
    return v.replace(pat, repl, cnt)


def _b64e(v):
    if not isinstance(v, str):
        return UNDEF
    return base64.b64encode(v.encode("utf-8")).decode("ascii")


def _b64d(v):
    if not isinstance(v, str):
        return UNDEF
    try:
        return base64.b64decode(v.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return UNDEF


def _ue(v):
    if not isinstance(v, str):
        return UNDEF
    return urllib.parse.quote(v, safe="")


def _ud(v):
    if not isinstance(v, str):
        return UNDEF
    try:
        return urllib.parse.unquote(v, errors="strict")
    except UnicodeError:
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
    if not _num(v) or not isinstance(picture, str):
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
    """`$sum(array)`：数值求和；undefined 元素跳过。"""
    if a is UNDEF:
        return UNDEF
    xs = a if isinstance(a, (list, tuple)) else [a]
    total = 0
    for v in xs:
        if v is UNDEF:
            continue
        if not _num(v):
            raise TypeError("$sum: 元素必须是数值")
        total += v
    return total


def _mn(a):
    # 性能：单遍扫描，不再先过滤出临时列表再 min()（350 -> 210 ns/op）。
    if not isinstance(a, (list, tuple)):
        return UNDEF
    best = None
    found = False
    for v in a:
        c = v.__class__
        if c is int or c is float:
            if not found or v < best:
                best = v
                found = True
    return best if found else UNDEF


def _mx(a):
    # 性能：单遍扫描，不再先过滤出临时列表再 max()（350 -> 202 ns/op）。
    if not isinstance(a, (list, tuple)):
        return UNDEF
    best = None
    found = False
    for v in a:
        c = v.__class__
        if c is int or c is float:
            if not found or v > best:
                best = v
                found = True
    return best if found else UNDEF


def _av(a):
    # 性能：单遍累加，不再「过滤列表 + all() 校验 + sum()」三遍（656 -> 231 ns/op）。
    if not isinstance(a, (list, tuple)):
        return UNDEF
    n = 0
    total = 0
    for v in a:
        c = v.__class__
        if c is int or c is float:
            total += v
            n += 1
        elif v is not UNDEF:
            return UNDEF
    return total / n if n else UNDEF


def _ap(a, b):
    xs = [] if a is UNDEF else (list(a) if isinstance(a, (list, tuple)) else [a])
    ys = [] if b is UNDEF else (list(b) if isinstance(b, (list, tuple)) else [b])
    return xs + ys


def _rv(a):
    if not isinstance(a, (list, tuple)):
        return UNDEF
    return list(reversed(a))


def _so(a, f=UNDEF):
    """`$sort(array[, comparator])`：默认升序；比较器返回布尔，true 表示
    第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。"""
    if not isinstance(a, (list, tuple)):
        return UNDEF
    xs = list(a)
    try:
        if f is UNDEF:
            return sorted(xs)
        key = functools.cmp_to_key(
            lambda x, y: 1 if f(x, y) else (-1 if f(y, x) else 0))
        return sorted(xs, key=key)
    except TypeError:
        return UNDEF


def _ds(a):
    """`$distinct(array)`：保序去重（等价判定走 `_eq`）。"""
    if not isinstance(a, (list, tuple)):
        return UNDEF
    out = []
    for v in a:
        if not any(_eq(v, w) is True for w in out):
            out.append(v)
    return out


def _mg(objs):
    if not isinstance(objs, (list, tuple)):
        return UNDEF
    out = {}
    for o in objs:
        if o is UNDEF:
            continue
        if not isinstance(o, dict):
            return UNDEF
        out.update(o)
    return out


def _ky(o):
    return list(o.keys()) if isinstance(o, dict) else UNDEF


def _vl(o):
    return list(o.values()) if isinstance(o, dict) else UNDEF


def _lk(o, k):
    return o.get(k, UNDEF) if isinstance(o, dict) else UNDEF


def _sr(o):
    if isinstance(o, dict):
        return [{k: v} for k, v in o.items()]
    if isinstance(o, (list, tuple)):
        return list(o)
    return UNDEF


def _sg(a, f=UNDEF):
    """`$single(array[, predicate])`：恰好一个元素时返回它，否则 undefined。"""
    if not isinstance(a, (list, tuple)):
        return UNDEF
    xs = [v for v in a if v is not UNDEF]
    if f is not UNDEF:
        xs = [v for i, v in enumerate(xs) if _tru(f(v, i))]
    return xs[0] if len(xs) == 1 else UNDEF


# ---------------------------------------------------- 高阶函数（B 类）
def _mp(a, f):
    """`$map(array, function($v,$i){...})`：结果跳过 undefined。"""
    if a is UNDEF:
        return UNDEF
    xs = a if isinstance(a, (list, tuple)) else [a]
    out = []
    for i, v in enumerate(xs):
        r = f(v, i)
        if r is not UNDEF:
            out.append(r)
    return out


def _fi(a, f):
    if not isinstance(a, (list, tuple)):
        return UNDEF
    return [v for i, v in enumerate(a) if _tru(f(v, i))]


def _rd(a, f, init=UNDEF):
    """`$reduce(array, function($acc,$v){...}[, init])`：逐元素折叠。"""
    if not isinstance(a, (list, tuple)):
        return UNDEF
    if not a:
        return init
    if init is UNDEF:
        acc, start = a[0], 1
    else:
        acc, start = init, 0
    for i in range(start, len(a)):
        acc = f(acc, a[i])
    return acc


def _ea(o, f):
    """`$each(obj, function($v,$k){...})`：按键值对回调。"""
    if isinstance(o, dict):
        items = list(o.items())
    elif isinstance(o, (list, tuple)):
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
    """`$sift(obj, function($v,$k){...})`：按谓词过滤对象键。"""
    if not isinstance(o, dict):
        return UNDEF
    return {k: v for k, v in o.items() if _tru(f(v, k))}


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
    """`$shuffle(array)`：洗牌副本。"""
    if not isinstance(a, (list, tuple)):
        return UNDEF
    return RANDOM.shuffle(a)


def _RW(x):
    """`input.source="index"` 解包后的行容器归一化：dict -> values（保序）。"""
    return list(x.values()) if isinstance(x, dict) else x


# ---------------------------------------------------------------- 类型 / 存在
def _ty(v):
    if v is UNDEF:
        return UNDEF
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
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


# 产物里需要 `import math` 的助手（_m 前缀别名在产物头部定义）。
#: 源码里出现 `math` 的助手 —— 内联它们时必须先 `import math as _m`。
#: 由源码派生，不手工维护。
SOURCE_NEEDS_MATH = frozenset(n for n, t in SOURCE.items() if "math" in t)

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
