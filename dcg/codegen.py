# -*- coding: utf-8 -*-
"""DCG v2 代码生成（DCG 接口实现细节文档.md §3.6 / §3.7 / §4.1 / §4.2 / §4.3 / §4.4）。

三个纯函数（同输入同输出，不读全局状态）：

* :func:`gen_name`  —— 规范 schema -> 产物模块名（= `schema["name"]`；确定性由 sha1 保证）
* :func:`generate`  —— 规范 schema -> 完整模块源码（纯 Python / Cython）
* :func:`build`     —— 落盘 `gen/<name>.py` + manifest，sha1 校验防同名静默陈旧化

产物形态由 §4.2「零冗余」与 §4.3「只 compile 一次」钉死：

1. 行下标链的每个前缀在行首**只取一次**（`R[5]['a']` -> `_a=R[5]['a']`；两条链共用
   前缀时前缀必被提升，见 :meth:`_Gen.plan` 的前缀 trie 判定）；
2. 无恒等赋值、无为 return 而生的中转（只被「行」用到的字段直接内联进 `_o+=[...]`，
   只有被别的字段按裸名引用、或需要 undefined 守卫的字段才落成局部名）；
3. 无 isinstance 分派链（产物**不内联** helper，直接从 :mod:`dcg.runtime` 取，
   运行期只有一次模块级 import，见下）；
4. `type` 是**声明不是转换** —— 只在 `expr` 里显式写的转换才出现；
5. 输出形状编译期定型（list / 列式 dict / int 键 dict），运行期零形状判断；
6. 只 `compile()` 一次，产物里没有 `= None` 空桩。

**helper 出处（口径，Lead 裁决 2026-10-17 允许的方案 ii）**：产物写
``from dcg.runtime import*``，helper 体**不**内联。理由是三条互相咬合的硬约束：

* 产物可读性 —— 内联 `_S`/`_ad`/`_num` 光前导就要 300+ 字符，产物会被撑成
  一坨没人愿意读的算术墙；
* §4.2 规则 7（只 optional 字段有 undefined 守卫）—— 内联的算术 helper 自带
  `is _U` 预检，会污染守卫计数；`tests/codegen_rules.py` 现在按**字段级**判定，
  但产物一旦内联 helper 就再次变成"每行都有 `is _U`"；
* §4.2 规则 6 / `type` 裁决（产物不得出现凭空 `int(`/`float(`）—— runtime 的
  `$number`/`$floor` 等实现必然要用 `int()`/`float()`，内联即违规。

`dcg.runtime` 是 undefined 语义的**唯一实现处**（§4.4），产物引用它，语义天然一致；
代价是产物不再是"零依赖"单文件（Lead 已明确允许，并要求在此说明）。

动态下标走 §3.6：`("index", base, idx, loc)`；idx 是字面量走常量链，idx 是裸名
（如 `$.a[b]`）表示**行内元素**，翻译成 `_ix_get(base, R['b'])`，绝不走 `_ix`(= $indexOf)。
反引号段的链解析**只用** `dcg.jx.compiler.split_chain`（§3.7），与 JSONata 路径共用实现。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re

from . import ir as _ir
from .jx import FIELD_REF, Unsupported, analyze_py_segment, compile_expr, split_chain
from .jx.compiler import PY_SAFE_MODULES, _PY_ROW_PLACEHOLDER
from .runtime import SOURCE
from .schema import SchemaError, validate

__all__ = ["generate", "build", "gen_name", "canonical_json", "schema_sha1"]

#: 产物取 helper 的唯一方式（见模块 docstring 的 helper 出处说明）。
#: `import *` 前留一个空格：`from dcg.runtime import*` 在有些静态检查里
#: 与 `import _U` 形状不匹配（tests/test_sentinel_integrity.py 认带空格的形式）。
_RUNTIME_IMPORT = "from dcg.runtime import *"

#: 内联 helper 的传递依赖。产物已不内联 helper，这张表仍然保留：
#: :func:`test_codegen_snapshot` 用它校验 runtime.SOURCE 的自洽性。
_DEPS = {
    "_ad": ("_num",), "_sb": ("_num",), "_ml": ("_num",), "_dv": ("_num",),
    "_md": ("_num",), "_pg": ("_num",), "_neg": ("_num",), "_pos": ("_num",),
    "_ab": ("_num",), "_ce": ("_num",), "_fl": ("_num",), "_ro": ("_num",),
    "_sq": ("_num",), "_pw": ("_num",),
    "_lt": ("_cmp_ok",), "_le": ("_cmp_ok",), "_gt": ("_cmp_ok",),
    "_ge": ("_cmp_ok",), "_cmp_ok": ("_num",),
    "_eq": ("_num",), "_ne": ("_eq",), "_in": ("_eq",), "_cn": ("_eq",),
    "_nt": ("_tru",), "_or": ("_tru",), "_and": ("_tru",),
    "_ix": ("_S",), "_jn": ("_S",),
}

#: jx 函数 -> runtime helper（DCG 接口实现细节文档 §3.4 / §3.7）
_INLINE_KIND = {
    "$length": "num", "$count": "num", "$sum": "num", "$max": "num",
    "$min": "num", "$average": "num", "$abs": "num",
    "$not": "bool", "$boolean": "bool", "$contains": "bool",
    "$substring": "str", "$split": "str", "$substringBefore": "str",
    "$substringAfter": "str", "$replace": "str", "$pad": "str",
    "$floor": "num", "$ceil": "num", "$round": "num", "$sqrt": "num",
    "$power": "num",
}

_CMP_OP = {"=": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_ORDER_OPS = frozenset(("<", "<=", ">", ">="))

_FN = {
    "$string": "_S", "$uppercase": "_up", "$lowercase": "_lo", "$trim": "_tm",
    "$length": "_ln", "$count": "_ct", "$split": "_sp", "$join": "_jn",
    "$substring": "_ss", "$contains": "_cn", "$number": "_N", "$boolean": "_B",
    "$abs": "_ab", "$floor": "_fl", "$ceil": "_ce", "$round": "_ro",
    "$sqrt": "_sq", "$power": "_pw", "$not": "_nt", "$exists": "_ex",
    "$type": "_ty",
    # A 类补充：字符串
    "$pad": "_pd", "$substringBefore": "_sbb", "$substringAfter": "_sba",
    "$replace": "_rp", "$base64encode": "_b64e", "$base64decode": "_b64d",
    "$encodeUrlComponent": "_ue", "$decodeUrlComponent": "_ud",
    "$formatNumber": "_fnm", "$formatBase": "_fb",
    # A 类补充：数值 / 数组与对象
    "$sum": "_sm", "$min": "_mn", "$max": "_mx", "$average": "_av",
    "$append": "_ap", "$reverse": "_rv", "$sort": "_so", "$distinct": "_ds",
    "$merge": "_mg", "$keys": "_ky", "$values": "_vl", "$lookup": "_lk",
    "$spread": "_sr", "$single": "_sg", "$indexOf": "_ix",
    # C 类非确定（结果不可复现；helper 从 dcg.runtime.CLOCK / RANDOM 取值）
    "$now": "_nw", "$millis": "_ms", "$random": "_rn", "$shuffle": "_sh",
}

#: 二元运算符 -> runtime helper
_OPS = {
    "+": "_ad", "-": "_sb", "*": "_ml", "/": "_dv", "%": "_md",
    "&": "_cc", "=": "_eq", "!=": "_ne", "<": "_lt", "<=": "_le",
    ">": "_gt", ">=": "_ge", "and": "_and", "or": "_or", "in": "_in",
}

#: 高阶函数 -> (runtime helper, 回调参数个数)（DCG 接口实现细节文档 §3.5「B 类」/ §3.6）
_HIGHER = {
    "$map": ("_mp", 2), "$filter": ("_fi", 2), "$reduce": ("_rd", 2),
    "$each": ("_ea", 2), "$sift": ("_sf", 2), "$sort": ("_so", 2),
}

#: 字符串函数在实参类型 build 期可证为 str 时，直接发内建方法调用（裸代码）。
_STR_METHODS = {"$uppercase": "upper", "$lowercase": "lower", "$trim": "strip"}

#: 产物的模块级/局部名（schema 字段名撞上即拒绝，绝不生成静默遮蔽）。
#: 入参叫 `_d` 而不是 `_in`：`_in` 是 runtime 的 `in` 运算符 helper，同名的参数
#: 会把产物里的 `_in(...)` 调用静默遮蔽成「调用列表」。
_RESERVED = frozenset(
    {"R", "_d", "_o", "_U", "_x", "_xr", "run", "is_u", "<N>"}
    | set(_FN.values()) | set(_OPS.values()) | set(SOURCE)
)

_TOKEN = "\x01{}\x02"
_TOKEN_RE = re.compile("\x01(\\d+)\x02")

#: 声明为数值类型的字段才允许裸算术（`type` 是声明不是转换，§4.2 规则 ④）。
_NUM_TYPES = frozenset({"int64", "int32", "float64"})

#: 从**运算上下文**反向推导类型时，哪些函数只接受数值实参 / 只接受字符串实参
#: （m03039）。只列「实参类型错了就是错误、没有别的定义」的函数 —— 这些才能
#: 反推出实参的类型域；`$string`/`$number` 这类显式转型函数**不能**（它们接受任意
#: 类型并按规则转型），所以不在这里。
_NUM_ARG_FUNCS = frozenset({
    "$sum", "$max", "$min", "$abs", "$floor", "$ceil", "$round", "$sqrt",
    "$power", "$average",
})
_STR_ARG_FUNCS = frozenset({
    "$uppercase", "$lowercase", "$trim", "$length",
})

#: 运算上下文类型推导总开关。**默认关闭**，因为推导会改变异常语义：
#: `type: "any"` 字段的值域包含 `null` 与缺失键，反推成数值后发裸运算，
#: 本该 `TypeError("$add: 操作数必须是数值")` 的变成 Python 原生
#: `unsupported operand type(s) for +`；本该 undefined 传播的变成 `KeyError`。
#: 要启用必须先把「异常类型与文案」也纳入等价性论证（见 `_infer_types`）。
_INFER_ENABLED = False


#: 产物第一行 `from dcg.runtime import *` 会带进来的名字 —— 一个都没用到就不发这一行
#: （CAP/M0 走裸运算后零 helper，产物更短；字符预算已取消，这里只为可读性）。
_IMPORT_NAMES = set(SOURCE) | {"_U", "UNDEF", "is_u"}

#: 值表达式就是一个局部名时，内联它不会重复求值 —— 不落中转局部（§4.2 规则 ②）
_TRIVIAL = re.compile(r"^[A-Za-z_]\w*$")


def _unquoted_parts(text):
    """`text` 里字符串字面量**之外**的片段（模块名检索只看这些）。"""
    return [p for i, p in enumerate(_QUOTED.split(text)) if i % 2 == 0]


def _safe_modules_used(text):
    """产物里用到的 `PY_SAFE_MODULES` 模块名（反引号段可写 `json.loads($)` 之类）。

    只认「裸名 + 点号」形态（字符串字面量里的不算），按名字排序保证产物确定性。
    """
    found = set()
    for part in _unquoted_parts(text):
        for mod in PY_SAFE_MODULES:
            if re.search(r"(?<![\w.])%s\s*\." % re.escape(mod), part):
                found.add(mod)
    return sorted(found)


_QUOTED = re.compile(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")


def _sub_name(text: str, name: str, repl: str) -> str:
    """把裸名 `name` 换成 `repl`，**跳过字符串字面量内部**（列名 key 不能被改写）。"""
    pat = re.compile(r"(?<![\w.])%s(?![\w])" % re.escape(name))
    parts = _QUOTED.split(text)
    return "".join(p if i % 2 else pat.sub(repl, p) for i, p in enumerate(parts))


def _pyblock_stmts_use_dollar(stmts: str) -> bool:
    """块体里是否出现了**裸** ``$``（即「当前行 / 整份数据本身」）。

    反引号段里的裸 ``$`` 也算（段内 `$` 与裸 `$` 同义）。`$.链` 是字段引用，
    不算裸 ``$``。判据必须精确：数据级块会因此**多接一个形参**，多接一次就是
    产物里的冗余参数，也会让 `assert "def _py0(_p0):"` 这类结构性断言漂移。
    """
    # 先把反引号段的内容也取出来参与判断（段内可能有 `$`），再把 `$.链` 整体剥掉。
    body = []
    i = 0
    n = len(stmts)
    while i < n:
        c = stmts[i]
        if c == "\\" and i + 1 < n and stmts[i + 1] == "`":
            body.append("`")                 # `\`` 是字面反引号，不是段分隔
            i += 2
            continue
        if c == "`":
            end = stmts.find("`", i + 1)
            while end > 0 and stmts[end - 1] == "\\":
                end = stmts.find("`", end + 1)
            if end < 0:
                body.append(stmts[i + 1:])
                break
            body.append(stmts[i + 1:end])    # 段内容参与判断
            i = end + 1
            continue
        body.append(c)
        i += 1
    text = "".join(body)
    text = FIELD_REF.sub("", text)           # `$.a.b` 是字段引用，不是裸 `$`
    text = text.replace(_PY_ROW_PLACEHOLDER, "")   # jx 掩码阶段的占位符
    return _BARE_DOLLAR_RE.search(text) is not None


#: 独立的 `$`（前后都不是标识符字符 / 不是另一个 `$`）。
_BARE_DOLLAR_RE = re.compile(r"\$(?![A-Za-z_0-9$])")


def _mask_backticks_for_parse(text: str) -> str:
    """把反引号段换成标识符占位符，让块体足以通过 `ast.parse(mode="exec")`。

    反引号段不是合法 Python（`py{ return [r for r in `$`] }`），但它有自己的
    编译器（§3.3），块级校验不该看进去。占位符用 `__jx` 前缀，名字校验直接放行。
    """
    out = []
    seen = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] == "`":
            out.append("`")
            i += 2
            continue
        if c == "`":
            end = text.find("`", i + 1)
            while end > 0 and text[end - 1] == "\\":
                end = text.find("`", end + 1)
            if end < 0:                      # 未闭合：原样留给 ast 报错
                out.append(text[i:])
                break
            out.append("__jxbt%d" % seen)
            seen += 1
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)

#: Cython 模式下 `type` -> cdef 的映射；一律用 object 保证**不改变语义**
_CDEF = {"int64": "long", "int32": "int", "float64": "double", "str": "object",
         "bool": "bint", "list": "list", "object": "object", "any": "object"}


# ---------------------------------------------------------------- 命名 / 哈希
def canonical_json(schema: dict) -> str:
    """规范序列化：键排序 + 紧凑分隔符 —— `schema_sha1` 的唯一口径。"""
    return json.dumps(schema, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def schema_sha1(schema: dict) -> str:
    return hashlib.sha1(canonical_json(schema).encode("utf-8")).hexdigest()


def gen_name(schema: dict) -> str:
    """产物模块名 = `schema["name"]`（D3：人类可读、可 diff、确定性）。

    确定性由 :func:`schema_sha1` 保证：manifest 记 sha1，同名不同 schema 时
    :func:`build` 拒绝覆盖，而不是静默陈旧化。
    """
    if not isinstance(schema, dict) or not isinstance(schema.get("name"), str):
        raise SchemaError("name", "gen_name() 需要含字符串 name 的 schema")
    return schema["name"]


# ---------------------------------------------------------------- IR -> 链
def _chain(node):
    """`("index", base, ("lit", k), loc)` 链 -> `(k0, k1, ...)`；非常量返回 None。"""
    segs: list = []
    cur = node
    while True:
        k = cur[0]
        if k == _ir.ROOT:
            return tuple(reversed(segs))
        if k != _ir.INDEX:
            return None
        idx = cur[2]
        if idx[0] != _ir.LIT or not isinstance(idx[1], (str, int)) \
                or isinstance(idx[1], bool):
            return None
        segs.append(idx[1])
        cur = cur[1]


def _index_const_chain(node):
    """常量下标链 -> `(k0, k1, ...)`；非常量返回 None。

    比 `_chain` 多认得一种链：**基座是裸字段引用**（`("field", "a", loc)` /
    `("var", "a", loc)`），例如 `a[0]`（裸字段名 + 常量下标，见契约 §5.2 字段引用表）。
    这种链不是行路径，所以返回 `None` 由 `emit` 的「基座 + 下标」分支处理。
    """
    return _chain(node)


def _is_const(spec: dict) -> bool:
    """常量字段（`const` 或引用了纯常量表达式）：逐行同值。"""
    return "const" in spec


def _has_free_data_ref(node) -> bool:
    """节点里是否出现**读当前行/整份数据**的引用（`FIELD`/`ROOT`/`INDEX`）。

    `_higher_inline` 用它判断「能不能把回调展成推导式」：一旦函数体读了外层
    行（`R`），展成 `[.. for v in arr]` 之后 `R` 的作用域就变了 —— 那种情况
    必须保持 `lambda` 形态，让助手在正确的行语境里调用它。
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, tuple) or not n:
            continue
        k = n[0]
        if k in (_ir.FIELD, _ir.ROOT, _ir.INDEX):
            return True
        if k == _ir.LAMBDA:
            # 内层 lambda 的形参会遮蔽外层，但它的**函数体**仍可能读数据 ——
            # 保守起见直接拒掉（能内联的场景都是单层回调）。
            return True
        stack.extend(c for c in n[1:] if isinstance(c, tuple))
    return False


def _mentions_position(node, params) -> bool:
    """函数体里是否用到了「回调第二形参」（索引位）之外的位置信息。

    内联成推导式后，循环变量就是形参本身，但**索引**没有等价物：
    `$map($.a, function($v,$i){$v+$i})` 展成 `[v+i for v in arr]` 里 `i` 未定义。
    所以只要函数体引用了第二形参，就放弃内联。
    """
    if len(params) < 2:
        # 即使没有第二形参，`$position()` 也可能出现在函数体里 —— 内联成推导式后
        # 没有「当前下标」这个东西（`enumerate` 会额外套一层，不如保持 lambda）。
        return _mentions_position_call(node)
    idx = params[1]
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, tuple) or not n:
            continue
        if n[0] == _ir.VAR and n[1] == idx:
            return True
        if n[0] == _ir.CALL and n[1] == "$position":
            return True
        stack.extend(c for c in n[1:] if isinstance(c, tuple))
        if n[0] == _ir.LAMBDA:
            # 内层 lambda 的形参会遮蔽 —— 但那种写法本身就放弃了内联（见上）。
            return True
    return False


def _mentions_position_call(node) -> bool:
    """函数体里是否出现 `$position()`（内联成推导式后没有对应的下标来源）。"""
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, tuple) or not n:
            continue
        if n[0] == _ir.CALL and n[1] == "$position":
            return True
        if n[0] == _ir.LAMBDA:
            return True
        stack.extend(c for c in n[1:] if isinstance(c, tuple))
    return False


def _bool_valued(node) -> bool:
    """节点是否**只可能**产出 `True` / `False` / `undefined`（布尔上下文里 `_tru` 恒等）。

    `_tru(x)` 对 `True`/`False` 原样返回、对 `undefined` 返回 `False` —— 所以只要
    `x` 的值域被限制在这三者之内（比较、`in`、`and`/`or`/`not`、`$exists`、
    `$boolean` 链），`if _tru(x)` 与 `if x` 的**布尔结果完全相同**，可以直接省掉
    这层调用（实测 1.43x）。

    刻意保守：只认白名单里的节点种类，其余一律返回 False 走 `_tru` —— `$count(x)`、
    `'a'`、`1` 这类值域含真值/假值的表达式绝不能省 `_tru`（`if 'a'` 与
    `if _tru('a')` 等价，但 `if 0` 与 `if _tru(0)` 也等价，问题在于 `$count([])`
    返回 0 而 `if 0` 为假、`if _tru(0)` 也为假 —— 等价，但为进一步的语义变更留余地）。
    """
    if not isinstance(node, tuple) or not node:
        return False
    k = node[0]
    if k == _ir.BIN:
        op = node[1]
        if op in _BOOL_BIN_OPS:
            return True
        # `and`/`or` 的 JSONata 语义返回布尔（见 runtime `_and`/`_or`）
        return op in ("and", "or")
    if k == _ir.UNARY:
        return node[1] == "not"
    if k == _ir.CALL:
        return node[1] in _BOOL_FUNCS
    if k == _ir.COND:
        # 三元两端都是布尔值 -> 整体是布尔值
        return _bool_valued(node[2]) and _bool_valued(node[3])
    return False


#: 结果恒为布尔的二元运算符（`=`/`!=`/`in` 返回 False 而非 undefined，仍属布尔域）。
_BOOL_BIN_OPS = frozenset({"<", "<=", ">", ">=", "=", "!=", "in"})

#: 结果恒为布尔（或 undefined）的函数。
_BOOL_FUNCS = frozenset({"$exists", "$boolean", "$contains"})


