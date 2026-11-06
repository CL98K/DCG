"""DCG v2 运行期 helper —— undefined 语义的**唯一实现处**。
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal as _DEC

# 模块级局部别名：helper 里反复用到的 `math.sqrt` / `math.pow` 经别名访问省一层
_M = math
# `±inf` 字面量：`_S`/`_N` 要判非有限数，但**不能**写成 `_M.isinf`/`math.isinf`
_INF = float("inf")
_NINF = -_INF

# ---------------------------------------------------- 可选 Rust 加速后端（双活）
try:  # pragma: no cover - 取决于本机是否编译过扩展
    from ._accel import _accel as _ACC
except Exception:  # noqa: BLE001 - 缺扩展是正常路径，绝不能影响 import
    _ACC = None

#: `$zip` 走 Rust 的最小列长，且只在**两列**时放行。用
_ZP_MIN = 384
_ZP_COLS = 2


#: `$formatInteger` 只在**十进制掩码图样**上走 Rust。
_FGI_ALPHA = frozenset(("A", "a", "I", "i", "w", "W", "Ww"))


#: `$clone` 走 Rust 的最小元素数。这里比的是「本层宽度」，不是嵌套深度，而且
_CL_MIN_DICT = 2
_CL_MIN_SEQ = 8


#: `$min`/`$max`/`$average` 的门槛（24）是**有意留的折中**，不是交叉点。



class _Undef:
    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "undefined"

    def get(self, key, default=None):
        """让 `value` 路径的 `dict.get(k, _U)` 链能穿过哨兵。
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


#: 算术失败文案。EAFP 骨架把类型判定推到失败路径，文案常量必须有名字 ——
_ADD_MSG = "$add: 操作数必须是数值"
_SUB_MSG = "$subtract: 操作数必须是数值"
_MUL_MSG = "$multiply: 操作数必须是数值"
_DIV_MSG = "$divide: 除数是 0"
_MOD_MSG = "$modulo: 除数是 0"
_POW_MSG = "$power: 操作数必须是数值"


def _ad(a, b):
    """`+`（JSONata 加法）。纯 EAFP：`a + b` 不抛异常就直接返回。
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
    """`/`。除数为 0 是 undefined，不是异常。"""
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
"""
    if a is UNDEF or b is UNDEF:
        return False
    ca, cb = type(a), type(b)
    if (ca is int or ca is float) and (cb is int or cb is float):
        return True
    return ca is str and cb is str


#: 四个比较算子的**同域判定内联版**。


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
    """`$uppercase`：EAFP —— 直接调用 `.upper`，非字符串才会抛 `AttributeError`。
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


_WS_RE = re.compile(r"[ \t\n\r]+")


def _tm(s):
    """`$trim`：去首尾空白，并把**内部连续空白压成一个空格**。
"""
    try:
        r = _WS_RE.sub(" ", s)
        if r[:1] == " ":
            r = r[1:]
        if r[-1:] == " ":
            r = r[:-1]
        return r
    except TypeError:
        return UNDEF


def _ln(s):
    """`$length`：字符串长度；数组 / 对象长度。
"""
    try:
        return len(s)
    except TypeError:
        return UNDEF


def _ct(a):
    """`$count`：数组返回元素个数；**其它任何值（含字符串/对象/标量）= 1**。
"""
    if a is UNDEF:
        return UNDEF
    c = type(a)
    if c is list or c is tuple:
        return len(a)
    return 1


def _sp(s, sep, limit=UNDEF):
    """`$split(str, separator[, limit])`，与 JSONata 1.8 的 `str.split(sep, limit)` 对齐。
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
"""
    if sub is UNDEF or sub is None:
        return UNDEF
    try:
        return s.find(_S(sub))
    except AttributeError:
        return UNDEF


def _cn(a, b):
    """`$contains`：EAFP —— 字符串走 `in`（一次 C 级查找），容器走 `_eq` 线性扫描。
"""
    if a is UNDEF or b is UNDEF:
        return UNDEF
    try:
        return b in a
    except TypeError:
        pass
    if type(a) is list or type(a) is tuple:
        return any(_eq(b, x) for x in a)
    return False


# ---------------------------------------------------------------- 数值函数
_NUM_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$")
#: JSONata 2.0.0（PR #573）起 `$number` 接受 0x/0b/0o 前缀。与 JS 的 `parseInt`
_RADIX_RE = re.compile(r"^0([xX])([0-9a-fA-F]+)\s*$|^0([bB])([01]+)\s*$|^0([oO])([0-7]+)\s*$")


def _N(v):
    """`$number`：不可转 -> undefined。
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
            m = _RADIX_RE.match(v)
            if m is None:
                return UNDEF
            # 2.0.0 起支持 0x/0b/0o 前缀（PR #573）；`int(s, base)` 已处理任意精度。
            hex_s, bin_s, oct_s = m.group(2), m.group(4), m.group(6)
            if hex_s is not None:
                return int(hex_s, 16)
            if bin_s is not None:
                return int(bin_s, 2)
            return int(oct_s, 8)
        f = float(v)
        return int(f) if f.is_integer() else f
    return UNDEF


def _B(v):
    """`$boolean`：JSONata 1.8 真值表（与 `_tru` 同口径）；undefined 原样传播。
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
"""
    return [x for x in items if x is not UNDEF]


def _ob(pairs):
    """对象字面量：**丢弃** undefined 键（语义 6）。
"""
    out = {}
    for k, v in pairs:
        if v is not UNDEF:
            out[k] = v
    return out


def _rl(vals):
    """位置行（`type:"list"` + `fields`）：按声明顺序输出，undefined 落成 None 以保住位置。
"""
    return [None if v is UNDEF else v for v in vals]


def _ix_get(a, i):
    """`a[i]`：容器按下标取；越界/缺键 -> UNDEF；标量对任何下标都返回自身（`a[0] == a`）。
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
    """`$pad(str, width[, char])`：width > 0 右补，< 0 左补。
"""
    if v is UNDEF or n is UNDEF or not _num(n):
        return UNDEF
    s = v if type(v) is str else _S(v)
    fill = c if type(c) is str and c else " "
    n = int(n)
    pad = abs(n) - len(s)
    if pad <= 0:
        return s
    if len(fill) > 1:
        fill = (fill * pad)[:pad]
    else:
        fill = fill * pad
    return s + fill if n > 0 else fill + s


def _sbb(v, t):
    """`$substringBefore(str, chars)`：找不到分隔符时返回**整个字符串**。
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


