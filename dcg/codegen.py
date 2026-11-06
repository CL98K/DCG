# -*- coding: utf-8 -*-
"""DCG v2 代码生成。
"""
from __future__ import annotations

import ast
import hashlib
import json
import importlib as _importlib
import os
import re

from . import ir as _ir
from .jx import FIELD_REF, Unsupported, analyze_py_segment, compile_expr, split_chain
from .jx.compiler import PY_SAFE_MODULES, _PY_ROW_PLACEHOLDER
from .runtime import SOURCE
from .schema import SchemaError, validate

__all__ = ["generate", "build", "gen_name", "canonical_json", "schema_sha1"]

#: 产物取 helper 的唯一方式（见模块 docstring 的 helper 出处说明）。
_RUNTIME_IMPORT = "from dcg.runtime import *"

#: 内联 helper 时的传递依赖闭包；现行产物不内联 helper，取值一律走 helper 调用。
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

#: jx 函数 -> runtime helper
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
    # JSONata 2.2.2 新增
    "$match": "_mt", "$zip": "_zp", "$clone": "_cl",
    "$encodeUrl": "_eu", "$decodeUrl": "_du",
    "$formatInteger": "_fgi", "$parseInteger": "_pgi",
    "$fromMillis": "_fms", "$toMillis": "_tms",
    # A 类补充：数值 / 数组与对象
    "$sum": "_sm", "$min": "_mn", "$max": "_mx", "$average": "_av",
    "$append": "_ap", "$reverse": "_rv", "$sort": "_so", "$distinct": "_ds",
    "$merge": "_mg", "$keys": "_ky", "$values": "_vl",
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

#: 高阶函数 -> (runtime helper, 回调参数个数)
_HIGHER = {
    "$map": ("_mp", 2), "$filter": ("_fi", 2), "$reduce": ("_rd", 2),
    "$each": ("_ea", 2), "$sift": ("_sf", 2), "$sort": ("_so", 2),
}

#: 不走 `_FN` 表、由 `_Gen.call()` 里的专用分支编译的算子（语法与普通函数不同，
#: 但**确实是已注册算子**：文档样例、保留字与一致性检查都以这份清单为准）。
_SPECIAL_FN = frozenset({"$lookup"})

#: 字符串函数在实参类型 build 期可证为 str 时，直接发内建方法调用（裸代码）。
_STR_METHODS = {"$uppercase": "upper", "$lowercase": "lower"}

#: 产物的模块级/局部名（schema 字段名撞上即拒绝，绝不生成静默遮蔽）。
_RESERVED = frozenset(
    {"R", "_d", "_o", "_U", "_x", "_xr", "run", "is_u", "<N>"}
    | set(_FN.values()) | set(_OPS.values()) | set(SOURCE)
)

_TOKEN = "\x01{}\x02"
_TOKEN_RE = re.compile("\x01(\\d+)\x02")

#: 声明为数值类型的字段才允许裸算术。
_NUM_TYPES = frozenset({"int64", "int32", "float64"})

#: 声明类型 -> :meth:`CodeGen._kind` 的类别名。字段（`type`）和元素（`itype`）
_TYPE_KIND = {
    "int64": "num", "int32": "num", "float64": "num",
    "str": "str", "bool": "bool",
}

#: 元素类型推导的**类别 -> schema 类型名**反查表（`_TYPE_KIND` 的逆）。
_KIND_TO_TYPE = {"num": "float64", "str": "str", "bool": "bool"}

#: 常量折叠的「不可折叠」哨兵（`None` 是合法字面量，不能拿它当哨兵）。
_NOFOLD = object()

#: 常量折叠允许的算术算子。`_ad`/`_sb`/`_ml` 的实现就是 `a + b` / `a - b` /
_FOLD_ARITH = frozenset({"+", "-", "*"})

#: 比较运算符 -> Python 比较。只在两侧字面量**同类**时使用：异类比较在 1.8 是
_FOLD_CMP = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _lit_kind(v):
    """字面量的 JSON 类别；`bool` 必须与 `num` 分开（1.8 里 `true = 1` 是 false）。"""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, str):
        return "str"
    if v is None:
        return "null"
    return "other"


def _lit_tru(v):
    """字面量的 JSONata 真值（`and`/`or` 折叠用，口径同 `runtime._tru`）。"""
    if v is None or isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return bool(v)


#: CSE 不提升的函数：每次调用结果不同，合并两次调用会改变语义。
_CSE_IMPURE = frozenset({"$random", "$shuffle", "$now", "$millis", "$eval"})


def _cse_key(node):
    """CSE 用的**结构键**：抹掉末尾的 `(行, 列)` 位置信息（`dcg.ir.loc_of`）。
"""
    if isinstance(node, list):
        return tuple(_cse_key(x) for x in node)
    if not isinstance(node, tuple):
        return node
    items = list(node)
    if len(items) > 1:
        last = items[-1]
        if (isinstance(last, tuple) and len(last) == 2
                and all(isinstance(v, int) for v in last)):
            items.pop()
    return tuple(_cse_key(x) for x in items)

#: 从**运算上下文**反向推导类型时，哪些函数只接受数值实参 / 只接受字符串实参
_NUM_ARG_FUNCS = frozenset({
    "$sum", "$max", "$min", "$abs", "$floor", "$ceil", "$round", "$sqrt",
    "$power", "$average",
})
_STR_ARG_FUNCS = frozenset({
    "$uppercase", "$lowercase", "$trim", "$length",
})

#: 实参在语义上**必须是数值**的函数（元素类型推导用）：它们的实参若是列表。
_ELEM_NUM_FUNCS = frozenset({"$sum", "$number", "$average", "$max", "$min"})

#: 运算上下文类型推导总开关。**默认关闭**，因为推导会改变异常语义。

#: 产物第一行 `from dcg.runtime import *` 会带进来的名字 —— 一个都没用到就不发这一行
_IMPORT_NAMES = set(SOURCE) | {"_U", "UNDEF", "is_u"}

#: 值表达式就是一个局部名时，内联它不会重复求值 —— 不落中转局部
_TRIVIAL = re.compile(r"^[A-Za-z_]\w*$")

#: 扁平列特化（见 `_Gen._stream_fold_plan`）：`after` 的折叠函数 -> runtime helper。
_STREAM_FOLDS = {"$sum": "_sm", "$average": "_av",
                 "$max": "_mx", "$min": "_mn"}


def _unquoted_parts(text):
    """`text` 里字符串字面量**之外**的片段（模块名检索只看这些）。"""
    return [p for i, p in enumerate(_QUOTED.split(text)) if i % 2 == 0]


def _safe_modules_used(text):
    """产物里用到的 `PY_SAFE_MODULES` 模块名（反引号段可写 `json.loads($)` 之类）。
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



# ---------------------------------------------------------------- 命名 / 哈希
def canonical_json(schema: dict) -> str:
    """规范序列化：键排序 + 紧凑分隔符 —— `schema_sha1` 的唯一口径。"""
    return json.dumps(schema, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def schema_sha1(schema: dict) -> str:
    return hashlib.sha1(canonical_json(schema).encode("utf-8")).hexdigest()


def gen_name(schema: dict) -> str:
    """产物模块名 = `schema["name"]`（D3：人类可读、可 diff、确定性）。
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
"""
    return _chain(node)


def _is_const(spec: dict) -> bool:
    """常量字段（`const` 或引用了纯常量表达式）：逐行同值。"""
    return "const" in spec


def _has_free_data_ref(node) -> bool:
    """节点里是否出现**读当前行/整份数据**的引用（`FIELD`/`ROOT`/`INDEX`）。
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
            return True
        stack.extend(c for c in n[1:] if isinstance(c, tuple))
    return False


