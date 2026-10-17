# -*- coding: utf-8 -*-
"""
DCG × JSONata 语义编译可行性验证 (自包含, 可直接 python jsonata_poc.py 运行)

问题: DCG 的 ext 字段让用户直接写 Python 来支撑复杂转换, 但用户希望拥有
      JSONata 那样的表达能力, 又不愿放弃 DCG 的结构编译/Cython 加速。
结论: 二者不冲突 —— 把 JSONata 表达式**编译**成 Python 表达式(而非运行时解释),
      就能接进 DCG 现有链路 (dcg.py:976 __ext 解析 -> dcg.py:339 tranfIFS 重写字段
      -> dcg.py:1247 compile 成字节码)。

本文件验证三件事:
  1) 语义能否对齐官方实现 —— 与 jsonata-python 逐例对照 (56 个用例)
  2) 速度 —— 编译后逐行 vs jsonata-python 解释器
  3) 边界的正确取舍 —— 不可逐行编译的结构必须"显式报错", 绝不静默降级

依赖: pip install jsonata   (仅用于对照, 生产接入不需要它)
"""
import ast as pyast
import re
import time

# ============================================================================
# 1. undefined 哨兵 (JSONata 的 "无此字段/空序列")
# ============================================================================
class _Undef:
    _inst = None
    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst
    def __repr__(self):  return "undefined"
    def __bool__(self):  return False

UNDEF = _Undef()
def _is_u(v): return v is UNDEF


# ============================================================================
# 2. 运行期语义 (JSONata 语义集中在这里; 全部经官方实现对照后修正)
# ============================================================================
# 教训 1: 守卫必须放进 helper, 不能把它们展开进编译产物。
#   第一版把 "x is not _U" 内联到每个运算符, 产物指数膨胀 + 触发 SyntaxWarning。
#   改成生成 _add(a, b) 这样的调用后, 产物保持线性 (134 字符)。
#
# 教训 2: 下列 6 条语义我第一版**全错**, 是靠与 jsonata-python 逐例对照才发现
#   (若只靠阅读 JSONata 文档写实现, 这几条几乎必然写错):
#   (a) `&` 的 undefined 不传播, 当空串:        'x' & miss == 'x'
#   (b) `or` 的 undefined 参与运算:             miss or true == True
#   (c) `=`/`!=` 遇 undefined 返回 False:       miss = miss == False
#   (d) 大小比较遇 undefined 返回 undefined:    miss < 5 == undefined
#   (e) 数组字面量跳过 undefined:               [miss, a] == [1]
#   (f) 对象字面量丢弃 undefined 键:            {'k': miss} == {}

def _jstr(v):
    if v is True:  return "true"
    if v is False: return "false"
    if v is None:  return "null"
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v)