_FNM_DEFAULTS = {
    "decimal-separator": ".",
    "grouping-separator": ",",
    "exponent-separator": "e",
    "infinity": "Infinity",
    "minus-sign": "-",
    "NaN": "NaN",
    "percent": "%",
    "per-mille": "\u2030",
    "zero-digit": "0",
    "digit": "#",
    "pattern-separator": ";",
}


def _fnm_props(options):
    """把 `options` 叠到 2.2.2 的默认 `properties` 上（非单字符串忽略）。"""
    p = dict(_FNM_DEFAULTS)
    if type(options) is dict:
        for k in p:
            val = options.get(k, UNDEF)
            if type(val) is str and len(val) >= 1:
                p[k] = val
    return p


def _fnm_family(zero_digit):
    """`zero-digit` 起的 10 个连续码点（官方 `decimalDigitFamily`）。"""
    return [chr(ord(zero_digit) + i) for i in range(10)]


def _fnm_split_parts(sub, props, active):
    """官方 `splitParts`：切出前缀/整数图/小数图/指数图/后缀。"""
    n = len(sub)
    exp_sep = props["exponent-separator"]
    dec_sep = props["decimal-separator"]

    def _is_active(ch):
        return ch in active and ch != exp_sep

    pre = 0
    while pre < n and not _is_active(sub[pre]):
        pre += 1
    last = n - 1
    while last >= 0 and not _is_active(sub[last]):
        last -= 1
    active_part = sub[pre:last + 1]
    exp_pos = sub.find(exp_sep, pre)
    if exp_pos == -1 or exp_pos > last + 1:
        mantissa = active_part
        epat = None
    else:
        mantissa = sub[pre:exp_pos]
        epat = sub[exp_pos + 1:last + 1]
    dpos = mantissa.find(dec_sep)
    if dpos == -1:
        ipat = mantissa
        fpat = sub[last + 1:]
    else:
        ipat = mantissa[:dpos]
        fpat = mantissa[dpos + 1:]
    return {"prefix": sub[:pre], "suffix": sub[last + 1:], "active": active_part,
            "mantissa": mantissa, "epat": epat, "ipat": ipat, "fpat": fpat,
            "sub": sub}


def _fnm_validate(parts, props, fam, active):
    """官方 `validate`（F&O 4.7.3）：返回错误码，None 表示合法。
"""
    sub = parts["sub"]
    dec = props["decimal-separator"]
    grp = props["grouping-separator"]
    pct = props["percent"]
    pm = props["per-mille"]
    digit = props["digit"]
    err = None
    if sub.find(dec) != sub.rfind(dec):
        err = "D3081"
    if sub.find(pct) != sub.rfind(pct):
        err = "D3082"
    if sub.find(pm) != sub.rfind(pm):
        err = "D3083"
    if sub.find(pct) != -1 and sub.find(pm) != -1:
        err = "D3084"
    if not any(ch in fam or ch == digit for ch in parts["mantissa"]):
        err = "D3085"
    if any(ch not in active for ch in parts["active"]):
        err = "D3086"
    dpos = sub.find(dec)
    if dpos != -1:
        if (dpos > 0 and sub[dpos - 1] == grp) or \
                (dpos + 1 < len(sub) and sub[dpos + 1] == grp):
            err = "D3087"
    elif parts["ipat"].endswith(grp):
        err = "D3088"
    if sub.find(grp + grp) != -1:
        err = "D3089"
    opt = parts["ipat"].find(digit)
    if opt != -1 and any(ch in fam for ch in parts["ipat"][:opt]):
        err = "D3090"
    opt = parts["fpat"].rfind(digit)
    if opt != -1 and any(ch in fam for ch in parts["fpat"][opt:]):
        err = "D3091"
    if parts["epat"] and (sub.find(pct) != -1 or sub.find(pm) != -1):
        err = "D3092"
    if parts["epat"] is not None and \
            (not parts["epat"] or any(ch not in fam for ch in parts["epat"])):
        err = "D3093"
    return err


def _fnm_positions(part, ipat, props, fam):
    """官方 `getGroupingPositions`：每个分隔符左/右侧数字位数。"""
    grp = props["grouping-separator"]
    digit = props["digit"]
    to_left = part is not ipat
    out = []
    gp = part.find(grp)
    while gp != -1:
        seg = part[:gp] if to_left else part[gp:]
        out.append(sum(1 for ch in seg if ch in fam or ch == digit))
        gp = ipat.find(grp, gp + 1)
    return out


def _fnm_analyse(parts, props, fam):
    """官方 `analyse`（F&O 4.7.4）：算出补零/分组/指数参数。"""
    grp = props["grouping-separator"]
    digit = props["digit"]
    ipat = parts["ipat"]
    fpat = parts["fpat"]
    ipos = _fnm_positions(ipat, ipat, props, fam)
    fpos = _fnm_positions(fpat, ipat, props, fam)

    def _regular(indexes):
        if not indexes:
            return 0
        factor = 0
        for x in indexes:
            factor = math.gcd(factor, x)
        if factor <= 0:
            return 0
        for i in range(1, len(indexes) + 1):
            if i * factor not in indexes:
                return 0
        return factor

    min_int = sum(1 for ch in ipat if ch in fam)
    scaling = min_int
    min_frac = sum(1 for ch in fpat if ch in fam)
    max_frac = sum(1 for ch in fpat if ch in fam or ch == digit)
    has_exp = parts["epat"] is not None
    if min_int == 0 and max_frac == 0:
        if has_exp:
            min_frac = 1
            max_frac = 1
        else:
            min_int = 1
    if has_exp and min_int == 0 and digit in ipat:
        min_int = 1
    if min_int == 0 and min_frac == 0:
        min_frac = 1
    min_exp = 0
    if has_exp:
        min_exp = sum(1 for ch in parts["epat"] if ch in fam)
    return {"ipos": ipos, "regular": _regular(ipos), "min_int": min_int,
            "scaling": scaling, "prefix": parts["prefix"], "fpos": fpos,
            "min_frac": min_frac, "max_frac": max_frac, "min_exp": min_exp,
            "suffix": parts["suffix"], "picture": parts["sub"]}