def _mentions_position(node, params) -> bool:
    """函数体里是否用到了「回调第二形参」（索引位）之外的位置信息。
"""
    if len(params) < 2:
        # 即使没有第二形参，`$position` 也可能出现在函数体里 —— 内联成推导式后
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
    """函数体里是否出现 `$position`（内联成推导式后没有对应的下标来源）。"""
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
                 trace: bool | None = None, registry=None):
        self.cython = bool(cython)
        self.inline_optional = bool(inline_optional)
        # `$name` 用户函数：只有显式传入时才把未在白名单里的
        self.registry = registry
        self.fields: dict = schema["out"]["fields"]
        self.shape: str = schema["output"]["shape"]
        # `before` / `after` 都是**整体数据钩子**。
        self.before = schema["output"].get("before")
        self.after = schema["output"].get("after")
        # `where`：逐行布尔表达式（行去留，作用在输入行上）；`expand`：1:N 展开列。
        self.where = schema["output"].get("where")
        self.expand = schema["output"].get("expand")
        # trace 只由显式参数决定：正式产物不得包含 TRACE；`trace=true` 时
        self.trace: bool = bool(trace)
        # 行集合在输入数据里的路径（规范化为段元组）； 表示整份输入就是行集合。
        self.src: tuple = schema["input"]["source"]
        self.rfuncs: list = []
        # `$lookup` 表连接：按出现顺序登记索引规格（顺序即槽位号 `s0`、`s1`…）。
        self.lookups: list = []
        self._lk_slots: dict = {}
        self.uses: dict = {}          # (key, ...) -> 引用次数
        self.order: list = []         # 引用顺序（确定性）
        self.locals: dict = {}        # 字段名 -> 局部名
        self._params: dict = {}       # 函数字面量的参数名 -> 产物里的同名形参
        # 形参名 -> **声明的元素类型**（`itype`）。只有 `$map`/`$filter` 的第一实参
        self._param_types: dict = {}
        # 当前字段的 CSE 表：`id(子树)` -> 临时量名（见 :meth:`_cse_plan`）。
        self._cse: dict = {}
        self._index_var = None        # 当前函数体里 `$position` 要用的下标形参名
        self.safe = 0                 # >0 时路径走 _ix_get（optional 字段专用）
        self.raw = None               # None | "num" | "str"：编译期可证安全 -> 裸运算
        # 裸 `$` 在反引号段里落到哪个局部名：行语境是 `R`（当前行）。
        self._data_var = "R"
        self._n = 0
        self._idx: dict = {}
        self._binds: dict = {}
        self._pre: list = []
        self._refs: set = set()
        self._safe_uses: set = set()
        # 走 `get_text`（`.get(k,_U)` 链）的安全路径：与 `_safe_uses` 区分口径
        self._get_uses: set = set()  # 被安全模式（optional/default/any）引用过的路径
        # 由**使用方式**反推出来的列表元素类型（见 `_infer_elem_types`）。
        self._inferred_elem: dict = {}
        # 列级求值（`_col_value`/`_row_value`）里正在展开**嵌套具名对象/位置行**。
        self._nested_col_mode: bool = False
        self._mixed_uses: set = set()  # 带位置下标的常量链（`$.m[0]`）：基座裸读 + 下标 _ix_get
        # **每次引用各自的读法口径**（DEFECT-020）：`path -> {True, False}`。
        self._ref_safe: dict = {}
        self._tok_safe: list = []
        self._exprs: dict = {}       # expr 源码文本 -> IR（记忆化：见 _expr_node）
        self.pyblocks: list = []     # `py{}` 多语句块的命名辅助函数（定义在 run 外）
        self._pykeys: dict = {}      # (块体, free 元组) -> `_pyN`，同块只发一次
        self._pyroot_args: dict = {}  # 同上 key -> 该块是否多接一个「全量数据」形参
        self.cur_field = ""          # 正在取值的字段（SchemaError 指到具体字段）
        self.cur_key = ""            # 该字段正在取值的键：value/const/expr/fields
        self._nested_scopes: list = []  # 嵌套 fields 的作用域栈（由内到外查找，内层遮蔽外层）
        self._nested_specs: list = []   # 与 _nested_scopes 平行的 spec 栈（判裸名绑定的可证性用）
        self._defs: list = []           # 当前 row-loop 的语句行（嵌套字段绑定也追加到这里）
        # **声明口径**（DEFECT-021）：路径 -> 它被字段声明成的读法集合（True = 安全读）。
        self._decl: dict = self._collect_decl_styles(self.fields)

    # ---------------------------------------------------- 声明口径 / 名字
    def _collect_decl_styles(self, fields: dict, prefix: tuple = (),
                             stack: list | None = None) -> dict:
        """收集 `fields` 里**恰好等于一条路径**的取值所声明的读法（递归嵌套 fields）。
"""
        decl: dict = {}
        if stack is None:
            stack = []
        # `expr` 里出现的路径只是**引用**，不是「这条路径的读法声明」：
        loose: list = []          # (path, style)：复合 expr 里的引用，第二遍才落地

        def scan(sub_fields, pre, stk):
            for spec in sub_fields.values():
                if "fields" in spec:
                    sty = self._decl_style(spec)
                    scan(spec["fields"], pre, stk + [sty])
                    continue
                if "value" in spec:
                    p = self._spec_path(spec, "value")
                    if p:
                        decl.setdefault(tuple(p), set()).update(
                            self._decl_style_pair(spec))
                    continue
                if "expr" not in spec:
                    continue
                try:
                    node = self._expr_node(spec)
                except Exception:  # noqa: BLE001 —— 预扫不负责报错语义
                    continue
                chain = _index_const_chain(node)
                if chain and node[0] in (_ir.FIELD, _ir.INDEX, _ir.ROOT):
                    decl.setdefault(tuple(chain), set()).update(
                        self._decl_style_pair(spec))
                    continue
                for sub in _ir.walk(node):
                    kp = _index_const_chain(sub)
                    if kp:
                        loose.append((tuple(kp), self._decl_style_pair(spec)))

        scan(fields, prefix, stack)
        for p, sts in loose:
            # 只在**这条路径还没有任何读法声明**时才拿引用点的声明兜底。
            if p not in decl:
                decl[p] = set(sts)
        return decl

    def _decl_style_pair(self, spec: dict) -> set:
        """一格里 `value` 与 `expr` 两侧各自声明的读法集合（元素是 `True`/`False`）。
"""
        return {bool(self._decl_style(spec))}

    def _decl_style(self, spec: dict) -> bool:
        """一个字段的声明读法：`True` = 安全（可 undefined），`False` = 裸读。
"""
        return bool(spec.get("optional")) or "default" in spec

    @staticmethod
    def _spec_path(spec: dict, key: str):
        """`spec[key]` 声明的**行路径**元组；不是 `$.a.5` 这种规范元组的返回 `None`。
"""
        raw = spec.get(key)
        if isinstance(raw, (list, tuple)) and raw:
            if all(isinstance(k, (str, int)) and not isinstance(k, bool)
                   for k in raw):
                return tuple(raw)
            return None
        if isinstance(raw, str):
            # 未规范化的 schema 可能把路径写成字符串（`"$.a.5"`）。这里不必
            try:
                from .schema import parse_value_path
                return tuple(parse_value_path(raw, key))
            except Exception:  # noqa: BLE001 —— 解析失败就是「不是可直接用的行路径」
                return None
        return None

    def _eff_safe(self, path: tuple) -> bool:
        """这次引用该用哪种读法：**声明就是唯一口径**，没有声明就是 required。
"""
        if self._data_var != "R":
            return bool(self.safe)
        sts = self._decl.get(tuple(path))
        if sts is not None:
            if len(sts) == 1:
                return next(iter(sts))
            return bool(self.safe)
        return False

    def tmp(self) -> str:
        self._n += 1
        return f"_t{self._n}"

    def token(self, path: tuple, safe=None) -> str:
        """登记一次路径引用，返回占位符；绑定决策要等所有引用都收齐后再做。
"""
        path = tuple(path)
        # 口径 = 声明优先（`_eff_safe`），没有唯一声明才落回调用点的 `self.safe`。
        safe = self._eff_safe(path) if safe is None else bool(safe)
        if safe:
            self._safe_uses.add(path)
        self.uses[path] = self.uses.get(path, 0) + 1
        self._ref_safe.setdefault(path, set()).add(safe)
        key = (path, safe)
        if key not in self._idx:
            self._idx[key] = len(self.order)
            self.order.append(path)
            self._tok_safe.append(safe)
        return _TOKEN.format(self._idx[key])

    def safe_text(self, path) -> str:
        """optional 字段的取值链：每一步都用 `_ix_get`（缺键 -> UNDEF，不抛 KeyError）。
"""
        path = tuple(path)
        self._safe_uses.add(path)
        self._ref_safe.setdefault(path, set()).add(True)
        par = None
        for L in range(len(path) - 1, 0, -1):
            if path[:L] in self._binds:
                par = path[:L]
                break
        t = self._binds[par] if par is not None else self._data_var
        for k in (path[len(par):] if par is not None else path):
            t = f"_ix_get({t},{k!r})"
        return t

    def safe_ref(self, path) -> str:
        """安全链（`optional` + `any`/未声明 -> `_ix_get` 链）的**令牌化**取值入口。
"""
        path = tuple(path)
        if self._data_var != "R":
            # 数据级钩子。
            return self.safe_text(path)
        return self.token(path, True)

    def _rows_mapping(self) -> bool:
        """行是否**一定是 dict**（决定 `dict.get(k,_U)` 能不能用）。
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
"""
        path = tuple(path)
        if not all(isinstance(k, str) for k in path):
            # 含整数/符号下标：`.get` 只对映射成立，整条回退到 _ix_get 链。
            return self.safe_ref(path)
        # 登记这次安全引用并**发 token**（不能就地拼文本）：`body` 阶段
        self._safe_uses.add(path)
        self._get_uses.add(path)
        return self.token(path, True)

    def emit_path(self, path) -> str:
        path = tuple(path)
        if not path:
            return self._data_var
        # 口径按**这次引用**判定（DEFECT-020）：`token` 把 `bool(self.safe)`
        if self._eff_safe(path):
            # 安全模式同样要**登记**这次引用：否则 `$.buyer.level`/`$.buyer.note`
            self._safe_uses.add(path)
            return self.token(path, True)
        if self._data_var != "R":
            # 数据级钩子（`before`/`after`）：没有行循环、没有前缀提升。
            self._ref_safe.setdefault(path, set()).add(False)
            return self._data_var + self.suffix(path)
        return self.token(path, False)

    # ---------------------------------------------------- 前缀 trie / 绑定计划
    def plan(self) -> None:
        """决定哪些链前缀提升成局部名，并生成提升语句。
"""
        # 候选前缀来自两处：普通链（`self.order`）与安全链（`_safe_uses`）。
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
        # DEFECT-018 的冲突判定在下方 `safe_prefix` 建好之后（需要它的前缀集）。
        taken: set = set()
        for p in chosen:
            nam = self.bind_name(p)
            while nam in taken or nam in self.fields:
                nam += "_"
            taken.add(nam)
            self._binds[p] = nam
        # 绑定语句一旦要服务某个安全模式引用（或它自己就是安全路径的前缀）。
        safe_prefix: set = set()
        get_prefix: set = set()
        for p in self._safe_uses:
            for L in range(1, len(p) + 1):
                safe_prefix.add(p[:L])
        for p in self._get_uses:
            for L in range(1, len(p) + 1):
                get_prefix.add(p[:L])
        # DEFECT-020/021：提升出来的局部名只能服务**一种**缺键语义。
        bare_hoist: set = set()
        mixed: set = set()
        for p in chosen:
            n = len(p)
            styles: set = set()
            for r, st in self._ref_safe.items():
                if r[:n] == p:
                    styles |= st
                    if len(styles) > 1:
                        break
            if len(styles) > 1:
                # 子树两种口径并存：退化成裸读绑定（required 语义优先）。
                bare_hoist.add(p)
        if mixed:
            chosen = [p for p in chosen if p not in mixed]
            for p in mixed:
                self._binds.pop(p, None)
        rhs = []
        for p in chosen:
            # 绑定口径必须与 `token`/`text_of` 的「声明优先」一致（DEFECT-021）。
            if p in bare_hoist:
                rhs.append(f"{self._binds[p]}={self.bind_rhs(p)}")
            elif p in get_prefix and self._eff_safe(p):
                rhs.append(f"{self._binds[p]}={self._get_chain_text(p)}")
            elif self._eff_safe(p):
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
       
        if steps and all(isinstance(k, str) for k in steps) and self._eff_safe(p):
            t = base
            for k in steps:
                t = f"_ix_get({t},{k!r})"
            return t
        return base + self.suffix(steps)

    def text_of(self, p: tuple, safe: bool = False) -> str:
        # 本函数的唯一口径来源是调用方传进来的 `safe`。
        if p in self._binds:
            return self._binds[p]
        # 安全链又分两种取值形态，从已提升前缀派生时必须保持同一口径。
        getf = safe and p in self._get_uses
        for L in range(len(p) - 1, 0, -1):
            if p[:L] in self._binds:
                par = self._binds[p[:L]]
                rest = p[L:]
                if not safe:
                    return par + self.suffix(rest)
                if getf:
                    return self._get_chain_text(p)
                if all(isinstance(k, str) for k in rest):
                    t = par
                    for k in rest:
                        t = f"_ix_get({t},{k!r})"
                    return t
                # 剩余段里有**整数下标**：安全口径同样是逐段 `_ix_get`。
                t = par
                for k in rest:
                    t = f"_ix_get({t},{k!r})" if isinstance(k, str) \
                        else f"_ix_get({t},{k})"
                return t
        if safe:
            return self._get_chain_text(p) if getf else self.safe_text(p)
        return self._data_var + self.suffix(p)

    def emit_holder(self, node) -> str:
        """发射**动态下标的基座**（`$.a[<...>]` 里的 `$.a`）。
"""
        path = _index_const_chain(node)
        if path is not None:
            if not path:
                return self._data_var
            # safe 模式（optional/default 字段）下基座缺键也要落 undefined。
            if self._eff_safe(path):
                return self.safe_ref(path)
            self._ref_safe.setdefault(path, set()).add(False)
            return self._data_var + self.suffix(path)
        return self.emit(node)

    def subst(self, text: str) -> str:
        if "\x01" not in text:
            return text
        order = self.order
        tok_safe = self._tok_safe
        return _TOKEN_RE.sub(
            lambda m: self.text_of(order[int(m.group(1))],
                                   tok_safe[int(m.group(1))]), text)

    # ---------------------------------------------------- IR 发射
    def emit(self, node) -> str:
        k = node[0]
        if self._cse:
            # 本字段的 CSE 表命中：该子树已经在行级 defs 里算过（P1：CSE）。
            hit = self._cse.get(id(node))
            if hit is not None:
                return hit
        if k == _ir.LIT:
            return self.lit(node[1])
        if k in (_ir.BIN, _ir.UNARY):
            # 常量折叠：两侧都是字面量时直接发结果字面量。
            v = self._folded(node)
            if v is not _NOFOLD:
                return self.lit(v)
        if k in (_ir.FIELD, _ir.VAR):
            if k == _ir.VAR:
                return self.var_name(node[1])
            return self.field_name(node[1])
        if k == _ir.INDEX:
            path = _index_const_chain(node)
            if path is not None:
                if not path:
                    return self._data_var
                # 契约 附加条目 3 + 架构文档 「读值形态」。
                if self._eff_safe(path) or self._data_var == "D":
                    # optional / default：整条链都要 undefined 安全 —— 缺键得
                    return self.safe_ref(path)
                # required（未声明 optional）：没有 undefined 出口，缺键 /
                return self.emit_path(path)

            # 非常量链：基座 + 动态下标（`$.a[b]` / `$.a[$.i]` / `$.a[expr]`）
            idx = node[2]
            holder = node[1]
            if idx[0] == _ir.LIT and isinstance(idx[1], int) \
                    and not isinstance(idx[1], bool) \
                    and self._bare_holder_ok(holder):
                # 已证明是容器类型（list/object）的字段 + 常量整数下标 -> 直接下标。
                return f"{self.emit(holder)}[{self.lit(idx[1])}]"
            # 下标表达式按普通表达式发射：`$.a[b]` 的 `b` 是裸名字段引用。
            itext = self.emit(idx)
            if self.safe:
                # optional / default 字段：越界或类型不符要落成 undefined（交给守卫或默认值）
                return f"_ix_get({self.emit_holder(holder)},{itext})"
            # required 字段：没有 optional/default 就没有 undefined 出口，缺数据即真实错误。
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
        if k == "rx":
            flags = node[2]
            return "_rx(%r,%r)" % (node[1], flags)
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
                "")
        raise Unsupported(f"codegen 不支持的 IR 节点 {k!r}")

    def _cse_collect(self, node, out) -> None:
        """后序收集可提升的 `CALL` 子树（**不进入 LAMBDA 体**）。
"""
        if node[0] == _ir.LAMBDA:
            return
        for c in _ir.children(node):
            self._cse_collect(c, out)
        if node[0] != _ir.CALL:
            return
        fname = node[1]
        if fname in _CSE_IMPURE or self._inline_ok(fname, node[2]):
            return
        if not self._cse_containers_ok(node):
            return
        out.append((_cse_key(node), node))

    def _cse_containers_ok(self, node) -> bool:
        """子树里没有用户代码（`py{}` / `py:{}` / `@外部函数`）——可能有副作用。"""
        if node[0] in (_ir.PY, _ir.PYBLOCK, _ir.RFUNC):
            return False
        return all(self._cse_containers_ok(c) for c in _ir.children(node))

    def _cse_plan(self, spec) -> None:
        """把同一表达式里**重复出现的纯函数调用**提升成行级临时量（P1：CSE）。
"""
        node = self._expr_node(spec)
        if node is None:
            return
        found: list = []
        self._cse_collect(node, found)
        if len(found) < 2:
            return
        groups: dict = {}
        for key, sub in found:
            groups.setdefault(key, []).append(sub)
        for occ in groups.values():
            if len(occ) < 2:
                continue
            t = self.tmp()
            # 后序：先处理内层（已登记），外层的产物文本就能直接引用内层临时量。
            text = self.emit(occ[0])
            self._defs.append(f"{t}={text}")
            for sub in occ:
                self._cse[id(sub)] = t

    def _folded(self, node):
        """常量折叠：能算出值就返回该 Python 值，否则返回 `_NOFOLD`。
"""
        k = node[0]
        if k == _ir.LIT:
            return node[1]
        if k == _ir.UNARY:
            if node[1] not in ("-", "+"):
                return _NOFOLD
            v = self._folded(node[2])
            if v is _NOFOLD or _lit_kind(v) != "num":
                return _NOFOLD
            return -v if node[1] == "-" else +v
        if k != _ir.BIN:
            return _NOFOLD
        op = node[1]
        a = self._folded(node[2])
        if a is _NOFOLD or a is None:
            return _NOFOLD
        b = self._folded(node[3])
        if b is _NOFOLD or b is None:
            return _NOFOLD
        ka, kb = _lit_kind(a), _lit_kind(b)
        if op in _FOLD_ARITH:
            # bool 不算数值：`true + 1` 在 1.8 里是类型错误，折叠成 2 会改语义。
            if ka == "num" and kb == "num":
                if op == "+":
                    return a + b
                return a - b if op == "-" else a * b
            return _NOFOLD
        if op == "&":
            return a + b if (ka == "str" and kb == "str") else _NOFOLD
        if op in _FOLD_CMP:
            if ka != kb or ka not in ("num", "str", "bool"):
                return _NOFOLD
            if op in _ORDER_OPS and ka == "bool":
                # 布尔比较大小在 1.8 里没有定义（`_lt(True,False)` 得 undefined）。
                return _NOFOLD
            return bool(_FOLD_CMP[op](a, b))
        if op in ("and", "or"):
            ta, tb = _lit_tru(a), _lit_tru(b)
            return True if ((ta and tb) if op == "and" else (ta or tb)) else False
        return _NOFOLD

    def bin(self, node) -> str:
        """二元运算。
"""
        op = node[1]
        a, b = node[2], node[3]
        if op == "?:":
            # JSONata 2.1.0 `?:`：左侧布尔等价为假（含 undefined）时取右侧。
            return f"(_e if _tru(_e:={self.emit(a)}) else {self.emit(b)})"
        if op == "??":
            # `??`：只有 undefined 才取右侧（null/0/""/[]/{} 都保留原值）。
            return f"(_e if (_e:={self.emit(a)}) is not _U else {self.emit(b)})"
        if op in ("+", "-", "*", "/", "%") and self._ok_num(a) \
                and self._ok_num(b):
            # 操作数类型可证 -> 裸运算，与目标字段类型无关
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
            # JSONata 的 `and`/`or` **恒返回布尔**（`1 and 2` -> true），Python 的
            if self._bool_provable(a) and self._bool_provable(b):
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
"""
        text = spec.get("expr")
        if text is None:
            return None
        node = self._exprs.get(text)
        if node is None:
            node = self._exprs[text] = compile_expr(text,
                                                    registry=self.registry)
        return node

    def _var_proven(self, name: str, want) -> bool:
        """`$i` 变量形态是否可证明：等价于本 schema 字段 `i` 的裸名引用。