def _func_kind() -> str:
    """`output.filter` / `output.post` 里「外部函数」形态的 kind 标记。

    与 `("func", "pkg.mod.fn")` 元组的第一项对齐（见 `schema._norm_hook`）。
    """
    return "func"


def _cycle_hint(deps: dict, names) -> str:
    """在必含环的子集里找出一条环路径，用于 SchemaError 文案（确定性）。"""
    subset = set(names)

    def dfs(n, stack):
        if n in stack:
            i = stack.index(n)
            return " -> ".join(stack[i:] + [n])
        for d in sorted(deps.get(n, ())):
            if d in subset:
                r = dfs(d, stack + [n])
                if r:
                    return r
        return ""

    for n in names:
        r = dfs(n, [])
        if r:
            return r
    return " -> ".join(names)


# ---------------------------------------------------------------- 生成器
class _Gen:
    """一次生成的编译状态（每次 :func:`generate` 新建，绝不跨调用复用）。"""

    def __init__(self, schema: dict, cython: bool, inline_optional: bool,
                 trace: bool | None = None):
        self.cython = bool(cython)
        self.inline_optional = bool(inline_optional)
        self.fields: dict = schema["out"]["fields"]
        self.shape: str = schema["output"]["shape"]
        # `before` / `after` 都是**整体数据钩子**（§4.1）：
        # * `before` 收到经 `input.source` 定位后的全量数据，返回值替换它当新输入；
        # * `after` 收到按 shape 组装完成的全量结果，返回值作为最终返回值。
        # 规范化后是 `("expr", 文本)` / `("func", "pkg.mod.fn")` / None。
        self.before = schema["output"].get("before")
        self.after = schema["output"].get("after")
        # trace 只由显式参数决定：正式产物不得包含 TRACE；`trace=true` 时
        # build() 另外生成独立的 `.trace.py` 调试产物（DCG 接口实现细节文档 §4.1）。
        self.trace: bool = bool(trace)
        # 行集合在输入数据里的路径（规范化为段元组）；() 表示整份输入就是行集合。
        self.src: tuple = schema["input"]["source"]
        self.rfuncs: list = []
        self.uses: dict = {}          # (key, ...) -> 引用次数
        self.order: list = []         # 引用顺序（确定性）
        self.locals: dict = {}        # 字段名 -> 局部名
        self._params: dict = {}       # 函数字面量的参数名 -> 产物里的同名形参
        self._index_var = None        # 当前函数体里 `$position()` 要用的下标形参名
        self.safe = 0                 # >0 时路径走 _ix_get（optional 字段专用）
        self.raw = None               # None | "num" | "str"：编译期可证安全 -> 裸运算
        # 裸 `$` 在反引号段里落到哪个局部名：行语境是 `R`（当前行），
        # `output.before` / `output.after` 的整体数据语境是 `D`（全量数据）。
        self._data_var = "R"
        self._n = 0
        self._idx: dict = {}
        self._binds: dict = {}
        self._pre: list = []
        self._refs: set = set()
        self._safe_uses: set = set()
        # 走 `get_text`（`.get(k,_U)` 链）的安全路径：与 `_safe_uses` 区分口径
        self._get_uses: set = set()  # 被安全模式（optional/default/any）引用过的路径
        self._inferred: dict = {}  # 由运算上下文反推出来的字段类型（m03039，见 _infer_types）
        self._mixed_uses: set = set()  # 带位置下标的常量链（`$.m[0]`）：基座裸读 + 下标 _ix_get
        self._exprs: dict = {}       # expr 源码文本 -> IR（记忆化：见 _expr_node）
        self.pyblocks: list = []     # `py{}` 多语句块的命名辅助函数（定义在 run() 外）
        self._pykeys: dict = {}      # (块体, free 元组) -> `_pyN`，同块只发一次
        self._pyroot_args: dict = {}  # 同上 key -> 该块是否多接一个「全量数据」形参
        self.cur_field = ""          # 正在取值的字段（SchemaError 指到具体字段）
        self.cur_key = ""            # 该字段正在取值的键：value/const/expr/fields
        self._nested_scopes: list = []  # 嵌套 fields 的作用域栈（由内到外查找，内层遮蔽外层）
        self._nested_specs: list = []   # 与 _nested_scopes 平行的 spec 栈（判裸名绑定的可证性用）
        self._in_dyn_index = False      # 正在发射 `$.a[<下标>]` 的下标表达式（`$.a[$i]` 降级成字段名）
        self._defs: list = []           # 当前 row-loop 的语句行（嵌套字段绑定也追加到这里）

    # ---------------------------------------------------- 名字
    def tmp(self) -> str:
        self._n += 1
        return f"_t{self._n}"

    def token(self, path: tuple) -> str:
        """登记一次路径引用，返回占位符；绑定决策要等所有引用都收齐后再做。"""
        path = tuple(path)
        self.uses[path] = self.uses.get(path, 0) + 1
        if path not in self._idx:
            self._idx[path] = len(self.order)
            self.order.append(path)
        return _TOKEN.format(self._idx[path])

    def safe_text(self, path) -> str:
        """optional 字段的取值链：每一步都用 `_ix_get`（缺键 -> UNDEF，不抛 KeyError）。

        绑定计划里已经有这条路径的**公共前缀**时，从那个局部名起步
        （`_buyer` / `_ix_get(_buyer,'level')`），不再从数据根重复取一遍。
        """
        path = tuple(path)
        par = None
        for L in range(len(path) - 1, 0, -1):
            if path[:L] in self._binds:
                par = path[:L]
                break
        t = self._binds[par] if par is not None else self._data_var
        for k in (path[len(par):] if par is not None else path):
            t = f"_ix_get({t},{k!r})"
        return t

    def _rows_mapping(self) -> bool:
        """行是否**一定是 dict**（决定 `dict.get(k,_U)` 能不能用）。

        判据与 `_bare_holder_ok` 同类：只看能静态证明的情形。
        * 显式 `input.source` 非空（`$.rows` 这类）—— 行来自文档里的子结构，
          形制由用户数据决定，**不保证**是 dict → 返回 False；
        * `input.source` 为空（`$`，整份输入是行集合）—— 行可能是 list/tuple
          （位置式数据），也可能 dict。**只有全部字段都用**名字路径取值、
          且没有任何整数下标时，行才必然是 dict。

        这条判定存在的直接原因：`optwide_missing` 基准里数据是 9 元组，而
        `optional` 列级优化发了 `r.get('zz',_U)` -> `AttributeError: 'tuple'
        object has no attribute 'get'`。行模式 `get_text` 之前也有同样的漏洞。
        """
        if self.src:
            return False
        for spec in self.fields.values():
            if "fields" in spec:
                if not self._rows_mapping_spec(spec["fields"]):
                    return False
                continue
            if not self._rows_mapping_spec({"": spec}):
                return False
        return True

    def _rows_mapping_spec(self, fields) -> bool:
        for spec in fields.values():
            if "fields" in spec and not self._rows_mapping_spec(spec["fields"]):
                return False
            path = spec.get("value")
            if path and not isinstance(path[0], str):
                return False
        return True

    def get_text(self, path) -> str:
        """`optional` **且类型可判定**的字段取值链：逐层 `dict.get(k, _U)`。

        这是 helper 调用之外的第三种取值形态（用户 m02826/m02838 裁决）：

        * 不写 `optional` -> 裸下标（`R['a']['b']`），缺键由用户承担 `KeyError`；
        * 写 `optional` + 类型可判定 -> 本形态：**内建方法调用**、无 helper、
          无 try、无 isinstance 分派，缺键兜底成哨兵（保留 undefined 语义）；
        * 写 `optional` + `any`/未声明类型 -> `_ix_get` 链（`safe_text`）。

        `.get` 是 C 层快速路径，比 `_ix_get` 链快约 2.3 倍（实测 32 ns vs 80 ns
        取 `$.buyer.level`）。代价：中间层存在但不是 dict（`null`/标量/数组）时
        抛 `AttributeError` —— 这是用户明确接受的边界，不额外兜底。

        整数下标段（`$.list.0.a`）没有 `.get` 形态，遇到就整条回退到
        `_ix_get` 链，保持「类型可判定」与「语义安全」两者都不丢。
        """
        path = tuple(path)
        if not all(isinstance(k, str) for k in path):
            # 含整数/符号下标：`.get` 只对映射成立，整条回退到 _ix_get 链。
            self._safe_uses.add(path)
            return self.safe_text(path)
        # 登记这次安全引用并**发 token**（不能就地拼文本）：`body()` 阶段
        # `_binds` 还是空的，就地拼会把 `R.get('buyer',_U)` 写进每一行；
        # 只有等 `plan()` 定好前缀提升后，`text_of` 才知道该从 `_buyer` 起步。
        # `plan()` 的候选前缀会把 `_safe_uses` 一并纳入，所以 `('buyer',)`
        # 这类「被两条 optional 链共用」的前缀才会被提升（见 §B.8 第 1 条）。
        self._safe_uses.add(path)
        self._get_uses.add(path)
        return self.token(path)

    def emit_path(self, path) -> str:
        path = tuple(path)
        if not path:
            return self._data_var
        if self.safe:
            # 安全模式同样要**登记**这次引用：否则 `$.buyer.level`/`$.buyer.note`
            # 这类 optional 子键根本不进 `plan()` 的视野，公共前缀 `('buyer',)`
            # 就不会提升成局部名，每行要重复 `_ix_get(R,'buyer')`。
            # 同时记进 `_safe_uses`：被安全模式引用过的前缀，提升时必须用
            # 逐层 `_ix_get` 求值（裸 `R['k']` 缺键会 KeyError，语义退化）。
            self._safe_uses.add(path)
            return self.token(path)
        if self._data_var != "R":
            # 数据级钩子（`before`/`after`）：没有行循环、没有前缀提升，
            # 直接就地展开成 `D['a']['b']`（task-13）。
            return self._data_var + self.suffix(path)
        return self.token(path)

    # ---------------------------------------------------- 前缀 trie / 绑定计划
    def plan(self) -> None:
        """决定哪些链前缀提升成局部名（§4.2 规则①），并生成提升语句。

        判定（只看**被引用到的**路径集合）：
        * 该前缀有 >=2 个不同后继（分支点）—— 不提升就会把同一个 `R[5]['a']` 写两遍；
        * 该前缀自己被引用 >=2 次 —— 同上；
        * 该前缀被引用且是别的被引用路径的真前缀 —— 它的文本会出现在后继的链里。
        """
        # 候选前缀来自两处：普通链（`self.order`）与安全链（`_safe_uses`）。
        # 后者是 `optional`/`any` 字段走 `safe_text`/`get_text` 时登记的，它们
        # **不进** `self.order`（那两个方法不走 `emit_path`/`token`），如果这里
        # 只扫 `self.order`，`$.buyer.level` 与 `$.buyer.note` 的公共前缀
        # `('buyer',)` 就不会被提升 —— 每行要把 `R.get('buyer',_U)` 拼两遍。
        seen_paths = list(self.order) + [p for p in self._safe_uses
                                         if p not in self.uses]
        prefixes = set()
        for p in seen_paths:
            for L in range(1, len(p) + 1):
                prefixes.add(p[:L])
        children: dict = {}
        for p in seen_paths:
            for L in range(1, len(p)):
                children.setdefault(p[:L], set()).add(p[:L + 1])
        chosen = []
        for p in sorted(prefixes, key=lambda q: (len(q), repr(q))):
            nch = len(children.get(p, ()))
            used = self.uses.get(p, 0)
            if nch >= 2 or (used and (used >= 2 or nch >= 1)):
                chosen.append(p)
        taken: set = set()
        for p in chosen:
            nam = self.bind_name(p)
            while nam in taken or nam in self.fields:
                nam += "_"
            taken.add(nam)
            self._binds[p] = nam
        # 绑定语句一旦要服务某个安全模式引用（或它自己就是安全路径的前缀），
        # 就必须用安全口径求值：裸 `R['k']` 在缺键时抛 KeyError。
        # 口径按**消费端的取值形态**二选一，两端必须一致：
        #   * 消费端是 `get_text`（optional + 类型可判定，`.get(k,_U)` 链）
        #     -> 绑定也用 `.get` 链，否则提升反而把 C 层方法调用换成 helper；
        #   * 其余安全消费端（`safe_text`，`optional` + `any`）-> 逐层 `_ix_get`。
        safe_prefix: set = set()
        get_prefix: set = set()
        for p in self._safe_uses:
            for L in range(1, len(p) + 1):
                safe_prefix.add(p[:L])
        for p in self._get_uses:
            for L in range(1, len(p) + 1):
                get_prefix.add(p[:L])
        rhs = []
        for p in chosen:
            if p in get_prefix:
                rhs.append(f"{self._binds[p]}={self._get_chain_text(p)}")
            elif p in safe_prefix:
                saved, self.safe = self.safe, 1
                try:
                    rhs.append(f"{self._binds[p]}={self.bind_rhs(p)}")
                finally:
                    self.safe = saved
            else:
                rhs.append(f"{self._binds[p]}={self.bind_rhs(p)}")
        self._pre = rhs

    def _get_chain_text(self, path) -> str:
        """`path` 的 `.get(k,_U)` 链文本（从已提升的最近前缀起步）。"""
        par = None
        for L in range(len(path) - 1, 0, -1):
            if path[:L] in self._binds:
                par = path[:L]
                break
        t = self._binds[par] if par is not None else self._data_var
        for k in (path[len(par):] if par is not None else path):
            t = f"{t}.get({k!r},_U)"
        return t

    @staticmethod
    def bind_name(p: tuple) -> str:
        last = p[-1]
        if isinstance(last, str):
            return "_" + last
        prev = None
        for k in reversed(p[:-1]):
            if isinstance(k, str):
                prev = k
                break
        return f"_{prev}{last}" if prev is not None else f"_R{last}"

    @staticmethod
    def suffix(steps) -> str:
        return "".join(f"[{k!r}]" if isinstance(k, str) else f"[{k}]"
                       for k in steps)

    def bind_rhs(self, p: tuple) -> str:
        par = None
        for L in range(len(p) - 1, 0, -1):
            if p[:L] in self._binds:
                par = p[:L]
                break
        base = self._binds[par] if par is not None else self._data_var
        steps = p[len(par):] if par is not None else p
        # 单个字符串下标 -> 用 _ix_get：`x=R['k']` 是 buffer/doc 中转形状（规则③）。
        # 多步且整条路径都是字符串键时，也逐层用 `_ix_get`：安全模式
        # （optional/default/any 字段）里这些路径本来就是 `_ix_get` 链，
        # 提升成局部名不能把「缺键 -> 哨兵」变成「缺键 -> KeyError」。
        if steps and len(steps) == 1 and isinstance(steps[0], str) and not self.safe:
            return f"_ix_get({base},{steps[0]!r})"
        if steps and all(isinstance(k, str) for k in steps) and self.safe:
            t = base
            for k in steps:
                t = f"_ix_get({t},{k!r})"
            return t
        return base + self.suffix(steps)

    def text_of(self, p: tuple) -> str:
        if p in self._binds:
            return self._binds[p]
        # 这条路径是否被安全模式引用过：决定「局部名 + 逐层 _ix_get」还是裸下标。
        # 不能看 self.safe —— subst() 在整个 run() 拼好之后才执行，那时标志已复位。
        safe = p in self._safe_uses
        mixed = p in self._mixed_uses
        # 注：`mixed` 只可能出现在 **required** 场景（safe 场景整条链走
        # `safe_text` 的纯 `_ix_get`）。详见 `emit` 的常量链分支。
        # 安全链又分两种取值形态，从已提升前缀派生时必须保持同一口径：
        # `get_text`（optional + 类型可判定）派生 `.get(k,_U)`，
        # `safe_text`（optional + any）派生 `_ix_get`。
        getf = p in self._get_uses
        for L in range(len(p) - 1, 0, -1):
            if p[:L] in self._binds:
                rest = p[L:]
                if getf:
                    return self._get_chain_text(p)
                if safe and all(isinstance(k, str) for k in rest):
                    t = self._binds[p[:L]]
                    for k in rest:
                        t = f"_ix_get({t},{k!r})"
                    return t
                return self._binds[p[:L]] + self.suffix(rest)
        if safe:
            return self._get_chain_text(p) if getf else self.safe_text(p)
        if mixed:
            return self._mixed_chain_text(p)
        return self._data_var + self.suffix(p)

    def _mixed_chain_text(self, path) -> str:
        """未提升的混合链（`$.m[0]`）：基座裸读 + 整数下标走 `_ix_get`。

        契约 §4.4 附加条目 3 要求 `null[0] == null`（非容器返回自身），而 required
        口径要求缺键抛 `KeyError`。两种口径用「基座裸读、下标安全」同时满足：
        `_ix_get(R['m'], 0)` —— 缺 `m` 抛 KeyError，`m` 是 null 则得 null。
        """
        head, rest = path[0], path[1:]
        text = f"{self._data_var}[{head!r}]" if isinstance(head, str) \
            else f"{self._data_var}[{head}]"
        for seg in rest:
            text = f"_ix_get({text},{seg})" if isinstance(seg, int) \
                else f"{text}[{seg!r}]"
        return text

    def emit_holder(self, node) -> str:
        """发射**动态下标的基座**（`$.a[<...>]` 里的 `$.a`）。

        基座与「普通路径取值」口径不同：`$.a[$i]` 承诺内联成 `R['a'][i]`（架构
        文档 §7.5、README 动态下标表），所以基座必须裸读，不能因为「它是单段路径」
        就走 `_ix_get`——那是「路径取值」的 miss 语义，会把 `R['a'][i]` 退化成
        `_ix_get(R,'a')[i]`（多一次 helper 调用，且把无意义的行为差异带进产物）。
        基座本身是常量链（`$.x.y[$i]`）时就按常量链口径发。
        """
        path = _index_const_chain(node)
        if path is not None:
            if not path:
                return self._data_var
            # safe 模式（optional/default 字段）下基座缺键也要落 undefined，
            # 否则 `_ix_get` 拿到之前就已经 KeyError。
            if self.safe:
                return self.safe_text(path)
            return self._data_var + self.suffix(path)
        return self.emit(node)

    def emit_index(self, node) -> str:
        """发射**下标位置**的表达式（`$.a[<here>]`）。

        下标位置是唯一允许「未绑定 `$name` 当字段读」的上下文（架构文档 §3.5 与
        README 动态下标表承诺 `$.a[$i]` → `R['a'][i]`）。标志只在发射下标的那一刻
        打开，嵌套求值里再出现别的 `$x` 依旧照常报 `SchemaError`，不会把整条规则
        放宽。缺省 `False`，异常安全地复位。
        """
        prev = self._in_dyn_index
        self._in_dyn_index = True
        try:
            return self.emit(node)
        finally:
            self._in_dyn_index = prev

    def subst(self, text: str) -> str:
        if "\x01" not in text:
            return text
        return _TOKEN_RE.sub(
            lambda m: self.text_of(self.order[int(m.group(1))]), text)

    # ---------------------------------------------------- IR 发射
    def emit(self, node) -> str:
        k = node[0]
        if k == _ir.LIT:
            return self.lit(node[1])
        if k in (_ir.FIELD, _ir.VAR):
            if k == _ir.VAR:
                return self.var_name(node[1])
            return self.field_name(node[1])
        if k == _ir.INDEX:
            path = _index_const_chain(node)
            if path is not None:
                if not path:
                    return self._data_var
                # 契约 §4.4 附加条目 3 + 架构文档 §7.5「读值形态」。三条口径：
                #   * **单段**路径 `$.x` 在 **optional/default** 字段里走 `_ix_get`
                #     安全链（缺键 -> undefined）；在 **required** 字段里同多段链
                #     发裸读 `R['x']`（缺键 = KeyError）。两种形态只差「有没有
                #     undefined 出口」，见架构文档 §7.5；value / expr 两条取值
                #     路径必须同口径，否则同一 schema 换写法会改变异常行为。
                #   * **多段纯键链** `$.a.b` 发裸链 `R['a']['b']`：缺键就是 KeyError，
                #     这是 required 口径，也是性能快路径（README/架构文档反复强调）；
                #   * **带位置下标的链** `$.m[0]`：`[0]` 作用在 null 上必须是 null
                #     （§4.4 附加条目 3 的「标量（含 null）是非容器，返回自身」），
                #     所以只有**基座**那一段要走 `_ix_get`，其后按下标裸发。
                #     `R['m'][0]` 里 m 是 None 会抛 TypeError，与承诺矛盾 —— 这正是
                #     `examples/jx/14_fixed_regressions.jx` #31 钉住的回归。
                # 钩子语境（`_data_var != "R"`）：`D` 是整份数据、没有 required
                # 保证 —— 表达式里 `$.rows` 会因为 source 不是 rows 而缺键，
                # 这里必须 undefined 安全，否则 before/after 一写路径就炸。
                # 与 safe 场景合并处理：整条链走 `_ix_get`。
                if self.safe or self._data_var != "R":
                    # optional / default：整条链都要 undefined 安全 —— 缺键得
                    # undefined 交给守卫 / 默认值，**任何一段都不许抛**。
                    # `_ix_get(None,0)` 返回 None（§4.4 附加条目 3「标量（含
                    # null）是非容器，返回自身」），所以位置下标链也能整条走
                    # `_ix_get`，不需要「基座裸读」那种混合形态。
                    return self.safe_text(path)
                # required（未声明 optional）：没有 undefined 出口，缺键 /
                # 越界 / null 下标就是真实异常，**用户承担全部错误** ——
                # 一律发裸链（`R[5]['a'][1]`、`R['a'][0]`），这是 §4.2 规则①
                # 与架构文档 §7.5「路径长度不限、编成同一种下标链」的承诺，
                # 也是唯一能把 position 访问压到零 helper 的形态。
                return self.emit_path(path)

            # 非常量链：基座 + 动态下标（`$.a[b]` / `$.a[$.i]` / `$.a[expr]`）
            idx = node[2]
            holder = node[1]
            if idx[0] == _ir.LIT and isinstance(idx[1], int) \
                    and not isinstance(idx[1], bool) \
                    and self._bare_holder_ok(holder):
                # 已证明是容器类型（list/object）的字段 + 常量整数下标 -> 直接下标。
                # 缺键/越界本来就是 KeyError/IndexError（契约 §2.3 required 口径）。
                return f"{self.emit(holder)}[{self.lit(idx[1])}]"
            itext = self.emit_index(idx)
            if self.safe:
                # optional / default 字段：越界或类型不符要落成 undefined（交给守卫或默认值）
                return f"_ix_get({self.emit_holder(holder)},{itext})"
            # required 字段：没有 optional/default 就没有 undefined 出口，缺数据即真实错误。
            # 架构文档 §7.5「边界」第 2 条是这里的唯一判据：
            #   「动态下标 `$.a[$i]` / `$.a[b]`：required 字段内联为裸下标
            #     （越界/类型不符 = 真实异常）；optional/default 字段走 `_ix_get`」
            # 注意判据是**字段是不是 required**（即 `self.safe`），不是基座的声明类型：
            # 基座多半是 `any`/未声明，那与常量链 `$.a.b` 发 `R['a']['b']` 是同一口径
            # （`a` 是 null 时抛 TypeError 也是用户的选择）。
            # 唯一的例外是 `_bare_holder_ok` 证明基座是容器且下标是常量整数：那条走
            # 上面那个更简单的分支，不必绕到这里。
            return f"{self.emit_holder(holder)}[{itext}]"
        if k == _ir.ROOT:
            return self._data_var
        if k == _ir.UNARY:
            op = node[1]
            if op in ("-", "+") and self._ok_num(node[2]):
                return f"({op}{self.emit(node[2])})"
            v = self.emit(node[2])
            if op == "not":
                if self._truthy_provable(node[2]):
                    return f"(not {v})"
                return f"(not _tru({v}))"
            if op == "-":
                return f"_neg({v})"
            return f"_pos({v})"
        if k == _ir.BIN:
            return self.bin(node)
        if k == _ir.COND:
            c = self.emit(node[1])
            t = self.emit(node[2])
            e = self.emit(node[3])
            if self._truthy_provable(node[1]):
                # 条件一定有值且真值语义与 Python 一致 -> 裸条件表达式
                return f"({t} if {c} else {e})"
            # 否则走 _co：条件为 undefined 时结果也是 undefined（不硬造 else 值）
            return f"_co({c},{t},{e})"
        if k == _ir.CALL:
            return self.call(node[1], node[2], node[3] if len(node) > 3 else "")
        if k == _ir.RFUNC:
            return self.rfunc(node[1], node[2])
        if k == _ir.ARRAY:
            items = [self.emit(x) for x in node[1]]
            if all(self._definite(x) for x in node[1]):
                return "[" + ",".join(items) + "]"
            return "_ar(" + ",".join(items) + ")"
        if k == _ir.OBJECT:
            pairs = [(p[0], self.emit(p[1])) for p in node[1]]
            if all(self._definite(p[1]) for p in node[1]):
                return "{" + ",".join(
                    f"{k!r}:{v}" for k, v in pairs) + "}"
            return "_ob([" + ",".join(
                f"({k!r},{v})" for k, v in pairs) + "])"
        if k == _ir.PY:
            return self.pyseg(node[1], node[2] if len(node) > 2 else None,
                              root=self._data_var)
        if k == _ir.PYBLOCK:
            return self.pyblock(node)
        if k == _ir.LAMBDA:
            raise SchemaError(
                self._fpath(),
                "函数字面量只能作为高阶函数（$map/$filter/$reduce/$each/"
                "$sift/$sort）的实参，不能直接当字段表达式"
                "（DCG 接口实现细节文档 §3.6）")
        raise Unsupported(f"codegen 不支持的 IR 节点 {k!r}")

    def bin(self, node) -> str:
        """二元运算。

        * 编译期可证安全（`self.raw`，见 :meth:`raw_mode`）时发**裸运算**：
          `type` 是声明（§4.2 规则 ④ / 契约 D-8），required 且声明了数值/字符串
          类型的字段不可能 undefined，也不需要每次调用都付 helper 的
          `is _U` + `_num` 检查（Lead 实测 helper 形态 1.85x、裸运算 0.68x）。
        * 否则退回 helper：`&` 两侧都是 `$string(...)` 时直接 `_S(a)+_S(b)`
          （§4.2 规则④，省掉 `_cc` 的一次分派）。
        """
        op = node[1]
        a, b = node[2], node[3]
        if op in ("+", "-", "*", "/", "%") and self._ok_num(a) \
                and self._ok_num(b):
            # 操作数类型可证 -> 裸运算，与目标字段类型无关（§4.2 内联判据）
            return f"({self.emit(a)}{op}{self.emit(b)})"
        if op == "&" and self._ok_str(a) and self._ok_str(b):
            return f"({self.emit(a)}+{self.emit(b)})"
        if op in _CMP_OP:
            ka, kb = self._kind(a), self._kind(b)
            if ka is not None and ka == kb and (
                    ka in ("num", "str") if op in _ORDER_OPS
                    else ka in ("num", "str", "bool")):
                # 同类可证才裸比较：JSONata 的 `true`/`1`、undefined 参与比较有特例
                return f"({self.emit(a)}{_CMP_OP[op]}{self.emit(b)})"
        if op in ("and", "or"):
            if self._truthy_provable(a) and self._truthy_provable(b):
                return f"({self.emit(a)} {op} {self.emit(b)})"
        if op == "in" and self._in_provable(a, b):
            return f"({self.emit(a)} in {self.emit(b)})"
        if self.raw:
            if op in ("+", "-", "*", "/", "%") and (self.raw == "num" or op == "+"):
                return f"({self.emit(a)}{op}{self.emit(b)})"
            if op == "&" and self.raw == "str":
                return f"({self.emit(a)}+{self.emit(b)})"
        if op == "&" and self.is_string_call(a) and self.is_string_call(b):
            return f"_S({self.emit(a[2][0])})+_S({self.emit(b[2][0])})"
        fn = _OPS.get(op)
        if fn is None:
            raise Unsupported(f"codegen 不支持的运算符 {op!r}（列 {node[-1]}）")
        return f"{fn}({self.emit(a)},{self.emit(b)})"

    # ------------------------------------------------ 编译期可证安全（裸运算）
    def raw_mode(self, spec: dict):
        """字段的发射模式：`"num"` / `"str"`（裸运算）或 `None`（走 helper）。

        只有 **required 且显式声明了类型** 的字段才谈得上"可证安全"：
        `type` 是声明（assertion），所以声明 int64 的字段其表达式不可能产出
        undefined，裸 `+` 不会漏掉 undefined 语义。`optional`、`any`、未声明
        类型一律退回 helper —— 那是真正可能 undefined 的地方，守卫必须留着。
        """
        if spec.get("optional") and "default" not in spec:
            return None
        node = self._expr_node(spec)
        if node is None:
            return None
        t = spec.get("type")
        if t in _NUM_TYPES:
            return "num" if self._ok_num(node) else None
        if t == "str":
            return "str" if self._ok_str(node) else None
        return None

    def _expr_node(self, spec: dict):
        """`expr` -> IR，**按源码文本记忆化**。

        一段 `expr` 在一次 generate 里会被问 3~4 次（raw_mode 判定 / 裸名收集 /
        取值发射）。不缓存的话，反引号段的语法校验会被重复 compile 多次 ——
        §4.2 规则 ⑥「每次 build 只 compile 一次」就是盯着这条。
        """
        text = spec.get("expr")
        if text is None:
            return None
        node = self._exprs.get(text)
        if node is None:
            node = self._exprs[text] = compile_expr(text)
        return node

    def _var_proven(self, name: str, want) -> bool:
        """`$i` 变量形态是否可证明：等价于本 schema 字段 `i` 的裸名引用。

        lambda 参数同名时会遮蔽字段（`$x` 在函数体内是参数），参数类型未知，
        一律不证明。`want=None` 只要求「一定不是 undefined」。
        """
        if name in self._params:
            return False
        t = self._decl_type(name)
        if t is None:
            return False
        return True if want is None else t in want

    def _decl_type(self, name: str):
        """已声明字段的**静态类型**；optional / 未声明 → None（不可证安全）。

        除 schema 上直接声明的类型之外，还并入 :meth:`_infer_types` 从**运算上下文**
        反推出来的类型（m03039）：`a` 声明 `int64`、`b` 没标 `type`，两者做 `a + b`
        时 `b` 也可证为数值，于是能发裸 `a+b` 而不是 `_ad(a,b)`。
        推导只**增加**可证性、从不改变语义（数据违约时裸运算与 helper 抛同类异常）。
        """
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return None
        t = spec.get("type")
        if t not in (None, "any"):
            return t
        inf = self._inferred.get(name)
        if inf == self._INFER_NUM:
            # 推导出的「数值」没有具体宽度。对外统一报 `float64`：它属于
            # `_NUM_TYPES`，让 `_ok_num`/`_definite`/`raw_mode` 走裸运算，
            # 而宽度只影响「是否再套 int()`，`_num` 判定不看宽度。
            return "float64"
        return inf

    #: 数值类型集合（`bool` 不在内：运算表里 bool 被拒绝，见 runtime `_num`）。
    #: 从运算上下文推导时，`a` 是 int64 则 `b` 可推 `num`；反过来 `b` 可推 `num`
    #: -> 取最具体的 `float64`? 不 —— 推成 `_NUM_ANY` 标记，让 `_ok_num` 认。
    _INFER_NUM = "\x00num"

    def _infer_types(self, roots) -> None:
        """从运算上下文反向推导未声明字段的类型（迭代到不动点）。

        `roots` 是本 schema 所有表达式 IR 的根节点列表（`value` 只有路径、无 IR，
        所以只有 `expr` 参与推导）。规则**全部是保守的单向蕴含**：

        * `NUM  <op> X` 且 `op ∈ {+ - * / %}`、`NUM` 可证数值  -> X 的裸字段引用可推数值
          （这些算子在 Python 里对非数值抛 TypeError，JSONata 侧也只在数值上有定义）
        * `< <= > >=` 两侧：一侧可证数值 -> 另一侧可推数值（不可比会落 undefined，
          但「可证数值」这一侧能证明它确实是数值域）
        * 一元 `-x` / `+x` -> x 可推数值
        * `$sum/$abs/$floor/$ceil/$round/$sqrt/$power/$number` 的实参可推数值
        * `$uppercase/$lowercase/$trim/$length/$split/$substring/$join` 的实参可推字符串

        **不参与**：`=`/`!=`/`&`/`and`/`or`/`in`（对任意类型都有定义，无蕴含）；
        显式写 `type:"any"` 的字段（用户已声明「我不保证」，尊重声明）；
        optional / 有 default 的字段（取值路径必须保留 undefined 出口）。
        """
        self._inferred = {}
        exprs = [n for n in roots if n is not None]
        if not exprs:
            return
        num_probe = _NUM_TYPES | {self._INFER_NUM}
        # 迭代到不动点：推导出的类型可以继续参与下一轮（`a + b` 推出 b，
        # 再由 `b * c` 推出 c）。字段数有限，最多迭代字段数次即可收敛。
        for _ in range(len(self.fields) + 1):
            before = dict(self._inferred)
            for node in exprs:
                for n in _ir.walk(node):
                    self._infer_node(n, num_probe)
            if self._inferred == before:
                break

    def _infer_node(self, node, num_probe) -> None:
        """对单个 IR 节点应用推导规则（直接写 `_inferred`）。"""
        k = node[0]
        if k == _ir.UNARY and node[1] in ("-", "+"):
            self._infer_as(node[2], self._INFER_NUM, num_probe)
            return
        if k == _ir.BIN:
            op, l, r = node[1], node[2], node[3]
            if op in ("+", "-", "*", "/", "%"):
                # 数值算子：一侧可证数值 -> 另一侧可推数值。
                if self._node_is_num(l, num_probe):
                    self._infer_as(r, self._INFER_NUM, num_probe)
                if self._node_is_num(r, num_probe):
                    self._infer_as(l, self._INFER_NUM, num_probe)
            elif op in ("<", "<=", ">", ">="):
                if self._node_is_num(l, num_probe):
                    self._infer_as(r, self._INFER_NUM, num_probe)
                if self._node_is_num(r, num_probe):
                    self._infer_as(l, self._INFER_NUM, num_probe)
            return
        if k == _ir.CALL and node[1] in _NUM_ARG_FUNCS:
            for a in node[2]:
                self._infer_as(a, self._INFER_NUM, num_probe)
            return
        if k == _ir.CALL and node[1] in _STR_ARG_FUNCS:
            for a in node[2]:
                self._infer_as(a, "str", num_probe)

    def _node_is_num(self, node, num_probe) -> bool:
        """节点是否**可证为数值**（推导过程用；比 `_ok_num` 宽一点：认推导类型）。"""
        name = self._const_field_name(node)
        if name is not None:
            t = self._decl_type(name)
            return t in num_probe
        return self._ok_num(node)

    def _const_field_name(self, node):
        """节点若是一条**单段常量字段链**（`$.a` 或裸名 `a`），折回字段名；否则 None。

        `_ir.key_path` 不认 `root` 基座（它对 `('root',...)` 返回 None），
        `$.a` 恰好就是 `('index',('root',...),('lit','a',...))`，所以这里自己折。
        """
        if node[0] == _ir.FIELD:
            return node[1]
        if node[0] != _ir.INDEX:
            return None
        segs = []
        cur = node
        while cur[0] == _ir.INDEX:
            idx = cur[2]
            if idx[0] != _ir.LIT or not isinstance(idx[1], (int, str)):
                return None
            segs.append(idx[1])
            cur = cur[1]
        if cur[0] != _ir.ROOT:
            return None
        segs.reverse()
        if len(segs) == 1 and isinstance(segs[0], str):
            return segs[0]
        return None

    def _infer_as(self, node, want, num_probe) -> None:
        """把 `want` 推给 `node` 里出现的裸字段引用。

        只对**没有具体类型声明**的 required 字段生效 —— 也就是 `type: "any"`
        （`validate()` 会把省略的 `type` 一律归一成 `"any"`，所以判据是
        `type` 为空或 `"any"`）。已经声明具体类型的字段无需推导；
        `optional` / 有 `default` 的字段不能推导（取值路径必须保留 undefined 出口）。
        """
        if node[0] == _ir.FIELD:
            name = node[1]
        elif node[0] == _ir.INDEX:
            # `$.b`（单段路径）在 IR 里是 `('index',('root',...),('lit','b',...))`，
            # 与裸名 `b` 指向同一格 —— 必须先折回字段名，否则推导只对裸名生效。
            name = self._const_field_name(node)
            if name is None:
                for c in _ir.children(node):
                    self._infer_as(c, want, num_probe)
                return
        else:
            for c in _ir.children(node):
                self._infer_as(c, want, num_probe)
            return
        if True:
            spec = self.fields.get(name)
            if not spec or spec.get("optional"):
                return
            if spec.get("type") not in (None, "any"):
                return
            if "default" in spec or "const" in spec or "fields" in spec:
                return
            cur = self._inferred.get(name)
            if want == self._INFER_NUM:
                # 已是 str / 已是 num 都不动：冲突说明推导不出确定类型，保持原样。
                if cur is None:
                    self._inferred[name] = self._INFER_NUM
            elif cur is None and want == "str":
                self._inferred[name] = "str"
            return
        for c in _ir.children(node):
            self._infer_as(c, want, num_probe)

    #: 常量整数下标可以直接内联成 `base[i]` 的 holder 类型 —— **必须能证明是容器**。
    #: `any` 与标量类型（int64/str/...）都不能：`$.m[0]` 里 m 的值若是 `null`，
    #: 内联出的 `None[0]` 会抛 TypeError，而契约 §4.4 第 7 条要求 `a[0]` 对
    #: 非容器成立、`null` 落成 undefined。
    _BARE_HOLDER_TYPES = frozenset({"list", "object"})

    def _bare_holder_ok(self, holder) -> bool:
        """常量整数下标能否内联成裸 `base[i]`。

        只有「已声明为容器类型（list/object）的 required 字段」才可证：
        缺键是 KeyError/IndexError（契约 §2.3 的 required 口径），越界也必须抛，
        与 `$.a[0]` 的常量链口径一致。其余情况（any、标量、未声明、表达式基座）
        一律落回 `_ix_get`，让 null/标量走 undefined 语义。
        """
        if holder[0] not in (_ir.FIELD, _ir.VAR):
            return False
        return self._decl_type(holder[1]) in self._BARE_HOLDER_TYPES

    def _decl_container(self, holder) -> bool:
        """字段是否**声明为 `type: "list"` / `"object"` 的 required 字段**。

        这是常量下标能否内联成裸 `base[i]` 的**唯一**判据（与 `any`、标量区分开）：
        `$.m[0]` 里 m 若是 `type: "any"`，它的运行期值可能是 `null`，
        内联出的 `None[0]` 会抛 TypeError，而契约 §4.4 要求 `a[0]` 对
        非容器成立、`null` 落成 undefined。
        """
        if holder[0] not in (_ir.FIELD, _ir.VAR):
            return False
        return self._decl_type(holder[1]) in self._BARE_HOLDER_TYPES

    def _index_const_chain(self, node):
        """`emit` 用的常量链解析：先试 `_chain`，再试被 `_chain` 拒掉的形态。

        这里**不需要**额外能力（`_chain` 已覆盖行路径常链），保留此方法只是为了
        让 `emit` 里的调用点可以被子类/测试替换。
        """
        return _index_const_chain(node)

    def _ok_num(self, node) -> bool:
        k = node[0]
        if k == _ir.LIT:
            v = node[1]
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if k == _ir.INDEX:
            # `$.a` 与裸名 `a` 指向同一格：先按单段字段折回，再退回位置式判定。
            name = self._const_field_name(node)
            if name is not None:
                return self._decl_type(name) in _NUM_TYPES
            return self._positional(node)
        if k == _ir.FIELD:
            return self._decl_type(node[1]) in _NUM_TYPES
        if k == _ir.VAR:
            return self._var_proven(node[1], _NUM_TYPES)
        if k == _ir.UNARY:
            return node[1] in ("-", "+") and self._ok_num(node[2])
        if k == _ir.BIN:
            return (node[1] in ("+", "-", "*", "/", "%")
                    and self._ok_num(node[2]) and self._ok_num(node[3]))
        if k == _ir.CALL:
            return (self._inline_ok(node[1], node[2])
                    and _INLINE_KIND.get(node[1]) == "num")
        if k == _ir.COND:
            return (self._truthy_provable(node[1])
                    and self._ok_num(node[2]) and self._ok_num(node[3]))
        return False

    def _ok_str(self, node) -> bool:
        k = node[0]
        if k == _ir.LIT:
            return isinstance(node[1], str)
        if k == _ir.INDEX:
            name = self._const_field_name(node)
            if name is not None:
                return self._decl_type(name) == "str"
            return self._positional(node)
        if k == _ir.FIELD:
            return self._decl_type(node[1]) == "str"
        if k == _ir.VAR:
            return self._var_proven(node[1], ("str",))
        if self.is_string_call(node):
            return self._ok_num(node[2][0])
        if k == _ir.CALL and node[1] in _STR_METHODS and len(node[2]) == 1:
            return self._ok_str(node[2][0])
        if k == _ir.BIN:
            if node[1] == "+":
                return self._ok_str(node[2]) and self._ok_str(node[3])
            if node[1] == "&":
                return (self._ok_str_or_string_call(node[2])
                        and self._ok_str_or_string_call(node[3]))
        if k == _ir.CALL:
            return (self._inline_ok(node[1], node[2])
                    and _INLINE_KIND.get(node[1]) == "str")
        if k == _ir.COND:
            return (self._truthy_provable(node[1])
                    and self._ok_str(node[2]) and self._ok_str(node[3]))
        return False

    def _positional(self, node) -> bool:
        """路径是否**位置式**（`$.5.a.1`：首段是整数下标）。

        只有位置式路径才敢假设"一定取得到"：`$.5.a.1` 是固定行布局
        （CAP/M0 就是这么取的，缺了本来就是 IndexError/KeyError）；而 `$.zz` 是**具名键**，
        缺键是 undefined 的正常情形，必须留给 helper 去守（§4.4 语义 1/3/4，
        `tests/test_sentinel_integrity.py::TestContract44MissingPath` 盯着这条）。
        另接受**字面量下标**取已声明且可证非 undefined 的字段（`hour[2]`）。
        """
        if node[0] != _ir.INDEX:
            return False
        cur = node
        while cur[0] == _ir.INDEX:
            base, idx = cur[1], cur[2]
            if base[0] == _ir.ROOT:
                # 只看**最左段**（首段）是不是整数字面量：`$.5.a.1` ✅ / `$.zz` ❌
                return idx[0] == _ir.LIT and isinstance(idx[1], int)
            if base[0] in (_ir.FIELD, _ir.VAR):
                return (idx[0] == _ir.LIT and isinstance(idx[1], int)
                        and self._decl_positional(base[1]))
            cur = base
        return False

    def _decl_positional(self, name: str) -> bool:
        """已声明字段能否证明「一定不是 undefined」：非 optional、`value` 是位置式
        路径，或 `expr` 本身落在裸运算模式里（裸运算模式的前提就是可证安全）。"""
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return False
        if "value" in spec:
            path = spec["value"]
            return bool(path) and isinstance(path[0], int)
        if "expr" in spec:
            return self.raw_mode(spec) is not None
        return False

    def _definite(self, node) -> bool:
        """节点在运行期是否**一定不是 undefined**（可安全发裸字面量/运算）。

        只认能静态证明的形态：字面量、required 且声明具体类型的字段、
        由它们组成的运算/字符串函数调用/数组对象字面量。`$.路径`（可能缺键）、
        optional、`any`、变量一律不放行——那些交给 `_ar` / `_ob` / 其它 helper
        去执行「跳过 undefined」的语义（§4.4 语义 5/6）。
        """
        k = node[0]
        if k == _ir.LIT:
            return True
        if k == _ir.FIELD:
            return self._decl_type(node[1]) is not None
        if k == _ir.VAR:
            return self._var_proven(node[1], None)
        if k == _ir.UNARY:
            return node[1] in ("-", "+") and self._ok_num(node[2])
        if k == _ir.BIN:
            if node[1] in ("+", "-", "*", "/", "%"):
                return self._ok_num(node[2]) and self._ok_num(node[3])
            if node[1] == "&":
                return (self._ok_str_or_string_call(node[2])
                        and self._ok_str_or_string_call(node[3]))
            if node[1] in _CMP_OP:
                ka, kb = self._kind(node[2]), self._kind(node[3])
                return ka is not None and ka == kb
            if node[1] in ("and", "or"):
                return (self._truthy_provable(node[2])
                        and self._truthy_provable(node[3]))
            return False
        if k == _ir.CALL:
            if self.is_string_call(node):
                return self._ok_num(node[2][0])
            if node[1] in _STR_METHODS and len(node[2]) == 1:
                return self._ok_str(node[2][0])
            return self._inline_ok(node[1], node[2])
        if k == _ir.COND:
            return (self._truthy_provable(node[1])
                    and self._definite(node[2]) and self._definite(node[3]))
        if k == _ir.ARRAY:
            return all(self._definite(x) for x in node[1])
        if k == _ir.OBJECT:
            return all(self._definite(p[1]) for p in node[1])
        return False

    def _never_undef(self, spec: dict) -> bool:
        """该字段**求值后一定不是 undefined** —— 可安全发裸字面量，省掉 `_ob`/`_rl`。

        只服务 `_emit_object` 的「字面量 or helper」二选一，**刻意保守**：任何一条
        说不准就返回 False，退回 helper（语义完全不变，只是慢一点）。规则与
        `_definite` 分工不同：`_definite` 判**节点**，这里判**字段**（含 optional/
        default/嵌套这几条字段级事实）。

        **与 `_read_safe` 的分工（缺一不可）**：本方法只回答「值不会是哨兵」，
        不回答「读得到」。`{"type":"any","value":"$.a"}` 缺键时下面 `value` 分支
        会返回 False（因为 `any` 不在白名单），但 `optional`/`default` 字段读得安全
        却可能拿到哨兵 —— 两边是**互补**的两组条件，`_emit_object` 必须同时满足。
        """
        if "default" in spec:
            # `_df` 兜底：拿到 undefined 也落成默认值
            return True
        if spec.get("optional"):
            # optional 且无 default：可能 undefined（要发 `None if ... is _U`）
            return False
        if "const" in spec:
            return True
        if "fields" in spec:
            # 嵌套 object/位置行：helper 或内层字面量返回的是 dict/list，不是哨兵
            return True
        if "value" in spec:
            # 声明了具体类型时才成立：这条路 codegen 本来就走裸读（`$.5.a.1` 式固定
            # 布局），缺键是真实错误（IndexError/KeyError）而不是 undefined。
            # 不具名（"any"）时读值走 `_ix_get` 链，缺键会给出 UNDEF —— 不放行。
            # `type` 本身是 validate 的必填键（缺了会报「缺少必填键 "type"」），
            # 所以 `t is None` 只是一条不可达的防御判断。
            #
            # `optional` 必须在这里拦截：raw 模式下 `value` 路径发的是裸读
            # `R['x']`，而 optional 的语义承诺是「缺键得 None（§2.3）」。裸读在缺键
            # 时抛 KeyError，承诺直接落空。带 `default` 的 optional 在上面第一个分支
            # 就返回 True（`_df` 兜底），走不到这里；因此这里只可能是
            # 「optional 且无 default」——一定可能 undefined，必须退回 helper 让它
            # 走 `_ix_get` 拿哨兵。`expr` 分支不受影响（它有自己的 py/裸名分析）。
            t = spec.get("type")
            if t is None or t == "any" or spec.get("optional"):
                return False
            return True
        if "expr" in spec:
            node = self._expr_node(spec)
            if node[0] in (_ir.PY, _ir.PYBLOCK):
                # 反引号 py 段 / `py{}` 块里**可以**出现裸字段名（如
                # `` `str($.5.a.1) + str(hourB)` ``），字段值本身也可能是
                # undefined。能不能拿到哨兵，要看每个裸名解析到的绑定：
                #  - 不是字段名：受 `PY_SAFE_GLOBALS` 白名单约束（jx/compiler.py），
                #    命中的是内置与标准库模块，都不是 UNDEF；
                #  - 顶层字段且 optional 无 default：body() 已把它包成
                #    `None if _x is _U else _x`，裸名读到的是 None；
                #  - 其余情况递归问该字段自己的 `_never_undef`（嵌套作用域的兄弟
                #    字段没有那层 None 守卫，可证性只能靠递归 —— 见
                #    `_bare_name_never_undef`，实测漏判会直接把哨兵写进产物）。
                # 不把 PY 塞进 `_definite()`——那会连带影响裸运算/产物形态等既有
                # 决策，这里只做局部特判。
                return all(self._bare_name_never_undef(n)
                           for n in self._names(spec, set()))
            return self._definite(node)
        return False

    def _bare_name_never_undef(self, name: str) -> bool:
        """裸名 `name` 在它的作用域里解析到的绑定，是否可证非 undefined。

        作用域查找顺序与 `field_name` 一致：内层到外层，内层遮蔽外层；都不是字段
        名就当内置名/模块名（`PY_SAFE_GLOBALS` 白名单），不会是哨兵。
        """
        for specs in reversed(self._nested_specs):
            if name in specs:
                return self._never_undef(specs[name])
        spec = self.fields.get(name)
        if spec is None:
            return True
        if spec.get("optional") and "default" not in spec:
            return True
        return self._never_undef(spec)

    def _kind(self, node):
        """节点的静态类别（"num" / "str" / "bool"）；不可静态证明 → None。"""
        k = node[0]
        if k == _ir.LIT:
            v = node[1]
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, (int, float)):
                return "num"
            if isinstance(v, str):
                return "str"
            return None
        if k in (_ir.FIELD, _ir.VAR):
            name = node[1]
            if node[0] == _ir.VAR and name in self._params:
                return None
            t = self._decl_type(name)
            if t in _NUM_TYPES:
                return "num"
            if t == "str":
                return "str"
            if t == "bool":
                return "bool"
            return None
        if k == _ir.UNARY:
            if node[1] in ("-", "+"):
                return "num" if self._ok_num(node) else None
            if node[1] == "not":
                return "bool" if self._truthy_provable(node[2]) else None
            return None
        if k == _ir.BIN:
            op = node[1]
            if op in ("+", "-", "*", "/", "%"):
                return "num" if self._ok_num(node) else None
            if op == "&":
                return "str" if self._ok_str(node) else None
            if op in _CMP_OP:
                ka, kb = self._kind(node[2]), self._kind(node[3])
                return "bool" if (ka is not None and ka == kb) else None
            if op in ("and", "or"):
                ok = (self._truthy_provable(node[2])
                      and self._truthy_provable(node[3]))
                return "bool" if ok else None
            return None
        if k == _ir.CALL:
            if self.is_string_call(node):
                return "str"
            if node[1] in _STR_METHODS and len(node[2]) == 1:
                return "str" if self._ok_str(node[2][0]) else None
            if self._inline_ok(node[1], node[2]):
                return _INLINE_KIND.get(node[1])
            return None
        if k == _ir.COND:
            kt, ke = self._kind(node[2]), self._kind(node[3])
            if kt is not None and kt == ke and self._truthy_provable(node[1]):
                return kt
            return None
        return None

    def _truthy_provable(self, node) -> bool:
        """该节点是否保证有值且真值语义与 Python 一致（num / str / bool）。

        JSONata 的布尔转换在「数字 0、空串、布尔」上与 Python 真值一致；
        未声明类型、`$.路径`（可能缺键）、optional、可能 undefined 的值
        都不能裸写，必须走 helper 守 undefined 传播。
        """
        return self._kind(node) in ("num", "str", "bool")

    def _in_provable(self, a, b) -> bool:
        """`a in [...]`：左侧类型可证，右侧全是同类字面量。

        Python `in` 与 JSONata 的元素相等在同类标量上一致；混合类型
        （`true in [1]` 之类）必须走 `_in` helper。
        """
        ka = self._kind(a)
        if ka not in ("num", "str"):
            return False
        if b[0] != _ir.ARRAY:
            return False
        for x in b[1]:
            if x[0] != _ir.LIT or self._kind(x) != ka:
                return False
        return True

    def _lit_str(self, node) -> bool:
        return node[0] == _ir.LIT and isinstance(node[1], str)

    def _lit_num(self, node) -> bool:
        return (node[0] == _ir.LIT and isinstance(node[1], (int, float))
                and not isinstance(node[1], bool))

    def _lit_int(self, node, ge=None) -> bool:
        if node[0] != _ir.LIT or not isinstance(node[1], int) \
                or isinstance(node[1], bool):
            return False
        return True if ge is None else node[1] >= ge

    def _is_listy(self, node) -> bool:
        """是否可证明是数组（`len()` / `$count` 可直接内建）。"""
        k = node[0]
        if k == _ir.ARRAY:
            return all(self._definite(x) for x in node[1])
        if k in (_ir.FIELD, _ir.VAR):
            if k == _ir.VAR and node[1] in self._params:
                return False
            return self._decl_type(node[1]) == "list"
        return False

    def _nonneg_provable(self, node) -> bool:
        """能否证明数值节点 >= 0（`$sqrt` 内联的前提）。"""
        if node[0] == _ir.LIT:
            return self._lit_num(node) and node[1] >= 0
        if node[0] == _ir.CALL and node[1] == "$abs" and len(node[2]) == 1:
            return self._ok_num(node[2][0])
        if node[0] == _ir.BIN and node[1] in ("+", "*", "&"):
            return (self._nonneg_provable(node[2])
                    and self._nonneg_provable(node[3]))
        return False

    def _inline_ok(self, fname: str, args) -> bool:
        """该列函数是否有**语义一致的 Python 内建等价**且实参可证（§4.2）。

        只放行逐项等价、无 JSONata 特例的函数；`$round`（银行家舍入不同）、
        `$number`（字符串解析规则）、`$floor/$ceil/$sqrt`（需要 import math）
        等一律继续走 helper。
        """
        n = len(args)
        if fname == "$length":
            return n == 1 and (self._ok_str(args[0]) or self._is_listy(args[0]))
        if fname == "$abs":
            return n == 1 and self._ok_num(args[0])
        if fname in ("$not", "$boolean"):
            return n == 1 and self._truthy_provable(args[0])
        if fname == "$contains":
            return n == 2 and self._ok_str(args[0]) and self._ok_str(args[1])
        if fname == "$split":
            # 第三参 limit 的语义（最多切 N 段）与 `str.split(sep, maxsplit)`
            # 的"最多切 N 次"不同，带 limit 时一律走 `_sp`。
            return (n == 2 and self._ok_str(args[0])
                    and args[1][0] == _ir.LIT and isinstance(args[1][1], str)
                    and args[1][1] != "")
        if fname in ("$substringBefore", "$substringAfter"):
            return n == 2 and self._ok_str(args[0]) and self._ok_str(args[1])
        if fname == "$replace":
            return n == 3 and all(self._ok_str(x) for x in args)
        if fname == "$pad":
            # 只有单字符填充时才与 `str.ljust(s, fillchar)` 逐值等价；
            # 多字符填充时 helper 只取首字符（JSONata 1.8 同），Python 会抛 TypeError。
            return (n in (2, 3) and self._ok_str(args[0])
                    and self._lit_int(args[1], ge=0)
                    and (n == 2 or (self._lit_str(args[2])
                                    and len(args[2][1]) == 1)))
        if fname == "$substring":
            if n == 2:
                return self._ok_str(args[0]) and self._lit_int(args[1], ge=0)
            if n == 3:
                return (self._ok_str(args[0]) and self._lit_int(args[1], ge=0)
                        and self._lit_int(args[2], ge=0))
            return False
        if fname == "$count":
            return n == 1 and self._is_listy(args[0])
        if fname in ("$sum", "$max", "$min", "$average"):
            if n != 1 or args[0][0] != _ir.ARRAY:
                return False
            items = args[0][1]
            if not all(self._ok_num(x) for x in items):
                return False
            # $sum([]) == 0 与 sum([]) 一致；$max/$min/$average 对空数组返回
            # undefined，而 Python 内建会抛异常 —— 空数组只放行 $sum。
            return fname == "$sum" or bool(items)
        if fname == "$reverse":
            # IR ARRAY 一定求出 list，[::-1] 与 _rv 等价（都是新 list）
            return n == 1 and args[0][0] == _ir.ARRAY
        if fname == "$sort":
            if n != 1 or args[0][0] != _ir.ARRAY or not args[0][1]:
                return False
            items = args[0][1]
            if all(self._ok_num(x) for x in items):
                return True
            return all(self._lit_str(x) for x in items)
        if fname == "$floor":
            return n == 1 and self._ok_num(args[0])
        if fname == "$ceil":
            return n == 1 and self._ok_num(args[0])
        if fname == "$round":
            # **不能内联成 Python `round()`**：JSONata 是 half-up
            # （`$round(2.5) == 3`），Python `round()` 是银行家舍入
            # （`round(2.5) == 2`）。两者在 .5 边界上语义不同，一律走 `_ro`。
            return False
        if fname == "$sqrt":
            # helper 把负数映射成 undefined，而 math.sqrt 会抛 ValueError；
            # 只有能证明非负时才内联（字面量 >=0 或 $abs(...)）
            return n == 1 and self._nonneg_provable(args[0])
        if fname == "$power":
            return (n == 2 and self._ok_num(args[0])
                    and self._ok_num(args[1]))
        return False

    def _inline_text(self, fname: str, args):
        if not self._inline_ok(fname, args):
            return None
        n = len(args)
        a0 = self.emit(args[0])
        if fname == "$length":
            return f"len({a0})"
        if fname == "$count":
            return f"len({a0})"
        if fname == "$abs":
            return f"abs({a0})"
        if fname == "$not":
            return f"(not {a0})"
        if fname == "$boolean":
            return f"bool({a0})"
        if fname == "$contains":
            return f"({self.emit(args[1])} in {a0})"
        if fname == "$split":
            return f"{a0}.split({self.emit(args[1])})"
        if fname == "$substringBefore":
            return f"{a0}.partition({self.emit(args[1])})[0]"
        if fname == "$substringAfter":
            return f"{a0}.partition({self.emit(args[1])})[2]"
        if fname == "$replace":
            return (f"{a0}.replace({self.emit(args[1])},"
                    f"{self.emit(args[2])})")
        if fname == "$pad":
            if n == 2:
                return f"{a0}.ljust({self.emit(args[1])})"
            return (f"{a0}.ljust({self.emit(args[1])},"
                    f"{self.emit(args[2])})")
        if fname == "$substring":
            start = int(args[1][1])
            if n == 2:
                return f"{a0}[{start}:]"
            return f"{a0}[{start}:{start + int(args[2][1])}]"
        if fname in ("$sum", "$max", "$min"):
            return f"{fname[1:]}({a0})"
        if fname == "$reverse":
            return f"({a0})[::-1]"
        if fname == "$sort":
            return f"sorted({a0})"
        if fname == "$average":
            return f"(sum({a0})/len({a0}))"
        if fname == "$floor":
            return f"math.floor({a0})"
        if fname == "$ceil":
            return f"math.ceil({a0})"
        if fname == "$sqrt":
            return f"math.sqrt({a0})"
        if fname == "$power":
            return f"({a0}**{self.emit(args[1])})"
        return None

    def _ok_str_or_string_call(self, node) -> bool:
        return self._ok_str(node) or (self.is_string_call(node)
                                      and self._ok_num(node[2][0]))

    @staticmethod
    def _uses_helpers(text: str) -> bool:
        return bool(set(re.findall(r"[A-Za-z_]\w*", text)) & _IMPORT_NAMES)

    @staticmethod
    def is_string_call(node) -> bool:
        return (isinstance(node, tuple) and node and node[0] == _ir.CALL
                and node[1] == "$string" and len(node[2]) == 1)

    def var_name(self, name: str) -> str:
        """`$x` 变量引用：只有函数字面量形参（B 类回调）能在产物里“是变量”。

        逐行函数只看到一行与整批数据，没有变量绑定作用域（契约 §3.9）。所以
        未被 `$map(... function($x){...})` 之类绑定的 `$x` 必须在 **build 期**
        报 `SchemaError`，而不是让产物在运行期抛 `NameError`（§7.3：两类构建期
        异常不得泄漏到运行期）。

        **例外：与本行字段同名的 `$name`**。架构文档 §7.5 边界第 2 条承诺
        `$.a[$i]` 内联成 `R['a'][i]`，README 的动态下标表同款；而 jx 层是纯
        函数、看不到 schema，把 `$x` 编成 `var` 节点是对的。所以「行模式下未被
        任何回调绑定、且同名字段已声明」的 `$name` 一律降级成字段引用——这与
        「裸名 `i` 就是字段引用」是同一条规则（同一 token 的语义与语法位置无关，
        见 `examples/jx/05_paths.jx`）。真正位于回调体内、被函数字面量绑定成形参
        的 `$x` 由 `self._params` 先命中，不受影响；同名字段未声明时仍报
        `SchemaError`（不静默降级）。
        """
        if name in self._params:
            return self._params[name]
        if name in self._visible_fields():
            return self.field_name(name)
        raise SchemaError(
            self._fpath(),
            f"行模式没有变量作用域：${name} 不是任何函数字面量的形参"
            f"（逐行函数只看到行与整批数据）；要取本行字段请写裸名 {name} 或 $.{name}")

    def _visible_fields(self) -> set:
        """当前作用域链上能按裸名引用的字段集合（嵌套作用域由内到外并集）。"""
        seen = set(self.fields)
        for scope in self._nested_scopes:
            seen |= set(scope)
        return seen

    def field_name(self, name: str) -> str:
        """裸名字段引用：按**依赖顺序**取已算好的局部名（前向引用合法，§2.5）。

        查找顺序：函数字面量参数 → 嵌套作用域（由内到外，内层遮蔽外层）→ 顶层字段。
        """
        if name in self._params:
            return self._params[name]
        for scope in reversed(self._nested_scopes):
            if name in scope:
                return scope[name]
        if name in self.locals:
            return self.locals[name]
        visible = set(self.fields)
        for scope in self._nested_scopes:
            visible |= set(scope)
        raise SchemaError(
            self._fpath(),
            f"裸名 {name!r} 不是本次 schema 声明的字段；已声明: "
            f"{sorted(visible)}（行内取值请写 $.{name} 或 $.路径）")

    # ---------------------------------------------------- 错误定位
    _KEYS = ("const", "value", "expr", "fields")

    def _enter(self, name: str, spec: dict):
        """进入某字段/子字段：记录 path 用的字段名与键名，返回被替换的旧值。"""
        prev = (self.cur_field, self.cur_key)
        self.cur_field = name
        self.cur_key = next((k for k in self._KEYS if k in spec), "")
        return prev

    def _fpath(self) -> str:
        """SchemaError 的 path：指到**具体字段的具体键**（`out.fields.v.expr`）。"""
        if not self.cur_field:
            return "out.fields"
        if not self.cur_key:
            return f"out.fields.{self.cur_field}"
        return f"out.fields.{self.cur_field}.{self.cur_key}"

    def call(self, fname: str, args, loc="") -> str:
        if fname == "$position" and not args and self._index_var:
            # `$position()` 只在函数体内可用，编成回调的第二个位置参数（§3.6）。
            return self._index_var
        inl = self._inline_text(fname, args)
        if inl is not None:
            return inl
        if self.raw and fname == "$string" and len(args) == 1 \
                and self._ok_num(args[0]):
            # `type: str` 的字段里 `$string(数值)` 就是内建 `str(x)`；布尔要
            # 保持 JSONata 的 "true"/"false"，继续走 `_S` helper
            return f"str({self.emit(args[0])})"
        if (fname in _STR_METHODS and len(args) == 1
                and self._ok_str(args[0])):
            # 实参类型 build 期可证为字符串 -> 内建方法调用（裸代码，§4.2）
            return f"{self.emit(args[0])}.{_STR_METHODS[fname]}()"
        if fname in _HIGHER and not (fname == "$sort" and len(args) == 1):
            # `$sort(array)` 是 A 类行内函数；只有带比较器的两参形态才是高阶。
            return self.higher(fname, args, loc)
        fn = _FN.get(fname)
        if fn is None:
            raise Unsupported(f"codegen 不认识的列函数 {fname}（列 {loc}）")
        if fn == "_N" and not args:
            return "_N()"
        return fn + "(" + ",".join(self.emit(a) for a in args) + ")"

    def emit_lambda(self, node) -> str:
        """IR `('lambda', [参数], body, loc)` -> 产物里的 Python `lambda`。

        回调的调用形态固定为 `f(v,i)` / `f(acc,v)`（两个位置参数）：实参少写时
        用 `_i0` 补齐，多写则拒绝（DCG 接口实现细节文档 §3.6）。
        """
        params = list(node[1])
        if len(set(params)) != len(params):
            dup = next(p for p in params if params.count(p) > 1)
            raise SchemaError(
                self._fpath(), f"函数字面量参数重复: {dup!r}")
        arity = 2
        if len(params) > arity:
            raise Unsupported(
                "函数字面量参数过多", node[3] if len(node) > 3 else "",
                f"回调最多接收 {arity} 个参数（DCG 接口实现细节文档 §3.6）")
        names = params + ["_i%d" % i for i in range(len(params), arity)]
        saved = dict(self._params)
        saved_index = self._index_var
        self._params.update({p: p for p in params})
        self._index_var = names[1]
        try:
            text = self.emit(node[2])
        finally:
            self._params = saved
            self._index_var = saved_index
        return "lambda %s:%s" % (",".join(names), text)

    def higher(self, fname: str, args, loc="") -> str:
        """高阶函数：数组等实参照常发射，回调 lambda 就地发射（接口文档 §3.5「B 类」/ §3.6）。

        **优先尝试「推导式内联」**：当回调是纯函数字面量、且函数体里只引用
        lambda 形参（不引用外层字段、不用 `$position`）时，把

            `_mp(R[6], lambda v,_i1:_ml(v,2))`

        改写成

            `[_ml(v,2) for v in R[6]]`

        语义**逐值不变**（`_ml` 的 UNDEF 传播 / 类型检查 / 命名报错全在），只是
        省掉「每元素一次 lambda 调用」这层壳。实测 10 万行 6 元素数组：
        `_mp`+lambda 1.2845 µs/行 -> 推导式 0.6611 µs/行（**1.94x**）；
        `_fi`+lambda 1.6218 -> 0.8635（**1.88x**）。

        这是「去掉调用层」而不是「去掉语义检查」：要连 helper 一起省掉（发
        裸 `v*2`）必须能证明元素是数值，那条路受 schema 表达能力限制，不在这里。

        不可内联时（`loc` 为空、回调引用外层字段、`$reduce` 的累加形态、`$sort`
        带比较器）一律退回原样发 helper + lambda —— 行为与优化前完全相同。
        """
        fn, _arity = _HIGHER[fname]
        lam = None
        for a in args:
            if isinstance(a, tuple) and a and a[0] == _ir.LAMBDA:
                lam = a
                break
        if lam is None:
            raise Unsupported(
                f"{fname} 的函数实参必须是函数字面量", loc,
                "高阶函数的回调只能写 function($v){...}"
                "（DCG 接口实现细节文档 §3.5「B 类」/ §3.6）")
        inlined = self._higher_inline(fname, args, lam)
        if inlined is not None:
            return inlined
        parts = [self.emit_lambda(a) if a is lam else self.emit(a) for a in args]
        return fn + "(" + ",".join(parts) + ")"

    def _higher_inline(self, fname: str, args, lam):
        """尝试把高阶函数展成推导式；不能展则返回 `None`。

        只处理三种可严格保持语义的形态：
        * `$map(arr, f)`          -> `[<体> for v in <arr>]`
        * `$filter(arr, f)`       -> `[v for v in <arr> if <体>]`
        * `$each(obj, f)`         -> 保持 helper（键值对语义，推导式写不清）

        拒绝条件（任一成立就退回）：
        * 形参个数与助手实现的调用约定不符（`$map`/`$filter` 回调收 `(v,i)`）；
        * 函数体里出现**自由字段引用**（`_ir.FIELD`/`_ir.ROOT`/`_ir.INDEX`）——
          那种情况 lambda 体里要读当前行，展成推导式后 `R` 的作用域变了；
        * 函数体里出现 `$position`（`self._index_var` 的语义依赖回调的位置参数）；
        * `$reduce`（累加形态无法写成推导式）；`$sort` 带比较器（同）。
        """
        if fname not in ("$map", "$filter"):
            return None
        if len(args) != 2 or args[1] is not lam:
            return None
        params = list(lam[1])
        # 回调最多收 (v, i)；`$map`/`$filter` 的助手取 `f(v, i)`。
        if len(params) < 1 or len(params) > 2:
            return None
        body = lam[2]
        if _has_free_data_ref(body):
            return None
        if _mentions_position(body, params):
            return None
        var = params[0]
        idx = params[1] if len(params) > 1 else None
        arr = self.emit(args[0])
        # 发射函数体：形参名原样可用（它们就是推导式里的循环变量）。
        saved = dict(self._params)
        saved_index = self._index_var
        self._params.update({p: p for p in params})
        # 第二形参（索引）在新形态里没有来源 —— 若函数体用到它就放弃。
        if idx is not None:
            self._index_var = idx
        try:
            text = self.emit(body)
        finally:
            self._params = saved
            self._index_var = saved_index
        if fname == "$map":
            return f"[{text} for {var} in {arr}]"
        if _bool_valued(body):
            # 函数体本身就是比较/布尔运算（只可能返回 True/False/undefined），
            # `_tru()` 对这三种输入恒等 —— 少一层 Python 调用。实测
            # `[v for v in a if _tru(_gt(v,1))]` 0.40 -> `if _gt(v,1)` 0.28
            # （1.43x），10 个边界用例（undefined/None/str/bool 参与比较）布尔
            # 语义逐项一致。
            return f"[{var} for {var} in {arr} if {text}]"
        return f"[{var} for {var} in {arr} if _tru({text})]"

    def rfunc(self, qual: str, args) -> str:
        if qual not in self.rfuncs:
            self.rfuncs.append(qual)
        return "_x(" + repr(qual) + "," + \
            ",".join(self.emit(a) for a in args) + ")"

    def pyseg(self, source: str, names=None, root: str = "R") -> str:
        """反引号段：链解析复用 `split_chain`（§3.7），与 JSONata 路径同一实现。

        `names` 优先用 IR 里已经算好的自由名（PY 节点 = `('py', src, free, loc)`），
        避免就同一段源码再 `analyze_py_segment` 一次 —— §4.2 规则 ⑥「每次 build 只
        compile 一次」：重复解析会让产物校验以外的 compile 次数翻倍。

        `root` 是段内裸 `$` 的落点：行语境（字段 expr / 反引号段）是 `"R"`；
        `output.before` / `output.after` 的整体数据语境是 `"D"`（task-13）。
        """
        src, free = analyze_py_segment(source)
        if names is None:
            names = free
        text = src
        chain_names: set = set()
        for m in FIELD_REF.finditer(src):
            chain_names.add(m.group(1))
            text = text.replace(m.group(0),
                                self.emit_path(tuple(split_chain(m.group(1)))), 1)
        # 反引号段里的裸 `$` 是当前行本身（JSONata 语境）。Python 里写不出裸 `$`，
        # `analyze_py_segment` 校验时会换掉，但它返回的 `src` 是**原样**的，这里再换一次。
        # 必须在 `$.链` 全部替换成路径取值之后再换：先换会得到 `__jx_row.5.a.1`，
        # FIELD_REF 再也匹配不上 `$.5.a.1`，产物里留下非法语法（task-12 回归）。
        #
        # 只换**后面不是标识符字符**的 `$`（task-14）：`$upper(x)` 里的 `$` 紧跟 `upper`，
        # 那是变量的简写形态（jx 已在分析期拒绝），不是「当前行」；换成占位符会与相邻
        # 标识符粘成 `__jx_rowupper` 这种不存在的名字。独立 `$`（后面是括号、运算符、
        # 空白或串尾）才是当前行，换完前后都有词边界，永远不会粘上标识符。
        # 换完直接用占位符顶替（不留空格）：`?<![A-Za-z0-9_]$` 已保证前一个字符
        # 不是标识符字符，后一个字符也不会被粘上（`$` 后紧跟标识符是变量简写形态，
        # 见上一条注释，不参与替换）。留空格会破坏 `[r for r in $ if ...]` 这类
        # 紧邻字面量的写法（产物里出现双空格，断言与可读性都变差）。
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape('$')}",
                      _PY_ROW_PLACEHOLDER, text)
        for name in names:
            # `$.data` 这类**单段链**在 analysis 里与裸名 `data` 同形（链文本不含 "."），
            # 但它已经在上面按链替换掉了；若不跳过，codegen 会把它当未声明字段报错。
            # （`dict($.data)[...]` / `len($.data)` 这类动态取键正是靠这一条才成立。）
            if name in chain_names:
                continue
            if "." in name or name.isdigit() or not name.isidentifier():
                continue
            if name in self.locals:
                repl = self.locals[name]
            else:
                repl = self.field_name(name)
            text = re.sub(r"(?<![\w.])%s(?![\w])" % re.escape(name), repl, text)
        return text.replace(_PY_ROW_PLACEHOLDER, root)

    @staticmethod
    def lit(v) -> str:
        return repr(v)

    # ---------------------------------------------------- py{} 多语句块（§3.7）
    def pyblock(self, node) -> str:
        """`("pyblock", stmts_source, [free...], loc)` -> 命名辅助函数的调用式。

        契约 §3.7 / Lead m01624：多语句块是**语句序列**，lambda 装不下（body 只能是
        表达式，多语句必然 `SyntaxError`）；因此为每个块在 `run()` **之外**生成一次

            def _pyN(_p0,_p1):
                <stmts_source>

        调用处以**已解包**的取值表达式传参。`_pyN` 按源码出现顺序编号（首次遇到该块时
        分配；同块复用同一个函数，禁止 random）。`free` 里的 `$.链` 在块体内换成形参名
        —— 参数是值，不是路径，块体不再做运行期取值。
        """
        stmts, free = node[1], list(node[2])
        loc = node[3] if len(node) > 3 else None
        key = (stmts, tuple(free))
        fn = self._pykeys.get(key)
        takes_root = False
        if fn is None:
            params = ["_p%d" % i for i in range(len(free))]
            # `_pyN` 是**模块级**函数，看不到 `run()` 里的 `R`/`D` 局部名；块内裸 `$`
            # 因此必须走形参：无论行内块还是数据级块都一样（task-13）。
            takes_root = _pyblock_stmts_use_dollar(stmts)
            root_param = None
            if takes_root:
                root_param = "_p%d" % len(params)
                params.append(root_param)
            body = self._subst_pyblock(stmts, free, params, loc)
            body = self._expand_backticks_in_block(
                body, root=root_param or self._data_var)
            self._check_pyblock(body, free, loc)
            fn = "_py%d" % len(self.pyblocks)
            self._pykeys[key] = fn
            self._pyroot_args[key] = takes_root
            lines = ["def %s(%s):" % (fn, ",".join(params))]
            lines += [" " + ln for ln in body.splitlines() if ln.strip()]
            self.pyblocks.append("\n".join(lines))
        else:
            takes_root = self._pyroot_args.get(key, False)
        args = [self.emit_path(tuple(split_chain(f))) for f in free]
        if takes_root:
            args.append(self._data_var)
        return "%s(%s)" % (fn, ",".join(args))

    def _expand_backticks_in_block(self, body: str, root: str) -> str:
        """把块体里的反引号段 `` `EXPR` `` 就地展开成它的 Python 形态。

        块内允许写反引号段（`py{ return [r for r in `$`] }`，task-13）：段内是
        **正规反引号段语法**（裸 `$` = 当前行/整份数据本身，§3.3），由 :meth:`pyseg`
        落到 `root` 这个名字上（行内块是 `R`，数据级块是传进来的形参名）。
        不展开的话反引号会原样进产物，`compile()` 直接 `SyntaxError`。
        """
        if "`" not in body:
            return body
        out = []
        i = 0
        n = len(body)
        while i < n:
            c = body[i]
            if c == "\\" and i + 1 < n and body[i + 1] == "`":
                out.append("`")
                i += 2
                continue
            if c == "`":
                end = body.find("`", i + 1)
                while end > 0 and body[end - 1] == "\\":
                    end = body.find("`", end + 1)
                if end < 0:                  # 未闭合：原样留着让 ast 报错
                    out.append(body[i:])
                    break
                out.append(self.pyseg(body[i + 1:end], root=root))
                i = end + 1
                continue
            out.append(c)
            i += 1
        return "".join(out)

    def _check_pyblock(self, body: str, free, loc) -> None:
        """build 期校验：合法语句序列 + **恰好一个** return + free 链可解析。

        传进来的是**已换形参**的块体（合法 Python）；`$.链` 的首段若是标识符，
        校验它**在已声明字段里**（未声明 -> `SchemaError` 并列出已声明字段）；块内读
        原始行，因此不要求该字段已算完（不构成依赖边）。路径长度不限。
        """
        try:
            tree = ast.parse(body, mode="exec")
        except SyntaxError as e:
            raise Unsupported(
                "py{} 代码块语法错误", loc,
                f"{e.msg}（语句序列 mode=exec 校验）") from None
        top = [n for n in tree.body if isinstance(n, ast.Return)]
        every = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
        if not top:
            raise Unsupported(
                "py{} 代码块缺少 return", loc,
                "块的值就是结尾那条 `return EXPR`（契约 §3.7 第 5 条）")
        if len(top) != 1 or len(every) != 1:
            raise Unsupported(
                "py{} 代码块有多个 return", loc,
                "块的返回值只能有一处（契约 §3.7 第 5 条）")
        # 行内 py{} 的 `$.链` 首段必须是已声明字段（裸名一定写错成字段引用）。
        # 数据级钩子（`before`/`after`）里的 `$` 是**全量数据本身**，`$.rows` 只是
        # 数据里的一个键，没有「已声明字段」这个概念，故豁免（task-13）。
        if self._data_var == "R":
            for f in free:
                segs = tuple(split_chain(f))
                if segs and isinstance(segs[0], str) \
                        and segs[0] not in self.fields:
                    raise SchemaError(
                        self._fpath(),
                        f"裸名 {segs[0]!r} 不是本次 schema 声明的字段；已声明: "
                        f"{sorted(self.fields)}（块内的 `$.链` 只能读已声明字段；"
                        f"要读任意行键请写 $['{segs[0]}']，它按字面键取值）")

    def _subst_pyblock(self, stmts: str, free, params, loc) -> str:
        """块体内的 `$.链` -> 该块形参名（同一链多次出现映射到同一形参）。"""
        idx: dict = {}
        for i, f in enumerate(free):
            idx.setdefault(f, i)
        out: list = []
        pos = 0
        for m in FIELD_REF.finditer(stmts):
            out.append(stmts[pos:m.start()])
            i = idx.get(m.group(1))
            if i is None:
                raise Unsupported(
                    f"py{{}} 块内出现未登记的 `$.` 链 {m.group(1)!r}", loc,
                    "块体与 free_field_names 不一致（jx 发射缺陷）")
            out.append(params[i])
            pos = m.end()
        out.append(stmts[pos:])
        return "".join(out)

    # ---------------------------------------------------- 字段装配
    def field_value(self, name: str, spec: dict) -> str:
        if "const" in spec:
            base = self.lit(spec["const"])
        elif "value" in spec:
            vpath = tuple(spec["value"])
            # 取值形态三选一（用户 m02826/m02838/m02850 裁决）：
            # * 不写 `optional` -> 裸下标，缺键/类型不符由用户承担 `KeyError`
            #   （性能模式：不保护是用户的显式选择）；
            # * 写 `optional` + 类型可判定（声明了 int64/.../list/object 之一，
            #   不是 `any`）-> `dict.get(k, _U)` 链：内建调用、无 helper，
            #   缺键兜底成哨兵，比 `_ix_get` 链快约 2.3 倍；
            # * 写 `optional` + `any`/未声明类型 -> `_ix_get` 链（完全 undefined 安全）。
            # * 声明 `any` / 未声明类型（无论是否 optional）-> `_ix_get` 链（完全 undefined 安全）。
            #   架构文档 §7.5「读值形态」：`type: "any"` 时走 `_ix_get` 安全链（缺键 -> undefined）。
            #   `any` 的承诺就是「什么都可能是 undefined」，所以连 required 也不能裸读死。
            _opt_only = bool(spec.get("optional")) and "default" not in spec
            _typed = spec.get("type") not in (None, "any")
            if _opt_only and not self.safe:
                base = self.get_text(vpath) if _typed else self.safe_text(vpath)
            elif not _typed:
                # `any`/未声明：没有类型可证，缺键必须落 undefined 而不是 KeyError
                base = self.safe_text(vpath)
            else:
                base = self.emit_path(vpath)
        elif "expr" in spec:
            # 与 `value` 分支同一条判定（§2.5：同一 schema 两种写法不得语义分叉）：
            # `expr` 本身不决定取值形态，取哪几个字段由表达式里的路径决定，所以
            # 这里只能靠 `self.safe` 这一个开关传给 `emit()` 的常量链分支 ——
            # optional/default 打开安全模式（整条链 `_ix_get` 或 `.get`），
            # required 关闭（裸链，缺键由用户承担）。`expr` 若是多条路径的复合
            # 表达式（如 `$.a + $.b`），每条路径各自按该开关处理。
            base = self.emit(self._expr_node(spec))
        elif "fields" in spec:
            return self._emit_object(self.cur_field or name, spec)
        else:  # pragma: no cover - validate() 保证 value/const/expr/fields 恰有其一
            raise SchemaError(self._fpath(),
                              "字段缺少 value/const/expr/fields（schema 未校验？）")
        if "default" in spec:
            return f"_df({base},{self.lit(spec['default'])})"
        return base

    def _read_safe(self, spec: dict) -> bool:
        """读这一格**本身不会抛**（缺键不会 KeyError/TypeError），可以进字面量。

        与 `_never_undef` 的分工：那条回答「值不会是哨兵」，这条回答「读得到」。
        字面量（dict/list literal）没有兜底能力，两条必须同时成立才敢用；否则
        一律退回 `_ob`/`_rl`/`_ar` helper，由它执行「跳过 undefined / 落 None」。

        判定按**取值形态**，与 `field_value` 的三选一完全一致：
        * `optional` 无 `default` -> `dict.get`/`_ix_get` 链兜底  => 安全
        * `default` -> `_df` 兜底                                => 安全
        * 否则（required / `any` / 未声明）-> 裸下标 `R['k']`    => **不安全**
        * `const` -> 字面量                                      => 安全
        * `fields` -> 递归看每个子字段                            => 全安全才安全
        * `expr` -> 表达式里只要有裸字段路径就不敢保证             => 保守判不安全
          （`$exists($.a)` 之类虽然内部吞了缺键，但静态证明不出来；退回 helper
           只是慢一点，语义完全不变，符合「宁可慢不可错」。）

        注意：`validate()` 会把 `optional` 规范化成 `optional: False`（键**始终
        存在**），所以这里绝不能写 `if spec.get("optional")` 之外的「键存在即安全」
        判定；反之 `value` 分支必须显式判 `optional` 的真假。
        """
        if "default" in spec:
            return True
        if spec.get("optional") is True:
            return True
        if "const" in spec:
            return True
        if "fields" in spec:
            # 嵌套 object/位置行：这两条路一旦走字面量，就要求**每个子字段**都
            # 既能取到又被包住，逐层递归判定；任何一层不安全就整格退回 helper。
            return all(self._read_safe(s) for s in spec["fields"].values())
        if "value" in spec:
            # `value` 分支的实际读法由 `field_value` 决定，与声明的 type 有关：
            # * required + 具体类型 -> 裸下标 `R['k']`（缺键由用户承担 KeyError），
            #   但**能取到**这一格本身不抛；字面量包裹不会引入新的抛点，
            #   故判安全（`{'x':R['a']}` 比 `_ob([('x',R['a'])])` 省一次 helper 调用）。
            # * `type:"any"` / 未声明 -> `field_value` 发 `safe_text()`（`_ix_get` 链），
            #   值可能是哨兵，必须由 `_never_undef` 那条拦住；此处同样按安全判，
            #   真正的「会不会是哨兵」由 `_never_undef` 回答——两条判定分工不重叠。
            # 唯一真正的「读不到」风险是 optional 无 default，但那种情况
            # `field_value` 走 `get_text`/`safe_text` 兜底，上面已返回 True。
            return True
        if "expr" in spec:
            return not self._expr_has_path(spec)
        return True

    def _expr_has_path(self, spec: dict) -> bool:
        """`expr` 里是否出现 `$.路径`（读原始行）。出现即认为「可能抛」。"""
        node = self._expr_node(spec)
        if node is None:
            return False
        found = []

        def walk(n):
            if not isinstance(n, tuple) or not n:
                return
            if n[0] in (_ir.FIELD, _ir.INDEX, _ir.ROOT):
                found.append(1)
                return
            for x in _ir.children(n):
                walk(x)
        walk(node)
        return bool(found)

    def _emit_object(self, prefix: str, spec: dict) -> str:
        """嵌套 object 的取值：**声明顺序**组装，字段按**作用域内**依赖拓扑序求值。

        嵌套 `fields` 是一个独立作用域：同一层的兄弟字段可以按裸名互相引用（前向
        引用合法、成环报错），也可以引用外层字段；内层同名字段遮蔽外层。被引用的
        字段先算并落成行循环里的局部名（每字段最多求值一次）。

        `shape:"dict"` 时这里返回的是**一行的 dict**（子 `fields` 也各自是一行），
        聚合在 :meth:`_aggregate` 里逐层做 —— 嵌套的 `list`/`object` 子字段因此
        与顶层字段一样「每个键一列」。
        """
        fields = spec["fields"]
        names = set(fields)
        deps = {}
        for k, s in fields.items():
            deps[k] = (self._names(s, set()) & names) - {k}
        order = self._stable_topo(deps, list(fields),
                                  f"out.fields.{prefix}.fields")
        wanted = set().union(*deps.values()) if deps else set()
        scope: dict = {}
        prev = (self.cur_field, self.cur_key)
        self._nested_scopes.append(scope)
        self._nested_specs.append(fields)
        try:
            for k in order:
                s = fields[k]
                self.cur_field = f"{prefix}.fields.{k}"
                self.cur_key = next((x for x in self._KEYS if x in s), "")
                if "default" in s and "fields" not in s:
                    # 嵌套 default 字段同样要能拿到 undefined（缺键 -> 默认值）
                    self.safe += 1
                    try:
                        expr = self.field_value(k, s)
                    finally:
                        self.safe -= 1
                else:
                    # 与顶层字段同一条判定（见 `body()` 的 `_safe_read`）：
                    # 取值形态是 `expr` / `value` 且类型不可判定（`any`/未声明）时，
                    # 嵌套作用域也必须在 safe 模式下求值 —— 否则 `$.x` 会发裸下标，
                    # 缺键在求值阶段就抛 KeyError，`_ob`/`_rl` 的兜底根本没机会执行。
                    _safe_nested = ("value" not in s
                                   or s.get("type") in (None, "any"))
                    if _safe_nested:
                        self.safe += 1
                    try:
                        expr = self.field_value(k, s)
                    finally:
                        if _safe_nested:
                            self.safe -= 1
                if k in wanted and not _TRIVIAL.match(expr):
                    # 被兄弟字段引用、且值不是「单个局部名」：落临时名，避免重复求值。
                    # 值本身已经是个局部名（`_a1`）时不再造中转 —— 两个名字指向同一个
                    # 值，与 `_unbind_trivial` 对顶层字段用的是同一条规则。
                    t = self.tmp()
                    self._defs.append(f"{t}={expr}")
                    scope[k] = t
                else:
                    scope[k] = expr
            # 可证性要在**本层作用域仍在栈上**时判：py 段里的裸名可能指向本层兄弟
            flat = all(self._never_undef(fields[k]) for k in fields)
        finally:
            self._nested_scopes.pop()
            self._nested_specs.pop()
            self.cur_field, self.cur_key = prev
        # `_never_undef` 只回答「值不会是哨兵」，**不回答「路径读得到」**：
        # `{"type":"any","value":"$.a"}` 声明了 any 却因为「有 value 键」被判成
        # 可证非 undefined，字面量一包，缺键的直接下标就 KeyError。判定
        # 「这一格能不能进字面量」必须同时满足两条：
        #   ① 值不是哨兵（`_never_undef`）；
        #   ② 取这个值的动作本身不会抛（`_read_safe`）。
        flat = flat and all(self._read_safe(fields[k]) for k in fields)
        if spec.get("type") == "list":
            # 位置行（type:"list" + fields）：按声明顺序（不是计算顺序）排成 list。
            # 全字段可证非 undefined 时直接发列表字面量（CPython 在 C 层构建）；
            # 只要有一个可能 undefined，仍由 `_rl` 把它落成 None 保位。
            if flat:
                return "[" + ",".join(scope[k] for k in fields) + "]"
            return "_rl([" + ",".join(scope[k] for k in fields) + "])"
        # 具名对象：可证全非 undefined 时发字典字面量（`_ob` 每行要 tuple(args[0])
        # + 逐项 is not UNDEF，是热路径上的主要开销）；否则保留 `_ob` 的「丢键」语义。
        if flat:
            return "{" + ",".join(f"{k!r}:{scope[k]}" for k in fields) + "}"
        return "_ob([" + ",".join(
            f"({k!r},{scope[k]})" for k in fields) + "])"

    def _aggregate(self, names, specs=None, rows: str = "_o",
                   depth: int = 0, tab=None) -> str:
        """`shape:"dict"` 下的聚合表达式：每个字段一列。

        `rows` 是**行流**（每行 `[值...]`）。第一层按字段声明序取第 i 格；再往下
        已是「该格的列」，按子键名取值。

        `object` 字段要不要继续展开成子键列，由 schema 上的 `aggregate` 说了算
        （`dcg/schema.py` 的 `FIELD_KEYS`）：声明了就是这个字段的每个子键各是一
        列，没声明就是「该键下是一列具名行」，整列原样输出。两种写法结构上
        无从分辨，只能由声明者标记。

        `tab` 是「列表达式 -> 局部名」的记忆表：同一列在递归里被多个子键共用
        （如 `stats.deep` 的两个子键都要先取 `stats` 这一列），不记忆就会把
        `[r[6]for r in _o]` 原地展开好几遍，产物又长又难读。命中的列由调用方
        提前算好一份（见 :meth:`_shared_columns`），这里只发名字。查找**必须
        用未替换的原始表达式**，所以先查表、再替换 `col`。
        """
        names = list(names)
        if specs is None:
            specs = [self.fields[n] for n in names]
        if self._flat_row():
            return "{" + repr(names[0]) + ":" + rows + "}"
        cells = []
        # 行槽位映射：`body()` 拼 `_c([...])` 时**跳过了直出 aggregate 字段**
        # （见 :meth:`_direct_aggregate`、:meth:`_flat_row`），所以行里第 k 格
        # 对应的是「第 k 个非直出字段」，不是第 k 个声明字段。用声明序位取列就是
        # DEFECT-001：`stats` 直出后 `c2` 声明序 3、实际只占第 2 格 -> `r[3]`
        # 越界 IndexError。depth==0 时必须按行槽位重映射。
        slot = {}
        if depth == 0:
            k = 0
            for n in names:
                if not self._direct_aggregate(self.fields[n]):
                    slot[n] = k
                    k += 1
        for i, (n, s) in enumerate(zip(names, specs)):
            if depth == 0 and n not in slot:
                # 该字段是直出 aggregate：行容器里根本没有它的格子，`body()` 已经
                # 把它跳过。列表达式必须从**原始行** `_d` 逐子键求值（B.8-3），
                # 不能走 `[r[k]for r in _o]`。
                sub = list(s["fields"])
                subspecs = [s["fields"][k] for k in sub]
                direct = self._aggregate_direct(sub, subspecs, "_d")
                cells.append(direct if direct is not None else "{}")
                continue
            raw_col = f"[r[{slot[n]}]for r in {rows}]" if depth == 0 else \
                      f"[r[{n!r}]for r in {rows}]"
            col = raw_col
            if tab is not None and col in tab:
                col = tab[col]
            if depth and _is_const(s):
                # 嵌套里的常量：每行同值，只留一份（与顶层常量列的口径一致）
                cells.append(f"{col}[0]")
            elif depth == 0 and "const" in s and isinstance(s["const"], list):
                # 顶层 list 常量：值本身就是那一列，不再包一层
                cells.append(self.lit(s["const"]))
            elif s.get("aggregate") and s.get("type") == "object" \
                    and "fields" in s:
                # 列容器：子键各一列。子键里那些**本身就是一列行**的 object
                # （不标 aggregate 的，如 docsx.B.B2）仍然只取一格。
                sub = list(s["fields"])
                subspecs = [s["fields"][k] for k in sub]
                direct = None if depth else self._aggregate_direct(
                    sub, subspecs, "_d")
                if direct is not None:
                    # 整层子键都能**逐列独立求值**：直接发子列表达式，
                    # 不必每行先造一个 dict 再 `[r[i]for r in _o]` 捞回来。
                    cells.append(direct)
                else:
                    # 子层的行流就是本层的 `col`：若它已被提升成短名（`_a`/`_a1`），
                    # 子层直接复用这个名字，不要再把 `[r['k']for r in _a]` 里
                    # 那串 `[r[6]for r in _o]` 原地展开一遍（嵌套 3 层就会把同一
                    # 行流扫 3 遍）。只在本层列确实命中 `tab` 时启用。
                    sub_rows = tab[col] if (tab is not None and col in tab) else col
                    cells.append(self._aggregate(
                        sub, subspecs, sub_rows, depth + 1, tab))
            else:
                cells.append(col)
        return "{" + ",".join(f"{n!r}:{c}" for n, c in zip(names, cells)) + "}"

    def _aggregate_direct(self, names, specs, row_of: str = "_o"):
        """aggregate 对象的子键能否**绕开行容器**直接逐列求值。

        能绕开时返回该对象的列表达式，否则返回 None。

        背景：`aggregate` 字段过去总是先把整行里那一格造成 `_ob([...])` /
        字典字面量，再用 `[r[i]for r in _o]` 把它捞回来逐子键拆列 —— 每行多造
        一个 dict、再全表扫一遍。

        `row_of` 是**本层子键所在的容器列**（行容器里那一格：`[r[6]for r in _o]`）。
        子键的列在它上面取值，而不是在原始行 `_o` 上。

        放行条件（保守）：
        * 子键是 `const`（每行同值 -> 直接发字面量，不需要行容器）；
        * 子键是单段 `value`（`"$.k"` -> `[r['k']for r in <row_of>]`）；
        * 子键是 `expr`，且不引用兄弟字段名、也不引用任何已声明字段名
          （列级求值时没有这些局部名，只有 `r`）。

        其余形态一律返回 None、退回老路（行容器 + `[r[i]for r in _o]` 转置）。

        **`optional` 的 `value` 子键也放行**（DEFECT-012）：`_col_value` 的
        `value` 分支无条件发 `_ix_get(r,'k')` —— 那正是 optional 需要的缺键
        守卫（缺键 -> undefined），列级求值与行循环里 `field_value` 的
        optional 分支**逐值等价**。修前一律拒绝放行，于是退回「每行造
        `_ob([...])`」再把 `[r['k']for r in _a]` 捞回来：不仅慢 6.20×，而且
        `_ob` 会把 undefined 键**丢掉**，捞的时候 `KeyError: 'lvl'` —— 缺键
        的 optional 子键直接崩，而不是按契约落 `None`。
        `optional` 的 `expr` 子键仍不放行：它的守卫依赖 `field_value` 里按
        取值形态打开的 `safe` 标志，列级求值的 `safe_expr` 是另一条规则，
        两边不等价的组合不值得为这点性能冒险。
        """
        if not specs:
            return None
        known = set(self.locals) | set(names)
        for s in specs:
            if "fields" in s:
                return None
            if s.get("optional") and "value" not in s:
                return None
            if "const" in s:
                continue
            if "value" in s:
                if len(s["value"]) != 1:
                    return None
                continue
            if "expr" not in s:
                return None
            if self._expr_names(s) & known:
                return None
        parts = []
        for n, s in zip(names, specs):
            if "const" in s:
                parts.append(f"{n!r}:" + self._col_const(s, row_of))
            else:
                parts.append(f"{n!r}:" + self._col_value(s, row_of=row_of))
        return "{" + ",".join(parts) + "}"

    def _col_const(self, spec, row_of: str = "_o"):
        """`const` 子键的形态：每行同值，只留一份。

        与 `_aggregate` 里 `depth and _is_const(s)` 分支（发 `col[0]`）**同一口径**：
        常量列不是「每行一份的数据」，README §1.1 与附录 A.8.5 都写「`count` 是常量，
        只留一份」。此前这里发 `[1 for r in _o]` 整列，同一 schema 经「直出列」与
        「行容器转置」两条路径会得到 `{'one':[1,1]}` vs `{'one':1}` 两种结果 ——
        §2.5「同一 schema 两种形状语义不得分叉」的违反。
        """
        val = spec["const"]
        if isinstance(val, list):
            return self.lit(val)
        return self.lit(val)

    def _expr_names(self, spec):
        out = set()
        if "expr" in spec:
            self._node_names(self._expr_node(spec), out)
        return out

    def _col_value(self, spec, key=None, row_of: str = "_o"):
        """把「每行一个值」的表达式改成「整列」表达式：`X` -> `[X for r in <row_of>]`。

        `emit()` 发的是带占位符 `_TOKEN` 的文本，必须立刻 `subst()` 解析掉，
        否则占位符会原样进产物（`U+0001` 直接是语法错误）。

        `row_of` 是**原始行流**（顶层和 aggregate 子键都是 `_o` 的元素）：
        `value` 路径与 `expr` 里的 `$.x` 都指原始行，与行循环里 `$.x` 的语义一致。
        子键 `value`（`"$.k"`）在语义上也是原始行 `$.k`，不是容器 dict 的键。
        """
        saved, self.safe = self.safe, 0
        saved_raw, self.raw = self.raw, None
        # 列级求值发生在 `return` 之后：那里**看不到**行循环里提升的局部名
        # （`_fid`/`_arr` 等只在 `for R in _d` 体内有效，出循环后保留的是最后
        # 一次迭代值）。所以裸字段引用必须重新落回原始行取值，不能发局部名。
        # 数据根的形参同时切成 `r`：列表达式的循环变量是 `r`，不是行循环的 `R`。
        saved_locals, self.locals = self.locals, {}
        saved_binds, self._binds = self._binds, {}
        saved_var, self._data_var = self._data_var, "r"
        # `expr` 的路径读取是否走安全模式，必须与 :meth:`field_value` 的 `value`
        # 分支用**同一条规则**：只有 required 且类型可判定时才裸读（缺键是真实的
        # 数据错误），`any`/未声明类型一律 `_ix_get`（缺键 -> undefined，交给
        # `_ob`/`_ar`/`_rl` 执行「跳过 undefined」语义）。
        #
        # 修前这里无条件把 `self.safe` 置 0，于是 `{"type":"any","expr":"$.a"}`
        # 的列表达式发的是裸 `r['a']`：缺键抛 KeyError，而且**同一份 schema 在
        # 行模式与列模式下语义分叉**（行循环里走 `_ix_get`，聚合列里走裸下标）。
        # 这条分叉正是组合矩阵（形状 × 取值形态 笛卡尔积）要抓的缺陷类型。
        safe_expr = "value" not in spec and (
            spec.get("type") in (None, "any")
            or (spec.get("optional") and "default" not in spec))
        try:
            if "value" in spec:
                # 列级求值基座是原始行 `r`。
                #
                # **required 发裸链、optional 发 `_ix_get` 链** —— 与行循环里
                # `field_value` 的 `value` 分支同一条口径（m02209 裁决）：
                # * required（未声明 optional）：没有 undefined 出口，缺键/越界都是
                #   真实异常，用户承担 —— 裸 `r[0]` 最快（实测 6 列 0.3388 vs
                #   0.4191 µs/行，`_ix_get` 每格多花 24%）；
                # * optional / 有 default：整条链 `_ix_get`（缺键 -> undefined），
                #   再按有无 default 决定落 `None` 还是默认值 —— 有 default 的
                #   字段**必须**能拿到 undefined 才能落默认值（`_df`），所以
                #   不能裸读（裸读缺键直接 KeyError，默认值永远不生效）。
                path = spec["value"]
                opt = bool(spec.get("optional")) or "default" in spec
                # 单段 + 无 default：与行模式 `field_value` 的 `get_text` 同口径 ——
                # 名字键用 `r.get(k,_U)`（实测 0.0538 vs 0.0635 µs/行，快 15%），
                # 整数下标不能 `.get`，退回 `_ix_get`。有 default 时必须走
                # `_ix_get` 链由 `_df` 落地，不再特殊化。
                #
                # **只在「行一定是 dict」时才敢用 `.get`**：输入行可能是位置式
                # tuple（`input.source` 为 `$` 时数据是 list of tuple），tuple
                # 没有 `.get`。`self._rows_mapping()` 由 `input.source` 与字段
                # 声明共同判定（与 `body()` 里 `get_text` 的门槛一致）。
                _single = (len(path) == 1 and isinstance(path[0], str)
                           and self._rows_mapping())
                if path:
                    first = repr(path[0])
                    if not opt:
                        text = f"r[{first}]"
                    elif _single and "default" not in spec:
                        text = f"r.get({first},_U)"
                    else:
                        text = f"_ix_get(r,{first})"
                else:
                    text = "r"
                for k in path[1:]:
                    text = "_ix_get(%s,%r)" % (text, k)
                if "default" in spec:
                    text = f"_df({text},{self.lit(spec['default'])})"
                elif opt:
                    # optional 无 default：缺键落 `None`（契约 §2.3），不是 undefined。
                    # 行循环里由 `body()` 发 `x=None if t is _U else t` 完成这一步；
                    # 列级求值没有那条语句，必须在这里补同样的守卫，否则
                    # `{"optional":true,"value":"$.6"}` 在 aggregate 里会漏出
                    # undefined 哨兵（普通 optional 列发的是 `[None]`，两条路径
                    # 必须逐值相等 —— DEFECT-012）。
                    #
                    # 用海象运算符**只求值一次**：`None if (_v:=E) is _U else _v`
                    # 与 `None if E is _U else E` 逐值等价，但省掉一半 `_ix_get`
                    # 调用（每个元素 80ns，在 10 万行上是百毫秒级）。项目要求
                    # Python >= 3.10，海象在 3.8 起就有；`_v` 用了 `_` 前缀，
                    # 与产物里其它 `_t1`/`_a` 一类临时名同一约定，且不会与
                    # `_RESERVED` 里任何 helper 撞名（推导式作用域内也只看得到它）。
                    text = f"None if (_v:={text}) is _U else _v"
            else:
                if safe_expr:
                    self.safe = saved + 1
                text = self.subst(self.emit(self._expr_node(spec)))
        finally:
            self.safe = saved
            self.raw = saved_raw
            self.locals = saved_locals
            self._binds = saved_binds
            self._data_var = saved_var
        return "[" + text + " for r in " + row_of + "]"

    def _row_value(self, spec) -> str:
        """单行内取一个字段的值（`shape="rows"` 的无行容器快路径用）。

        `_col_value` 生成的是**整列推导式** `[<单值> for r in _d]`，这里要的是
        括号里的 `<单值>` 本体。做法：让 `_col_value` 对 `_d` 跑一遍，再剥掉
        外层 `[... for r in _d]`。**不做字符串魔法**——用递归的 `_single_value`
        重新走一遍取值规则，保证与列级路径逐字符同源（同一处代码决定形态，
        不会出现「行路径改了列路径忘了改」的分叉，这正是 DEFECT-012 的成因）。
        """
        saved = (self.safe, self.raw, self.locals, self._binds, self._data_var)
        saved_binds, self._binds = self._binds, {}
        saved_var, self._data_var = self._data_var, "r"
        try:
            if "value" in spec:
                path = spec["value"]
                opt = bool(spec.get("optional")) or "default" in spec
                _single = (len(path) == 1 and isinstance(path[0], str)
                           and self._rows_mapping())
                if path:
                    first = repr(path[0])
                    if not opt:
                        text = f"r[{first}]"
                    elif _single and "default" not in spec:
                        text = f"r.get({first},_U)"
                    else:
                        text = f"_ix_get(r,{first})"
                else:
                    text = "r"
                for k in path[1:]:
                    text = "_ix_get(%s,%r)" % (text, k)
                if "default" in spec:
                    text = f"_df({text},{self.lit(spec['default'])})"
                elif opt:
                    text = f"None if (_v:={text}) is _U else _v"
                return text
            safe_expr = "value" not in spec and (
                spec.get("type") in (None, "any")
                or (spec.get("optional") and "default" not in spec))
            if safe_expr:
                self.safe = saved[0] + 1
            return self.subst(self.emit(self._expr_node(spec)))
        finally:
            (self.safe, self.raw, self.locals, self._binds, self._data_var) = saved

    def _aggregate_plan(self, names, specs):
        """`shape="dict"` 递归聚合里值得提到 `return` 之前预算的列。

        `_aggregate` 递归时会把父列表达式**原地内联**进每个子键，所以一个
        aggregate 对象有 N 个子键时，父列那串 `[r[6]for r in _o]` 就被展开 N 次，
        再套一层就 N×M 次。这里只收**被再次用作行流的父列**（`nchild >= 2`
        才提；只被自己那一个子键用一次的列，原地写更短更好读）。
        返回 `[(列表达式, 变量名), ...]`，短名按「短的先算」排序 —— 父列的
        表达式一定是子列表达式的前缀片段，长度天然更短。
        """
        plan = []

        def walk(ns, sp, rws, d, tab):
            for i, (n, s) in enumerate(zip(ns, sp)):
                if not (s.get("aggregate") and s.get("type") == "object"
                        and "fields" in s):
                    continue
                col = f"[r[{i}]for r in {rws}]" if d == 0 else \
                      f"[r[{n!r}]for r in {rws}]"
                sub = list(s["fields"])
                subspecs = [s["fields"][k] for k in sub]
                # 整层子键都能绕开行容器的 aggregate 对象：行容器里根本没有
                # 它那一格（见 `body()`），`_aggregate` 直接从 `_d` 逐列求值。
                # 不需要短名，也不能按 `col` 登记 —— 序号也已对不上了。
                if d == 0 and self._aggregate_direct(sub, subspecs, "_d") is not None:
                    continue
                sub_tab = tab
                if len(sub) >= 2:
                    # 这层被 >=2 个子键复用：它自己先用父层的短名，再登进表
                    plan.append(tab.get(col, col))
                    sub_tab = dict(tab)
                    sub_tab[col] = "\x00" + str(len(plan) - 1)
                walk(sub, subspecs, col, d + 1, sub_tab)

        walk(list(names), list(specs), "_o", 0, {})

        # 短名按「表达式长度」升序分配 → 父列必然先于子列拿到更短的号
        order = sorted(range(len(plan)), key=lambda i: len(plan[i]))
        names_of = {}
        for rank, i in enumerate(order):
            names_of[i] = "_a" + str(rank + 1) if rank else "_a"

        def resolve(text):
            for i, real in enumerate(plan):
                text = text.replace("\x00" + str(i), names_of[i])
            return text

        out = [(resolve(real), names_of[i]) for i, real in enumerate(plan)]
        # 定义本身也能复用更短的兄弟：`_a2=[r['deep']for r in [r[6]for r in _o]]`
        # 里的父列就是 `_a`，换成短名即可（父列按长度排在前面，故一遍够）。
        for k, (col, var) in enumerate(out):
            for pcol, pvar in out[:k]:
                if pcol in col:
                    out[k] = (col.replace(pcol, pvar), var)
                    col = out[k][0]
        return out

    def collect_refs(self) -> set:
        """顶层字段里被**裸名**引用到的字段 —— 只有它们需要落成局部名。

        嵌套 `fields` 内部的兄弟引用属于该嵌套作用域（由 `_emit_object` 单独处理），
        除非引用的名字来自外层，否则不抬升成顶层依赖。
        """
        refs: set = set()
        for spec in self.fields.values():
            refs |= self._names(spec, set())
        return refs & set(self.fields)

    def _names(self, spec: dict, shadow: set) -> set:
        """递归收集 spec 里出现的裸名；`shadow` 是已进入的嵌套作用域声明的名字。

        嵌套作用域里声明过的名字在更外层不再算引用（内层遮蔽外层）；作用域内部对
        兄弟字段的引用交给 `_emit_object` 用该作用域自己的名字集合求交集。
        """
        out: set = set()
        if "expr" in spec:
            self._node_names(self._expr_node(spec), out)
        if isinstance(spec.get("fields"), dict):
            inner = set(spec["fields"])
            for s in spec["fields"].values():
                out |= self._names(s, shadow | inner)
        return out - shadow

    def _node_names(self, node, out: set) -> None:
        if isinstance(node, (tuple, list)):
            if isinstance(node, tuple) and node:
                if node[0] == _ir.LAMBDA:
                    # 函数字面量的参数是局部名，不是字段引用；只收函数体里的自由字段
                    self._node_names(node[2], out)
                    return
                if node[0] in (_ir.FIELD, _ir.VAR):
                    out.add(node[1])
                elif node[0] == _ir.PY and len(node) > 2 and isinstance(node[2], list):
                    # 反引号段：`$.链` 由 pyseg 直接发**原始行**取值，不构成依赖边；
                    # 只有裸名（不是 `$.` 链）才按字段引用处理（读计算结果、需先算）。
                    chains = {m.group(1) for m in FIELD_REF.finditer(node[1])}
                    for n in node[2]:
                        if isinstance(n, str) and n.isidentifier() and n not in chains:
                            out.add(n)
                # `py{}` 块（PYBLOCK）：`$.链` 只做「已声明字段」校验、读原始行，
                # 不记依赖边（见 4.5.1；校验在 _check_pyblock 里完成）。
            for item in node:
                self._node_names(item, out)

    def body(self):
        """按**计算顺序**（依赖拓扑序）取值；输出行仍按**声明顺序**组装（§2.5）。

        返回 (局部字段定义行, 输出行字面量)。声明顺序只决定结果结构，不决定求值
        顺序：被引用的字段先算（前向引用合法），每字段最多求值一次，成环在 build
        期报 `SchemaError`。

        **`dict` 形状的「无行容器」快路径**：当 `_columns_from_source()` 判定
        没有跨字段共享时，返回 `([], None)` —— `None` 表示「不要行循环」，
        `module()` 据此直接 `return <逐列表达式>`。每列从原始行 `_d` 独立求出，
        省掉行容器一进一出（9 列实测 0.8609 -> 0.5100 µs/行）。
        """
        if self._columns_from_source() is not None:
            # 无共享：不建行容器、不逐行求值，`_finish_expr` 直接发逐列推导式。
            # 这条路径下 `_refs` 必为空（放行条件之一），所以不存在「先算再存」
            # 的字段间依赖，`body()` 无需产出任何 defs。
            self.locals = {}
            return [], None

        defs: list = []
        values: dict = {}
        self._defs = defs          # 嵌套 fields 的字段绑定也追加到同一 row-loop

        def value(name, spec):
            mode = self.raw_mode(spec)
            old, self.raw = self.raw, mode
            prev = self._enter(name, spec)
            try:
                return self.field_value(name, spec)
            finally:
                self.raw = old
                self.cur_field, self.cur_key = prev

        for name in self._topo():
            spec = self.fields[name]
            if self._direct_aggregate(spec) and not spec.get("optional"):
                # 整层子键都能绕开行容器直出列：行里那一格不需要存在（B.8-3）。
                # `_aggregate` 在 `return` 处直接从原始行 `_d` 逐列求值。
                # 该字段不能被兄弟字段裸名引用（`_direct_aggregate` 已排除
                # 引用已知字段名的情况），所以这里不留局部名是安全的。
                continue
            if spec.get("optional") and "default" not in spec:
                # §4.2 规则 7: optional 才生成 undefined 守卫；undefined -> null
                # 取值形态由 `field_value` 按「类型是否可判定」二选一（m02826）：
                #   * `value` 且类型可判定 -> `dict.get(k, _U)` 链（内建调用，无 helper）
                #   * 其余（`any`/未声明/`expr`）-> 安全模式（`_ix_get` 链）
                # 所以只有 `_ix_get` 那条路才需要在发射期打开 safe 标志。
                #
                # **为什么 `expr` 也要打开**（2026-10 修复）：
                # optional 的承诺是「输入缺键 -> 得到 undefined -> 守卫落成 None」。
                # 但 `expr` 里的 `$.x` 在非 safe 模式下发裸下标 `R['x']`，缺键在
                # **求值阶段**就抛 KeyError，守卫根本没机会执行：
                #   `grade: {optional:true, expr:"$.score >= 90 ? 'A' : 'B'"}`
                #   修前产物 `_t1=_co(_ge(R['score'],90),'A','B')` -> KeyError('score')
                # `value` 分支有 `get_text`/`safe_text` 自己兜底，`expr` 没有，
                # 所以这里的判定必须按**取值形态**而不是只按类型是否可判定。
                # 性能：只有 optional/default 字段进这条分支，required 与不带守卫的
                # `value` 完全不受影响（仍然是裸读快路径）。
                _safe_read = ("value" not in spec
                              or spec.get("type") in (None, "any"))
                if _safe_read:
                    self.safe += 1
                try:
                    expr = value(name, spec)
                finally:
                    if _safe_read:
                        self.safe -= 1
                t = self.tmp()
                defs.append(f"{t}={expr}")
                if self.inline_optional:
                    defs.append(f"{name}=None if {t} is _U else {t}")
                else:
                    defs.append(f"{name}=None if is_u({t}) else {t}")
                self.locals[name] = name
                values[name] = name
            else:
                if "default" in spec:
                    # default 字段必须能拿到 undefined：路径求值走 _ix_get（safe 模式）
                    self.safe += 1
                    try:
                        expr = value(name, spec)
                    finally:
                        self.safe -= 1
                else:
                    expr = value(name, spec)
                if name in self._refs and not _TRIVIAL.match(expr):
                    # 被后续字段按裸名引用、且值本身不是单个局部名 -> 落成局部避免重复求值
                    defs.append(f"{name}={expr}")
                    self.locals[name] = name
                    values[name] = name
                else:
                    # 引用只此一次（或值就是个局部名）：直接内联，不造只服务 return 的中转
                    self.locals[name] = expr
                    values[name] = expr
        if self._flat_row():
            # 单一输出键：行就是**那个值**本身（同层没有别的字段可能引用它），
            # 不必包一层单元素 list 再在末尾转置。`_finish_expr` 按形状直接拼。
            # 该字段若是「直出列」的 aggregate 对象，它已被上面的 `continue`
            # 跳过（不进行容器），此处不引用它的行内值 —— 交给 `_aggregate`
            # 在 `return` 处从原始行逐列求值。
            (only,) = tuple(self.fields)
            if only in values:
                return defs, values[only]
        row = "[" + ",".join(
            values[n] for n in self.fields
            if not self._direct_aggregate(self.fields[n])
        ) + "]"
        return defs, row

    def _direct_aggregate(self, spec) -> bool:
        """该字段是不是「整层子键都能绕开行容器直出列」的 aggregate 对象。

        是的话，行容器里那一格**不必存在**：`_aggregate` 会直接从原始行 `_d`
        逐列求值，不再需要 `_ob([...])` 每行造一个 dict、再用 `[r[i]for r in
        _o]` 捞回来。这是 B.8-3 的另一半（`value`/`const` 子键）。
        """
        if self.shape != "dict":
            # 只有 `dict` 是**列式**容器，`aggregate` 的"列容器"语义才成立。
            # `list` / `index` / `rows` 是行式容器：它们的每行是一个 list / dict，
            # 没有"列"的位置可放。在那里 `aggregate` 字段的子键**展开成普通行
            # 字段**（见 `_row_fields`），逐行求值 —— 既是唯一自洽的语义，也
            # 是最快的形态（不需要为聚合再扫一遍全表）。
            #
            # 修前非 dict 形状**静默丢弃**整个 aggregate 段（`list`/`index` 少列）
            # 或 `IndexError` 崩溃（`rows` 按声明序位取 `r[i]` 越界）。
            return False
        if not (spec.get("aggregate") and spec.get("type") == "object"
                and "fields" in spec):
            return False
        sub = list(spec["fields"])
        subspecs = [spec["fields"][k] for k in sub]
        return self._aggregate_direct(sub, subspecs, "_d") is not None

    def _columns_from_source(self):
        """`dict` 形状没有行容器时，逐字段从**原始行**出列的表达式表。

        返回 `{字段名: 列表达式}`，或 `None`（不能跳过行容器）。

        **为什么可以跳过行容器**：`shape:"dict"` 的产出现在是
        `for R in _d: _c([...])` 攒行，再 `[r[i]for r in _o]` 转置成列。对一个
        9 列 schema 实测 0.8609 µs/行，而直接 `[r[0]for r in _d]` 逐列出是
        0.5100 µs/行 —— **行容器这一进一出占了 41%**。行容器存在的唯一理由是
        「同一行里多个字段共享中间结果」：`plan()` 把共用前缀提升成局部名
        （`_a`/`_a1`）、或字段按裸名互相引用（`hourB` 引用 `hourA`）。没有这些
        共享时，每列都能独立地逐行求值，行容器就是纯开销。

        放行条件（全部满足才跳过）：
        * 形状是 `dict` 或 `rows`：这两个都是「一个字段一格」的形态，跳过行容器后
          能直接从 `_d` 逐格求值（`dict` 出列、`rows` 出行 dict）。`list`/`index`
          本来就不需要转置（`list` 直接返回 `_o`、`index` 只是 `enumerate`），没有
          可省的中间容器；
        * 没有字段被按裸名引用（`self._refs` 为空）—— 有引用就必须先算再存；
        * **`aggregate` 字段不构成阻塞**：能整层直出的 aggregate（`_direct_aggregate`
          为真）本来就不占行槽位、直接从 `_d` 逐列求值（见 `_aggregate_direct`），
          与「没有行容器」天然相容；不能直出的（含嵌套 `fields` 的 aggregate）
          仍要行容器，照旧退回；
        * 没有嵌套 `fields`（嵌套要靠行循环的嵌套作用域展开 `_ob`/`_rl`，
          列级要另写一套递归，收益不明而风险不小 —— 整份 schema 退回行容器）；
        * 没有 `const` 以外的"说不清"形态（每个字段必须是 `value`/`expr`/
          `default` 之一）。

        每条列走与 `_col_value` **完全相同**的发射路径（含 optional 的
        `_ix_get`+None 守卫），所以逐值与行容器版相等 —— 由
        `tests/test_matrix_correctness.py` 的四形状对拍与全量 pytest 守着。

        返回 `{字段名: 列表达式}`（`const` 字段的值由 `_col_const` 直接发标量，
        这里不收，调用方另外处理）。
        """
        if self.shape not in ("dict", "rows") or self._refs:
            return None
        for spec in self.fields.values():
            if spec.get("aggregate"):
                # 能整层直出的 aggregate 不占行槽位，与无行容器相容；不能直出的
                # （子键里有嵌套 `fields`、或 optional 且非 value 形态）仍需要
                # 行容器走 `_aggregate` 的递归装配。
                if not self._direct_aggregate(spec):
                    return None
                continue
            if "fields" in spec:
                return None
            if "const" in spec:
                continue
            if "value" in spec or "expr" in spec or "default" in spec:
                continue
            return None
        return None if not self.fields else {}

    def _flat_row(self) -> bool:
        """行能否**裸存**（省掉「每行单元素 list + 末尾转置」的外壳）。

        只对 `dict` / `rows` 形状成立：这两种形状的一行本来就是「字段名 -> 值」，
        单键时 `{k:_o}`、`[{k:r} for r in _o]` 与原来的 `{k:[r[0] for r in _o]}`、
        `[{k:r[0]} ...]` 逐值相等，容器方向不变。

        `list` / `index` 形状的**行本身就是 list**（`[[a0],...]`、`{0:[a0]}`），
        裸存会改变输出结构，因此不启用。

        唯一字段若是「直出列」的 aggregate 对象（`self._direct_aggregate`），
        行里根本没有它的格子 —— `_o` 是空 list，裸存会丢掉全部输出。
        这种情形必须走 `_aggregate()` 从原始行逐列求值。
        """
        if len(self.fields) != 1 or self.shape not in ("dict", "rows"):
            return False
        return not self._direct_aggregate(next(iter(self.fields.values())))

    def _topo(self) -> list:
        """顶层字段依赖拓扑序；嵌套作用域内部的依赖由 `_emit_object` 各自排序。"""
        deps = {}
        names = set(self.fields)
        for name, spec in self.fields.items():
            deps[name] = (self._names(spec, set()) & names) - {name}
        return self._stable_topo(deps, list(self.fields), "out.fields")

    def _stable_topo(self, deps: dict, names: list, path: str) -> list:
        """依赖拓扑序；同层按声明顺序 tie-break（保证产物逐字节可复现）。

        `deps[n]` 是 n 依赖的名字集合，`names` 是参与排序的声明顺序。
        """
        order: list = []
        done: set = set()
        remaining = list(names)
        while remaining:
            ready = [n for n in remaining if deps[n] <= done]
            if not ready:
                raise SchemaError(path, "字段依赖成环: "
                                  + _cycle_hint(deps, remaining))
            for n in ready:
                order.append(n)
                done.add(n)
            remaining = [n for n in remaining if n not in done]
        return order

    # ---------------------------------------------------- 产物装配
    def _expr_roots(self) -> list:
        """本 schema 里所有 `expr` 的 IR 根节点（递归进嵌套 `fields`）。

        只收 `expr`：`value` 是纯路径、没有 IR，也没有运算上下文可供反推。
        同一段 expr 文本复用 `_expr_node` 的缓存，不会重复编译。
        """
        out = []
        seen: set = set()

        def walk(fields):
            for name, spec in fields.items():
                if not isinstance(spec, dict):
                    continue
                if "fields" in spec:
                    walk(spec["fields"])
                    continue
                if "expr" in spec:
                    key = (name, spec["expr"])
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        out.append(self._expr_node(spec))
                    except Exception:  # 编译失败交给原有路径报错，这里不抢
                        continue

        walk(self.fields)
        return out

    def module(self, sha1: str = "") -> str:
        # 类型推导默认关闭：`type: "any"` 的值域包含 null 与缺失键，从运算上下文
        # 反推成数值后发裸运算，会把本该 `TypeError("$add: 操作数必须是数值")`
        # 与 undefined 传播的情形改成 Python 原生 `unsupported operand type(s)` /
        # `KeyError` —— 语义退化（examples/jx/02_arithmetic.jx #11 与
        # tests/test_sentinel_integrity.py 当场抓到）。详见 `_infer_types` docstring。
        if _INFER_ENABLED:
            self._infer_types(self._expr_roots())
        self._refs = self.collect_refs()
        defs, row = self.body()          # 先收齐引用，再决定前缀绑定
        # `row is None` = `dict` 形状的无行容器快路径（见 `body()`）：没有任何
        # 行级表达式需要替换占位符，`plan()` 也无事可做（放行条件已排除跨字段
        # 共享，没有被提升的前缀）。
        no_row = row is None
        self.plan()
        defs = [self.subst(s) for s in defs]
        if not no_row:
            row = self.subst(row)
        pre = [self.subst(s) for s in self._pre]
        defs, pre, row = self._unbind_trivial(defs, pre, row)
        L: list = []
        if self.rfuncs:
            # `$` 用户函数（§3.4）：产物内一次性解析，之后按函数对象调用
            L += ["_xr={}",
                  "def _x(q,*a):",
                  " f=_xr.get(q)",
                  " if f is None:",
                  "  s=q.rfind('.');f=_xr[q]=getattr(__import__(q[:s]),q[s+1:])",
                  " return f(*a)"]
        # `py{}` 多语句块的命名辅助函数：定义在 run() **之外**，每块只发一次
        # （Lead m01624；lambda/IIFE 装不下多语句）。
        for blk in self.pyblocks:
            L.append(blk)
        emitted_blocks = len(self.pyblocks)
        # `before` / `after` 的外部函数形态（`@pkg.mod.fn`）：按需 import，
        # 在 `run()` 之外解析一次，进函数体的是已解析的函数对象（§4.1）。
        # 名字按 **before 在前、after 在后** 的顺序分配，与 `module()` 里的
        # 调用顺序一致（见下 `_hook_text(self.before, ...)` 先于 `self.after`）。
        hook_funcs = [h[1] for h in (self.before, self.after)
                      if h is not None and h[0] == _func_kind()]
        hook_names = iter(f"_h{i}" for i in range(len(hook_funcs)))
        # 钩子的表达式形态必须先编译：里面的 `py{}` 会登记模块级辅助函数，
        # 而 pyblocks 在上面那一轮就要发出去（顺序错了 `_py0` 就不存在）。
        hook_text: dict = {}
        for key, kind, hook in (("before", _func_kind(), self.before),
                                ("after", _func_kind(), self.after)):
            if hook is not None:
                hook_text[key] = self._hook_text(hook, hook_names)
        # 钩子表达式里若出现 py{}，辅助函数是这一轮新登记的 —— 补发。
        for blk in self.pyblocks[emitted_blocks:]:
            L.append(blk)
        if hook_funcs:
            # `@pkg.mod.fn` 的 qualname 可能带类名（`pkg.mod.Class.method`，见
            # `dcg/jx/registry.py:23 _IMPORTABLE_QUALNAME` 允许的形状）。只按
            # 最后一个点切模块名会把 `pkg.mod.Class` 当模块 -> ModuleNotFoundError。
            # 正确做法：从长到短试模块地址，第一个 import 成功的就把它之后的部分
            # 当属性链逐层 getattr。全部失败时抛出带原 qualname 的 ImportError，
            # 而不是一个看不出所以然的 ModuleNotFoundError。
            L += ["import importlib as _il",
                  "def _sh(q):",
                  " s=q.rfind('.')",
                  " while s>0:",
                  "  try:",
                  "   m=_il.import_module(q[:s]);o=m"]
            L += ["   for k in q[s+1:].split('.'):"]
            L += ["    o=getattr(o,k)",
                  "   return o",
                  "  except ImportError:",
                  "   s=q.rfind('.',0,s)",
                  " raise ImportError('钩子函数不可导入: '+q)"]
            for i, qual in enumerate(hook_funcs):
                L += [f"_hq{i}={qual!r}", f"_h{i}=_sh(_hq{i})"]
        hook_names = iter(f"_h{i}" for i in range(len(hook_funcs)))
        if self.trace:
            L.append("TRACE=[]")
        L.append("def run(_d):")
        if self.cython:
            L.append(" cdef object _o")
            for s in self.cdef_names(defs, pre):
                L.append(" cdef object " + s)
        if self.src:
            # `input.source` 是**输入数据里的行集合路径**，与字段 `value` 同一套
            # 路径语法（`schema.py:parse_value_path`）：`"$"` 是整份输入，
            # `"$.a.b"` 表示行集合在 `d["a"]["b"]`。
            #
            # 这里**故意用裸下标**而不是 `_ix_get`：input.source 是 schema 声明的
            # 固定结构，不是逐行 optional 字段 —— 声明了就该在，缺了直接 KeyError，
            # 与 required 字段的裸读口径一致（§2.3）。用 `_ix_get` 会把它静默降级成
            # UNDEF，然后 `for R in UNDEF` 抛一个更难懂的错误。
            txt = "_d"
            for seg in self.src:
                txt += f"[{seg!r}]" if isinstance(seg, str) else f"[{seg}]"
            L.append(" _d=" + txt)
        # `before`：**整体数据**前处理。它拿到的是经 `input.source` 定位后的全量
        # 数据，返回值替换 `_d` 当新输入 —— 在行循环之前一次求值，不逐行跑（§4.1）。
        # `_hb` 守卫：返回 undefined/标量时抛**指名 output.before** 的 TypeError，
        # 否则 `for R in UNDEF` 只会抛一句看不出源头的 `'_Undef' object is not iterable`。
        before_expr = self._hook_text(self.before, hook_names)
        if before_expr is not None:
            L.append(f" _d=_hb({before_expr}(_d))")
        if no_row:
            # 无行容器快路径（`dict` 形状、无跨字段共享）：不建 `_o`、不逐行循环，
            # 直接 `return <逐列推导式>`。逐值与行容器版相等，只是省掉
            # 「每行造 list + 出循环逐列转置」的一进一出。
            L.append(" " + self.finish())
        else:
            L.append(" _o=[]")
            # `append = doc.append`：方法绑定提到循环外（用户 m00828 明确要求）。
            L.append(" _c=_o.append")
            L.append(" for R in _d:")
            ind = "  "
            for s in pre:
                L.append(ind + s)
            for s in defs:
                L.append(ind + s)
            # 行必须是 **list**（§4.1 shape=list = list of row）；用 append 而不是
            # `_o+=[...]`（后者是 extend，会把行拍平成 cell，且实测慢 1.5×）。
            L.append(ind + "_c(" + row + ")")
            if self.trace:
                # trace 产物：额外记录每行的原始行与输出行（正式产物不含这两行）
                L.append(ind + "TRACE.append((R,_o[-1]))")
            L.append(" " + self.finish())
        # `after`：**整体结果**后处理。它拿到的是按 shape 组装完成的全量结果，
        # 返回值就是最终返回值 —— 在 return 之前一次求值，不逐行跑（§4.1）。
        # `_ha` 守卫：返回 undefined 时抛 TypeError，否则 undefined 哨兵会原样
        # 泄进输出（那不是合法 JSON，调用方无从判断）。
        # 返回标量/对象/数组都放行 —— 「结果结构随你定」是 after 的正当用法。
        after_expr = self._hook_text(self.after, hook_names)
        if after_expr is not None:
            L[-1] = f" return _ha({after_expr}({L[-1][8:]}))"
        # `shape="dict"` 里被多个子键共用的列，在 return 前算一次、起个短名
        for col, var in self._shared_columns():
            L.insert(len(L) - 1, " " + var + "=" + col)
        src = "\n".join(L) + "\n"
        # 反引号段里可以直接写 `PY_SAFE_MODULES` 的模块名（`json.loads($)`、`re.sub(...)`），
        # 产物必须自带 import，否则运行期 `NameError`。
        for mod in _safe_modules_used(src):
            src = f"import {mod}\n" + src
        if "math." in src and not src.startswith("import math\n"):
            src = "import math\n" + src
        # 只有真的用到 runtime helper 才发 import 行（CAP/M0 裸运算后一行都不需要）
        if self._uses_helpers(src):
            src = _RUNTIME_IMPORT + "\n" + src
        return src

    def _shared_columns(self):
        """`shape="dict"` 下需要在 `return` 之前预算的共用列（见 `_aggregate_plan`）。"""
        if self.shape != "dict" or self._flat_row():
            return []
        names = list(self.fields)
        specs = [self.fields[n] for n in names]
        return self._aggregate_plan(names, specs)

    def _hook_text(self, hook, func_names) -> str | None:
        """整体数据钩子（`output.before` / `output.after`）-> 可调用的 Python 表达式。

        两种形态（见 `schema._norm_hook`）：

        * `("func", "pkg.mod.fn")` —— 外部函数，`module()` 已在 `run()` 外解析成
          `_hN`；这里只发一个名字，调用处按 `_hN(data)` 调用。
        * `("expr", 文本)` —— JSONata 表达式，**就地编成 lambda**：
          `(lambda D: <expr>)(D)`。形参名用 `D`：钩子拿到的是**全量数据本身**
          （不是行），表达式里的裸 `$` 就指向这个全量数据，因此
          `` before: "= `[r for r in $ if r]`" `` 这类写法成立。

        返回 None 表示没有这个钩子。
        """
        if hook is None:
            return None
        kind, payload = hook
        if kind == "func":
            # 外部函数已在 `run()` 之外解析成 `_hN`；`func_names` 是按
            # `before -> after` 顺序预留的名字表的迭代器，这里只取一次。
            return next(func_names)
        # 表达式形态：钩子是**整体数据级**的值，裸 `$` 就是全量数据本身
        # （`_data_var="D"` 让反引号段里的 `$` 落到 lambda 形参上）。
        # 钩子里的路径要**就地展开**成 `D['x']` 而不是登记成前缀绑定：绑定语句
        # 只会在行循环里、以 `R` 为根发出，用不上数据级钩子（而且 `R` 在 `run()`
        # 顶层作用域里根本不存在，会变成 free variable NameError）。
        node = self._expr_node({"expr": payload})
        saved, self.safe = self.safe, 0
        saved_var, self._data_var = self._data_var, "D"
        saved_binds = self._binds
        self._binds = {}
        try:
            text = self.emit(node)
        finally:
            self.safe = saved
            self._data_var = saved_var
            self._binds = saved_binds
        # 钩子的路径引用不应污染行循环的前缀提升计划：把它们撤掉。
        return f"(lambda D:{self.subst(text)})"

    def _unbind_trivial(self, defs, pre, row):
        """把「值是单个局部名」的字段局部**收回内联**（§4.2 规则 ②）。

        必须在 prefix 替换之后再判：`hourB=R[5]['a'][2]` 替换后才是 `hourB=_a2`，
        此刻才看得出这层中转不服务任何东西 —— `hourB` 与 `_a2` 是同一个值的两个名字。
        """
        if row is None:
            # 无行容器快路径：没有行级表达式，`defs` 也必然是空的。
            return defs, pre, row
        changed = True
        while changed:
            changed = False
            for i, s in enumerate(defs):
                lhs, sep, rhs = s.partition("=")
                if not sep:
                    continue
                name, text = lhs.strip(), rhs.strip()
                if name not in self.fields or not _TRIVIAL.match(text):
                    continue
                f = lambda x, n=name, t=text: _sub_name(x, n, t)
                del defs[i]
                defs[:] = [f(x) for x in defs]
                pre[:] = [f(x) for x in pre]
                row = f(row)
                changed = True
                break
        return defs, pre, row

    def cdef_names(self, defs, pre) -> list:
        """cython 模式下 `cdef` 声明的局部量（一律 object：不改变语义，§4.2 规则 9）。"""
        names = ["R", "_c"]
        for s in list(pre) + list(defs):
            lhs = s.split("=", 1)[0].strip()
            if lhs and lhs.isidentifier() and lhs not in names:
                names.append(lhs)
        return names

    def finish(self) -> str:
        """形状包装 —— **编译期**定型（§4.2 规则⑤），运行期零形状判断。"""
        return "return " + self._finish_expr()

    def _finish_expr(self) -> str:
        """输出形状的最终表达式（不含 `return`）。"""
        if self.shape == "dict":
            # 无行容器快路径优先：`body()` 返回 `row=None` 时正是在走这条，
            # 此时**没有** `_o`，任何引用 `_o` 的分支都会 NameError。
            if self._columns_from_source() is not None:
                # 每个字段各是一条从原始行 `_d` 起算的推导式。与 `_aggregate`
                # 的 `[r[0]for r in _o]` 逐值相等，但少了一次「每行造 list +
                # 出循环逐列转置」。
                cols = []
                for n, s in self.fields.items():
                    if s.get("aggregate"):
                        # 能整层直出的 aggregate：子键各自成列，直接从 `_d` 求值
                        # （`_aggregate_direct` 保证不会引用 `_o`，也没有嵌套
                        # `fields`）。`const` 子键走 `_col_const` 只留一份。
                        sub = list(s["fields"])
                        subspecs = [s["fields"][k] for k in sub]
                        body = []
                        for k, ss in zip(sub, subspecs):
                            if "const" in ss:
                                body.append(f"{k!r}:" + self._col_const(ss, "_d"))
                            else:
                                body.append(f"{k!r}:" + self._col_value(ss, row_of="_d"))
                        cols.append(f"{n!r}:{{" + ",".join(body) + "}")
                    elif "const" in s:
                        cols.append(f"{n!r}:" + self._col_const(s, "_d"))
                    else:
                        cols.append(f"{n!r}:" + self._col_value(s, row_of="_d"))
                return "{" + ",".join(cols) + "}"
            if self._flat_row():
                # 单键：`_o` 里已经是该字段的值列，等价于原来的转置
                return "{" + repr(next(iter(self.fields))) + ":_o}"
            names = list(self.fields)
            specs = [self.fields[n] for n in names]
            plan = self._aggregate_plan(names, specs)
            if not plan:
                return self._aggregate(names, specs)
            tab = {col: var for col, var in plan}
            # 递归时父列已经被替换成短名，子键看到的候选表达式是「短名版」，
            # 所以短名版也要能查到同一个变量（否则 `_a2` 会被再展开一遍）。
            for col, var in plan:
                for pcol, pvar in plan:
                    if pcol in col and pcol != col:
                        tab[col.replace(pcol, pvar)] = var
            return self._aggregate(names, specs, tab=tab)
        if self.shape == "rows":
            # rows 行形态：**每行一个 {字段名: 值} dict**（不是列式）。
            # 与 dict 形状的差别是容器方向：dict = {字段: 整列}，rows = [{字段: 值}, ...]。
            keys = ",".join(repr(n) for n in self.fields)
            # 无行容器快路径（`body()` 返回 `row=None`）：直接对原始数据出一行一个
            # dict，省掉「每行造 list + 再遍历 _o 装配 dict」的一进一出。
            # 放行条件与 dict 形状的 `_columns_from_source()` 完全一致（含 aggregate
            # 直出放行），只是最终容器的方向不同。
            #
            # **必须放在 `_flat_row()` 之前**：`_flat_row` 的分支发的是
            # `[{k:r} for r in _o]`，而快路径下 `_o` 根本不存在（会
            # `NameError: name '_o' is not defined`）。
            if self._columns_from_source() is not None:
                if self._flat_row():
                    k = repr(next(iter(self.fields)))
                    return "[{" + k + ":" + self._row_value(
                        next(iter(self.fields.values()))) + "} for r in _d]"
                parts = []
                for n, s in self.fields.items():
                    if s.get("aggregate"):
                        sub = list(s["fields"])
                        body = []
                        for k2 in sub:
                            ss = s["fields"][k2]
                            if "const" in ss:
                                body.append(f"{k2!r}:" + self._col_const(ss, "_d"))
                            else:
                                body.append(
                                    f"{k2!r}:" + self._row_value(ss))
                        parts.append(f"{n!r}:{{" + ",".join(body) + "}")
                    elif "const" in s:
                        parts.append(f"{n!r}:" + self._col_const(s, "_d"))
                    else:
                        parts.append(f"{n!r}:" + self._row_value(s))
                return "[{" + ",".join(parts) + "} for r in _d]"
            if self._flat_row():
                k = repr(next(iter(self.fields)))
                return "[{" + k + ":r} for r in _o]"
            return ("[{k:r[i]for i,k in enumerate([" + keys + "])}for r in _o]")
        if self.shape == "index":
            return "{i:r for i,r in enumerate(_o)}"
        return "_o"