def _fnm_round(arg, precision):
    """官方 `round(arg, precision)`：十进制移位 + 半偶整数化 + 复位。"""
    try:
        d = _DEC(repr(float(arg)))
    except (ValueError, ArithmeticError, TypeError):
        return None
    if precision:
        d = d.scaleb(int(precision)).to_integral_value(rounding="ROUND_HALF_EVEN")
        d = d.scaleb(-int(precision))
    else:
        d = d.to_integral_value(rounding="ROUND_HALF_EVEN")
    f = float(d)
    return 0.0 if f == 0 else f


def _fnm_fixed(value, dp, props, fam):
    """官方 `makeString`：`Math.abs(value).toFixed(dp)` + 数字族映射。"""
    a = abs(float(value))
    if a >= 1e21:
        # JS 的 toFixed 在 1e21 以上退化成指数形态（2.2.2 实测 `1e+21`）。
        return repr(a).replace("e+0", "e+").replace("e-0", "e-")
    try:
        txt = "%.*f" % (int(dp), a)
    except (ValueError, TypeError):
        return None
    zero = props["zero-digit"]
    if zero != "0":
        out = []
        for ch in txt:
            out.append(fam[ord(ch) - 48] if "0" <= ch <= "9" else ch)
        txt = "".join(out)
    return txt


def _fnm_rust_ok(parts, info):
    """Rust 侧 `fnm` 的窄域：纯数字图样，无字面前后缀、无指数、小数侧不含
    可选位 `#`、分组符若出现必须规则每 3 位。
"""
    if parts["epat"] is not None:
        return False
    if parts["prefix"] or parts["suffix"]:
        return False
    if _FNM_DEFAULTS["digit"] in parts["fpat"]:
        return False
    if info["fpos"]:
        return False
    if info["ipos"]:
        return info["regular"] == 3
    return info["regular"] == 0


def _fnm(v, picture, options=UNDEF):
    """`$formatNumber(value, picture[, options])`（2.2.2 子图语义）。
"""
    if type(v) is bool or type(picture) is not str:
        return UNDEF
    if not _num(v):
        return UNDEF
    props = _fnm_props(options)
    fam = _fnm_family(props["zero-digit"])
    dec = props["decimal-separator"]
    grp = props["grouping-separator"]
    zero = props["zero-digit"]
    active = set(fam) | {dec, props["exponent-separator"], grp,
                         props["digit"], props["pattern-separator"]}
    subs = picture.split(props["pattern-separator"])
    if len(subs) > 2:
        return UNDEF
    parts = [_fnm_split_parts(s, props, active) for s in subs]
    for pt in parts:
        if _fnm_validate(pt, props, fam, active) is not None:
            return UNDEF
    variables = [_fnm_analyse(pt, props, fam) for pt in parts]
    if _ACC is not None and (options is UNDEF or options is None) and \
            len(parts) == 1 and _fnm_rust_ok(parts[0], variables[0]):
        # 双活：实测 3.77×/3.68×。必须等 picture 解析完、确认落在
        r = _ACC.fnm(v, picture, options)
        if r is not None:
            return r
    if len(variables) == 1:
        copy = dict(variables[0])
        copy["ipos"] = list(copy["ipos"])
        copy["fpos"] = list(copy["fpos"])
        copy["prefix"] = props["minus-sign"] + copy["prefix"]
        variables.append(copy)
    val = float(v)
    pic = variables[0] if val >= 0 else variables[1]
    if props["percent"] in pic["picture"]:
        adjusted = val * 100.0
    elif props["per-mille"] in pic["picture"]:
        adjusted = val * 1000.0
    else:
        adjusted = val
    exponent = None
    if pic["min_exp"] == 0:
        mantissa = adjusted
    else:
        max_m = 10.0 ** pic["scaling"]
        min_m = 10.0 ** (pic["scaling"] - 1)
        mantissa = adjusted
        exponent = 0
        if mantissa != 0:
            while abs(mantissa) < min_m:
                mantissa *= 10.0
                exponent -= 1
            while abs(mantissa) > max_m:
                mantissa /= 10.0
                exponent += 1
    rounded = _fnm_round(mantissa, pic["max_frac"])
    if rounded is None:
        return UNDEF
    text = _fnm_fixed(rounded, pic["max_frac"], props, fam)
    if text is None:
        return UNDEF
    dpos = text.find(".")
    if dpos == -1:
        text += dec
    else:
        text = text[:dpos] + dec + text[dpos + 1:]
    while text[:1] == zero:
        text = text[1:]
    while text[-1:] == zero:
        text = text[:-1]
    dpos = text.find(dec)
    pad_left = pic["min_int"] - dpos
    pad_right = pic["min_frac"] - (len(text) - dpos - 1)
    if pad_left > 0:
        text = zero * pad_left + text
    if pad_right > 0:
        text += zero * pad_right
    dpos = text.find(dec)
    if pic["regular"] > 0:
        group = 1
        count = (dpos - 1) // pic["regular"]
        while group <= count:
            cut = dpos - group * pic["regular"]
            text = text[:cut] + grp + text[cut:]
            group += 1
    else:
        for pos in pic["ipos"]:
            cut = dpos - pos
            text = text[:cut] + grp + text[cut:]
            dpos += 1
    dpos = text.find(dec)
    for pos in pic["fpos"]:
        cut = pos + dpos + 1
        text = text[:cut] + grp + text[cut:]
    dpos = text.find(dec)
    if pic["picture"].find(dec) == -1 or dpos == len(text) - 1:
        text = text[:-1]
    if exponent is not None:
        etext = _fnm_fixed(exponent, 0, props, fam)
        if etext is None:
            return UNDEF
        gap = pic["min_exp"] - len(etext)
        if gap > 0:
            etext = zero * gap + etext
        text += props["exponent-separator"] + \
            (props["minus-sign"] if exponent < 0 else "") + etext
    return pic["prefix"] + text + pic["suffix"]


# ---------------------------------------------------- 数值/数组（A 类）
def _sum_type_msg(a):
    """`$sum` 的失败文案。
"""
    if type(a) is dict:
        return ("$sum: 元素必须是数值（实参是整个对象，不是数组 —— 想对某一列求和请写 "
                "$sum($.列名)；`output.after` 里 `$` 是列字典时尤其如此）")
    return "$sum: 元素必须是数值"