"""
        if name in self._params:
            pt = self._param_types.get(name)
            if pt is None:
                return False
            return True if want is None else pt in want
        t = self._decl_type(name)
        if t is None:
            return False
        return True if want is None else t in want

    def _decl_type(self, name: str):
        """已声明字段的**静态类型**；optional / 未声明 → None（不可证安全）。
"""
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return None
        t = spec.get("type")
        if t not in (None, "any"):
            return t
        return None

    #: 数值类型集合（`bool` 不在内：运算表里 bool 被拒绝，见 runtime `_num`）。

    def _infer_elem_types(self, roots) -> None:
        """从**使用方式**反推列表字段的元素类型（写 `_inferred_elem`）。
"""
        self._inferred_elem = {}
        exprs = [n for n in roots if n is not None]
        if not exprs:
            return
        cand: dict = {}   # 字段名 -> set of "num"/"str"（类别，最后映射成 schema 类型名）
        for node in exprs:
            for n in _ir.walk(node):
                self._infer_elem_node(n, cand)
        for name, kinds in cand.items():
            if len(kinds) != 1:
                # 冲突：同一列表要素既被当数值用又被当字符串用 —— 推不出确定类型。
                continue
            kind = next(iter(kinds))
            typed = _KIND_TO_TYPE.get(kind)
            if typed is not None:
                self._inferred_elem[name] = typed

    def _elem_list_field(self, node):
        """节点若是「可证为列表」的裸字段读值，返回字段名；否则 None。
"""
        if node is None:
            return None
        if node[0] == _ir.VAR and node[1] in self._params:
            return None
        name = self._const_field_name(node)
        if name is None:
            return None
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return None
        if spec.get("type") != "list":
            return None
        return name

    def _infer_elem_node(self, node, cand) -> None:
        """单节点的元素类型推导规则（把候选写进 `cand`）。"""
        k = node[0]
        if k == _ir.CALL:
            fname = node[1]
            args = node[2]
            if fname in _ELEM_NUM_FUNCS and args:
                name = self._elem_list_field(args[0])
                if name is not None:
                    cand.setdefault(name, set()).add("num")
            if fname in ("$map", "$filter") and len(args) >= 2:
                self._infer_elem_from_lambda(args[0], args[1], cand)
            return
        if k == _ir.BIN:
            op, l, r = node[1], node[2], node[3]
            # `$sum(xs) + 1` 这类：`$sum` 的实参已在上面处理，这里不重复。
            return

    def _infer_elem_from_lambda(self, src, lam, cand) -> None:
        """`$map`/`$filter` 的回调形参用法 -> 来源列表的元素类型。"""
        name = self._elem_list_field(src)
        if name is None:
            return
        if not (isinstance(lam, tuple) and lam and lam[0] == _ir.LAMBDA):
            return
        params = list(lam[1])
        if not params:
            return
        var = params[0]
        body = lam[2]
        for n in _ir.walk(body):
            if n[0] == _ir.BIN:
                op, l, r = n[1], n[2], n[3]
                if op in ("+", "-", "*", "/", "%", "<", "<=", ">", ">="):
                    # 一侧是本形参、另一侧可证为数值 -> 形参是数值。
                    if self._is_param(n[2], var) and self._node_is_num(n[3], _NUM_TYPES):
                        cand.setdefault(name, set()).add("num")
                    elif self._is_param(n[3], var) and self._node_is_num(n[2], _NUM_TYPES):
                        cand.setdefault(name, set()).add("num")
            elif n[0] == _ir.UNARY and n[1] in ("-", "+"):
                if self._is_param(n[2], var):
                    cand.setdefault(name, set()).add("num")
            elif n[0] == _ir.CALL and n[1] in _ELEM_NUM_FUNCS and n[2]:
                if self._is_param(n[2][0], var):
                    cand.setdefault(name, set()).add("num")
            elif n[0] == _ir.CALL and n[1] in _STR_ARG_FUNCS and n[2]:
                if self._is_param(n[2][0], var):
                    cand.setdefault(name, set()).add("str")

    @staticmethod
    def _is_param(node, var) -> bool:
        return (isinstance(node, tuple) and node and node[0] == _ir.VAR
                and node[1] == var)

    def _node_is_num(self, node, num_probe) -> bool:
        """节点是否**可证为数值**（推导过程用；比 `_ok_num` 宽一点：认推导类型）。"""
        name = self._const_field_name(node)
        if name is not None:
            t = self._decl_type(name)
            return t in num_probe
        return self._ok_num(node)

    def _const_field_name(self, node):
        """节点若是一条**单段常量字段链**（`$.a` 或裸名 `a`），折回字段名；否则 None。
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

    def _elem_type(self, node):
        """第一实参的**元素类型**；不可证 → None。
"""
        if node is None:
            return None
        if node[0] == _ir.VAR:
            name = node[1]
        else:
            name = self._const_field_name(node)
        if name is None or name in self._params:
            return None
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return None
        if spec.get("type") != "list":
            return None
        declared = spec.get("itype")
        if declared is not None:
            return declared
        return self._inferred_elem.get(name)

    #: 常量整数下标可以直接内联成 `base[i]` 的 holder 类型 —— **必须能证明是容器**。
    _BARE_HOLDER_TYPES = frozenset({"list", "object"})

    def _bare_holder_ok(self, holder) -> bool:
        """常量整数下标能否内联成裸 `base[i]`。
"""
        if holder[0] not in (_ir.FIELD, _ir.VAR):
            return False
        return self._decl_type(holder[1]) in self._BARE_HOLDER_TYPES

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
        路径，或 `expr` 本身落在裸运算模式里（裸运算模式的前提就是可证安全）。
