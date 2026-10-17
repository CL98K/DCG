"""DCG v2 运行期 helper —— undefined 语义的**唯一实现处**。

产物的引用方式（Lead 裁决 2026-10-17，方案 ii）：生成码顶部写
``from dcg.runtime import*``，**不内联** helper 体。本模块因此是
① 产物 helper 的真相源 ② 单元测试的直接被测对象；本文件里的 `SOURCE` 字典
（内联形态的等价函数体）保留给 snapshot 测试做自洽性校验，不再是产物的一部分。
两条理由把内联方案钉死了：契约 §7 的字符预算（CAP<=180 / M0<=210，按 `len(src)`
全量计数）容不下 300+ 字符的 helper 前导；且内联的 `$number`/`$floor` 必然带
`int(`/`float(` 与大量 `is _U`，直接违反规则 6（type 是声明不是转换）与规则 7
（只有 optional 字段带 undefined 守卫）。

硬约束（CONTRACT.md §4.4）：
- 每个运算符一个**专用函数**，禁止 dict-of-lambda 分派表（M0 实测派发脚手架占守卫成本 89%）
- undefined 用单例 `UNDEF` 身份比较，不用 None；短名 `_U` 是同一个对象（P0-1）
- 六条语义逐条保留（见 tests/test_codegen_snapshot.py::test_runtime_undefined_semantics）

命名取两字符（`_ad`/`_cc`/`_eq`/`_lt`...）：产物里的每个引用约占 4 字符，
两字符名是"每运算符专用函数"与 §7 字符预算之间的折中。
"""

import inspect


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
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tru(v):
    """JSONata truthiness：undefined/null/false/''/0/[]/{} 为假。"""
    if v is UNDEF or v is None or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    if isinstance(v, (list, dict, tuple)):
        return len(v) > 0
    return True


# ---------------------------------------------------------------- 二元算术
def _ad(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b):
        return a + b
    raise TypeError("$add: 操作数必须是数值")


def _sb(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b):
        return a - b
    raise TypeError("$subtract: 操作数必须是数值")


def _ml(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b):
        return a * b
    raise TypeError("$multiply: 操作数必须是数值")


def _dv(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b) and b != 0:
        return a / b
    return UNDEF


def _md(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b) and b != 0:
        return a % b
    return UNDEF


def _pg(a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if _num(a) and _num(b):
        return a ** b
    return UNDEF


# ---------------------------------------------------------------- 拼接 / 逻辑
def _cc(a, b):
    """`&`：undefined **不传播**，当空串；null 当 ''；非字符串先转字符串。"""
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
    """`$indexOf`。"""
    if s is UNDEF or s is None or not isinstance(s, str):
        return UNDEF
    if sub is UNDEF or sub is None:
        return UNDEF
    return s.find(_S(sub))


def _cn(a, b):
    """`$contains`。"""
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
    if v is UNDEF:
        return UNDEF
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if _num(v):
        return v
    if isinstance(v, str):
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
    return abs(v) if _num(v) else UNDEF


def _fl(v):
    import math as _m
    return _m.floor(v) if _num(v) else UNDEF


def _ce(v):
    import math as _m
    return _m.ceil(v) if _num(v) else UNDEF


def _ro(v, p=0):
    if not _num(v):
        return UNDEF
    return round(v, p) if p else round(v)


def _sq(v):
    import math as _m
    if not _num(v) or v < 0:
        return UNDEF
    return _m.sqrt(v)


def _pw(a, b):
    if not (_num(a) and _num(b)):
        return UNDEF
    return a ** b


# ---------------------------------------------------------------- 数组 / 下标
def _ar(*items):
    """数组字面量：**跳过** undefined（语义 5）。"""
    return [x for x in items if x is not UNDEF]


def _ob(*args, **kw):
    """对象字面量：**丢弃** undefined 键（语义 6）。

    两种调用形态等价：`_ob(a=1)`（名字是标识符时）与 `_ob([(k, v), ...])`
    （名字任意，产物用这种，避免为非法标识符的键生成 kwargs）。
    """
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = tuple(args[0])
    out = {}
    for it in args:
        k, v = it
        if v is not UNDEF:
            out[k] = v
    for k, v in kw.items():
        if v is not UNDEF:
            out[k] = v
    return out


def _ix_get(a, i):
    """`a[i]`：列表按下标；标量对任何下标都返回自身（`a[0] == a`）。"""
    if a is UNDEF:
        return UNDEF
    if isinstance(a, (list, tuple)):
        try:
            return a[i]
        except IndexError:
            return UNDEF
    if isinstance(a, dict):
        return a.get(i, UNDEF) if isinstance(i, str) else UNDEF
    if isinstance(a, str):
        try:
            return a[i]
        except IndexError:
            return UNDEF
    return a


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


HELPERS = {
    "_ad": _ad, "_sb": _sb, "_ml": _ml, "_dv": _dv, "_md": _md, "_pg": _pg,
    "_cc": _cc, "_and": _and, "_or": _or, "_nt": _nt, "_neg": _neg, "_pos": _pos,
    "_eq": _eq, "_ne": _ne, "_lt": _lt, "_le": _le, "_gt": _gt, "_ge": _ge,
    "_in": _in, "_S": _S, "_up": _up, "_lo": _lo, "_tm": _tm, "_ln": _ln,
    "_ct": _ct, "_sp": _sp, "_jn": _jn, "_ss": _ss, "_ix": _ix, "_cn": _cn,
    "_N": _N, "_B": _B, "_ab": _ab, "_fl": _fl, "_ce": _ce, "_ro": _ro,
    "_sq": _sq, "_pw": _pw, "_ar": _ar, "_ob": _ob, "_ix_get": _ix_get,
    "_ty": _ty, "_ex": _ex, "_b": _b, "_num": _num, "_tru": _tru, "is_u": is_u,
    "_co": _co,
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
# verifier-dev 的对拍（16094 组）测出旧手抄版有 1942 处漂移（`_cn`/`_ix_get`/`_jn`
# 语义相反、`_N` 引用不存在的 `_NNN`、`_fl/_ce/_sq` 缺 `_m`）。派生后漂移在构造上
# 不可能发生；`tests/test_codegen_snapshot.py` 逐名对拍守住这条。
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
#: 由源码派生，不再手抄（手抄正是旧版漏掉 `_m` 的原因）。
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
     "SOURCE_PRELUDE", "UNDEFINED_SEMANTICS"}
)