def _sm(a):
    """`$sum(array)`：数值求和；undefined 跳过，不可加值报错。
"""
    # `dict` 必须在**任何 try 之前**拦下（DEFECT-002 复核发现的静默错误）。
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
    # 性能：单遍扫描（350 -> 210 ns/op）。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升：1.8 的 validateArguments 对数组型形参把标量包成单元素数组
        if a is UNDEF:
            return UNDEF
        a = (a,)
    if _ACC is not None and len(a) >= 24:
        # 双活：元素全是精确 int 时 Rust 的结果与下面的循环逐值相同；否则
        r = _ACC.mn(a)
        if r is not None:
            return r
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
    # 性能：单遍扫描（350 -> 202 ns/op）。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升，理由同 `_mn`
        if a is UNDEF:
            return UNDEF
        a = (a,)
    if _ACC is not None and len(a) >= 24:
        # 双活，理由同 `_mn`
        r = _ACC.mx(a)
        if r is not None:
            return r
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
    # 性能：单遍累加，不再「过滤列表 + all 校验 + sum」三遍（656 -> 231 ns/op）。
    c = type(a)
    if c is not list and c is not tuple:
        # 单值提升，理由同 `_mn`
        if a is UNDEF:
            return UNDEF
        a = (a,)
    if _ACC is not None and len(a) >= 24:
        # 双活：`_ACC.av` 只在元素全是精确 int 且和不超过 2^53 时给值。
        r = _ACC.av(a)
        if r is not None:
            return r
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
"""
    return _as_list(a) + _as_list(b)


def _as_list(v):
    """把「不保证是数组」的实参提升成数组（JSONata 1.8 的数组型形参语义）。
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
"""
    c = type(a)
    if c is list or c is tuple:
        return list(reversed(a))
    if a is UNDEF:
        return UNDEF
    return [a]


def _sort_merge(left, right, comp):
    """`$sort` 比较器路径的归并一步（1.8 `merge` 的逐行移植）。
"""
    out = []
    ap = out.append
    i = j = 0
    nl = len(left)
    nr = len(right)
    while i < nl and j < nr:
        # 关键：比较器返回真就取右元素。JS 真值判定意味着负数也是真。
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
"""
    c = type(a)
    if c is not list and c is not tuple:
        return UNDEF if a is UNDEF else a
    if _ACC is not None and len(a) >= 8:
        # 双活：全是精确 int 时 Rust 的 HashSet 去重与下面的快路径逐值相同
        r = _ACC.ds(a)
        if r is not None:
            return r
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
            # bool 与非 bool 在 `_eq` 里永不相等，所以单独一个集合；
            if v in bools:
                continue
            ap(v)
            addb(v)
            continue
        if t is int or t is float:
            # `v == v` 为假只可能是 NaN；NaN 与任何值（含自身）都不等。
            if v != v:
                ap(v)
                slow.append(v)
                continue
        elif t is not str and v is not None:
            # 其它类型（list/dict 不可哈希、自定义对象语义不明）走兜底。
            for w in out:
                if _eq(v, w) is True:
                    break
            else:
                ap(v)
                slow.append(v)
            continue
        if v in seen:
            continue
        if slow:
            for w in slow:
                if _eq(v, w) is True:
                    break
            else:
                ap(v)
                add(v)
            continue
        ap(v)
        add(v)
    return out


def _mg(objs):
    """`$merge`：对象数组合并；任一元素不是对象 -> undefined。
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
"""
    if o is UNDEF:
        return UNDEF
    c = type(o)
    if c is dict:
        # 空对象 -> 空序列 -> undefined。
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
    """`$values`：EAFP —— 非对象没有 `values`，`AttributeError` 即 undefined。"""
    try:
        return list(o.values())
    except AttributeError:
        return UNDEF


def _lk(o, k):
    """`$lookup`：EAFP —— 直接 `get`，非对象落 `AttributeError` 返回 undefined。
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
    """`$spread`：对象 -> 逐键拆成单键对象；数组 -> 递归展开；标量 -> 原样。
"""
    c = type(o)
    if c is list or c is tuple:
        out = []
        for v in o:
            r = _sr(v)
            if r is UNDEF:
                continue
            if type(r) is list or type(r) is tuple:
                out.extend(r)
            else:
                out.append(r)
        return out if out else UNDEF
    if c is dict:
        if not o:
            return UNDEF
        out = [{k: v} for k, v in o.items()]
        return out[0] if len(out) == 1 else out
    if o is UNDEF:
        return UNDEF
    return o


def _sg(a, f=UNDEF):
    """`$single(array[, predicate])`：恰好一个元素时返回它，否则 undefined。
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
"""
    try:
        return {k: v for k, v in o.items() if _tru(f(v, k))}
    except AttributeError:
        return UNDEF


# ---------------------------------------------------- 非确定函数（C 类）
class _Clock:
    """可替换时钟：``dcg.runtime.CLOCK.now_ms`` 返回 epoch 毫秒。"""

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
    """`$now`：ISO 8601 UTC 毫秒时间戳。"""
    ms = CLOCK.now_ms()
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _ms():
    """`$millis`：epoch 毫秒。"""
    return CLOCK.now_ms()


def _rn():
    """`$random`：`[0,1)` 浮点。"""
    return RANDOM.random()


def _sh(a):
    """`$shuffle(array)`：EAFP —— 直接交给随机源洗牌，标量落异常返回 undefined。"""
    try:
        return RANDOM.shuffle(a)
    except (TypeError, AttributeError):
        return UNDEF


def _RW(x):
    """`input.source="index"` 解包后的行容器归一化：dict -> values（保序）。