"""
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
                # 理由同 `_emit_bin` 的 and/or：Python 返回操作数，JSONata 返回布尔
                return self._bool_provable(node[2]) and self._bool_provable(node[3])
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
            # 声明了具体类型时才成立：这条路 codegen 本来就走裸读。
            t = spec.get("type")
            if t is None or t == "any" or spec.get("optional"):
                # 注意：这一行判的是「**值会不会是哨兵**」，与「这一格按哪种口径读」
                return False
            return True
        if "expr" in spec:
            node = self._expr_node(spec)
            if node[0] in (_ir.PY, _ir.PYBLOCK):
                # 反引号 py 段 / `py{}` 块里**可以**出现裸字段名。
                return all(self._bare_name_never_undef(n)
                           for n in self._names(spec, set()))
            return self._definite(node)
        return False

    def _bare_name_never_undef(self, name: str) -> bool:
        """裸名 `name` 在它的作用域里解析到的绑定，是否可证非 undefined。
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
                # 循环变量：只有声明过元素类型（`itype`）才可证类别。
                return _TYPE_KIND.get(self._param_types.get(name))
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
"""
        return self._kind(node) in ("num", "str", "bool")

    def _in_provable(self, a, b) -> bool:
        """`a in [...]`：左侧类型可证，右侧全是同类字面量。
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

    def _bool_provable(self, node) -> bool:
        """`_bool_valued` 的放宽版：再加上「声明类型就是 bool」的字段/变量。
"""
        if _bool_valued(node):
            return True
        if not isinstance(node, tuple) or not node:
            return False
        k = node[0]
        if k == _ir.FIELD:
            return self._decl_type(node[1]) == "bool"
        if k == _ir.INDEX:
            name = self._const_field_name(node)
            return name is not None and self._decl_type(name) == "bool"
        if k == _ir.VAR:
            return self._var_proven(node[1], ("bool",))
        return False

    def _decl_of(self, node):
        """节点若是一个**已声明字段的读值**，返回它的声明（规范形 spec）。
"""
        k = node[0] if isinstance(node, tuple) and node else None
        if k == _ir.FIELD:
            name = node[1]
        elif k == _ir.INDEX:
            path = _index_const_chain(node)
            if not path:
                return None
            name = path[0]
        else:
            return None
        spec = self.fields.get(name)
        return spec if isinstance(spec, dict) else None

    def _is_listy(self, node) -> bool:
        """是否可证明是数组（`len` / `$count` 可直接内建）。
"""
        k = node[0]
        if k == _ir.ARRAY:
            return all(self._definite(x) for x in node[1])
        spec = self._decl_of(node)
        if spec is not None:
            return (not spec.get("optional")) and spec.get("type") == "list"
        if k == _ir.VAR:
            if node[1] in self._params:
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
        """该列函数是否有**语义一致的 Python 内建等价**且实参可证。
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
            return (n == 2 and self._ok_str(args[0])
                    and args[1][0] == _ir.LIT and isinstance(args[1][1], str)
                    and args[1][1] != "")
        if fname == "$substringBefore":
            # 未找到分隔符时 1.8 返回整串，`partition[0]` 恰好也是整串
            return n == 2 and self._ok_str(args[0]) and self._ok_str(args[1])
        if fname == "$substringAfter":
            # `partition[2]` 在未命中时给 `''`，而 1.8 给整串。
            return False
        if fname == "$replace":
            return n == 3 and all(self._ok_str(x) for x in args)
        if fname == "$pad":
            # 只有单字符填充时才与 `str.ljust(s, fillchar)` 逐值等价；
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
            # 舍入规则本身与 Python `round` 一致（都是 round half to even）。
            return False
        if fname == "$sqrt":
            # helper 把负数映射成 undefined，而 math.sqrt 会抛 ValueError；
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
    def _local_binds(text: str) -> list:
        """把产物里**实际用到**的 runtime helper 在 `run` 开头绑成局部名。
"""
        used = set(re.findall(r"[A-Za-z_]\w*", text)) & set(SOURCE)
        return [" " + n + "=_g[" + repr(n) + "]" for n in sorted(used)]

    @staticmethod
    def is_string_call(node) -> bool:
        return (isinstance(node, tuple) and node and node[0] == _ir.CALL
                and node[1] == "$string" and len(node[2]) == 1)

    def var_name(self, name: str) -> str:
        """`$x` 变量引用：只有函数字面量形参（B 类回调）能在产物里“是变量”。