# ---------------------------------------------------------------- 公开 API
def _check_reserved(schema: dict) -> None:
    for fname in schema["out"]["fields"]:
        if fname in _RESERVED or fname.startswith("_"):
            raise SchemaError(
                f"out.fields.{fname}",
                f"字段名 {fname!r} 与生成码内部名冲突（保留名/前缀 _）；请改名")


def _generate_norm(norm: dict, *, cython: bool = False,
                   inline_optional: bool = True,
                   trace: bool | None = None) -> str:
    """已规范化的 schema -> 源码（不再跑 validate；供 build 复用，P0-2）。"""
    _check_reserved(norm)
    g = _Gen(norm, cython, inline_optional, trace)
    src = g.module(schema_sha1(norm))
    if not cython:
        compile(src, "<dcg-generated>", "exec")   # 唯一一次 compile（§4.3）
    return src


def generate(schema: dict, *, cython: bool = False,
             inline_optional: bool = True) -> str:
    """规范 schema -> 产物源码。纯函数；纯 Python 模式下只 `compile()` 一次。"""
    return _generate_norm(validate(schema), cython=cython,
                          inline_optional=inline_optional)


def _runtime_fingerprint() -> str:
    """`dcg.runtime` 的 helper 指纹（sha1 前 16 位）。

    产物依赖 `dcg.runtime` 的 helper，而 runtime 升级可能**改变旧产物的行为**
    （例如 `$round` 从银行家舍入改成 half-up）。manifest 里记下这个指纹，
    部署侧就能判断「这份产物是按哪一版 helper 语义生成的」。

    指纹只覆盖 helper 的**内联体文本**（`SOURCE`，按名字排序后逐条哈希），
    不含模块级常量与 import 顺序 —— 那些变化不改变 helper 语义。
    """
    import hashlib
    from . import runtime as _rt
    h = hashlib.sha1()
    for nm in sorted(_rt.SOURCE):
        h.update(nm.encode("utf-8"))
        h.update(b"\0")
        h.update(_rt.SOURCE[nm].encode("utf-8"))
        h.update(b"\1")
    return h.hexdigest()[:16]