"""
    try:
        return list(x.values())
    except AttributeError:
        return x


# ------------------------------------------------------------ 数据级钩子守卫
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


def _xp(rows, col):
    """`output.expand`：把 `col` 列的数组展开成多行（1:N / fanout）。

    - 该列是数组 → 一行展开成 N 行，其余字段复制；空数组丢弃该行。
    - 该列不是数组（含缺失 / null）→ 原样保留一行（按单元素处理）。
    - `shape="list"` 且单字段扁平时，行本身就是该列的值，展开后直接是元素。
    """
    out = []
    ap = out.append
    for r in rows:
        if type(r) is dict:
            v = r.get(col)
            if type(v) is list:
                for e in v:
                    c = dict(r)
                    c[col] = e
                    ap(c)
            else:
                ap(r)
        elif type(r) is list:
            for e in r:
                ap(e)
        else:
            ap(r)
    return out



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
    """字段级 default：值为 undefined 时替换成编译期常量 d。"""
    return d if v is UNDEF else v


# ---------------------------------------------- JSONata 2.2.2 新增（A 类）
def _eu(v):
    """`$encodeUrl`：保留 RFC3986 保留字符与 `-._~`，其余百分号编码（2.2.2 新增）。"""
    if _ACC is not None and type(v) is str:
        # 双活：Rust 侧纯 ASCII 状态机，实测 4.34×/7.51×；非 str 落回下面。
        r = _ACC.eu(v)
        if r is not None:
            return r
    try:
        return urllib.parse.quote(v, safe="-._~!$&'()*+,;=:@/?#")
    except (AttributeError, TypeError):
        return UNDEF


#: `$decodeUrl` 不解码的字符（官方 2.2.2 实测保留集）。
_URL_KEEP = frozenset("-._~/?#&=:@,;$+")
_HEX = frozenset("0123456789abcdefABCDEF")


def _du(v):
    """`$decodeUrl`：只解**非**保留字符的转义，保留字符原样留 `%XX`（2.2.2 新增）。"""
    if type(v) is not str:
        return UNDEF
    if _ACC is not None:
        # 双活：实测 3.25×/29.3×（纯 ASCII 状态机，无回退）。
        r = _ACC.du(v)
        if r is not None:
            return r
    out = []
    i, n = 0, len(v)
    while i < n:
        if v[i] != "%":
            out.append(v[i])
            i += 1
            continue
        if i + 2 >= n or v[i + 1] not in _HEX or v[i + 2] not in _HEX:
            return UNDEF
        b = int(v[i + 1:i + 3], 16)
        if b < 0x80:
            out.append(v[i:i + 3] if chr(b) in _URL_KEEP else chr(b))
            i += 3
            continue
        buf, j = bytearray(), i
        while j + 2 < n and v[j] == "%" and v[j + 1] in _HEX and v[j + 2] in _HEX:
            nb = int(v[j + 1:j + 3], 16)
            if nb < 0x80:
                break
            buf.append(nb)
            j += 3
        try:
            out.append(buf.decode("utf-8"))
        except UnicodeDecodeError:
            return UNDEF
        i = j
    return "".join(out)


def _zp(*arrays):
    """`$zip(...)`：按最短序列截断后逐列打包（2.2.2 新增）。
"""
    if not arrays:
        return UNDEF
    cols = []
    for a in arrays:
        if type(a) is list or type(a) is tuple:
            cols.append(a)
        else:
            return UNDEF
    if (_ACC is not None and len(cols) == _ZP_COLS
            and min(len(c) for c in cols) >= _ZP_MIN):
        r = _ACC.zp(*arrays)
        if r is not None:
            return r
    return [[c[i] for c in cols] for i in range(min(len(c) for c in cols))]


def _deep(v):
    """`$clone` 的递归体：容器深拷贝，标量原样返回。"""
    t = type(v)
    if t is dict:
        return {k: _deep(x) for k, x in v.items()}
    if t is list or t is tuple:
        return [_deep(x) for x in v]
    return v


def _cl(v):
    """`$clone(v)`：深拷贝对象/数组；顶层标量按官方语义拒绝（2.2.2 新增）。
"""
    if type(v) is dict:
        if _ACC is not None and len(v) >= _CL_MIN_DICT:
            r = _ACC.cl(v)
            if r is not None:
                return r
        return _deep(v)
    if type(v) is list or type(v) is tuple:
        if _ACC is not None and len(v) >= _CL_MIN_SEQ:
            r = _ACC.cl(v)
            if r is not None:
                return r
        return _deep(v)
    return UNDEF


#: 正则字面量允许的标志位（与 2.2.2 一致，不支持 `g`）。
_RX_FLAGS = {"i": re.I, "m": re.M, "s": re.S, "x": re.X}


def _rx(pattern, flags=""):
    """把正则字面量编译成 `re.Pattern`（`$match` 的通道；2.2.2 新增）。
"""
    bits = 0
    for ch in flags or "":
        bit = _RX_FLAGS.get(ch)
        if bit is None:
            return UNDEF
        bits |= bit
    try:
        return re.compile(pattern, bits)
    except (re.error, TypeError, ValueError):
        return UNDEF


def _mt(v, rx, limit=UNDEF):
    """`$match(str, /re/[, limit])`：`[{match,index,groups}]`；无命中 undefined。