"""
        if name in self._params:
            return self._params[name]
        raise SchemaError(
            self._fpath(),
            f"行模式没有变量作用域：${name} 不是任何函数字面量的形参"
            f"（逐行函数只看到行与整批数据）；要取本行字段请写裸名 {name} 或 $.{name}")

    def field_name(self, name: str) -> str:
        """裸名字段引用：按**依赖顺序**取已算好的局部名。
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
        if fname == "$lookup":
            return self.lookup(args, loc)
        if fname == "$position" and not args and self._index_var:
            # `$position` 只在函数体内可用，编成回调的第二个位置参数。
            return self._index_var
        inl = self._inline_text(fname, args)
        if inl is not None:
            return inl
        if self.raw and fname == "$string" and len(args) == 1 \
                and self._ok_num(args[0]):
            # `type: str` 的字段里 `$string(数值)` 就是内建 `str(x)`；布尔要
            return f"str({self.emit(args[0])})"
        if (fname in _STR_METHODS and len(args) == 1
                and self._ok_str(args[0])):
            # 实参类型 build 期可证为字符串 -> 内建方法调用
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

    # ---------------------------------------------------- 表连接 `$lookup`
    #: 谓词里的左行只能是 `$l.` / `$.`；`$r.` 留给候选右行
    _PRED_VAR = re.compile(r"\$(l|r)\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")

    @staticmethod
    def _pred_flatten(text, side):
        """把 `$.a.b` 折成 `$<side>.a.b`，让谓词只剩 `$l.` / `$r.` 两种写法。

        JX 的 `$.` 与 `$l.` 是同一行（左行）：不折算的话 `$.amount` 会以 `$` 变量
        进入下标链，既匹配不上区间图样（拿不到 `bisect`，退化成逐行扫全表），
        报错信息也解释不清。折成 `$l.` 之后两种写法走同一条路。
        """
        out = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c in "'\"":
                j = i + 1
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                out.append(text[i:j + 1])
                i = j + 1
                continue
            if text.startswith("$.", i) or (c == "$" and i + 1 == n):
                out.append("$" + side)
            else:
                out.append(c)
            i += 1
        return "".join(out)

    def _pred_rewrite(self, text):
        """把谓词里的 `$l.x` / `$r.x` 换成可逐行编译的变量引用。

        JX 的路径只支持 `$` 起头（`$r.lo` 不是合法路径），所以先换成 `$l0` /
        `$r0` 这样的变量，发射时再经 `_params` 映射成 `R['x']` / `_r['x']`。
        返回 `(新文本, {变量名: (side, 路径)})`。
        """
        text = self._pred_flatten(text, "l")
        info = {}

        def sub(m):
            base, path = m.group(1), m.group(2)
            name = "%s%d" % (base, len(info))
            info[name] = (base, path)
            return "$" + name

        return self._PRED_VAR.sub(sub, text), info

    @staticmethod
    def _pred_map(info, data_var):
        """`{变量名: (side, 路径)}` -> 发射用的 `{变量名: Python 取值表达式}`。"""
        out = {}
        for name, (base, path) in info.items():
            prefix = "_r" if base == "r" else data_var
            out[name] = prefix + "".join("[%r]" % p for p in path.split("."))
        return out

    def _need_lit_str(self, node, what: str) -> str:
        if not self._lit_str(node):
            raise SchemaError(self._fpath(), f"{what} 必须是字符串字面量")
        return node[1]

    @staticmethod
    def _ppath(node, info):
        """`$l.a.b` / `$.a` / `$r.a` -> `('l'|'r', 'a.b')`；不是纯路径返回 None。"""
        segs = []
        cur = node
        while isinstance(cur, tuple) and cur and cur[0] == _ir.INDEX:
            name = cur[2]
            if not (isinstance(name, tuple) and name and name[0] == "name"):
                return None
            segs.append(name[1])
            cur = cur[1]
        if not (isinstance(cur, tuple) and cur and cur[0] == _ir.VAR):
            return None
        base = cur[1]
        if base in info:
            if segs:
                return None
            return info[base]
        if base not in ("$l", "$r", "$") or not segs:
            return None
        segs.reverse()
        return ("r" if base == "$r" else "l", ".".join(segs))

    def _null_eq_path(self, node, info):
        """`$r.hi = null`（两侧顺序任意）-> `'hi'`。"""
        if not (isinstance(node, tuple) and node[0] == _ir.BIN
                and node[1] == "="):
            return None
        a, b = node[2], node[3]
        pa = self._ppath(a, info)
        if pa and pa[0] == "r" and b[0] == _ir.LIT and b[1] is None:
            return pa[1]
        pb = self._ppath(b, info)
        if pb and pb[0] == "r" and a[0] == _ir.LIT and a[1] is None:
            return pb[1]
        return None

    def _hi_of(self, node, info):
        """`x < $r.hi` / `$r.hi > x` -> `'hi'`（上界为开区间才可走 bisect）。"""
        if not (isinstance(node, tuple) and node[0] == _ir.BIN):
            return None
        op = node[1]
        pa, pb = self._ppath(node[2], info), self._ppath(node[3], info)
        if pa is None or pb is None:
            return None
        if op == "<" and pa[0] == "l" and pb[0] == "r":
            return pb[1]
        if op == ">" and pa[0] == "r" and pb[0] == "l":
            return pa[1]
        return None

    def _cmp_hi(self, node, info):
        if not (isinstance(node, tuple) and node[0] == _ir.BIN):
            return None
        if node[1] == "or":
            # `($r.hi = null or x < $r.hi)`：开区间上界写成「无上界」的常见形态
            for a, b in ((node[2], node[3]), (node[3], node[2])):
                name = self._null_eq_path(a, info)
                if name is not None:
                    return name if self._hi_of(b, info) == name else None
            return None
        return self._hi_of(node, info)

    def _cmp_lo(self, node, info):
        """`$r.lo <= x` / `x >= $r.lo` -> `(x_node, 'lo')`。"""
        if not (isinstance(node, tuple) and node[0] == _ir.BIN):
            return None
        op = node[1]
        a, b = node[2], node[3]
        pa, pb = self._ppath(a, info), self._ppath(b, info)
        if pa is None or pb is None:
            return None
        if op == "<=" and pa[0] == "r" and pb[0] == "l":
            return (b, pa[1])
        if op == ">=" and pa[0] == "l" and pb[0] == "r":
            return (a, pb[1])
        return None

    def _range_pat(self, node, info):
        """识别 `$r.lo <= x and x < $r.hi`（含 `$r.hi = null or ...` 变体）。

        命中返回 `(x_node, lo_path, hi_path)`，交给 `RangeIndex` 走 bisect；
        其它谓词返回 None（逐行扫描候选）。
        """
        if not (isinstance(node, tuple) and node[0] == _ir.BIN
                and node[1] == "and"):
            return None
        lo = self._cmp_lo(node[2], info)
        if lo is None:
            lo = self._cmp_lo(node[3], info)
            if lo is None:
                return None
            hi = self._cmp_hi(node[2], info)
        else:
            hi = self._cmp_hi(node[3], info)
        if hi is None:
            return None
        return (lo[0], lo[1], hi)

    def _lk_slot(self, table, rkeys, mode, strat, kind, hi) -> int:
        """登记索引规格，返回槽位号（同规格复用同一槽位）。"""
        sig = (table, tuple(rkeys) if rkeys else None, mode, strat, kind, hi)
        slot = self._lk_slots.get(sig)
        if slot is None:
            slot = len(self.lookups)
            self._lk_slots[sig] = slot
            key = None if not rkeys else (
                rkeys[0] if len(rkeys) == 1 else list(rkeys))
            self.lookups.append({"table": table, "key": key, "mode": mode,
                                 "strategy": strat, "kind": kind, "hi": hi})
        return slot

    def lookup(self, args, loc="") -> str:
        """`$lookup(键,'表','右键','列'[,'模式'[,'策略'[,'谓词']]])`。

        编译成对索引槽位的方法调用（`_i0.gv(k,'name')`）：索引在 `prepare` /
        `bind` 阶段一次建好，运行期不再判断模式与策略。语义见
        `docs/join-design.md`。
        """
        n = len(args)
        if n == 2:
            raise SchemaError(
                self._fpath(),
                "$lookup 的两参形态（对象取键）已在 2.2.3 移除；"
                "取对象字段请写 $.path 或 $.a.b，表连接请写 "
                "$lookup(键,'表','右键','列')")
        if n < 3:
            raise SchemaError(
                self._fpath(),
                "$lookup 至少 3 个参数：$lookup(键,'表','右键'[,'列'"
                "[,'模式'[,'策略'[,'谓词']]]])")
        if n > 7:
            raise SchemaError(self._fpath(),
                              f"$lookup 最多 7 个参数，得到 {n} 个")
        table = self._need_lit_str(args[1], "第 2 个参数（表名）")
        col = None
        if n > 3 and not (args[3][0] == _ir.LIT and args[3][1] is None):
            col = self._need_lit_str(args[3], "第 4 个参数（列名）")
        mode = "strict"
        if n > 4:
            mode = self._need_lit_str(args[4], "第 5 个参数（模式）")
            if mode not in ("strict", "loose"):
                raise SchemaError(
                    self._fpath(),
                    f"第 5 个参数（模式）只能是 'strict' 或 'loose'，"
                    f"得到 {mode!r}")
        strat = "one"
        if n > 5:
            strat = self._need_lit_str(args[5], "第 6 个参数（策略）")
            if strat not in ("one", "first", "all"):
                raise SchemaError(
                    self._fpath(),
                    f"第 6 个参数（策略）只能是 'one' / 'first' / 'all'，"
                    f"得到 {strat!r}")
        pred_text = None
        if n > 6:
            pred_text = self._need_lit_str(args[6], "第 7 个参数（谓词）")
        rkey_node = args[2]
        if rkey_node[0] == _ir.LIT and rkey_node[1] is None:
            keys = None
        elif rkey_node[0] == _ir.ARRAY:
            keys = [self._need_lit_str(x, "第 3 个参数（右键数组的元素）")
                    for x in rkey_node[1]]
            if not keys:
                raise SchemaError(self._fpath(), "第 3 个参数（右键）不能是空数组")
        else:
            keys = [self._need_lit_str(rkey_node, "第 3 个参数（右键）")]
        info = {}
        pred = None
        if pred_text:
            rewritten, info = self._pred_rewrite(pred_text)
            pred = self._expr_node({"expr": rewritten})
        rng = self._range_pat(pred, info) if pred is not None else None
        # 区间 + `all`：`RangeIndex` 只给单个位置，取全部候选得走逐行扫描，
        # 于是丢掉区间、用右键做等值/前缀过滤 + 谓词逐行判定，语义不变。
        if rng is not None and strat == "all":
            rng = None
        if rng is None and keys is None:
            # 右键为 null 时只能走区间：谓词里必须有一对 `$r.<lo>` / `$r.<hi>`，
            # 识别不出来多半是谓词写法不在支持范围内（左行统一写 `$l.` 或 `$.`）。
            raise SchemaError(
                self._fpath(),
                "第 3 个参数（右键）为 null 时只支持区间谓词 "
                "$r.<lo> <= <左行取值> and <左行取值> < $r.<hi>"
                "（上界可写成「$r.<hi> = null 或 ...」，左行写 $l. 或 $. 都行）；"
                "等值连接请给右键")
        if rng is not None:
            kind, rkeys, hi = "range", [rng[1]], rng[2]
        else:
            kind, rkeys, hi = "hash", keys, None
        slot = self._lk_slot(table, rkeys, mode, strat, kind, hi)
        # 左键 / 区间取值 / 谓词：`$l` 就是左行（与 `$.` 等价）
        saved = dict(self._params)
        self._params["$l"] = self._data_var
        try:
            # 谓词变量映射必须先于任何 emit：区间取值 `rng[0]` 本身就是谓词里的
            # 左行表达式（`$l1`），晚一步就会当成未知变量报「没有变量作用域」。
            if pred is not None:
                self._params.update(self._pred_map(info, self._data_var))
            kexpr = self.emit(args[0])
            xexpr = self.emit(rng[0]) if rng is not None else None
            ptext = self.emit(pred) if pred is not None else None
        finally:
            self._params = saved
        i = f"_i{slot}"
        if rng is not None:
            return (f"{i}.pr({xexpr})" if col is None
                    else f"{i}.pv({xexpr},{col!r})")
        if pred is None:
            if strat == "all":
                return (f"{i}.ga({kexpr},{col!r})" if col is not None
                        else f"{i}.gar({kexpr})")
            return (f"{i}.gv({kexpr},{col!r})" if col is not None
                    else f"{i}.gr({kexpr})")
        lam = f"(lambda _l,_r:{ptext})"
        if strat == "all":
            return f"{i}.qa({kexpr},{self._data_var},{lam},{col!r})"
        return f"{i}.q({kexpr},{self._data_var},{lam},{col!r})"

    def emit_lambda(self, node) -> str:
        """IR `('lambda', [参数], body, loc)` -> 产物里的 Python `lambda`。
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
                f"回调最多接收 {arity} 个参数")
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
        """高阶函数：数组等实参照常发射，回调 lambda 就地发射。
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
                "")
        inlined = self._higher_inline(fname, args, lam)
        if inlined is not None:
            return inlined
        parts = [self.emit_lambda(a) if a is lam else self.emit(a) for a in args]
        return fn + "(" + ",".join(parts) + ")"

    def _higher_inline(self, fname: str, args, lam):
        """尝试把高阶函数展成推导式；不能展则返回 `None`。
"""
        if fname == "$reduce":
            # `$reduce` 的累加形态展成行级 `for` 循环（见 `_reduce_inline`）。
            return self._reduce_inline(args, lam)
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
        if not self._is_listy(args[0]):
            # 标量包成单元素、undefined 变空序列 —— 与 `_mp`/`_fi` 的入口提升一致。
            arr = f"_as_list({arr})"
        # 发射函数体：形参名原样可用（它们就是推导式里的循环变量）。
        saved = dict(self._params)
        saved_types = dict(self._param_types)
        saved_index = self._index_var
        self._params.update({p: p for p in params})
        et = self._elem_type(args[0])
        if et is not None:
            # 只有循环变量是元素本体；索引形参（若有）是 int，但推导式形态下
            self._param_types[var] = et
        # 第二形参（索引）在新形态里没有来源 —— 若函数体用到它就放弃。
        if idx is not None:
            self._index_var = idx
        try:
            text = self.emit(body)
        finally:
            self._params = saved
            self._param_types = saved_types
            self._index_var = saved_index
        if fname == "$map":
            return f"[{text} for {var} in {arr}]"
        if _bool_valued(body):
            # 函数体本身就是比较/布尔运算（只可能返回 True/False/undefined）。
            return f"[{var} for {var} in {arr} if {text}]"
        return f"[{var} for {var} in {arr} if _tru({text})]"

    def _reduce_inline(self, args, lam):
        """`$reduce(arr, function($acc,$v){$acc OP $v}, init)` -> 行级累加循环。
"""
        if len(args) != 3 or args[1] is not lam:
            return None
        if args[2][0] != _ir.LIT:
            return None
        params = list(lam[1])
        if len(params) != 2:
            return None
        acc, val = params
        body = lam[2]
        if not (isinstance(body, tuple) and body and body[0] == _ir.BIN):
            return None
        op = body[1]
        if op not in ("+", "-", "*"):
            return None
        lhs, rhs = body[2], body[3]
        if not (isinstance(lhs, tuple) and lhs[0] == _ir.VAR and lhs[1] == acc):
            return None
        if not (isinstance(rhs, tuple) and rhs[0] == _ir.VAR and rhs[1] == val):
            return None
        if _has_free_data_ref(body) or _mentions_position_call(body):
            return None
        arr = self.emit(args[0])
        # `_as_list` 是**可证**提升（不是「也许」）：`_rd` 对非容器入参返回
        if not self._is_listy(args[0]):
            return None
        init = self.lit(args[2][1])
        helper = _OPS[op]
        acc_var = self.tmp()
        item_var = self.tmp()
        self._defs.append(f"{acc_var}={init}")
        self._defs.append(f"for {item_var} in {arr}:")
        self._defs.append(f" {acc_var}={helper}({acc_var},{item_var})")
        return acc_var

    def rfunc(self, qual: str, args) -> str:
        if qual not in self.rfuncs:
            self.rfuncs.append(qual)
        return "_x(" + repr(qual) + "," + \
            ",".join(self.emit(a) for a in args) + ")"

    def pyseg(self, source: str, names=None, root: str = "R") -> str:
        """反引号段：链解析复用 `split_chain`，与 JSONata 路径同一实现。
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
        # 反引号段里的裸 `$` 是当前行本身（JSONata 语境）。Python 里写不出裸 `$`。
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape('$')}",
                      _PY_ROW_PLACEHOLDER, text)
        for name in names:
            # `$.data` 这类**单段链**在 analysis 里与裸名 `data` 同形（链文本不含 "."）。
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

    # ---------------------------------------------------- py{} 多语句块
    def pyblock(self, node) -> str:
        """`("pyblock", stmts_source, [free...], loc)` -> 命名辅助函数的调用式。
