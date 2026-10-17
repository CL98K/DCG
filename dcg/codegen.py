# -*- coding: utf-8 -*-
"""DCG v2 代码生成（CONTRACT.md §3.6 / §3.7 / §4.1 / §4.2 / §4.3 / §4.4）。

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

* 字符预算（§7：CAP<=180 / M0<=210，口径 `len(src)`）—— 内联 `_S`/`_ad`/`_num` 光前导
  就要 300+ 字符，内联版 CAP=571 / M0=669，**无论怎么写都超**；
* §4.2 规则 7（只 optional 字段有 undefined 守卫）—— 内联的算术 helper 自带
  `is _U` 预检，会污染守卫计数；`bench/codegen_rules.py` 现在按**字段级**判定，
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

#: jx 函数 -> runtime helper（契约 §3.4 白名单）
_FN = {
    "$string": "_S", "$uppercase": "_up", "$lowercase": "_lo", "$trim": "_tm",
    "$length": "_ln", "$count": "_ct", "$split": "_sp", "$join": "_jn",
    "$substring": "_ss", "$contains": "_cn", "$number": "_N", "$boolean": "_B",
    "$abs": "_ab", "$floor": "_fl", "$ceil": "_ce", "$round": "_ro",
    "$sqrt": "_sq", "$power": "_pw", "$not": "_nt", "$exists": "_ex",
    "$type": "_ty",
}

#: 二元运算符 -> runtime helper
_OPS = {
    "+": "_ad", "-": "_sb", "*": "_ml", "/": "_dv", "%": "_md",
    "&": "_cc", "=": "_eq", "!=": "_ne", "<": "_lt", "<=": "_le",
    ">": "_gt", ">=": "_ge", "and": "_and", "or": "_or", "in": "_in",
}

#: 明确拒绝（批处理 / 高阶 / 非确定；绝不静默生成错语义）
_REFUSE = ("$sum", "$average", "$min", "$max", "$sort", "$reverse", "$distinct",
           "$now", "$millis", "$random", "$eval", "$map", "$filter", "$reduce",
           "$each", "$count_")

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
#: （CAP/M0 走裸运算后零 helper，整模块更短，见 §7 字符预算）。
_IMPORT_NAMES = set(SOURCE) | {"_U", "UNDEF", "is_u"}

#: 值表达式就是一个局部名时，内联它不会重复求值 —— 不落中转局部（§4.2 规则 ②）
_TRIVIAL = re.compile(r"^[A-Za-z_]\w*$")

_QUOTED = re.compile(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")


def _sub_name(text: str, name: str, repl: str) -> str:
    """把裸名 `name` 换成 `repl`，**跳过字符串字面量内部**（列名 key 不能被改写）。"""
    pat = re.compile(r"(?<![\w.])%s(?![\w])" % re.escape(name))
    parts = _QUOTED.split(text)
    return "".join(p if i % 2 else pat.sub(repl, p) for i, p in enumerate(parts))

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


# ---------------------------------------------------------------- 生成器
class _Gen:
    """一次生成的编译状态（每次 :func:`generate` 新建，绝不跨调用复用）。"""

    def __init__(self, schema: dict, cython: bool, inline_optional: bool):
        self.cython = bool(cython)
        self.inline_optional = bool(inline_optional)
        self.fields: dict = schema["out"]["fields"]
        self.shape: str = schema["output"]["shape"]
        self.single = bool(schema["output"]["single"])
        self.filter = bool(schema["output"]["filter"])
        self.src: str = schema["input"]["source"]      # "rows" | "index"
        self.rfuncs: list = []
        self.uses: dict = {}          # (key, ...) -> 引用次数
        self.order: list = []         # 引用顺序（确定性）
        self.locals: dict = {}        # 字段名 -> 局部名
        self.safe = 0                 # >0 时路径走 _ix_get（optional 字段专用）
        self.raw = None               # None | "num" | "str"：编译期可证安全 -> 裸运算
        self._n = 0
        self._idx: dict = {}
        self._binds: dict = {}
        self._pre: list = []
        self._refs: set = set()
        self._exprs: dict = {}       # expr 源码文本 -> IR（记忆化：见 _expr_node）
        self.pyblocks: list = []     # `py{}` 多语句块的命名辅助函数（定义在 run() 外）
        self._pykeys: dict = {}      # (块体, free 元组) -> `_pyN`，同块只发一次
        self.cur_field = ""          # 正在取值的字段（SchemaError 指到具体字段）
        self.cur_key = ""            # 该字段正在取值的键：value/const/expr/fields/items

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

    def safe_text(self, path: tuple) -> str:
        """optional 字段的取值链：每一步都用 `_ix_get`（缺键 -> UNDEF，不抛 KeyError）。"""
        t = "R"
        for k in path:
            t = f"_ix_get({t},{k!r})"
        return t

    def emit_path(self, path) -> str:
        path = tuple(path)
        if not path:
            return "R"
        if self.safe:
            return self.safe_text(path)
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
        base = self._binds[par] if par is not None else "R"
        steps = p[len(par):] if par is not None else p
        # 单个字符串下标 -> 用 _ix_get：`x=R['k']` 是 v1 的 buffer/doc 中转形状（规则③）
        if len(steps) == 1 and isinstance(steps[0], str):
            return f"_ix_get({base},{steps[0]!r})"
        return base + self.suffix(steps)

    def text_of(self, p: tuple) -> str:
        if p in self._binds:
            return self._binds[p]
        for L in range(len(p) - 1, 0, -1):
            if p[:L] in self._binds:
                return self._binds[p[:L]] + self.suffix(p[L:])
        return "R" + self.suffix(p)

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
                return self.emit_path(path)
            base = self.emit(node[1])
            idx = node[2]
            if self.raw and idx[0] == _ir.LIT and self._positional(node):
                # 裸运算模式：已声明且可证非 undefined 的字段 + 字面量下标 -> 直接下标
                # （`hour[2]` 对 v1 也是直取，缺了本来就是 IndexError）
                return f"{base}[{self.lit(idx[1])}]"
            if idx and idx[0] in (_ir.FIELD, _ir.VAR):
                # §3.6 裁定 (i)：`$.a[b]` ≡ `$.a[$.b]` —— 裸名是**字段引用**，
                # 未声明就是 build 期 SchemaError（列出可用字段），绝不是行键查找。
                itext = self.field_name(idx[1])
            else:
                itext = self.emit(idx)
            return f"_ix_get({base},{itext})"
        if k == _ir.ROOT:
            return "R"
        if k == _ir.UNARY:
            op = node[1]
            v = self.emit(node[2])
            if op == "not":
                return f"(not _tru({v}))"
            if op == "-":
                return f"_neg({v})"
            return f"_pos({v})"
        if k == _ir.BIN:
            return self.bin(node)
        if k == _ir.COND:
            # `c ? a : b`：走 _co，cond 为 undefined 时结果也是 undefined（不硬造 else 值）
            c = self.emit(node[1])
            t = self.emit(node[2])
            e = self.emit(node[3])
            return f"_co({c},{t},{e})"
        if k == _ir.CALL:
            return self.call(node[1], node[2], node[3] if len(node) > 3 else "")
        if k == _ir.RFUNC:
            return self.rfunc(node[1], node[2])
        if k == _ir.ARRAY:
            return "_ar(" + ",".join(self.emit(x) for x in node[1]) + ")"
        if k == _ir.OBJECT:
            return "_ob([" + ",".join(
                f"({p[0]!r},{self.emit(p[1])})" for p in node[1]) + "])"
        if k == _ir.PY:
            return self.pyseg(node[1], node[2] if len(node) > 2 else None)
        if k == _ir.PYBLOCK:
            return self.pyblock(node)
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
        if spec.get("optional"):
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

    def _ok_num(self, node) -> bool:
        k = node[0]
        if k == _ir.LIT:
            v = node[1]
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if k == _ir.INDEX:
            return self._positional(node)
        if k in (_ir.FIELD, _ir.VAR):
            return True
        if k == _ir.BIN:
            return (node[1] in ("+", "-", "*", "/", "%")
                    and self._ok_num(node[2]) and self._ok_num(node[3]))
        return False
    def _ok_str(self, node) -> bool:
        k = node[0]
        if k == _ir.LIT:
            return isinstance(node[1], str)
        if k == _ir.INDEX:
            return self._positional(node)
        if k in (_ir.FIELD, _ir.VAR):
            return True
        if self.is_string_call(node):
            return self._ok_num(node[2][0])
        if k == _ir.BIN:
            if node[1] == "+":
                return self._ok_str(node[2]) and self._ok_str(node[3])
            if node[1] == "&":
                return (self._ok_str_or_string_call(node[2])
                        and self._ok_str_or_string_call(node[3]))
        return False

    def _positional(self, node) -> bool:
        """路径是否**位置式**（`$.5.a.1`：首段是整数下标）。

        只有位置式路径才敢假设"一定取得到"：`$.5.a.1` 是固定行布局（v1 的 CAP/M0
        就是这么取的，缺了本来就是 IndexError/KeyError）；而 `$.zz` 是**具名键**，
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
        """裸名 = 字段名（§1）。未声明 -> build 期 SchemaError，绝不造幽灵引用。"""
        if name in self.locals:
            return self.locals[name]
        if name in self.fields:
            raise SchemaError(
                self._fpath(),
                f"裸名 {name!r} 只能引用**先声明**的字段（字段引用按 schema 顺序取值）")
        raise SchemaError(
            self._fpath(),
            f"裸名 {name!r} 不是本次 schema 声明的字段；已声明: "
            f"{sorted(self.fields)}（行内取值请写 $.{name} 或 $.路径）")

    # ---------------------------------------------------- 错误定位
    _KEYS = ("const", "value", "expr", "fields", "items")

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
        if self.raw and fname == "$string" and len(args) == 1:
            # `type: str` 的字段里 `$string(x)` 就是内建 `str(x)`（裸运算模式）
            return f"str({self.emit(args[0])})"
        fn = _FN.get(fname)
        if fn is None:
            if any(fname.startswith(r) for r in _REFUSE):
                raise Unsupported(
                    f"codegen 拒绝 {fname}（列 {loc}）：批处理/高阶/非确定函数"
                    f"本次不支持（列级语义见 CONTRACT §3.4）")
            raise Unsupported(f"codegen 不认识的列函数 {fname}（列 {loc}）")
        if fn == "_N" and not args:
            return "_N()"
        return fn + "(" + ",".join(self.emit(a) for a in args) + ")"

    def rfunc(self, qual: str, args) -> str:
        if qual not in self.rfuncs:
            self.rfuncs.append(qual)
        return "_x(" + repr(qual) + "," + \
            ",".join(self.emit(a) for a in args) + ")"

    def pyseg(self, source: str, names=None) -> str:
        """反引号段：链解析复用 `split_chain`（§3.7），与 JSONata 路径同一实现。

        `names` 优先用 IR 里已经算好的自由名（PY 节点 = `('py', src, free, loc)`），
        避免就同一段源码再 `analyze_py_segment` 一次 —— §4.2 规则 ⑥「每次 build 只
        compile 一次」：重复解析会让产物校验以外的 compile 次数翻倍。
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
        return text

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
        if fn is None:
            params = ["_p%d" % i for i in range(len(free))]
            # 先把 `$.链` 换成形参名，再解析：还原后的文本含 `$.a`，本身不是合法
            # Python（jx 只在占位符形态下 mode="exec" 校验过），必须以**实际发射**
            # 的文本为准做 build 期校验。
            body = self._subst_pyblock(stmts, free, params, loc)
            self._check_pyblock(body, free, loc)
            fn = "_py%d" % len(self.pyblocks)
            self._pykeys[key] = fn
            lines = ["def %s(%s):" % (fn, ",".join(params))]
            lines += [" " + ln for ln in body.splitlines() if ln.strip()]
            self.pyblocks.append("\n".join(lines))
        args = [self.emit_path(tuple(split_chain(f))) for f in free]
        return "%s(%s)" % (fn, ",".join(args))

    def _check_pyblock(self, body: str, free, loc) -> None:
        """build 期校验：合法语句序列 + **恰好一个** return + free 链可解析。

        传进来的是**已换形参**的块体（合法 Python）；`$.链` 的声明校验与反引号段
        **同一错误路径**：单段裸名走 :meth:`field_name`（未声明 -> `SchemaError`
        并列出已声明字段），多段深路径与 §6.1 同口径拒绝。
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
        for f in free:
            segs = tuple(split_chain(f))
            if sum(1 for s in segs if isinstance(s, str)) >= 2:
                raise Unsupported(
                    f"py{{}} 块内深路径 $.{f}", loc,
                    "块内 `$.` 只支持单层字段或常量下标（契约 §6.1）："
                    "要访问属性请先 `let x = $.名称`，再写 `x.属性`")
            if len(segs) == 1 and isinstance(segs[0], str):
                self.field_name(segs[0])      # 未声明 -> SchemaError（列出可用字段）

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
            return self.lit(spec["const"])
        if "value" in spec:
            return self.emit_path(tuple(spec["value"]))
        if "expr" in spec:
            return self.emit(self._expr_node(spec))
        if "fields" in spec:
            # 嵌套字段的 path 与 schema.py 同口径：out.fields.n.fields.x
            prev, prevk = self.cur_field, self.cur_key
            parts = []
            for k, v in spec["fields"].items():
                self.cur_field = f"{prev}.fields.{k}"
                self.cur_key = next((x for x in self._KEYS if x in v), "")
                parts.append(f"({k!r},{self.field_value(k, v)})")
            self.cur_field, self.cur_key = prev, prevk
            return "_ob([" + ",".join(parts) + "])"
        # 嵌套数组（契约 §2 line 128）：`items` 是**单个** FieldSpec，与嵌套对象同构 ——
        # 产出由该元素 spec 求值得到的列表（`_ar` 会自动滤掉 undefined 元素）。
        prev, prevk = self.cur_field, self.cur_key
        self.cur_field = f"{prev}.items"
        self.cur_key = next((x for x in self._KEYS if x in spec["items"]), "")
        part = self.field_value(f"{name}.items", spec["items"])
        self.cur_field, self.cur_key = prev, prevk
        return f"_ar({part})"

    def collect_refs(self) -> set:
        """被别的字段按**裸名**引用到的字段 —— 只有它们需要落成局部名。"""
        refs: set = set()
        for spec in self.fields.values():
            refs |= self._refs_of(spec)
        return refs

    def _refs_of(self, spec: dict) -> set:
        out: set = set()
        if "expr" in spec:
            self._node_names(self._expr_node(spec), out)
        if isinstance(spec.get("fields"), dict):
            for s in spec["fields"].values():
                out |= self._refs_of(s)
        if isinstance(spec.get("items"), dict):
            out |= self._refs_of(spec["items"])
        return out

    def _node_names(self, node, out: set) -> None:
        if isinstance(node, (tuple, list)):
            if isinstance(node, tuple) and node:
                if node[0] in (_ir.FIELD, _ir.VAR):
                    out.add(node[1])
                elif node[0] == _ir.PY and len(node) > 2 and isinstance(node[2], list):
                    for n in node[2]:
                        if isinstance(n, str) and n.isidentifier():
                            out.add(n)
                elif node[0] == _ir.PYBLOCK and len(node) > 2 and isinstance(node[2], list):
                    # `py{}` 块按裸名引用字段时，字段需先落成局部（链文本不是标识符，跳过）
                    for n in node[2]:
                        if isinstance(n, str) and n.isidentifier():
                            out.add(n)
            for item in node:
                self._node_names(item, out)

    def body(self):
        """按 schema 顺序取字段值；返回 (局部字段定义行, 输出行字面量)。"""
        defs: list = []
        refs: list = []

        def value(name, spec):
            mode = self.raw_mode(spec)
            old, self.raw = self.raw, mode
            prev = self._enter(name, spec)
            try:
                return self.field_value(name, spec)
            finally:
                self.raw = old
                self.cur_field, self.cur_key = prev

        for name, spec in self.fields.items():
            if spec.get("optional"):
                # §4.2 规则 7: optional 才生成 undefined 守卫；undefined -> null
                self.safe += 1
                try:
                    expr = value(name, spec)
                finally:
                    self.safe -= 1
                t = self.tmp()
                defs.append(f"{t}={expr}")
                # undefined -> null（保行宽一致，列式形状才成立）；守卫只此一处
                defs.append(f"{name}=None if {t} is _U else {t}")
                self.locals[name] = name
                refs.append(name)
            else:
                expr = value(name, spec)
                if name in self._refs and not _TRIVIAL.match(expr):
                    # 被后续字段按裸名引用、且值本身不是单个局部名 -> 落成局部避免重复求值
                    defs.append(f"{name}={expr}")
                    self.locals[name] = name
                    refs.append(name)
                else:
                    # 引用只此一次（或值就是个局部名）：直接内联，不造只服务 return 的中转
                    self.locals[name] = expr
                    refs.append(expr)
        return defs, "[" + ",".join(refs) + "]"

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
        L.append("def run(_d):")
        if self.cython:
            L.append(" cdef object _o")
            for s in self.cdef_names(defs, pre):
                L.append(" cdef object " + s)
        if self.src == "index":
            L.append(" _d=_d['index']")
        L.append(" _o=[]")
        L.append(" for R in _d:")
        # filter 逐行判 falsy（§4.4：v1 等价 `[data for data in datas if data]`）。
        # 写成 `if R:` 嵌套而不是 `if not R:continue`：同样语义，省 7 字符
        # （嵌套缩进 +5，去掉 `not `(4) + `continue`(8)），见 §7 字符预算。
        if self.filter:
            L.append("  if R:")
        ind = "   " if self.filter else "  "
        for s in pre:
            L.append(ind + s)
        for s in defs:
            L.append(ind + s)
        # 行必须是 **list**（§4.1 shape=list = list of row）；用 append 而不是
        # `_o+=[...]`（后者是 extend，会把行拍平成 cell，且实测慢 1.5×）。
        L.append(ind + "_o.append(" + row + ")")
        L.append(" " + self.finish())
        src = "\n".join(L) + "\n"
        # 只有真的用到 runtime helper 才发 import 行（CAP/M0 裸运算后一行都不需要）
        if self._uses_helpers(src):
            src = _RUNTIME_IMPORT + "\n" + src
        return src

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
        names = ["R"]
        for s in list(pre) + list(defs):
            lhs = s.split("=", 1)[0].strip()
            if lhs and lhs.isidentifier() and lhs not in names:
                names.append(lhs)
        return names

    def finish(self) -> str:
        """形状包装 —— **编译期**定型（§4.2 规则⑤），运行期零形状判断。

        `single` 与 shape 的组合合法性已在 :func:`validate` 判过；这里只做最后一道
        断言，防止绕过 validate 直接调 generate。
        """
        if self.shape in ("dict", "index"):
            if self.single:
                raise SchemaError("output", "shape=dict/index 下 single=true 无意义")
            if self.shape == "index":
                return "return{i:r for i,r in enumerate(_o)}"
            keys = ",".join(repr(n) for n in self.fields)
            return f"return{{k:[r[i]for r in _o]for i,k in enumerate([{keys}])}}"
        if self.shape == "rows":
            # v1 `protocol: False` 的行形态：**每行一个 {字段名: 值} dict**（不是列式）。
            # 与 dict 形状的差别是容器方向：dict = {字段: 整列}，rows = [{字段: 值}, ...]。
            keys = ",".join(repr(n) for n in self.fields)
            if self.single:
                return ("return _o and {k:_o[0][i]for i,k in enumerate(["
                        + keys + "])}")
            return ("return[{k:r[i]for i,k in enumerate([" + keys + "])}for r in _o]")
        if self.single:
            return "return _o and _o[0]"
        return "return _o"


# ---------------------------------------------------------------- 公开 API
def _check_reserved(schema: dict) -> None:
    for fname in schema["out"]["fields"]:
        if fname in _RESERVED or fname.startswith("_"):
            raise SchemaError(
                f"out.fields.{fname}",
                f"字段名 {fname!r} 与生成码内部名冲突（保留名/前缀 _）；请改名")


def _generate_norm(norm: dict, *, cython: bool = False,
                   inline_optional: bool = True) -> str:
    """已规范化的 schema -> 源码（不再跑 validate；供 build 复用，P0-2）。"""
    _check_reserved(norm)
    g = _Gen(norm, cython, inline_optional)
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
    return {
        "name": mod,
        "schema_sha1": sha1,
        "artifact": path,
        "lines": len(src.splitlines()),
        "chars": len(src),
        "cython": False,
    }


def load(path: str, mod_name: str | None = None):
    """把 `build()` 落盘的产物当模块加载（verifier/bench 用）。"""
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