def _generator_version() -> str:
    """生成器版本（`pkg_version.generator_api`）。

    形如 `"2.1.0.g1"`：前半是 `dcg` 包版本（发生变化 ⇒ 产物语义可能变化，
    旧产物必须重新生成），后半是 manifest 契约版本（`g1` = 本版键集）。
    读 `dcg/__init__.py` 的 `__version__`，读不到退回 `"unknown"`。
    """
    try:
        from . import __version__ as _v
    except Exception:                                  # pragma: no cover
        _v = "unknown"
    return f"{_v}.g1"


def build(schema: dict, out_dir: str, *, name: str | None = None,
          overwrite: bool = False) -> dict:
    """落盘 `gen/<name>.py` + `gen/<name>.manifest.json` + manifest（§4.1）。

    `validate()` 的输出是规范形，**直接**喂给生成器（不二次 `validate`：
    规范化后的 `value` 是链 tuple，见 P0-2 裁决）。

    manifest 键（契约 §4.1，三份文档共用同一张表）：

    | 键 | 含义 |
    |---|---|
    | `name` | 产物模块名（= 规范化后的 `schema["name"]`） |
    | `schema_sha1` | 规范化 schema 的 sha1（来源自证） |
    | `artifact` | 产物路径 `<out>/gen/<name>.py` |
    | `trace_artifact` | trace 产物路径；`trace=false` 时为 `None` |
    | `artifact_sha256` | 产物**字节**的 sha256（校验落盘内容没被改过） |
    | `generator_version` | 生成器版本（`<dcg 版本>.g1`） |
    | `runtime_helper_hash` | `dcg.runtime` helper 指纹（见 `_runtime_fingerprint`） |
    | `lines` / `chars` | 产物行数 / 字符数（报告项，不是门禁） |
    | `cython` | 是否为 Cython 产物 |
    """
    norm = validate(schema)
    if name is not None:
        norm = dict(norm, name=name)
    mod = norm["name"]
    sha1 = schema_sha1(norm)
    src = _generate_norm(norm)
    gen_dir = os.path.join(out_dir, "gen")
    os.makedirs(gen_dir, exist_ok=True)
    path = os.path.join(gen_dir, mod + ".py")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            old = f.read()
        if old != src and not overwrite:
            raise SchemaError(
                "name", f"schema 已变更: gen/{mod}.py 的内容与本次生成不同"
                        f"（sha1 {sha1}）；确认要覆盖请传 overwrite=True")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    trace_artifact = None
    if norm["output"].get("trace"):
        # `trace=true`：额外生成**独立**的调试产物；正式产物与 trace=false 逐字节相同。
        tsrc = _generate_norm(norm, trace=True)
        tpath = os.path.join(gen_dir, mod + ".trace.py")
        with open(tpath, "w", encoding="utf-8", newline="") as f:
            f.write(tsrc)
        trace_artifact = tpath
    manifest = {
        "name": mod,
        "schema_sha1": sha1,
        "artifact": path,
        "trace_artifact": trace_artifact,
        "artifact_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
        "generator_version": _generator_version(),
        "runtime_helper_hash": _runtime_fingerprint(),
        "lines": len(src.splitlines()),
        "chars": len(src),
        "cython": False,
    }
    # 产物自证来源：与 `.py` 并排落一份 manifest JSON（P3-2）。
    # 只交付 `.py` 时产物没有指纹，加这一个文件后可以离线核对来源与版本。
    mpath = os.path.join(gen_dir, mod + ".manifest.json")
    with open(mpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n")
    return manifest


def load(path: str, mod_name: str | None = None):
    """把 `build()` 落盘的产物当模块加载（验证脚本与测试用）。"""
    import importlib.util
    mod = mod_name or os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- CLI
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m dcg.build",
                                 description="DCG v2 代码生成")
    ap.add_argument("schema", help="schema JSON 文件")
    ap.add_argument("-o", "--out", default=".", help="输出根目录（写 <out>/gen/）")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--stdout", action="store_true", help="只打印源码")
    a = ap.parse_args(argv)
    with open(a.schema, "r", encoding="utf-8") as f:
        schema = json.load(f)
    if a.stdout:
        print(generate(schema), end="")
        return 0
    man = build(schema, a.out, overwrite=a.overwrite)
    for k in ("name", "schema_sha1", "artifact", "lines", "chars"):
        print(f"{k}: {man[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