"""
        stmts, free = node[1], list(node[2])
        loc = node[3] if len(node) > 3 else None
        key = (stmts, tuple(free))
        fn = self._pykeys.get(key)
        takes_root = False
        if fn is None:
            params = ["_p%d" % i for i in range(len(free))]
            # `_pyN` 是**模块级**函数，看不到 `run` 里的 `R`/`D` 局部名；块内裸 `$`
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
                "块的值就是结尾那条 `return EXPR`")
        if len(top) != 1 or len(every) != 1:
            raise Unsupported(
                "py{} 代码块有多个 return", loc,
                "块的返回值只能有一处")
        # 行内 py{} 的 `$.链` 首段必须是已声明字段（裸名一定写错成字段引用）。
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
        return self._field_value(name, spec)

    def _field_value(self, name: str, spec: dict) -> str:
        if "const" in spec:
            base = self.lit(spec["const"])
        elif "value" in spec:
            vpath = tuple(spec["value"])
            # 取值形态**只看 `optional` 一个开关**。
            _opt_only = bool(spec.get("optional")) and "default" not in spec
            _typed = spec.get("type") not in (None, "any")
            if _opt_only and not self.safe:
                base = self.get_text(vpath) if _typed else self.safe_ref(vpath)
            else:
                # required（含 `type:"any"`）与 self.safe 场景都走 emit_path。
                base = self.emit_path(vpath)
        elif "expr" in spec:
            # 与 `value` 分支同一条判定。
            base = self.emit(self._expr_node(spec))
        elif "fields" in spec:
            return self._emit_object(self.cur_field or name, spec)
        else:  # pragma: no cover - validate 保证 value/const/expr/fields 恰有其一
            raise SchemaError(self._fpath(),
                              "字段缺少 value/const/expr/fields（schema 未校验？）")
        if "default" in spec:
            return f"_df({base},{self.lit(spec['default'])})"
        return base

    def _read_safe(self, spec: dict) -> bool:
        """读这一格**本身不会抛**（缺键不会 KeyError/TypeError），可以进字面量。
"""
        if "default" in spec:
            return True
        if spec.get("optional") is True:
            return True
        if "const" in spec:
            return True
        if "fields" in spec:
            # 嵌套 object/位置行：这两条路一旦走字面量，就要求**每个子字段**都
            return all(self._read_safe(s) for s in spec["fields"].values())
        if "value" in spec:
            # `value` 分支的实际读法由 `field_value` 决定，与声明的 type 有关。
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
                    # 与顶层字段同一条判定（见 `body` 的 `_safe_read`）。
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
        # `_never_undef` 只回答「值不会是哨兵」，**不回答「路径读得到」**。
        flat = flat and all(self._read_safe(fields[k]) for k in fields)
        # 列级求值（`_col_value`）在 `return` 之后：令牌占位符由 `plan` 定好后
        if self._nested_col_mode:
            if spec.get("type") == "list":
                if flat:
                    return "[" + ",".join(self.subst(scope[k]) for k in fields) + "]"
                return "_rl([" + ",".join(self.subst(scope[k]) for k in fields) + "])"
            if flat:
                return "{" + ",".join(
                    f"{k!r}:{self.subst(scope[k])}" for k in fields) + "}"
            return "_ob([" + ",".join(
                f"({k!r},{self.subst(scope[k])})" for k in fields) + "])"
        if spec.get("type") == "list":
            # 位置行（type:"list" + fields）：按声明顺序（不是计算顺序）排成 list。
            if flat:
                return "[" + ",".join(scope[k] for k in fields) + "]"
            return "_rl([" + ",".join(scope[k] for k in fields) + "])"
        # 具名对象：可证全非 undefined 时发字典字面量。
        if flat:
            return "{" + ",".join(f"{k!r}:{scope[k]}" for k in fields) + "}"
        return "_ob([" + ",".join(
            f"({k!r},{scope[k]})" for k in fields) + "])"

    def _aggregate(self, names, specs=None, rows: str = "_o",
                   depth: int = 0, tab=None) -> str:
        """`shape:"dict"` 下的聚合表达式：每个字段一列。
"""
        names = list(names)
        if specs is None:
            specs = [self.fields[n] for n in names]
        if self._flat_row():
            return "{" + repr(names[0]) + ":" + rows + "}"
        cells = []
        # 行槽位映射：`body` 拼 `_c([...])` 时**跳过了直出 aggregate 字段**
        slot = {}
        if depth == 0:
            k = 0
            for n in names:
                if not self._direct_aggregate(self.fields[n]):
                    slot[n] = k
                    k += 1
        for i, (n, s) in enumerate(zip(names, specs)):
            if depth == 0 and n not in slot:
                # 该字段是直出 aggregate：行容器里根本没有它的格子，`body` 已经
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
                sub = list(s["fields"])
                subspecs = [s["fields"][k] for k in sub]
                direct = None if depth else self._aggregate_direct(
                    sub, subspecs, "_d")
                if direct is not None:
                    # 整层子键都能**逐列独立求值**：直接发子列表达式。
                    cells.append(direct)
                else:
                    # 子层的行流就是本层的 `col`：若它已被提升成短名（`_a`/`_a1`）。
                    sub_rows = tab[col] if (tab is not None and col in tab) else col
                    cells.append(self._aggregate(
                        sub, subspecs, sub_rows, depth + 1, tab))
            else:
                cells.append(col)
        return "{" + ",".join(f"{n!r}:{c}" for n, c in zip(names, cells)) + "}"

    def _aggregate_direct(self, names, specs, row_of: str = "_o"):
        """aggregate 对象的子键能否**绕开行容器**直接逐列求值。
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
"""
        saved, self.safe = self.safe, 0
        saved_raw, self.raw = self.raw, None
        # 列级求值发生在 `return` 之后：那里**看不到**行循环里提升的局部名
        saved_locals, self.locals = self.locals, {}
        saved_binds, self._binds = self._binds, {}
        saved_var, self._data_var = self._data_var, "r"
        # `expr` 的路径读取是否走安全模式，必须与 :meth:`field_value` 用**同一条
        safe_expr = bool(spec.get("optional")) or "default" in spec
        # `optional` / `default` **一个开关管两件事**：读法（安全链还是裸链）与
        opt = safe_expr
        try:
            if "fields" in spec:
                # 嵌套具名对象 / 位置行：整行内取一个对象值，再包一层列推导式。
                saved_nm, self._nested_col_mode = self._nested_col_mode, True
                try:
                    text = self._emit_object(key or "_", spec)
                finally:
                    self._nested_col_mode = saved_nm
                return "[" + text + " for r in " + row_of + "]"
            if "value" in spec:
                # 列级求值基座是原始行 `r`。
                path = spec["value"]
                opt = bool(spec.get("optional")) or "default" in spec
                # 单段 + 无 default：与行模式 `field_value` 的 `get_text` 同口径 ——
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
                    # required 逐段裸读、optional 逐段 `_ix_get`。
                    if opt:
                        text = "_ix_get(%s,%r)" % (text, k)
                    else:
                        text = "%s[%r]" % (text, k)
                if "default" in spec:
                    text = f"_df({text},{self.lit(spec['default'])})"
                elif opt:
                    # optional 无 default：缺键落 `None`，不是 undefined。
                    text = f"None if (_v:={text}) is _U else _v"
            else:
                if safe_expr:
                    self.safe = saved + 1
                text = self.subst(self.emit(self._expr_node(spec)))
                # DEFECT-017/018 + 接口 「`optional: true` 才生成守卫。
                if "default" in spec:
                    text = f"_df({text},{self.lit(spec['default'])})"
                elif opt:
                    text = f"None if (_v:={text}) is _U else _v"
        finally:
            self.safe = saved
            self.raw = saved_raw
            self.locals = saved_locals
            self._binds = saved_binds
            self._data_var = saved_var
        return "[" + text + " for r in " + row_of + "]"

    def _row_value(self, spec) -> str:
        """单行内取一个字段的值（`shape="rows"` 的无行容器快路径用）。
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
                    # required 逐段裸读、optional 逐段 `_ix_get`。
                    if opt:
                        text = "_ix_get(%s,%r)" % (text, k)
                    else:
                        text = "%s[%r]" % (text, k)
                if "default" in spec:
                    text = f"_df({text},{self.lit(spec['default'])})"
                elif opt:
                    # 与 `_col_value` L2595-2606 / `_row_value` 同一处修正
                    text = f"None if (_v:={text}) is _U else _v"
                return text
            if "fields" in spec:
                # 嵌套具名对象/位置行：逐行组装。
                saved_nm, self._nested_col_mode = self._nested_col_mode, True
                try:
                    return self._emit_object("_", spec)
                finally:
                    self._nested_col_mode = saved_nm
            # 与 :meth:`field_value` 逐字同口径（DEFECT-014）：**只看 optional /
            safe_expr = bool(spec.get("optional")) or "default" in spec
            if safe_expr:
                self.safe = saved[0] + 1
            text = self.subst(self.emit(self._expr_node(spec)))
            # `expr` 的 `default` 兜底，与 `_col_value` 同一处修正（DEFECT-016）。
            if "default" in spec:
                text = f"_df({text},{self.lit(spec['default'])})"
            elif bool(spec.get("optional")):
                # 与 `_col_value` 的 `expr` 分支同一处守卫（规则 7）：optional 无
                text = f"None if (_v:={text}) is _U else _v"
            return text
        finally:
            (self.safe, self.raw, self.locals, self._binds, self._data_var) = saved

    def _aggregate_plan(self, names, specs):
        """`shape="dict"` 递归聚合里值得提到 `return` 之前预算的列。
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
        for k, (col, var) in enumerate(out):
            for pcol, pvar in out[:k]:
                if pcol in col:
                    out[k] = (col.replace(pcol, pvar), var)
                    col = out[k][0]
        return out

    def _all_expr_roots(self) -> list:
        """本 schema 所有**顶层与嵌套** spec 的表达式 IR 根节点（含 `value` 路径）。
"""
        out: list = []

        def walk(specs: dict) -> None:
            for spec in specs.values():
                if not isinstance(spec, dict):
                    continue
                for key in ("expr", "value"):
                    if key in spec:
                        try:
                            node = self._expr_node(spec) if key == "expr" else None
                        except Exception:
                            node = None
                        if key == "value":
                            try:
                                node = self._value_node(spec) if hasattr(
                                    self, "_value_node") else None
                            except Exception:
                                node = None
                        if node is not None:
                            out.append(node)
                if isinstance(spec.get("fields"), dict):
                    walk(spec["fields"])

        walk(self.fields)
        return out

    def collect_refs(self) -> set:
        """顶层字段里被**裸名**引用到的字段 —— 只有它们需要落成局部名。
