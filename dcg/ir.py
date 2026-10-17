"""DCG v2 冻结 IR（CONTRACT.md §1）。

IR 是**纯 tuple**，不是 class：便于比较、可哈希、零构造开销、snapshot 友好。
每个节点的**末元素恒为 loc**（`(line, col)` 或 `None`）。

本模块只定义 IR 与构造函数辅助，**不 import 任何同级模块**（依赖方向最底层）。
"""

# ---------------------------------------------------------------- 节点种类
LIT = "lit"
FIELD = "field"
INDEX = "index"
ROOT = "root"
VAR = "var"
UNARY = "unary"
BIN = "bin"
COND = "cond"
CALL = "call"
RFUNC = "rfunc"
ARRAY = "array"
OBJECT = "object"
PY = "py"
PYBLOCK = "pyblock"
RANGE = "range"
LAMBDA = "lambda"

KINDS = frozenset({LIT, FIELD, INDEX, ROOT, VAR, UNARY, BIN, COND, CALL,
                   RFUNC, ARRAY, OBJECT, PY, PYBLOCK, RANGE, LAMBDA})

UNARY_OPS = frozenset({"-", "+", "not"})

#: `bin.op` -> (含义, undefined 语义族)
BIN_OPS = {
    "+": ("算术", "propagate"), "-": ("算术", "propagate"),
    "*": ("算术", "propagate"), "/": ("算术", "propagate"),
    "%": ("算术", "propagate"),
    "&": ("字符串拼接", "empty"),
    "=": ("相等", "false"), "!=": ("相等", "false"),
    "<": ("大小", "undef"), "<=": ("大小", "undef"),
    ">": ("大小", "undef"), ">=": ("大小", "undef"),
    "and": ("逻辑", "jsonata"), "or": ("逻辑", "jsonata"),
    "in": ("成员", "false"),
}


# ---------------------------------------------------------------- 构造辅助
def lit(value, loc=None):
    return (LIT, value, loc)


def field(name, loc=None):
    return (FIELD, name, loc)


def index(base, idx, loc=None):
    return (INDEX, base, idx, loc)


def root(loc=None):
    return (ROOT, loc)


def var(name, loc=None):
    return (VAR, name, loc)


def unary(op, operand, loc=None):
    if op not in UNARY_OPS:
        raise ValueError(f"未知一元运算符: {op!r}")
    return (UNARY, op, operand, loc)


def bin_(op, left, right, loc=None):
    if op not in BIN_OPS:
        raise ValueError(f"未知二元运算符: {op!r}")
    return (BIN, op, left, right, loc)


def cond(test, then, orelse, loc=None):
    return (COND, test, then, orelse, loc)


def call(fname, args, loc=None):
    return (CALL, fname, list(args), loc)


def rfunc(qualname, args, loc=None):
    return (RFUNC, qualname, list(args), loc)


def array(items, loc=None):
    return (ARRAY, list(items), loc)


def obj(pairs, loc=None):
    return (OBJECT, list(pairs), loc)


def py(source, free_field_names=(), loc=None):
    return (PY, source, list(free_field_names), loc)


def pyblock(stmts_source, free_field_names=(), loc=None):
    """`py{ ... }` 多语句块（契约 §1 / §3.7）。

    `stmts_source` 是**编译期定稿的语句序列**（含唯一一条 `return`，语句之间用 `;`
    分隔），语义上是一个函数体、不是表达式；`free_field_names` = 块内 `$.`-链的链文本
    （不含前导 `$.`），按出现顺序。codegen 为每个块发一个命名辅助函数。
    """
    return (PYBLOCK, stmts_source, list(free_field_names), loc)


def rng(lo, hi, loc=None):
    return (RANGE, lo, hi, loc)


def lam(params, body, loc=None):
    return (LAMBDA, list(params), body, loc)


# ---------------------------------------------------------------- 结构访问
def kind(node):
    return node[0]


def loc_of(node):
    if not isinstance(node, tuple) or not node:
        return None
    last = node[-1]
    return last if isinstance(last, tuple) and len(last) == 2 else None


def children(node):
    """返回所有直接子 IR 节点（用于遍历/分析）。"""
    k = node[0]
    if k == INDEX:
        return (node[1], node[2])
    if k == UNARY:
        return (node[2],)
    if k == BIN:
        return (node[2], node[3])
    if k == COND:
        return (node[1], node[2], node[3])
    if k in (CALL, RFUNC):
        return tuple(node[2])
    if k == ARRAY:
        return tuple(node[1])
    if k == OBJECT:
        return tuple(v for _, v in node[1])
    if k == RANGE:
        return (node[1], node[2])
    if k == LAMBDA:
        return (node[2],)
    return ()


def walk(node):
    """深度优先遍历 IR，产出每个节点（含自身）。"""
    if not isinstance(node, tuple):
        return
    yield node
    for c in children(node):
        yield from walk(c)


def is_const(node):
    """静态常量节点（可在编译期求值）。"""
    k = node[0]
    if k == LIT:
        return True
    if k == ARRAY:
        return all(is_const(i) for i in node[1])
    if k == OBJECT:
        return all(is_const(v) for _, v in node[1])
    if k == UNARY:
        return is_const(node[2])
    if k == BIN:
        return is_const(node[2]) and is_const(node[3])
    return False


def key_path(node):
    """把 FIELD/INDEX 链折成从根出发的键路径；不是路径访问则返回 None。

    `$.5.a.1` -> `(5, "a", 1)`；`$.2` -> `(2,)`。`loc` 不参与。
    路径未包含 ROOT 前缀（`$` 为隐式起点）。
    """
    k = node[0]
    if k == FIELD:
        return (node[1],)
    if k == INDEX:
        base = key_path(node[1])
        idx = node[2]
        if base is None or idx[0] != LIT or not isinstance(idx[1], (int, str)):
            return None
        return base + (idx[1],)
    return None


__all__ = [
    "LIT", "FIELD", "INDEX", "ROOT", "VAR", "UNARY", "BIN", "COND", "CALL",
    "RFUNC", "ARRAY", "OBJECT", "PY", "PYBLOCK", "RANGE", "LAMBDA", "KINDS",
    "UNARY_OPS", "BIN_OPS",
    "lit", "field", "index", "root", "var", "unary", "bin_", "cond", "call",
    "rfunc", "array", "obj", "py", "pyblock", "rng", "lam",
    "kind", "loc_of", "children", "walk", "is_const", "key_path",
]