"""
    if type(v) is not str or not hasattr(rx, "finditer"):
        return UNDEF
    cap = -1
    if limit is not UNDEF:
        if type(limit) is bool or not _num(limit):
            return UNDEF
        cap = int(limit)
        if cap <= 0:
            return UNDEF
    out = []
    try:
        for m in rx.finditer(v):
            out.append({"match": m.group(0), "index": m.start(),
                        "groups": list(m.groups())})
            if cap > 0 and len(out) >= cap:
                break
    except (TypeError, ValueError, re.error):
        return UNDEF
    return out or UNDEF


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_DAY_NAMES = ("", ) + _WEEKDAYS
_MS_PIC = re.compile(r"\[([A-Za-z])([^\[\]]*)\]")
# 2.2.2 的 defaultPresentationModifiers：m/s 默认两位，其余数字分量默认一位
_FMS_DEFAULT = {"Y": "1", "M": "1", "D": "1", "d": "1", "F": "n", "W": "1",
                "w": "1", "X": "1", "x": "1", "H": "1", "h": "1", "P": "n",
                "m": "01", "s": "01", "f": "1", "Z": "01:01", "z": "01:01",
                "C": "n", "E": "n"}
_FMS_INT = frozenset("YMDdFWwXxHhmsf")
_FMS_utc = timezone.utc
_FMS_EPOCH = datetime(1970, 1, 1, tzinfo=_FMS_utc)


def _fms_int_fmt(pic):
    """`analyseIntegerPicture` 的 Python 版：只认 letters/roman/words/十进制。"""
    fmt = {"primary": "decimal", "case": "lower", "ordinal": False,
           "mandatory": 0, "optional": 0, "seps": [], "regular": 0}
    semi = pic.rfind(";")
    if semi == -1:
        primary, mod = pic, None
    else:
        primary, mod = pic[:semi], pic[semi + 1:]
        if mod[:1] == "o":
            fmt["ordinal"] = True
    if primary == "A":
        fmt["case"] = "upper"
        fmt["primary"] = "letters"
        return fmt
    if primary == "a":
        fmt["primary"] = "letters"
        return fmt
    if primary == "I":
        fmt["case"] = "upper"
        fmt["primary"] = "roman"
        return fmt
    if primary == "i":
        fmt["primary"] = "roman"
        return fmt
    if primary == "W":
        fmt["case"] = "upper"
        fmt["primary"] = "words"
        return fmt
    if primary == "Ww":
        fmt["case"] = "title"
        fmt["primary"] = "words"
        return fmt
    if primary == "w":
        fmt["primary"] = "words"
        return fmt
    pos = 0
    for ch in reversed(primary):
        if ch == "#":
            pos += 1
            fmt["optional"] += 1
        elif "0" <= ch <= "9":
            pos += 1
            fmt["mandatory"] += 1
        else:
            fmt["seps"].append({"position": pos, "character": ch})
    if fmt["mandatory"]:
        seps = fmt["seps"]
        if not seps:
            fmt["regular"] = 0
        else:
            p0 = seps[0]["position"]
            same = all(x["position"] == p0 * (i + 1) for i, x in enumerate(seps))
            chars = set(x["character"] for x in seps)
            fmt["regular"] = p0 if (same and len(chars) == 1) else 0
    return fmt


def _fms_format_int(value, fmt):
    """`_formatInteger` 的 Python 版。"""
    negative = value < 0
    value = abs(value)
    prim = fmt["primary"]
    if prim == "letters":
        out = _alpha_num(value, fmt["case"] != "upper")
        if fmt["case"] == "upper":
            out = out.upper()
        return ("-" if negative else "") + out
    if prim == "roman":
        out = _roman_num(value)
        if fmt["case"] == "upper":
            out = out.upper()
        return ("-" if negative else "") + out
    if prim == "words":
        out = _en_words(value)
        if fmt["case"] == "upper":
            out = out.upper()
        elif fmt["case"] == "lower":
            out = out.lower()
        elif fmt["case"] == "title":
            out = out.title()
        return ("-" if negative else "") + out
    out = str(value)
    pad = fmt["mandatory"] - len(out)
    if pad > 0:
        out = "0" * pad + out
    if fmt["regular"]:
        step = fmt["regular"]
        n = (len(out) - 1) // step
        for i in range(n, 0, -1):
            p = len(out) - i * step
            out = out[:p] + fmt["seps"][0]["character"] + out[p:]
    else:
        for sep in reversed(fmt["seps"]):
            p = len(out) - sep["position"]
            out = out[:p] + sep["character"] + out[p:]
    return ("-" if negative else "") + out


def _fms_parse_picture(picture):
    """`analyseDateTimePicture` 的 Python 版：返回 [(literal|marker, ...)] 序列。"""
    spec = []
    buf = []

    def flush():
        if buf:
            spec.append({"kind": "literal", "text": "".join(buf)})
            del buf[:]

    i, n = 0, len(picture)
    while i < n:
        ch = picture[i]
        if ch == "[":
            if i + 1 < n and picture[i + 1] == "[":
                buf.append("[")
                i += 2
                continue
            j = picture.find("]", i + 1)
            if j == -1:
                buf.append(picture[i:])
                break
            flush()
            spec.append({"kind": "marker", "text": picture[i + 1:j]})
            i = j + 1
        elif ch == "]" and i + 1 < n and picture[i + 1] == "]":
            buf.append("]")
            i += 2
        else:
            buf.append(ch)
            i += 1
    flush()
    for m in spec:
        if m["kind"] != "marker":
            continue
        raw = m["text"].replace(" ", "")
        comp = raw[:1]
        comma = raw.rfind(",")
        width = {}
        if comma != -1:
            wm = raw[comma + 1:]
            raw = raw[:comma]
            mn, _, mx = wm.partition("-")
            if mn not in ("", "*"):
                try:
                    width["min"] = int(mn)
                except ValueError:
                    return None
            if mx not in ("", "*"):
                try:
                    width["max"] = int(mx)
                except ValueError:
                    return None
        pres1 = raw[1:]
        pres2 = None
        if len(pres1) > 1 and pres1[-1] in "atco":
            pres2 = pres1[-1]
            pres1 = pres1[:-1]
        if not pres1:
            pres1 = _FMS_DEFAULT.get(comp)
            if pres1 is None:
                return None
        m["component"] = comp
        m["width"] = width
        m["pres2"] = pres2
        m["ordinal"] = pres2 == "o"
        names = None
        if pres1[0] == "n":
            names = "lower"
        elif pres1[0] == "N":
            names = "title" if len(pres1) > 1 and pres1[1] == "n" else "upper"
        m["names"] = names
        m["intfmt"] = None
        if names is None and comp in _FMS_INT:
            pat = pres1 + (";" + pres2 if pres2 else "")
            m["intfmt"] = _fms_int_fmt(pat)
            if "min" in width and m["intfmt"]["mandatory"] < width["min"]:
                m["intfmt"]["mandatory"] = width["min"]
            if comp == "Y":
                m["yn"] = -1
                if "max" in width:
                    m["yn"] = width["max"]
                    m["intfmt"]["mandatory"] = width["max"]
                else:
                    w = m["intfmt"]["mandatory"] + m["intfmt"]["optional"]
                    if w >= 2:
                        m["yn"] = w
        if comp in ("Z", "z"):
            m["intfmt"] = _fms_int_fmt(pres1)
        m["ordinal"] = bool(m.get("ordinal"))
    return spec


def _fms_frag(dt, comp):
    """`getDateTimeFragment` 的 Python 版。"""
    d = dt.date()
    if comp == "Y":
        return dt.year
    if comp == "M":
        return dt.month
    if comp == "D":
        return dt.day
    if comp == "d":
        return (d - d.replace(month=1, day=1)).days + 1
    if comp == "F":
        return d.isoweekday()
    if comp in ("W", "X", "x", "w"):
        def start_first_week(year, month0):
            jan1 = datetime(year, month0 + 1, 1, tzinfo=_FMS_utc).date()
            dow = jan1.isoweekday()
            return jan1 + timedelta(days=8 - dow) if dow > 4 else jan1 - timedelta(days=dow - 1)

        def delta_weeks(a, b):
            return (b - a).days // 7 + 1

        if comp == "W":
            sw = start_first_week(d.year, 0)
            week = delta_weeks(sw, d)
            if week > 52:
                if d >= start_first_week(d.year + 1, 0):
                    week = 1
            elif week < 1:
                week = delta_weeks(start_first_week(d.year - 1, 0), d)
            return week
        if comp == "w":
            sw = start_first_week(d.year, d.month - 1)
            week = delta_weeks(sw, d)
            if week > 4:
                ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
                if d >= start_first_week(ny, nm - 1):
                    week = 1
            elif week < 1:
                py, pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
                week = delta_weeks(start_first_week(py, pm - 1), d)
            return week
        if comp == "X":
            start = start_first_week(d.year, 0)
            end = start_first_week(d.year + 1, 0)
            if d < start:
                return d.year - 1
            if d >= end:
                return d.year + 1
            return d.year
        start = start_first_week(d.year, d.month - 1)
        ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        end = start_first_week(ny, nm - 1)
        if d < start:
            py, pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
            return pm
        if d >= end:
            return nm
        return d.month
    if comp == "H":
        return dt.hour
    if comp == "h":
        return dt.hour % 12 or 12
    if comp == "P":
        return "pm" if dt.hour >= 12 else "am"
    if comp == "m":
        return dt.minute
    if comp == "s":
        return dt.second
    if comp == "f":
        return dt.microsecond // 1000
    return 0


def _fms_component(dt, m):
    comp = m["component"]
    val = _fms_frag(dt, comp)
    if comp in _FMS_INT:
        if comp == "Y" and m.get("yn", -1) != -1:
            val = val % (10 ** m["yn"])
        if m["names"]:
            if comp in ("M", "x"):
                val = _MONTHS[val - 1]
            elif comp == "F":
                val = _DAY_NAMES[val]
            else:
                return UNDEF
            if m["names"] == "upper":
                val = val.upper()
            elif m["names"] == "lower":
                val = val.lower()
            w = m["width"]
            if w and "max" in w and len(val) > w["max"]:
                val = val[:w["max"]]
            return val
        return _fms_format_int(val, m["intfmt"])
    if comp == "f":
        return _fms_format_int(val, m["intfmt"])
    if comp in ("Z", "z"):
        fmt = m["intfmt"]
        offset = 0
        if fmt["regular"]:
            out = _fms_format_int(offset, fmt)
        else:
            nd = fmt["mandatory"]
            if nd in (1, 2):
                out = _fms_format_int(0, fmt)
            elif nd in (3, 4):
                out = _fms_format_int(offset, fmt)
            else:
                return UNDEF
        if offset >= 0:
            out = "+" + out
        if comp == "z":
            out = "GMT" + out
        if offset == 0 and m["pres2"] == "t":
            out = "Z"
        return out
    if comp in ("C", "E"):
        return "ISO"
    if comp == "P":
        return val.upper() if m["names"] == "upper" else val
    return None


def _fms(ms, picture=UNDEF):
    """`$fromMillis(ms[, picture])`：2.2.2 的 ISO8601 默认与 picture 分量语义。"""
    if type(ms) is bool or not _num(ms):
        return UNDEF
    if _ACC is not None and (picture is UNDEF or picture is None):
        # 双活：实测 7.41×/3.16×，只覆盖默认 ISO8601 形态。picture 一律走下面的
        r = _ACC.fms(ms)
        if r is not None:
            return r
    try:
        dt = _FMS_EPOCH + timedelta(milliseconds=float(ms))
    except (ValueError, OverflowError):
        return UNDEF
    if picture is UNDEF or picture is None:
        return (dt.strftime("%Y-%m-%dT%H:%M:%S.")
                + "%03d" % (dt.microsecond // 1000) + "Z")
    if type(picture) is not str:
        return UNDEF
    spec = _fms_parse_picture(picture)
    if spec is None:
        return UNDEF
    out = []
    for m in spec:
        if m["kind"] == "literal":
            out.append(m["text"])
            continue
        piece = _fms_component(dt, m)
        if piece is UNDEF:
            return UNDEF
        if piece is None:
            out.append("[" + m["text"] + "]")
        else:
            out.append(piece)
    return "".join(out)


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def _tms(v):
    """`$toMillis(str)`：ISO8601 -> 毫秒；小数秒超过 3 位截断（2.2.2 新增）。"""
    if type(v) is not str:
        return UNDEF
    if _ACC is not None:
        # 双活：实测 7.59×/7.12×（纯整数历法计算）。
        r = _ACC.tms(v)
        if r is not None:
            return r
    m = _ISO_RE.match(v.strip())
    if m is None:
        return UNDEF
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0),
                      int(ss or 0), tzinfo=timezone.utc)
        base = int(dt.timestamp()) * 1000
    except (ValueError, OSError, OverflowError):
        return UNDEF
    if tz is not None and tz != "Z":
        body = tz[1:].replace(":", "")
        off = int(body[:2]) * 60 + int(body[2:])
        base -= (off if tz[0] == "+" else -off) * 60000
    return base + int((frac or "0")[:3].ljust(3, "0"))


_ONES_EN = ("zero", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
            "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS_EN = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
            "eighty", "ninety")
_SCALES_EN = ("", "thousand", "million", "billion", "trillion")


def _en2(n):
    if n < 20:
        return _ONES_EN[n]
    t, r = divmod(n, 10)
    return _TENS_EN[t] + ("-" + _ONES_EN[r] if r else "")


def _en3(n):
    h, r = divmod(n, 100)
    out = []
    if h:
        out.append(_ONES_EN[h] + " hundred")
    if r:
        if h:
            out.append("and")
        out.append(_en2(r))
    return " ".join(out)


def _en_words(n):
    if n == 0:
        return "zero"
    neg, n, groups = n < 0, abs(n), []
    while n:
        n, r = divmod(n, 1000)
        groups.append(r)
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        if not groups[i]:
            continue
        seg = _en3(groups[i])
        if i:
            seg += " " + _SCALES_EN[i]
        parts.append(seg)
    return ("-" if neg else "") + ", ".join(parts)


_EN_WORDS = {w: i for i, w in enumerate(_ONES_EN)}
_EN_WORDS.update({w: 10 * i for i, w in enumerate(_TENS_EN) if w})
_EN_WORDS.update({"hundred": 100, "thousand": 1000, "million": 1000000,
                  "billion": 1000000000, "trillion": 1000000000000})


def _en_parse(s):
    total, cur = 0, 0
    for tok in s.lower().replace(",", " ").replace("-", " ").split():
        if tok == "and":
            continue
        val = _EN_WORDS.get(tok)
        if val is None:
            return UNDEF
        if val >= 100:
            total += (cur or 1) * val
            cur = 0
        else:
            cur += val
    return total + cur


def _alpha_num(n, lower):
    if n <= 0:
        return ""
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out.lower() if lower else out


_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
          (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
          (4, "IV"), (1, "I"))


#: 罗马数字输出的长度上限（字符）。罗马图样是「值多大就多长」的图样：`n` 每涨
_ROMAN_MAX = 1 << 20


def _roman_len(n):
    """罗马数字表示的长度（不分配字符串，只算位数）。"""
    if n <= 0:
        return 0
    total = 0
    for val, sym in _ROMAN:
        k = n // val
        if k:
            total += k * len(sym)
            n -= k * val
    return total


def _roman_num(n):
    """整数转罗马数字（与 2.2.2 的 `formatInteger` 一致）。