"""
        refs: set = set()
        for spec in self.fields.values():
            refs |= self._names(spec, set())
        return refs & set(self.fields)

    def _names(self, spec: dict, shadow: set) -> set:
        """递归收集 spec 里出现的裸名；`shadow` 是已进入的嵌套作用域声明的名字。
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
                    chains = {m.group(1) for m in FIELD_REF.finditer(node[1])}
                    for n in node[2]:
                        if isinstance(n, str) and n.isidentifier() and n not in chains:
                            out.add(n)
                # `py{}` 块（PYBLOCK）：`$.链` 只做「已声明字段」校验、读原始行。
            for item in node:
                self._node_names(item, out)

    def body(self):
        """按**计算顺序**（依赖拓扑序）取值；输出行仍按**声明顺序**组装。
"""
        self._row_parts = None     # 见 `_stream_fold_plan`：行字面量的逐格表达式
        if self._columns_from_source() is not None:
            # 无共享：不建行容器、不逐行求值，`_finish_expr` 直接发逐列推导式。
            self.locals = {}
            return [], None

        defs: list = []
        values: dict = {}
        self._defs = defs          # 嵌套 fields 的字段绑定也追加到同一 row-loop

        def value(name, spec):
            mode = self.raw_mode(spec)
            old, self.raw = self.raw, mode
            prev = self._enter(name, spec)
            saved_cse, self._cse = self._cse, {}
            try:
                # 先把本字段表达式里重复的昂贵子树提升成行级临时量（P1：CSE）。
                self._cse_plan(spec)
                return self.field_value(name, spec)
            finally:
                self._cse = saved_cse
                self.raw = old
                self.cur_field, self.cur_key = prev

        for name in self._topo():
            spec = self.fields[name]
            if self._direct_aggregate(spec) and not spec.get("optional"):
                # 整层子键都能绕开行容器直出列：行里那一格不需要存在（B.8-3）。
                continue
            if spec.get("optional") and "default" not in spec:
                # : optional 才生成 undefined 守卫；undefined -> null
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
            # 单一输出键：行就是**那个值**本身（同层没有别的字段可能引用它）。
            (only,) = tuple(self.fields)
            if only in values:
                return defs, values[only]
        parts = [values[n] for n in self.fields
                 if not self._direct_aggregate(self.fields[n])]
        # 逐格表达式单独留一份：`_stream_fold_plan` 要按列下标取其中一格
        self._row_parts = parts
        row = "[" + ",".join(parts) + "]"
        return defs, row

    def _direct_aggregate(self, spec) -> bool:
        """该字段是不是「整层子键都能绕开行容器直出列」的 aggregate 对象。
"""
        if self.shape != "dict":
            # 只有 `dict` 是**列式**容器，`aggregate` 的"列容器"语义才成立。
            return False
        if not (spec.get("aggregate") and spec.get("type") == "object"
                and "fields" in spec):
            return False
        sub = list(spec["fields"])
        subspecs = [spec["fields"][k] for k in sub]
        return self._aggregate_direct(sub, subspecs, "_d") is not None

    def _columns_from_source(self):
        """`dict` 形状没有行容器时，逐字段从**原始行**出列的表达式表。
"""
        if self.shape not in ("dict", "rows") or self._refs:
            return None
        for spec in self.fields.values():
            if spec.get("aggregate"):
                # 能整层直出的 aggregate 不占行槽位，与无行容器相容；不能直出的
                if not self._direct_aggregate(spec):
                    return None
                continue
            if "fields" in spec:
                # 嵌套具名对象 / 位置行：「先凑前 42 字符」→ 逐行独立求值也能
                if self._nested_has_bare_sibling_ref(spec["fields"]):
                    return None
                continue
            if "const" in spec:
                continue
            if "value" in spec or "expr" in spec or "default" in spec:
                continue
            return None
        return None if not self.fields else {}

    def _nested_has_bare_sibling_ref(self, fields: dict) -> bool:
        """嵌套 `fields` 里是否存在「子键按裸名引用同层兄弟」（含更深层递归）。
"""
        if not isinstance(fields, dict):
            return False
        names = set(fields)
        for k, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            # 本层互引：与 `_emit_object` 的 `deps[k] = (names(spec) & names) - {k}`
            if self._names(spec, set()) & (names - {k}):
                return True
            # 更深层的嵌套 fields 同样递归判（内层也可能互引）
            deep = spec.get("fields")
            if isinstance(deep, dict) and self._nested_has_bare_sibling_ref(deep):
                return True
        return False

    def _flat_row(self) -> bool:
        """行能否**裸存**（省掉「每行单元素 list + 末尾转置」的外壳）。
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
        # 元素类型推导（A 项）：从**使用方式**反推 `type:"list"` 字段的元素类型。
        self._infer_elem_types(self._all_expr_roots())
        # 类型推导默认关闭：`type: "any"` 的值域包含 null 与缺失键，从运算上下文
        self._refs = self.collect_refs()
        defs, row = self.body()          # 先收齐引用，再决定前缀绑定
        # `row is None` = `dict` 形状的无行容器快路径（见 `body`）：没有任何
        no_row = row is None
        self.plan()
        defs = [self.subst(s) for s in defs]
        if not no_row:
            row = self.subst(row)
            if self._row_parts is not None:
                # 逐格表达式必须与 `row` 同步变换，否则 `_stream_fold_plan`
                self._row_parts = [self.subst(p) for p in self._row_parts]
        pre = [self.subst(s) for s in self._pre]
        defs, pre, row = self._unbind_trivial(defs, pre, row)
        L: list = []
        if self.rfuncs:
            # `$` 用户函数：产物内一次性解析，之后按函数对象调用。
            L += ["import importlib as _il",
                  "_xr={}",
                  "def _x(q,*a):",
                  " f=_xr.get(q)",
                  " if f is None:",
                  "  s=q.rfind('.')",
                  "  while s>0:",
                  "   try:",
                  "    m=_il.import_module(q[:s]);o=m",
                  "    for k in q[s+1:].split('.'):",
                  "     o=getattr(o,k)",
                  "    f=_xr[q]=o;break",
                  "   except ImportError:",
                  "    s=q.rfind('.',0,s)",
                  "  else:",
                  "   raise ImportError('$ 函数不可导入: '+q)",
                  " return f(*a)"]
        # `py{}` 多语句块的命名辅助函数：定义在 run **之外**，每块只发一次
        for blk in self.pyblocks:
            L.append(blk)
        emitted_blocks = len(self.pyblocks)
        # `before` / `after` 的外部函数形态（`@pkg.mod.fn`）：按需 import。
        hook_funcs = [h[1] for h in (self.before, self.after)
                      if h is not None and h[0] == _func_kind()]
        hook_names = iter(f"_h{i}" for i in range(len(hook_funcs)))
        # 钩子的表达式形态必须先编译：里面的 `py{}` 会登记模块级辅助函数。
        for hook in (self.before, self.after):
            if hook is not None:
                self._hook_text(hook, iter(hook_names))
        # 钩子表达式里若出现 py{}，辅助函数是这一轮新登记的 —— 补发。
        for blk in self.pyblocks[emitted_blocks:]:
            L.append(blk)
        emitted_blocks = len(self.pyblocks)
        # `py{}` 辅助函数的**统一归位点**：模块级 `def _pyN(...)` 一律发在
        py_tail = len(L)
        if hook_funcs:
            # `@pkg.mod.fn` 的 qualname 可能带类名。
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
        def_at = len(L)
        L.append("def run(_d):")
        if self.cython:
            L.append(" cdef object _o")
            for s in self.cdef_names(defs, pre):
                L.append(" cdef object " + s)
        # runtime helper 的**局部绑定**插点（见 `_local_binds`）：必须在 `cdef`
        bind_at = len(L)
        if self.src:
            # `input.source` 是**输入数据里的行集合路径**，与字段 `value` 同一套
            txt = "_d"
            for seg in self.src:
                txt += f"[{seg!r}]" if isinstance(seg, str) else f"[{seg}]"
            L.append(" _d=" + txt)
        # `before`：**整体数据**前处理。它拿到的是经 `input.source` 定位后的全量
        pro_at = len(L)      # `$lookup` 槽位绑定插点（字段发射后才齐全）
        before_expr = self._hook_text(self.before, hook_names)
        if before_expr is not None:
            L.append(f" _d=_hb({before_expr}(_d))")
        if self.where:
            # `output.where`：逐行布尔表达式，作用在**输入行**上（与字段 `expr`
            # 同作用域，`$.` = 当前行）；早于行循环，故 `out_count` 按留下的行算。
            L.append(" _d=[R for R in _d if (%s)]"
                     % self.subst(self._where_cond()))
        # `after` 是否命中「折叠某一列」的窄图样（见 `_stream_fold_plan`）。必须在
        stream = None if no_row else self._stream_fold_plan()
        if no_row:
            # 无行容器快路径（`dict` 形状、无跨字段共享）：不建 `_o`、不逐行循环。
            L.append(" " + self.finish())
        else:
            L.append(" _o=[]")
            # `append = doc.append`：方法绑定提到循环外。
            L.append(" _c=_o.append")
            if stream is not None and stream[2]:
                # undefined 哨兵绑成局部（见下面的过滤分支）
                L.append(" _u=_U")
            L.append(" for R in _d:")
            ind = "  "
            for s in pre:
                L.append(ind + s)
            for s in defs:
                L.append(ind + s)
            # 行必须是 **list**；用 append 而不是
            if stream is None:
                L.append(ind + "_c(" + row + ")")
            elif stream[2]:
                # 扁平列 + 逐行过滤（只有 `$average` 需要）：`_o` 存的是那一格的
                L.append(ind + "_v=" + self._row_parts[stream[1]])
                L.append(ind + "if _v is not _u: _c(_v)")
            else:
                # 扁平列（`$sum`/`$min`/`$max`）：helper 自己会跳过 undefined。
                L.append(ind + "_c(" + self._row_parts[stream[1]] + ")")
            if self.trace:
                # trace 产物：额外记录每行的原始行与输出行（正式产物不含这两行）
                L.append(ind + "TRACE.append((R,_o[-1]))")
            L.append(" " + self.finish())
        # `after`：**整体结果**后处理。它拿到的是按 shape 组装完成的全量结果。
        after_expr = None if stream is not None else self._hook_text(
            self.after, hook_names)
        if stream is not None:
            # 扁平列已经就是 `$map` 的结果，`after` 的折叠直接套在 `_o` 上。
            L[-1] = f" return _ha({stream[0]}({L[-1][8:]}))"
        elif after_expr is not None:
            L[-1] = f" return _ha({after_expr}({L[-1][8:]}))"
        # `shape="dict"` 里被多个子键共用的列，在 return 前算一次、起个短名
        for col, var in self._shared_columns():
            L.insert(len(L) - 1, " " + var + "=" + col)
        # `py{}` 辅助函数的**第三次**补发：`dict`/`rows` 走「无行容器快路径」时。
        late_blocks = self.pyblocks[emitted_blocks:]
        if late_blocks:
            L[py_tail:py_tail] = late_blocks
            # 插入点是列表**中部**（`py_tail` 之后还有 `_sh` 等模块级辅助）。
            bind_at += len(late_blocks)
            def_at += len(late_blocks)
            emitted_blocks += len(late_blocks)
        src = "\n".join(L) + "\n"
        if self.lookups:
            # 签名与规格表在**字段发射之后**才定：`$lookup` 是在 `finish()` 里
            # 才被编译到的（`self.lookups` 此时才齐全）。
            L[def_at] = "def run(_d,tables=None):"
            pro = [" _T=_dot(tables,_SP)"]
            pro += [" _i%d=_T[%r].s%d" % (i, s["table"], i)
                    for i, s in enumerate(self.lookups)]
            L[pro_at:pro_at] = pro
            bind_at += len(pro)
            L.insert(def_at, "_SP=" + repr(self.lookups))
            def_at += 1
            bind_at += 1
            src = "\n".join(L) + "\n"
        binds = self._local_binds(src)
        if binds:
            L.insert(def_at, "_g=globals()")
            L[bind_at + 1:bind_at + 1] = binds
            src = "\n".join(L) + "\n"
        # 反引号段里可以直接写 `PY_SAFE_MODULES` 的模块名（`json.loads($)`、`re.sub(...)`）。
        for mod in _safe_modules_used(src):
            src = f"import {mod}\n" + src
        if "math." in src and not src.startswith("import math\n"):
            src = "import math\n" + src
        # 只有真的用到 runtime helper 才发 import 行（CAP/M0 裸运算后一行都不需要）
        if self._uses_helpers(src):
            src = _RUNTIME_IMPORT + "\n" + src
        if self.lookups:
            src = "from dcg.table import _tm as _dot\n" + src
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
"""
        if hook is None:
            return None
        kind, payload = hook
        if kind == "func":
            # 外部函数已在 `run` 之外解析成 `_hN`；`func_names` 是按
            return next(func_names)
        # 表达式形态：钩子是**整体数据级**的值，裸 `$` 就是全量数据本身
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
        """把「值是单个局部名」的字段局部**收回内联**。
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
                if self._row_parts is not None:
                    # 逐格表达式同步收回内联（见 `_stream_fold_plan`）
                    self._row_parts = [f(x) for x in self._row_parts]
                changed = True
                break
        return defs, pre, row

    def _stream_fold_plan(self):
        """识别「`list` 形状 + `after` 恰好折叠某一列」的窄图样 -> `(helper, K)`。