def _add(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    if a is None or b is None: raise TypeError("JSONata: null 不能参与 '+' 运算")
    return a + b

def _sub(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    if a is None or b is None: raise TypeError("JSONata: null 不能参与 '-' 运算")
    return a - b

def _mul(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    if a is None or b is None: raise TypeError("JSONata: null 不能参与 '*' 运算")
    return a * b

def _div(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    if a is None or b is None: raise TypeError("JSONata: null 不能参与 '/' 运算")
    return a / b

def _mod(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    if a is None or b is None: raise TypeError("JSONata: null 不能参与 '%' 运算")
    return a % b

def _concat(a, b):
    """`&`: 两侧可为任意类型, 先 string() 化; undefined 视为空串 (教训 2a)"""
    if a is UNDEF: a = ""
    if b is UNDEF: b = ""
    return _jstr(a) + _jstr(b)

def _eq(a, b):
    if a is UNDEF or b is UNDEF: return False        # 教训 2c
    return a == b

def _ne(a, b):
    if a is UNDEF or b is UNDEF: return False        # miss != miss == False
    return a != b

def _lt(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF        # 教训 2d
    return a < b

def _le(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    return a <= b

def _gt(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    return a > b

def _ge(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    return a >= b

def _and(a, b):
    if a is UNDEF or b is UNDEF: return False
    return bool(a) and bool(b)

def _or(a, b):
    """教训 2b: undefined 视为 false 参与 or, 不是整体返回 False"""
    return (False if a is UNDEF else bool(a)) or (False if b is UNDEF else bool(b))

def _not(a):
    return UNDEF if a is UNDEF else (not _truthy(a))

def _in(a, b):
    if a is UNDEF or b is UNDEF: return False
    return a in b

def _truthy(v):
    """JSONata boolean(): null/空串/0/空数组 -> false"""
    if v is None or v is False or v == "" or v == 0: return False
    if isinstance(v, list) and not v: return False
    return True

def _arr(*items):
    """教训 2e: 数组字面量跳过 undefined"""
    return [x for x in items if x is not UNDEF]

def _obj(pairs):
    """教训 2f: 对象字面量丢弃值为 undefined 的键"""
    return {k: v for k, v in pairs if v is not UNDEF}

def _index(a, idx):
    """序列下标: JSONata 对标量也成立 (a[0] == a), undefined 保持 undefined"""
    if a is UNDEF: return UNDEF
    if isinstance(idx, list):
        if not isinstance(a, list): return [a] if 0 in idx else UNDEF
        return [a[i] for i in idx if isinstance(i, int) and -len(a) <= i < len(a)]
    if not isinstance(idx, int): return UNDEF
    if isinstance(a, list):
        return a[idx] if -len(a) <= idx < len(a) else UNDEF
    return a if idx == 0 else UNDEF

def _map(a, key):
    """路径映射 a.b (逐行场景: 单值/单层字典)。完整 JSONata map 会遍历数组并展平
    序列, 那属于批量阶段, 见 BATCH_ONLY 说明。"""
    if a is UNDEF: return UNDEF
    if isinstance(key, list): return UNDEF
    if isinstance(a, list): return _arr(*[_map(x, key) for x in a])
    if isinstance(a, dict): return a.get(key, UNDEF)
    return UNDEF

def _u1(fn):
    def h(a): return UNDEF if a is UNDEF else fn(a)
    return h

_lower    = _u1(lambda a: a.lower())
_upper    = _u1(lambda a: a.upper())
_trim     = _u1(lambda a: a.strip())
_str      = _u1(_jstr)
_num      = _u1(float)
_bool     = _u1(_truthy)
_abs      = _u1(abs)
_floor    = _u1(lambda a: float(__import__("math").floor(a)))
_ceil     = _u1(lambda a: float(__import__("math").ceil(a)))
_round    = _u1(lambda a: float(round(a)))
_length   = _u1(len)
_count    = lambda a: 0 if a is UNDEF else (len(a) if isinstance(a, list) else 1)

def _slice(a, start, end=None):
    if a is UNDEF: return UNDEF
    start = int(start)
    return a[start:] if end is None else a[start:start + int(end)]

def _before(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    i = a.find(b)
    return a[:i] if i >= 0 else a

def _after(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    i = a.find(b)
    return a[i + len(b):] if i >= 0 else ""

def _split(a, b):
    if a is UNDEF: return UNDEF
    return list(a) if not b else a.split(b)

def _join(a, b):
    if a is UNDEF: return UNDEF
    return b.join(_jstr(x) for x in a) if isinstance(a, list) else _jstr(a)

def _contains(a, b):
    if a is UNDEF or b is UNDEF: return UNDEF
    return b in a

def _replace(a, b, c):
    if a is UNDEF: return UNDEF
    pat = b.pattern if hasattr(b, "pattern") else __import__("re").escape(str(b))
    return __import__("re").sub(pat, str(c), str(a))

def _helpers():
    ns = {k: v for k, v in globals().items()
          if k.startswith("_") and callable(v) and not k.startswith("__")}
    ns["_U"] = UNDEF     # 哨兵不是可调用对象, 必须显式放入
    return ns

# 注意: 哨兵名与全局 UNDEF 必须区分。第一版写成 U = "_U", 导致 _helpers()
# 把字符串 "_U" 塞进命名空间, 全部用例 NameError。
UNDEF_NAME = "_U"


# ============================================================================
# 3. 词法 + 语法: JSONata 表达式 -> 自有 AST
# ============================================================================
TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+\.\d+|\d+)
  | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<re>/(?:[^/\\]|\\.)+/)
  | (?P<var>\$\$|\$[A-Za-z_][A-Za-z0-9_]*|\$)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>:=|!=|<=|>=|\.\.|[-+*/%<>=&.,()\[\]{}:])
""", re.X)

def lex(src):
    out, pos = [], 0
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise SyntaxError(f"无法识别的字符 @{pos}: {src[pos:pos+12]!r}")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        out.append((m.lastgroup, m.group()))
    out.append(("eof", ""))
    return out

class P:
    """Pratt 解析器"""
    BP = {"or": 10, "and": 20, "=": 30, "!=": 30, "<": 30, "<=": 30, ">": 30, ">=": 30,
          "in": 30, "&": 40, "+": 50, "-": 50, "*": 60, "/": 60, "%": 60}
    WORD_OPS = {"and", "or", "in"}      # JSONata 里是关键字, 词法上是 name

    def __init__(self, toks): self.t, self.i = toks, 0
    def peek(self, k=0):  return self.t[self.i + k]
    def next(self):       tok = self.t[self.i]; self.i += 1; return tok
    def eat(self, val):
        if self.peek()[1] != val:
            raise SyntaxError(f"期望 {val!r}, 实际 {self.peek()[1]!r}")
        return self.next()

    def parse(self):
        node = self.expr(0)
        if self.peek()[0] != "eof":
            raise SyntaxError(f"多余内容: {self.peek()[1]!r}")
        return node

    def expr(self, minbp):
        left = self.prefix()
        while True:
            kind, val = self.peek()
            # 调用统一按后缀处理: name/$var 在 prefix 里先成为 ref, 这里再吃实参表。
            # 修正前只在 prefix 识别调用, 导致 `$not(miss)`、`a[0]` 解析到一半报
            # "多余内容" (前缀函数名后的实参没被消费)。
            if kind == "op" and val == "(":
                if 100 < minbp: break
                self.next()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr(0))
                    while self.peek()[1] == ",":
                        self.next(); args.append(self.expr(0))
                self.eat(")")
                if left[0] != "ref":
                    raise SyntaxError("只有名字/变量可以调用")
                left = ("call", left[1], args)
                continue
            if kind == "op" and val == ".":
                if 90 < minbp: break
                self.next(); right = self.expr(90)
                left = ("map", left, right); continue
            if kind == "op" and val == "[":
                if 95 < minbp: break
                self.next(); idx = self.expr(0); self.eat("]")
                left = ("idx", left, idx); continue
            if kind == "op" and val == "..":
                if 45 < minbp: break
                self.next(); right = self.expr(45)
                left = ("range", left, right); continue
            key = None
            if kind == "op":
                key = val
            elif kind == "name" and val in self.WORD_OPS:
                key = val
            if key not in self.BP: break
            bp = self.BP[key]
            if bp < minbp: break
            self.next()
            right = self.expr(bp + 1)
            left = ("bin", key, left, right)
        return left

    def prefix(self):
        kind, val = self.next()
        if kind == "num":  return ("lit", float(val) if "." in val else int(val))
        if kind == "str":  return ("lit", pyast.literal_eval(val))
        if kind == "re":   return ("lit", re.compile(val[1:-1]))
        if kind == "name":
            if val == "function":
                raise SyntaxError("暂不支持 function(...) 字面量 (高阶函数需整体求值)")
            if val in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[val])
            return ("ref", val)
        if kind == "var":
            return ("ref", val)              # $ / $x / $$ ; 调用由后缀处理
        if val == "(":
            node = self.expr(0); self.eat(")"); return node
        if val == "-":  return ("neg", self.expr(70))
        if val == "[":
            items = []
            if self.peek()[1] != "]":
                items.append(self.expr(0))
                while self.peek()[1] == ",":
                    self.next(); items.append(self.expr(0))
            self.eat("]")
            return ("arr", items)
        if val == "{":
            keys, vals = [], []
            if self.peek()[1] != "}":
                while True:
                    keys.append(self.expr(0)); self.eat(":"); vals.append(self.expr(0))
                    if self.peek()[1] != ",": break
                    self.next()
            self.eat("}")
            return ("obj", list(zip(keys, vals)))
        raise SyntaxError(f"不支持的起始记号: {kind} {val!r}")


# ============================================================================
# 4. 编译: 自有 AST -> Python 表达式源码
# ============================================================================
class Unsupported(Exception):
    """不能逐行编译的结构。必须显式抛出, 由调用方回退到批量阶段 —— 绝不静默降级。"""

ROW_FUNCS = {
    "uppercase": "_upper", "lowercase": "_lower", "trim": "_trim",
    "string": "_str", "number": "_num", "boolean": "_bool", "not": "_not",
    "abs": "_abs", "floor": "_floor", "ceil": "_ceil", "round": "_round",
    "length": "_length", "count": "_count", "replace": "_replace",
}
ROW_TEMPLATES = {
    "substring":       lambda a, *r: f"_slice({a}, {r[0]}, {r[1] if len(r) > 1 else 'None'})",
    "substringBefore": lambda a, b: f"_before({a}, {b})",
    "substringAfter":  lambda a, b: f"_after({a}, {b})",
    "split":           lambda a, b: f"_split({a}, {b})",
    "join":            lambda a, b: f"_join({a}, {b})",
    "contains":        lambda a, b: f"_contains({a}, {b})",
}
# 这些函数**不能**逐行编译, 必须走批量阶段 (每个都需要整批数据或非确定值)
BATCH_ONLY = {
    "sum", "max", "min", "average", "sort", "reverse", "distinct", "shuffle",
    "reduce", "each", "sift", "keys", "values", "lookup", "merge", "spread",
    "now", "millis", "random", "eval", "map", "filter", "single", "position",
    "exists", "error", "assert", "type", "formatNumber", "formatBase",
    "formatInteger", "parseInteger", "toMillis", "fromMillis",
}

BIN_HELPER = {"&": "_concat", "+": "_add", "-": "_sub", "*": "_mul", "/": "_div",
              "%": "_mod", "=": "_eq", "!=": "_ne", "<": "_lt", "<=": "_le",
              ">": "_gt", ">=": "_ge", "and": "_and", "or": "_or", "in": "_in"}

# ---- 内联守卫 (inline=True) 使用的展开模板 ----
# 思路: 把 "先查 undefined 再运算" 直接写进产物, 省掉 helper 的函数调用。
# 用 walrus 绑定, 保证带副作用的子表达式也只求值一次; 常量/裸字段名直接复制不绑定。
INLINE_CMP = {"=": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
INLINE_NEEDS_NULL = {"+", "-", "*", "/", "%"}          # 这些运算符 null 会抛 TypeError

def compile_py(node, ctxname=None, inline=False):
    """把自有 AST 编译成 Python 表达式源码。
    ctxname=None  -> 字段用**裸名** (推荐: 交给 DCG tranfIFS 重写成 createIdx)
    ctxname="_ctx" -> 字段用 _ctx.get(name, _U) 查表 (独立使用本模块时更方便)
    inline=True   -> 不调用 helper, 把 undefined 守卫展开到产物里 (更热但更长)。
                     实测收益见 bench_overhead.py: 只在超热路径才值得开。"""
    _n = [0]

    def _is_trivial(n):
        """能否安全地把源码复制多份 (无副作用, 不会重复求值)"""
        if n[0] == "lit":  return True
        if n[0] == "ref":  return not n[1].startswith("$")   # $var 查表也算便宜
        return False

    def _bind(src_expr, n):
        """需要复用时用 walrus 绑定一次, 返回 (绑定表达式, 引用名)"""
        if _is_trivial(n):
            return None, src_expr
        _n[0] += 1
        name = f"_t{_n[0]}"
        return f"(({name} := {src_expr}) is not _U)", name

    def _guarded(args_src, args_ast, body):
        """生成: 若任一参数为 _U 则返回 _U, 否则求 body"""
        binds = []
        refs = []
        for src, ast_node in zip(args_src, args_ast):
            b, r = _bind(src, ast_node)
            if b: binds.append(b)
            refs.append(r)
        if not binds:
            return f"({body} if all(_x is not _U for _x in ({', '.join(refs)},)) else _U)"
        return f"(({') and ('.join(binds)}) and ({body}) or _U)"

    def emit_inline(n):
        tag = n[0]
        if tag == "bin":
            op = n[1]
            ls, rs = emit(n[2]), emit(n[3])
            lsl = _is_trivial(n[2]); rsl = _is_trivial(n[3])
            if lsl and rsl:                      # 两侧可复制: 直接比较, 产物最短
                if op in INLINE_CMP:
                    return f"({ls} {INLINE_CMP[op]} {rs} if ({ls} is not _U and {rs} is not _U) else False)"
                if op == "&":
                    return f"(_jstr({ls}) + _jstr({rs}) if ({ls} is not _U and {rs} is not _U) else _jstr({ls} if {ls} is not _U else '') + _jstr({rs} if {rs} is not _U else ''))"
                if op in INLINE_NEEDS_NULL:
                    pyop = INLINE_CMP.get(op, op)
                    return f"({ls} {pyop} {rs} if ({ls} is not _U and {rs} is not _U) else _U)"
                if op == "and":
                    return f"(bool({ls}) and bool({rs}) if ({ls} is not _U and {rs} is not _U) else False)"
                if op == "or":
                    return f"bool(({ls} if {ls} is not _U else False) or ({rs} if {rs} is not _U else False))"
                if op == "in":
                    return f"({ls} in {rs} if ({ls} is not _U and {rs} is not _U) else False)"
            return f"{BIN_HELPER[op]}({ls}, {rs})"       # 复杂子表达式: 退回 helper 更划算
        if tag == "call":
            raw, name = n[1], n[1].lstrip("$")
            if raw in ("$", "$$"):
                raise Unsupported("$() 动态求值无法逐行编译")
            if name not in ROW_FUNCS and name not in ROW_TEMPLATES:
                why = "需整批数据求值" if name in BATCH_ONLY else "未知/高阶函数"
                raise Unsupported(f"${name} 不可逐行编译 ({why})")
            args_src = [emit(a) for a in n[2]]
            single = {"_upper": "_upper", "_lower": "_lower", "_trim": "_trim",
                      "_str": "_str", "_num": "_num", "_bool": "_bool", "_not": "_not",
                      "_abs": "_abs", "_floor": "_floor", "_ceil": "_ceil",
                      "_round": "_round", "_length": "_length", "_count": "_count"}
            if name in ROW_FUNCS and ROW_FUNCS[name] in single and len(n[2]) == 1:
                return _guarded(args_src, n[2], f"{ROW_FUNCS[name]}({args_src[0]})")
            return f"{ROW_FUNCS[name] if name in ROW_FUNCS else '_tpl'}({', '.join(args_src)})" \
                if name in ROW_FUNCS else ROW_TEMPLATES[name](*args_src)
        return emit(n)

    def emit(n):
        tag = n[0]
        if tag == "lit":
            if isinstance(n[1], re.Pattern):
                raise Unsupported("正则字面量暂不编译")
            return repr(n[1])
        if tag == "ref":
            name = n[1]
            if name.startswith("$"):
                if ctxname is None:
                    raise Unsupported(f"{name} 需要 ctx 绑定 (请用 ctxname='_ctx')")
                return f"{ctxname}[{name!r}]"
            if ctxname is None:
                return name                                   # 裸字段名, 等 DCG 重写
            return f"({ctxname}.get({name!r}, {UNDEF_NAME}))"
        if tag == "neg":  return f"(-{emit(n[1])})"
        if tag == "arr":  return f"_arr({', '.join(emit(x) for x in n[1])})"
        if tag == "obj":
            return "_obj([" + ", ".join(f"({emit(k)}, {emit(v)})" for k, v in n[1]) + "])"
        if tag == "bin":
            op = n[1]
            return f"{BIN_HELPER[op]}({emit(n[2])}, {emit(n[3])})"
        if tag == "call":
            raw, name = n[1], n[1].lstrip("$")
            if raw in ("$", "$$"):
                raise Unsupported("$() 动态求值无法逐行编译")
            # 先判批量函数, 再递归实参 —— 否则 $map(xs, $string) 会先因实参
            # 需要 ctx 绑定而报错, 掩盖了 "它本来就属于批量阶段" 这个真正原因。
            if name not in ROW_FUNCS and name not in ROW_TEMPLATES:
                why = "需整批数据求值" if name in BATCH_ONLY else "未知/高阶函数"
                raise Unsupported(f"${name} 不可逐行编译 ({why})")
            args = [emit(a) for a in n[2]]
            if name in ROW_FUNCS:
                return f"{ROW_FUNCS[name]}({', '.join(args)})"
            return ROW_TEMPLATES[name](*args)
        if tag == "idx":   return f"_index({emit(n[1])}, {emit(n[2])})"
        if tag == "map":   return f"_map({emit(n[1])}, {emit(n[2])})"
        if tag == "range": raise Unsupported("区间 (a..b) 需要序列语义, 不能逐行编译")
        raise Unsupported(f"未支持的节点: {tag}")
    return emit_inline(node) if inline else emit(node)


# ============================================================================
# 5. 运行期: 编译 + 执行一次 (生产环境会把 code object 缓存复用)
# ============================================================================
def build(expr, ctxname="_ctx"):
    return compile(compile_py(P(lex(expr)).parse(), ctxname), "<jsonata>", "eval")

def run(expr, row):
    ns = _helpers(); ns["_ctx"] = row
    return eval(build(expr), ns)


# ============================================================================
# 6. 与官方实现逐例对照
# ============================================================================
CORE = [
    ("a + b", {"a": 3, "b": 4}), ("a * b - 1", {"a": 3, "b": 4}),
    ("a > b and b > 0", {"a": 3, "b": 4}), ("a > b or b = 4", {"a": 3, "b": 4}),
    ('$uppercase(s) & "!"', {"s": "hu"}), ("$substring(s, 0, 2)", {"s": "hello"}),
    ("$length(s)", {"s": "hello"}), ("a = 0", {"a": 0}),
    ("miss", {"a": 1}), ("miss + 1", {"a": 1}),
    ('$string(a) & "-"', {"a": 7}), ("$abs(a - b)", {"a": 3, "b": 4}),
    ("$count(xs)", {"xs": [1, 2, 3]}),
    ('{"k": a + 1, "m": b}', {"a": 1, "b": 2}),
    ("[a, b, a + b]", {"a": 1, "b": 2}), ("a != null", {"a": 1}),
    ("$length(miss)", {"a": 1}), ("$floor(a / b)", {"a": 7, "b": 2}),
    ("$split(s, ',')", {"s": "a,b,c"}), ("$join(xs, '-')", {"xs": ["a", "b"]}),
    ("$contains(s, 'ell')", {"s": "hello"}),
    ("$substringBefore(s, 'l')", {"s": "hello"}),
    ("$replace(s, 'l', 'L')", {"s": "hello"}),
    ("$trim(s)", {"s": "  hi  "}), ("a", {"a": None}), ("s & a", {"s": "x", "a": 1}),
]
EDGES = [
    ("miss & 'x'", {"a": 1}), ("'x' & miss", {"a": 1}), ("miss & miss", {"a": 1}),
    ("a & miss", {"a": "x"}), ("miss and true", {"a": 1}), ("true and miss", {"a": 1}),
    ("miss or true", {"a": 1}), ("false or miss", {"a": 1}),
    ("$not(miss)", {"a": 1}), ("$not(a)", {"a": 0}), ("$not(a)", {"a": ""}),
    ("miss = miss", {"a": 1}), ("miss != miss", {"a": 1}),
    ("null & 'x'", {"a": 1}), ("a = null", {"a": None}), ("miss = null", {"a": 1}),
    ("miss < 5", {"a": 1}), ("[miss, a]", {"a": 1}), ("{'k': miss}", {"a": 1}),
    ("'..' & miss", {"a": 1}), ("a[0]", {"a": 1}), ("miss[0]", {"a": 1}),
    # 这两条官方实现抛异常, 本实现也必须抛 (行为一致, 不是语义分歧)
    ("null + 1", {"a": 1}), ("a + null", {"a": 1}),
]
REFUSED = ["$sum(xs)", "$now()", "$map(xs, $string)"]

def compare(cases, title):
    from jsonata import Jsonata
    print(f"\n=== {title} ===")
    same = diff = 0
    for expr, ctx in cases:
        try:    ref = Jsonata(expr).evaluate(ctx)
        except Exception as e: ref = ("ERR", e.__class__.__name__)
        try:    got = run(expr, ctx)
        except Exception as e: got = ("ERR", e.__class__.__name__)
        # 双方都报错即视为行为一致 (异常类型不必逐字相同)
        ok = (isinstance(ref, tuple) and isinstance(got, tuple)) or \
             (got == ref) or (got is UNDEF and ref is None)
        same += ok; diff += (not ok)
        if not ok:
            print(f"  ❌ {expr:28s} ref={ref!r:22s} got={got!r}")
    print(f"  一致 {same} / 不一致 {diff}")
    return diff

def main():
    total_diff = compare(CORE, "核心表达式 (26 例)")
    total_diff += compare(EDGES, "边界/undefined 语义 (24 例)")

    print("\n=== 必须显式拒绝的结构 (证明边界不静默降级) ===")
    for expr in REFUSED:
        try:
            compile_py(P(lex(expr)).parse())
            print(f"  ❌ {expr:22s} 竟然编译通过了 —— 危险! 应报错")
            total_diff += 1
        except Unsupported as e:
            print(f"  ✅ {expr:22s} -> Unsupported: {e}")

    print("\n=== 编译产物规模 (helper 化后保持线性) ===")
    for expr in ['$uppercase(s) & "-" & $string(a)', "a + b * 2",
                 '{"id": a, "sum": a + b}']:
        src = compile_py(P(lex(expr)).parse())
        print(f"  {expr:34s} ({len(src):3d} 字符)\n      => {src}")

    # ---- 速度: 编译后 vs 官方解释器 ----
    from jsonata import Jsonata
    rows = [{"a": i, "b": i * 2, "s": " abc "} for i in range(3000)]
    print("\n=== 逐行吞吐 (已编译/已解析复用) ===")
    for expr in ["a + b", '{"x": a + b, "u": $uppercase(s), "n": $length(s)}']:
        prog = Jsonata(expr)
        try:
            t0 = time.perf_counter()
            for r in rows: prog.evaluate(r)
            t_jx = (time.perf_counter() - t0) / len(rows)
        except Exception as e:
            t_jx = None
        code = build(expr, None)
        ns = _helpers()
        t0 = time.perf_counter()
        for r in rows:
            ns["a"] = r["a"]; ns["b"] = r["b"]; ns["s"] = r["s"]
            eval(code, ns)
        t_py = (time.perf_counter() - t0) / len(rows)
        sp = f"{t_jx/t_py:5.1f}x" if t_jx else "  n/a"
        jxs = f"{t_jx*1e6:7.1f}us" if t_jx else "  <报错>"
        print(f"  {expr:52s} 解释器 {jxs}  编译后 {t_py*1e6:6.3f}us  快 {sp}")

    print(f"\n{'='*66}")
    print(f"总不一致: {total_diff}  " + ("✅ 全部对齐" if total_diff == 0 else "❌ 存在分歧"))

if __name__ == "__main__":
    main()