"""
    if n <= 0:
        return ""
    out = []
    for val, sym in _ROMAN:
        k = n // val
        if k:
            out.append(sym * k)
            n -= k * val
    return "".join(out)


def _roman_parse(s):
    up = s.upper()
    vals = dict((sym, val) for val, sym in _ROMAN)
    total, i = 0, 0
    while i < len(up):
        two = up[i:i + 2]
        if len(two) == 2 and two in vals:
            total += vals[two]
            i += 2
            continue
        one = up[i]
        if one not in vals:
            return UNDEF
        total += vals[one]
        i += 1
    return total


def _fgi(v, picture):
    """`$formatInteger(value, picture)`：十进制/字母/罗马/英文单词（2.2.2 子集）。
"""
    if type(v) is bool or not _num(v) or type(picture) is not str or not picture:
        return UNDEF
    # 门控只开给十进制掩码图样（判据见 `_FGI_ALPHA`）：字母/英文单词图样
    if _ACC is not None and picture not in _FGI_ALPHA and "." not in picture:
        r = _ACC.fgi(v, picture)
        if r is not None:
            return r
    n = int(v)
    if picture == "A":
        return _alpha_num(n, False)
    if picture == "a":
        return _alpha_num(n, True)
    if picture in ("I", "i"):
        # 罗马图样的输出长度与值成正比：超过 `_ROMAN_MAX` 直接 undefined。
        if _roman_len(n) > _ROMAN_MAX:
            return UNDEF
        out = _roman_num(n)
        return out if picture == "I" else out.lower()
    if picture in ("w", "W", "Ww"):
        txt = _en_words(n)
        return txt.upper() if picture == "W" else (txt.title() if picture == "Ww" else txt)
    # 十进制 picture：官方只认「以 0 结尾的整数子图」，`#` 开头/无 0 一律 D3130
    if not picture or picture[0] == "," or picture[-1] == ",":
        return UNDEF
    if "." in picture:
        return UNDEF
    last = picture.rsplit(",", 1)[-1]
    if "0" not in last or set(last) - set("0#"):
        return UNDEF
    width = sum(1 for ch in last if ch in "0#")
    mand = last.count("0")
    digits = str(abs(n)).zfill(mand)
    if "," in picture:
        parts, s = [], digits
        while len(s) > width:
            parts.insert(0, s[-width:])
            s = s[:-width]
        if s:
            parts.insert(0, s)
        digits = ",".join(parts)
    return ("-" if n < 0 else "") + digits


def _pgi(v, picture):
    """`$parseInteger(str, picture)`：十进制/字母/罗马/单词（2.2.2 子集）。"""
    if type(v) is not str or type(picture) is not str or not picture:
        return UNDEF
    if _ACC is not None:
        # 双活：实测 2.74×/3.29×；非 ASCII 字母图样与超 i64 位数落回下面。
        r = _ACC.pgi(v, picture)
        if r is not None:
            return r
    s = v.strip()
    if picture in ("A", "a"):
        if not s.isalpha():
            return UNDEF
        n = 0
        for ch in s.upper():
            n = n * 26 + (ord(ch) - 64)
        return n
    if picture in ("I", "i"):
        return _roman_parse(s)
    if picture in ("w", "W", "Ww"):
        return _en_parse(s)
    if not picture or picture[0] == "," or picture[-1] == "," or "." in picture:
        return UNDEF
    last = picture.rsplit(",", 1)[-1]
    if "0" not in last or set(last) - set("0#"):
        return UNDEF
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return UNDEF
    return -int(digits) if neg else int(digits)


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
    # 数据级钩子守卫
    "_hb": _hb, "_ha": _ha, "_xp": _xp,
    # JSONata 2.2.2 新增（A 类）
    "_eu": _eu, "_du": _du, "_zp": _zp, "_cl": _cl, "_rx": _rx,
    "_mt": _mt, "_fms": _fms, "_tms": _tms, "_fgi": _fgi, "_pgi": _pgi,
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
def _helper_source(name, fn):
    """`HELPERS[name]` -> 源码文本；取不到就报错，绝不静默留空。"""
    try:
        text = inspect.getsource(fn)
    except (OSError, TypeError) as exc:  # pragma: no cover - 打包/REPL 无源码时
        raise RuntimeError(f"无法取得 helper {name} 的源码: {exc}") from exc
    return text if text.endswith("\n") else text + "\n"


SOURCE = {name: _helper_source(name, fn) for name, fn in HELPERS.items()}


# 产物里需要 `import math` 的助手。产物发的是**裸** `import math`。
SOURCE_NEEDS_MATH = frozenset(
    n for n, t in SOURCE.items() if "math" in t or "_M." in t) - {"_pw"}

#: 产物头部的前导常量。
SOURCE_PRELUDE = ""

#: 产物里的短名哨兵（P0-1）：与 `UNDEF` **同一个**单例对象，绝不是字面量。
_U = UNDEF

#: `from dcg.runtime import*` 的导出面（产物就靠这一行拿到全部 helper）。
__all__ = sorted(
    set(SOURCE) | set(HELPERS) | {"_cmp_ok"} |
    {"UNDEF", "_U", "is_u", "HELPERS", "SOURCE", "SOURCE_NEEDS_MATH",
     "SOURCE_PRELUDE", "UNDEFINED_SEMANTICS", "CLOCK", "RANDOM"}
)