"""
        if self.shape != "list" or self.trace:
            return None
        parts = self._row_parts
        if not parts or self.after is None or self.after[0] != "expr":
            return None
        node = self._expr_node({"expr": self.after[1]})
        if _ir.kind(node) != _ir.CALL or len(node[2]) != 1:
            return None
        helper = _STREAM_FOLDS.get(node[1])
        if helper is None:
            return None
        inner = node[2][0]
        if _ir.kind(inner) != _ir.CALL or inner[1] != "$map" \
                or len(inner[2]) != 2:
            return None
        src, fn = inner[2]
        if _ir.kind(src) != _ir.ROOT or _ir.kind(fn) != _ir.LAMBDA:
            return None
        params, body = fn[1], fn[2]
        if len(params) != 1 or _ir.kind(body) != _ir.INDEX:
            return None
        base, idx = body[1], body[2]
        if _ir.kind(base) != _ir.VAR or base[1] != params[0]:
            return None
        if _ir.kind(idx) != _ir.LIT:
            return None
        k = idx[1]
        # `type(k) is not int`：排除 `True`。
        if type(k) is not int or not 0 <= k < len(parts):
            return None
        # `$average` 的 helper 是 `_sm/len`，**分母**就是 `_mp` 丢弃 undefined
        return helper, k, helper == "_av"

    def cdef_names(self, defs, pre) -> list:
        """cython 模式下 `cdef` 声明的局部量。"""
        names = ["R", "_c"]
        for s in list(pre) + list(defs):
            lhs = s.split("=", 1)[0].strip()
            if lhs and lhs.isidentifier() and lhs not in names:
                names.append(lhs)
        return names

    def finish(self) -> str:
        """形状包装 —— **编译期**定型，运行期零形状判断。"""
        e = self._finish_expr()
        if self.expand:
            # `output.expand`：1:N 展开放在 shape 组装之后、`after` 之前。
            e = "_xp(%s,%r)" % (e, self.expand)
        return "return " + e

    def _where_cond(self) -> str:
        """编译 `output.where`：`$l.` / `$.` 指当前输入行，不允许 `$r.`。"""
        text, info = self._pred_rewrite(self.where)
        for base, _p in info.values():
            if base == "r":
                raise SchemaError(
                    "output.where",
                    "output.where 里不能用 $r.（只有 $lookup 的谓词才有右行）；"
                    "要用右表字段请把条件写进 $lookup 的第 7 个参数")
        node = self._expr_node({"expr": text})
        saved = dict(self._params)
        self._params[self._data_var] = self._data_var
        self._params.update(self._pred_map(info, self._data_var))
        try:
            return self.emit(node)
        finally:
            self._params = saved

    def _finish_expr(self) -> str:
        """输出形状的最终表达式（不含 `return`）。"""
        if self.shape == "dict":
            # 无行容器快路径优先：`body` 返回 `row=None` 时正是在走这条。
            if self._columns_from_source() is not None:
                # 每个字段各是一条从原始行 `_d` 起算的推导式。与 `_aggregate`
                cols = []
                for n, s in self.fields.items():
                    if s.get("aggregate"):
                        # 能整层直出的 aggregate：子键各自成列，直接从 `_d` 求值
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
                # 单键：`_o` 里已经是该字段的值列，无需再转置
                return "{" + repr(next(iter(self.fields))) + ":_o}"
            names = list(self.fields)
            specs = [self.fields[n] for n in names]
            plan = self._aggregate_plan(names, specs)
            if not plan:
                return self._aggregate(names, specs)
            tab = {col: var for col, var in plan}
            # 递归时父列已经被替换成短名，子键看到的候选表达式是「短名版」。
            for col, var in plan:
                for pcol, pvar in plan:
                    if pcol in col and pcol != col:
                        tab[col.replace(pcol, pvar)] = var
            return self._aggregate(names, specs, tab=tab)
        if self.shape == "rows":
            # rows 行形态：**每行一个 {字段名: 值} dict**（不是列式）。
            keys = ",".join(repr(n) for n in self.fields)
            # 无行容器快路径（`body` 返回 `row=None`）：直接对原始数据出一行一个
            if self._columns_from_source() is not None:
                if self._flat_row():
                    k = repr(next(iter(self.fields)))
                    s = next(iter(self.fields.values()))
                    # `const` 必须先分流：`_row_value` 只认 value/expr/fields。
                    if "const" in s:
                        body = self.lit(s["const"])
                    else:
                        body = self._row_value(s)
                    return "[{" + k + ":" + body + "} for r in _d]"
                parts = []
                for n, s in self.fields.items():
                    if s.get("aggregate"):
                        # 不可达：`rows` 形状下带
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


def _check_aggregate_shape(norm: dict) -> None:
    """`aggregate` 只在 `shape="dict"` 下有意义，其余形状一律**构建期**报错。
"""
    shape = norm["output"]["shape"]
    if shape == "dict":
        return

    def walk(fields: dict, prefix: str) -> None:
        for fname, spec in fields.items():
            path = f"{prefix}.{fname}"
            if spec.get("aggregate"):
                raise SchemaError(
                    path,
                    f'"aggregate" 只在 output.shape 为 "dict" 时生效（它把该 '
                    f'object 字段拆成子键「列」，行式容器没有列的位置），当前 '
                    f'output.shape 是 "{shape}"；要聚合请把 output.shape 改成 '
                    f'"dict"，或删掉该字段的 "aggregate"')
            sub = spec.get("fields")
            if sub:
                walk(sub, path + ".fields")

    walk(norm["out"]["fields"], "out.fields")


def _qualname_importable(q: str) -> tuple[bool, str]:
    """qualname 能否被产物运行期的 `_x` 解析回来（与产物同款「从长到短试」）。
"""
    if not q:
        return False, "qualname 为空"
    if "<locals>" in q or "<lambda>" in q or "<listcomp>" in q:
        return False, f"qualname 不可导入（局部作用域/匿名函数）：{q}"
    s = q.rfind(".")
    first_err = ""
    while s > 0:
        try:
            mod = _importlib.import_module(q[:s])
        except Exception as e:                      # noqa: BLE001 - 见下
            if not first_err:
                first_err = f"{type(e).__name__}: {e}"
            s = q.rfind(".", 0, s)
            continue
        o = mod
        try:
            for k in q[s + 1:].split("."):
                o = getattr(o, k)
        except AttributeError as e:
            # 模块对了但属性链断了：换更短的前缀也没意义，直接判失败
            return False, f"属性链不可取（{e}）；模块 {q[:s]!r} 里没有 " \
                          f"{q[s + 1:]!r}"
        return True, ""
    return False, (f"没有可取的前缀能 import 成模块（最后一次错误：{first_err}）；"
                   f"产物在运行期也无法解析，注册对象必须模块级可导入")


def _check_rfunc_importable(quals) -> None:
    """构建期验证每个 `rfunc` 的 qualname 能被产物 import 回来（见 `_qualname_importable`）。
"""
    bad = []
    for q in quals:
        ok, why = _qualname_importable(q)
        if not ok:
            bad.append(f"$ 函数 {q!r} 在产物运行期无法导入：{why}")
    if bad:
        raise SchemaError(
            "out.fields", "；".join(bad) +
            "。修复：把注册对象定义在**模块级**（不要用 lambda / 闭包 / 局部函数），"
            "或放在可 import 的模块里再注册")


def _generate_norm(norm: dict, *, cython: bool = False,
                   inline_optional: bool = True,
                   trace: bool | None = None, registry=None,
                   strict_qualname: bool = True) -> str:
    """已规范化的 schema -> 源码（不再跑 validate；供 build 复用，P0-2）。"""
    _check_reserved(norm)
    _check_aggregate_shape(norm)
    g = _Gen(norm, cython, inline_optional, trace, registry)
    if strict_qualname:
        _check_rfunc_importable(g.rfuncs)
    src = g.module(schema_sha1(norm))
    if not cython:
        compile(src, "<dcg-generated>", "exec")   # 唯一一次 compile
    return src


def generate(schema: dict, *, cython: bool = False,
             inline_optional: bool = True, registry=None,
             strict_qualname: bool = True) -> str:
    """规范 schema -> 产物源码。纯函数；纯 Python 模式下只 `compile` 一次。
"""
    return _generate_norm(validate(schema), cython=cython,
                          inline_optional=inline_optional, registry=registry,
                          strict_qualname=strict_qualname)


def specs_of(schema: dict, *, registry=None) -> list:
    """schema 需要的右表索引规格（按 `$lookup` 出现顺序，即槽位号 `s0`、`s1`…）。

    每项是 dict：``{"table","key","mode","strategy","kind","hi"}``；`kind` 为
    ``"hash"`` 或 ``"range"``。`dcg.prepare` / `dcg.bind` 用它把 schema 与调用方
    建好的 ``dcg.Table`` 对上。**纯查询**：不产生副作用，也不落盘。
    """
    g = _Gen(validate(schema), False, True, registry=registry)
    g.module()
    return [dict(s) for s in g.lookups]


def _runtime_fingerprint() -> str:
    """`dcg.runtime` 的 helper 指纹（sha1 前 16 位）。
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
"""
    try:
        from . import __version__ as _v
    except Exception:                                  # pragma: no cover
        _v = "unknown"
    return f"{_v}.g1"


def build(schema: dict, out_dir: str, *, name: str | None = None,
          overwrite: bool = False, registry=None,
          strict_qualname: bool = True) -> dict:
    """落盘 `gen/<name>.py` + `gen/<name>.manifest.json` + manifest。
"""
    norm = validate(schema)
    if name is not None:
        norm = dict(norm, name=name)
    mod = norm["name"]
    sha1 = schema_sha1(norm)
    src = _generate_norm(norm, registry=registry,
                         strict_qualname=strict_qualname)
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
        tsrc = _generate_norm(norm, trace=True, registry=registry,
                              strict_qualname=strict_qualname)
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
    mpath = os.path.join(gen_dir, mod + ".manifest.json")
    with open(mpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n")
    return manifest


def load(path: str, mod_name: str | None = None):
    """把 `build` 落盘的产物当模块加载（验证脚本与测试用）。"""
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
