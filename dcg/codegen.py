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


def _is_const(spec: dict) -> bool:
    """常量字段（`const` 或引用了纯常量表达式）：逐行同值。"""
    return "const" in spec


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
        self._math: bool = False
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
        self._exprs: dict = {}       # expr 源码文本 -> IR（记忆化：见 _expr_node）
        self.pyblocks: list = []     # `py{}` 多语句块的命名辅助函数（定义在 run() 外）
        self._pykeys: dict = {}      # (块体, free 元组) -> `_pyN`，同块只发一次
        self._pyroot_args: dict = {}  # 同上 key -> 该块是否多接一个「全量数据」形参
        self.cur_field = ""          # 正在取值的字段（SchemaError 指到具体字段）
        self.cur_key = ""            # 该字段正在取值的键：value/const/expr/fields
        self._nested_scopes: list = []  # 嵌套 fields 的作用域栈（由内到外查找，内层遮蔽外层）
        self._nested_specs: list = []   # 与 _nested_scopes 平行的 spec 栈（判裸名绑定的可证性用）
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
        """optional 字段的取值链：每一步都用 `_ix_get`（缺键 -> UNDEF，不抛 KeyError）。"""
        t = self._data_var
        for k in path:
            t = f"_ix_get({t},{k!r})"
        return t

    def emit_path(self, path) -> str:
        path = tuple(path)
        if not path:
            return self._data_var
        if self.safe:
            return self.safe_text(path)
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
        prefixes = set()
        for p in self.order:
            for L in range(1, len(p) + 1):
                prefixes.add(p[:L])
        children: dict = {}
        for p in self.order:
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
        self._pre = [f"{self._binds[p]}={self.bind_rhs(p)}" for p in chosen]

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
        # 单个字符串下标 -> 用 _ix_get：`x=R['k']` 是 buffer/doc 中转形状（规则③）
        if len(steps) == 1 and isinstance(steps[0], str):
            return f"_ix_get({base},{steps[0]!r})"
        return base + self.suffix(steps)

    def text_of(self, p: tuple) -> str:
        if p in self._binds:
            return self._binds[p]
        for L in range(len(p) - 1, 0, -1):
            if p[:L] in self._binds:
                return self._binds[p[:L]] + self.suffix(p[L:])
        return self._data_var + self.suffix(p)

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
            return self.field_name(node[1])
        if k == _ir.INDEX:
            path = _chain(node)
            if path is not None:
                if not self.safe and len(path) == 1 \
                        and isinstance(path[0], str):
                    # 表达式里的单段路径（$.x）：缺键 -> undefined（契约 §4.4 miss）
                    return f"_ix_get({self._data_var},{path[0]!r})"
                return self.emit_path(path)
            base = self.emit(node[1])
            idx = node[2]
            holder = node[1]
            if idx[0] == _ir.LIT and isinstance(idx[1], int) \
                    and not isinstance(idx[1], bool) \
                    and holder[0] in (_ir.FIELD, _ir.VAR) \
                    and self._decl_type(holder[1]) is not None:
                # 已声明类型（required）的字段 + 常量整数下标 -> 直接下标。
                # 与 `$.a[0]` 的常量链口径一致：缺键/越界本来就是 KeyError/IndexError。
                return f"{base}[{self.lit(idx[1])}]"
            if idx and idx[0] in (_ir.FIELD, _ir.VAR):
                # §3.6 裁定 (i)：`$.a[b]` ≡ `$.a[$.b]` —— 裸名是**字段引用**，
                # 未声明就是 build 期 SchemaError（列出可用字段），绝不是行键查找。
                itext = self.field_name(idx[1])
            else:
                itext = self.emit(idx)
            if self.safe:
                # optional / default 字段：越界或类型不符要落成 undefined（交给守卫或默认值）
                return f"_ix_get({base},{itext})"
            # required 字段：没有 optional/default 就没有 undefined 出口，缺数据即真实错误。
            # base 若是行路径就直接裸读（不走绑定里的安全读值，否则会回退成 _ix_get）。
            bp = _chain(node[1])
            raw_base = self._data_var + self.suffix(bp) if bp is not None else base
            return f"{raw_base}[{itext}]"
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
        """已声明字段的**静态类型**；optional / 未声明 → None（不可证安全）。"""
        spec = self.fields.get(name)
        if not spec or spec.get("optional"):
            return None
        return spec.get("type")

    def _ok_num(self, node) -> bool:
        k = node[0]
        if k == _ir.LIT:
            v = node[1]
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if k == _ir.INDEX:
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
            return (n == 2 and self._ok_str(args[0])
                    and args[1][0] == _ir.LIT and isinstance(args[1][1], str)
                    and args[1][1] != "")
        if fname in ("$substringBefore", "$substringAfter"):
            return n == 2 and self._ok_str(args[0]) and self._ok_str(args[1])
        if fname == "$replace":
            return n == 3 and all(self._ok_str(x) for x in args)
        if fname == "$pad":
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
        if fname in ("$floor", "$ceil", "$round"):
            return n == 1 and self._ok_num(args[0])
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
            self._math = True
            return f"math.floor({a0})"
        if fname == "$ceil":
            self._math = True
            return f"math.ceil({a0})"
        if fname == "$sqrt":
            self._math = True
            return f"math.sqrt({a0})"
        if fname == "$round":
            return f"round({a0})"
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
        """高阶函数：数组等实参照常发射，回调 lambda 就地发射（接口文档 §3.5「B 类」/ §3.6）。"""
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
        parts = [self.emit_lambda(a) if a is lam else self.emit(a) for a in args]
        return fn + "(" + ",".join(parts) + ")"

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
                        f"{sorted(self.fields)}（行内取值请写 $.{segs[0]} 或 $.路径）")

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
            # `optional` 且无 `default`：缺键必须落成 None（契约 §2.3），而
            # `emit_path` 对固定布局发的是裸读 `R['x']`——缺键直接 KeyError，
            # 承诺落空。safe 模式发 `_ix_get` 链才能把缺键变成哨兵。
            # （带 default 的 optional 由 `_df` 兜底，走哪条路都能拿到哨兵。）
            _opt_only = bool(spec.get("optional")) and "default" not in spec
            if (spec.get("type") in (None, "any") or _opt_only) and not self.safe:
                # type 未声明 / any / optional：读值保留 undefined 语义（缺键 -> UNDEF）
                base = self.safe_text(vpath)
            else:
                base = self.emit_path(vpath)
        elif "expr" in spec:
            base = self.emit(self._expr_node(spec))
        elif "fields" in spec:
            return self._emit_object(self.cur_field or name, spec)
        else:  # pragma: no cover - validate() 保证 value/const/expr/fields 恰有其一
            raise SchemaError(self._fpath(),
                              "字段缺少 value/const/expr/fields（schema 未校验？）")
        if "default" in spec:
            return f"_df({base},{self.lit(spec['default'])})"
        return base

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
                    expr = self.field_value(k, s)
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
        for i, (n, s) in enumerate(zip(names, specs)):
            col = f"[r[{i}]for r in {rows}]" if depth == 0 else \
                  f"[r[{n!r}]for r in {rows}]"
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
                direct = None if depth else self._aggregate_direct(sub, subspecs)
                if direct is not None:
                    # 整层子键都能**逐列独立求值**：直接发子列表达式，
                    # 不必每行先造一个 dict 再 `[r[i]for r in _o]` 捞回来。
                    cells.append(direct)
                else:
                    cells.append(self._aggregate(
                        sub, subspecs, col, depth + 1, tab))
            else:
                cells.append(col)
        return "{" + ",".join(f"{n!r}:{c}" for n, c in zip(names, cells)) + "}"

    def _aggregate_direct(self, names, specs):
        """aggregate 对象的子键能否**绕开行容器**直接逐列求值。

        能绕开时返回该对象的列表达式，否则返回 None。

        背景：`aggregate` 字段过去总是先把整行里那一格造成 `_ob([...])` /
        字典字面量，再用 `[r[i]for r in _o]` 把它捞回来逐子键拆列 —— 每行多造
        一个 dict、再全表扫一遍。

        放行条件（保守）：
        * 子键全是单字段的 `expr`（无 `fields`、无 `optional`、非 `value`/`const`）
          —— `expr` 的列级求值只看原始行，不依赖行容器里那一格的形状；
        * `expr` 不引用兄弟字段名、也不引用任何已声明字段名（列级求值时没有
          这些局部名，只有 `r` 这个原始行）。

        其余形态一律返回 None、退回老路（行容器 + `[r[i]for r in _o]` 转置）。
        因为行容器里那一格仍是 `_ob([...])` 造的 dict，直出列若按 `value` 路径
        寻址会取到整个 dict 而不是标量 —— 这是 readme_complex 与
        TestOutputAggregate 回归的根因，故 `value`/`const` 暂不放行。
        """
        if not specs:
            return None
        known = set(self.locals) | set(names)
        for s in specs:
            if "fields" in s or s.get("optional") or "expr" not in s:
                return None
            if self._expr_names(s) & known:
                return None
        parts = []
        for n, s in zip(names, specs):
            parts.append(f"{n!r}:" + self._col_value(s))
        return "{" + ",".join(parts) + "}"

    def _expr_names(self, spec):
        out = set()
        if "expr" in spec:
            self._node_names(self._expr_node(spec), out)
        return out

    def _col_value(self, spec, key=None):
        """把「每行一个值」的表达式改成「整列」表达式：`X` -> `[X for r in _o]`。

        `emit()` 发的是带占位符 `_TOKEN` 的文本，必须立刻 `subst()` 解析掉，
        否则占位符会原样进产物（`U+0001` 直接是语法错误）。
        """
        saved, self.safe = self.safe, 0
        saved_raw, self.raw = self.raw, None
        # 列级求值发生在 `return` 之后：那里**看不到**行循环里提升的局部名
        # （`_fid`/`_arr` 等只在 `for R in _d` 体内有效，出循环后保留的是最后
        # 一次迭代值）。所以裸字段引用必须重新落回 `R[...]` 取值，不能发局部名。
        saved_locals, self.locals = self.locals, {}
        saved_binds, self._binds = self._binds, {}
        try:
            if "value" in spec:
                # 列级求值：基座是列表推导的循环变量 `r`（不是行循环的 `R`）。
                # `r` 是**该 aggregate 子键所在的字典**，所以首段要换成子键名。
                path = spec["value"]
                text = "_ix_get(r,%r)" % (path[0],) if path else "r"
                for k in path[1:]:
                    text = "_ix_get(%s,%r)" % (text, k)
            else:
                text = self.subst(self.emit(self._expr_node(spec)))
        finally:
            self.safe = saved
            self.raw = saved_raw
            self.locals = saved_locals
            self._binds = saved_binds
        return "[" + text + " for r in _o]"

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
                sub_tab = tab
                if len(sub) >= 2:
                    # 这层被 >=2 个子键复用：它自己先用父层的短名，再登进表
                    plan.append(tab.get(col, col))
                    sub_tab = dict(tab)
                    sub_tab[col] = "\x00" + str(len(plan) - 1)
                walk(sub, [s["fields"][k] for k in sub], col, d + 1, sub_tab)

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
        """
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
            if spec.get("optional") and "default" not in spec:
                # §4.2 规则 7: optional 才生成 undefined 守卫；undefined -> null
                self.safe += 1
                try:
                    expr = value(name, spec)
                finally:
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
            (only,) = tuple(self.fields)
            return defs, values[only]
        row = "[" + ",".join(values[n] for n in self.fields) + "]"
        return defs, row

    def _flat_row(self) -> bool:
        """行能否**裸存**（省掉「每行单元素 list + 末尾转置」的外壳）。

        只对 `dict` / `rows` 形状成立：这两种形状的一行本来就是「字段名 -> 值」，
        单键时 `{k:_o}`、`[{k:r} for r in _o]` 与原来的 `{k:[r[0] for r in _o]}`、
        `[{k:r[0]} ...]` 逐值相等，容器方向不变。

        `list` / `index` 形状的**行本身就是 list**（`[[a0],...]`、`{0:[a0]}`），
        裸存会改变输出结构，因此不启用。
        """
        return len(self.fields) == 1 and self.shape in ("dict", "rows")

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
    def module(self, sha1: str = "") -> str:
        self._refs = self.collect_refs()
        defs, row = self.body()          # 先收齐引用，再决定前缀绑定
        self.plan()
        defs = [self.subst(s) for s in defs]
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
            L.append("import importlib as _il")
            for i, qual in enumerate(hook_funcs):
                last = qual.rfind(".")
                L += [f"_hq{i}={qual!r}",
                      f"_hp{i}=_hq{i}.rfind('.')",
                      f"_h{i}=getattr(_il.import_module(_hq{i}[:_hp{i}]),"
                      f"_hq{i}[_hp{i}+1:])"]
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
        before_expr = self._hook_text(self.before, hook_names)
        if before_expr is not None:
            L.append(f" _d={before_expr}(_d)")
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
        after_expr = self._hook_text(self.after, hook_names)
        if after_expr is not None:
            L[-1] = f" return {after_expr}({L[-1][8:]})"
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
        if self.shape in ("dict", "index"):
            if self.shape == "index":
                return "{i:r for i,r in enumerate(_o)}"
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
            if self._flat_row():
                k = repr(next(iter(self.fields)))
                return "[{" + k + ":r} for r in _o]"
            return ("[{k:r[i]for i,k in enumerate([" + keys + "])}for r in _o]")
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


def build(schema: dict, out_dir: str, *, name: str | None = None,
          overwrite: bool = False) -> dict:
    """落盘 `gen/<name>.py` + manifest；同名不同 sha1 默认拒绝覆盖（§4.1）。

    `validate()` 的输出是规范形，**直接**喂给生成器（不二次 `validate`：
    规范化后的 `value` 是链 tuple，见 P0-2 裁决）。
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
    return {
        "name": mod,
        "schema_sha1": sha1,
        "artifact": path,
        "trace_artifact": trace_artifact,
        "lines": len(src.splitlines()),
        "chars": len(src),
        "cython": False,
    }


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
