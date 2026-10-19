# -*- coding: utf-8 -*-
"""DCG v2 · JSONata -> 契约 §1 的纯 tuple IR。

公开面只有 :func:`compile_expr`（契约 §3.1），它是**纯函数**：同输入同输出，
不读任何全局 MODE。``registry=`` 是可选关键字参数（默认 ``None``），只有显式传入
时才会把不在白名单里的 ``$name`` 解析成 ``("rfunc", ...)``。

设计要点
--------
* 输出是**契约 §1 的纯 tuple**，末元素恒为 ``loc = (line, col)``。
* **裸标识符 = 字段**（契约 §6.2）：JSONata 里 ``$.name`` 与 ``name`` 等价，都编成
  ``("field", name, loc)``。**反引号段内**的裸名则是 Python 全局（内置名/安全模块名）
  或**字段引用**（``str(hourB)`` 这种：CAP 工作负载的 ext 就是它）—— 后者进
  ``free_field_names``，由 codegen 按 schema 在 build 期校验。
* ``..`` 区间 / 正则字面量 / ``function(){}`` / ``#$`` / ``@`` / ``~>`` / ``|..|`` /
  Reduce / 分组 / Sort 一律在**编译期**抛 :class:`Unsupported`（契约 §3.5），
  因此 IR 里不会真的出现 ``("range", ...)`` / ``("lambda", ...)``。
* **先判函数归属、再 emit 实参**（原型坑 b）：``$map(xs, $string)`` 必须报
  ``$map`` 而不是被实参里的 ``$string`` 抢答。
"""
from __future__ import annotations

import ast as pyast
import re

from .lexer import JxSyntaxError, lex_tokens
from .parser import parse
from .paths import FIELD_REF, parse_chain, parse_path
from .policy import (
    BARE_CALL_ALIASES,
    HIGHER_ORDER,
    NONDETERMINISTIC,
    ROW_FUNCS,
    STRUCTURAL,
    Unsupported,
    rejection_reason,
)

__all__ = [
    "Unsupported",
    "compile_expr",
    "compile_ast",
    "analyze_py_segment",
    "compile_py_block",
    "parse_path",
    "parse_path_chain",
    "split_chain",
    "chain_ir",
]


# ---------------------------------------------------------------------------
# 反引号内联 Python 段
# ---------------------------------------------------------------------------
#: 段内允许直接出现的 Python 全局（模块）。裸名只允许这些 + 内置名，其余必须写 ``$.name``。
PY_SAFE_MODULES = frozenset(
    {
        "math", "cmath", "re", "json", "decimal", "fractions", "statistics",
        "datetime", "time", "itertools", "functools", "operator", "collections",
        "string", "base64", "hashlib", "binascii", "unicodedata", "textwrap",
        "uuid", "zoneinfo", "types", "typing", "dataclasses", "enum",
    }
)

#: 段内 ``$.`` 链的**切分正则**与解析都来自 :mod:`dcg.jx.paths`（唯一权威），
#: 本模块不再自带第二套链语法。
#: ``__jx`` 是 jx 保留给 codegen 占位符/别名的命名空间：用户段里出现即报错，
#: 因此 codegen 可以放心用 ``__jx_*`` 做别名，绝不会撞上用户源码里的标识符
#: （反例：codegen 现在用的 ``_y0`` 是**可被用户书写**的合法名，会静默撞车）。
RESERVED_PREFIX = "__jx"
_PY_PLACEHOLDER = "__jx_f"

try:  # pragma: no cover - 依解释器实现
    import builtins as _builtins
except ImportError:  # pragma: no cover
    import __builtin__ as _builtins  # type: ignore

PY_SAFE_GLOBALS = frozenset(dir(_builtins)) | PY_SAFE_MODULES


def split_chain(chain: str, loc=None):
    """链文本 -> 段序列（``[5, "a", 1]``）—— 契约 §3.7。

    唯一权威是 :func:`dcg.jx.paths.parse_path`；这里只是"解析失败就抛 Unsupported"
    的薄包装。合法：``$.a`` / ``$.5`` / ``$.5.a`` / ``$.5.a.1`` / ``$.5.a.1.b``；
    路径长度不限，多层路径由 :func:`_chain` 编成下标链。
    """
    segs = parse_chain(chain)
    if segs is None:
        raise Unsupported(
            f"字段链 {chain!r} 非法",
            loc,
            "契约 §3.7：$. 后的段只能是标识符 [A-Za-z_][A-Za-z0-9_]* 或十进制下标 [0-9]+",
        )
    return segs


def parse_path_chain(chain: str, loc=(1, 1)):
    """链文本（``"5.a.1"``，**不含**前导 ``$.``）-> 契约 §1 的 IR。

    JSONata 路径编译、反引号段链、codegen 共用的**唯一入口**。``loc`` 是 ``$`` 的位置；
    链节点用 ``(line, col + 1)``（与路径编译逐位一致），因此默认 ``loc=(1, 1)`` 时::

        parse_path_chain("5.a.1") == compile_expr("$.5.a.1")      # 逐元素 ==

    —— 这是契约 §3.7 第 3 条要求的"同一个解析结果"，不是"两侧碰巧长得像"。
    非法链抛 :class:`Unsupported`，**绝不**静默回退成"单个字段名"（契约 §6.1）。
    """
    segs = split_chain(chain, loc)
    if loc is None:
        root_loc = seg_loc = None
    else:
        root_loc = (loc[0], loc[1])
        seg_loc = (loc[0], loc[1] + 1)
    return _chain(("root", root_loc), segs, None, seg_loc)


def chain_ir(chain: str, loc=None):
    """同 :func:`parse_path_chain`；``loc=None`` 时所有 loc 为 ``None``（结构对比用）。"""
    return parse_path_chain(chain, loc)


def analyze_py_segment(source: str, loc=None):
    """校验反引号段并抽出 ``free_field_names``。

    规则（PLAN §4 硬规则 1 + 契约 §1 ``py`` 节点）：

    * 段内必须是 **Python 表达式** —— ``ast.parse(source, mode="eval")`` 必须通过；
      声明/赋值/多语句一律报错（``build`` 期，不是运行期 ``SyntaxError``）。
    * 字段**只能**写 ``$.`` + 链（``$.a`` / ``$.5`` / ``$.5.a.1``，契约 §3.7）；
      正则替换成占位符后再 parse（因为 ``$.a`` 本身不是合法 Python）。占位符方案让
      "裸名"与"字段名"在同一棵 AST 里可区分。``free_field_names`` 的元素是**完整链**
      （如 ``"5.a.1"``，不含前导 ``$.``）。
    * 段内出现的**裸名**（``ast.Name``，``Load`` 上下文）分两类（契约 §6.2）：
      Python 内置名或 :data:`PY_SAFE_MODULES` 里的模块名 -> 保持全局；
      **其余一律当作字段引用**（CAP 工作负载的 ext 就是 ``str($.5.a.1) + str(hourB)``），
      其裸名同样登记进 ``free_field_names``。jx 看不到 schema，所以"这个字段名是否已声明"
      由 codegen 在 **build 期**报 ``SchemaError`` 并列出可用字段名。
    * ``free_field_names`` 的元素是**字段引用文本**：``$.``-链的链文本（``"a"``、
      ``"5.a.1"``，不含前导 ``$.``）或裸名（``"hourB"``）。顺序 = 链按源码出现顺序，
      其后是裸名按源码位置顺序。

    返回 ``(source_str, free_field_names)``；``source_str`` 原样保留（codegen 负责
    把 ``$.name`` 换成生成局部名）。
    """
    if not isinstance(source, str):
        raise TypeError(f"反引号段必须是 str，收到 {type(source).__name__}")
    if RESERVED_PREFIX in source:
        raise Unsupported(
            f"反引号段含保留前缀 {RESERVED_PREFIX!r}", loc, "请改名（jx/codegen 占位符命名空间）"
        )

    names: list[str] = []
    chunks: list[str] = []
    pos = 0
    for m in FIELD_REF.finditer(source):
        chunks.append(source[pos : m.start()])
        name = m.group(1)
        split_chain(name, loc)      # 唯一权威 parse_path（契约 §3.7 第 2 条）
        if name not in names:
            names.append(name)
        chunks.append(f"{_PY_PLACEHOLDER}{names.index(name)}")
        pos = m.end()
    chunks.append(source[pos:])
    parse_src = "".join(chunks)

    try:
        tree = pyast.parse(parse_src, mode="eval")
    except SyntaxError as e:
        raise Unsupported(
            "反引号段 Python 语法错误",
            loc,
            f"{e.msg}（段内偏移 {e.offset}）—— 必须是单个 Python 表达式",
        ) from None

    placeholders = {f"{_PY_PLACEHOLDER}{i}" for i in range(len(names))}
    bare: list[tuple[int, int, str]] = []
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
            if node.id in placeholders or node.id in PY_SAFE_GLOBALS:
                continue
            bare.append((node.lineno, node.col_offset, node.id))
    for _, _, name in sorted(bare):
        if name not in names:
            names.append(name)
    return source, names


# ---------------------------------------------------------------------------
# py{ ... } 多语句代码块（task-7 / 契约 §3.7）
# ---------------------------------------------------------------------------
#: 块内局部 -> ``__jx_<块序号>_<原名>``：模块级唯一临时名，避开用户字段名
#: （codegen 侧同样只允许用 ``__jx`` 前缀做占位符/别名）。
_BLOCK_PLACEHOLDER = "__jxq"

_BLOCK_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LET_RE = re.compile(r"let[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(?!=)")
_RETURN_RE = re.compile(r"return\b")
_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*[ \t]*=(?!=)|[A-Za-z_][A-Za-z0-9_]*[ \t]*[-+*/%@&|^]=")

#: 块内**禁止**的语句起始关键字 -> ``(feature, msg)``（契约 §3.7 能力边界，逐条拒绝）。
BLOCK_DENIED = {
    "import": ("块内禁止 import", "需要模块请在生成码模块级 import（契约 §3.7 第 3 条）"),
    "from": ("块内禁止 import", "需要模块请在生成码模块级 import（契约 §3.7 第 3 条）"),
    "for": ("块内不支持 for 循环", "逐行编译器不引入循环语义（契约 §3.7 能力边界）"),
    "while": ("块内不支持 while 循环", "逐行编译器不引入循环语义（契约 §3.7 能力边界）"),
    "try": ("块内不支持 try/except", "块内不做异常处理（契约 §3.7 能力边界）"),
    "with": ("块内不支持 with", "块内不做上下文管理（契约 §3.7 能力边界）"),
    "class": ("块内不支持 class", "块内不能定义类（契约 §3.7 能力边界）"),
    "def": ("块内不支持 def", "块内不能定义函数；要复用请用 `let NAME = ...`（契约 §3.7 能力边界）"),
    "if": ("块内不支持 if 语句", "请用 JSONata 三元 `?:` 或 Python 条件表达式 `a if c else b`"),
    "elif": ("块内不支持 if 语句", "请用 JSONata 三元 `?:` 或 Python 条件表达式 `a if c else b`"),
    "else": ("块内不支持 if 语句", "请用 JSONata 三元 `?:` 或 Python 条件表达式 `a if c else b`"),
    "global": ("块内不支持 global", "契约 §3.7 第 4 条：块内名字不得泄漏到块外"),
    "nonlocal": ("块内不支持 nonlocal", "契约 §3.7 第 4 条：块内名字不得泄漏到块外"),
    "del": ("块内不支持 del", "契约 §3.7 能力边界"),
    "assert": ("块内不支持 assert", "契约 §3.7 能力边界"),
    "raise": ("块内不支持 raise", "契约 §3.7 能力边界"),
    "pass": ("空语句 pass 没有意义", "块由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成"),
    "yield": ("块内不支持 yield", "契约 §3.7 能力边界"),
    "await": ("块内不支持 await", "契约 §3.7 能力边界"),
    "async": ("块内不支持 async", "契约 §3.7 能力边界"),
    "match": ("块内不支持 match", "契约 §3.7 能力边界"),
}

#: 块内额外禁止的全局名：``import`` 语句被禁，``__import__`` 不能成为后门。
_BLOCK_DENIED_GLOBALS = frozenset({"__import__"})


def _py_pos_in(body: str, offset: int, loc):
    """块体内偏移 -> **块内真实行列**（``loc`` = 块体首字符的 (line, col)）。"""
    if loc is None:
        return None
    head = body[:offset]
    line, col = loc
    nl = head.count("\n")
    if nl:
        return line + nl, len(head) - head.rfind("\n")
    return line, col + len(head)


def _py_skip_string(text: str, i: int) -> int:
    """``text[i]`` 处字符串字面量之后的偏移；未闭合返回 ``-1``。"""
    c = text[i]
    quote = c * 3 if text.startswith(c * 3, i) else c
    j = i + len(quote)
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text.startswith(quote, j):
            return j + len(quote)
        if len(quote) == 1 and text[j] == "\n":
            return -1
        j += 1
    return -1


def _split_block_statements(body: str, loc=None):
    """按**顶层** ``;`` 切分块内语句（字符串/注释/括号里的 ``;`` 不算）。

    返回 ``[(offset, raw), ...]``，``offset`` 是相对块体首字符的偏移。
    """
    out = []
    start = 0
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "#":
            nl = body.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if c in "\"'":
            end = _py_skip_string(body, i)
            if end < 0:
                raise Unsupported("代码块里的字符串未闭合", _py_pos_in(body, i, loc))
            i = end
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            out.append((start, body[start:i]))
            start = i + 1
        i += 1
    out.append((start, body[start:]))
    return out


def _py_strip_leading(raw: str):
    """去掉语句开头的空白与 ``#`` 注释，返回 ``(文本, 相对偏移)``。

    ``py{ let t = 1; # }\n return t }`` 的第二条语句以注释开头（分号后的注释
    属于下一条语句的文本），必须剥掉才不会误判成"不是合法语句"。
    """
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c in " \t\r\n\f\v":
            i += 1
            continue
        if c == "#":
            nl = raw.find("\n", i)
            if nl < 0:
                return "", n
            i = nl + 1
            continue
        break
    return raw[i:], i


def _py_bound_names(tree) -> set:
    """表达式里**由自身绑定**的名字（推导式/生成器变量、lambda 形参、海象赋值）。"""
    bound = set()
    for node in pyast.walk(tree):
        if isinstance(node, pyast.comprehension):
            for tgt in pyast.walk(node.target):
                if isinstance(tgt, pyast.Name):
                    bound.add(tgt.id)
        elif isinstance(node, pyast.Lambda):
            args = node.args
            for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                bound.add(a.arg)
            if args.vararg is not None:
                bound.add(args.vararg.arg)
            if args.kwarg is not None:
                bound.add(args.kwarg.arg)
        elif isinstance(node, pyast.NamedExpr) and isinstance(node.target, pyast.Name):
            bound.add(node.target.id)
    return bound


def _py_find_ident(text: str, name: str) -> int:
    m = re.search(r"(?<![\w.])%s(?![\w])" % re.escape(name), text)
    return m.start() if m else 0


def _py_rename_locals(text: str, mapping: dict) -> str:
    """把 ``let`` 局部名换成 ``__jx_<块序号>_<名>``（字符串/注释/属性名不动）。"""
    if not mapping:
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j])
            i = j
            continue
        if c in "\"'":
            end = _py_skip_string(text, i)
            if end < 0:
                out.append(text[i:])
                break
            out.append(text[i:end])
            i = end
            continue
        m = _BLOCK_IDENT_RE.match(text, i)
        if m is not None and (i == 0 or text[i - 1] != "."):
            word = m.group()
            out.append(mapping.get(word, word))
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _py_restore_chains(text: str, free) -> str:
    """占位符 -> 原样的 ``$.链``（倒序替换，避免 ``__jxq1`` 吃掉 ``__jxq10``）。"""
    for i in range(len(free) - 1, -1, -1):
        text = text.replace(f"{_BLOCK_PLACEHOLDER}{i}", f"$.{free[i]}")
    return text


def _py_block_expr(text: str, offset: int, body: str, loc, mapping, let_names, free) -> str:
    """校验块内一条表达式，返回**占位符形态**（已验证）的可发射源码。"""
    chunks = []
    pos = 0
    for m in FIELD_REF.finditer(text):
        chunks.append(text[pos : m.start()])
        chain = m.group(1)
        chain_loc = _py_pos_in(body, offset + m.start(), loc)
        split_chain(chain, chain_loc)   # 链语法校验；路径长度不限
        if chain not in free:
            free.append(chain)
        chunks.append(f"{_BLOCK_PLACEHOLDER}{free.index(chain)}")
        pos = m.end()
    chunks.append(text[pos:])
    masked = "".join(chunks)

    if "$" in masked:
        raise Unsupported(
            "代码块里的 $ 引用",
            _py_pos_in(body, offset + masked.index("$"), loc),
            "块内只有 `$.字段链`（契约 §3.7 第 1 条）；`$$` 与裸 `$` 在行模式下没有意义",
        )

    try:
        tree = pyast.parse(masked, mode="eval")
    except SyntaxError as e:
        raise Unsupported(
            "代码块 Python 语法错误",
            _py_pos_in(body, offset, loc),
            f"{e.msg}（块内偏移 {e.offset}；语句之间用 `;` 分隔）",
        ) from None

    bound = set(let_names) | _py_bound_names(tree)
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Lambda):
            raise Unsupported(
                "块内不支持 lambda",
                _py_pos_in(body, offset, loc),
                "请用 `let NAME = ...` + `return`（契约 §3.7 能力边界）",
            )
        if isinstance(node, (pyast.Yield, pyast.YieldFrom, pyast.Await)):
            raise Unsupported(
                "块内不支持 yield/await", _py_pos_in(body, offset, loc), "契约 §3.7 能力边界"
            )
        if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
            name = node.id
            if name in bound or name.startswith(RESERVED_PREFIX):
                continue
            if name in PY_SAFE_GLOBALS and name not in _BLOCK_DENIED_GLOBALS:
                continue
            raise Unsupported(
                f"代码块里引用了未声明的名字 {name!r}",
                _py_pos_in(body, offset + _py_find_ident(text, name), loc),
                "块内不暴露裸字段名：字段写 `$.名称`，块内局部先用 `let NAME = ...` 声明"
                "（契约 §3.7 第 1 条）",
            )
    return _py_rename_locals(masked, mapping)


def compile_py_block(source: str, loc=None, block_index: int = 0):
    """``py{ ... }`` 多语句代码块 -> ``("pyblock", stmts_source, [free], loc)``。

    块内规则（契约 §3.7 / Lead 裁决 m00788 + m00841）：

    1. **不暴露裸字段名**：``$`` 是唯一字段/变量入口（``$.a.b`` 链、``$x`` 变量）；
       块内裸名只允许 Python 全局（内置名 / :data:`PY_SAFE_MODULES`）与 ``let`` 局部。
    2. 合法 Python **语句**序列：逐条按表达式校验 + 整体 ``ast.parse(..., mode="exec")``。
    3. 禁止 ``import``（``Import`` / ``ImportFrom`` / ``__import__``）。
    4. 不得泄漏新名字：``let NAME = EXPR;`` 的 NAME 重命名为 ``__jx_<块序号>_<名>``。
    5. ``return`` **恰好一个且是最后一条**；缺失报"缺少 return"（位置是块内真实行列）。
    6. 块内**不参与 undefined 语义**：jx 原样保留块内 Python，不把 ``$.链`` 改写成
       运行期取值表达式 —— codegen 负责把链映射成已解包的生成局部名。
    7. ``free_field_names`` = ``$.``-链的**链文本**（不含前导 ``$.``），按出现顺序；
       ``$x`` 变量不进（与反引号段同口径）。

    返回 ``(stmts_source, free_field_names)``。
    """
    if not isinstance(source, str):
        raise TypeError(f"py{{}} 代码块必须是 str，收到 {type(source).__name__}")
    if RESERVED_PREFIX in source:
        raise Unsupported(
            f"代码块含保留前缀 {RESERVED_PREFIX!r}", loc, "请改名（jx/codegen 占位符命名空间）"
        )

    free: list[str] = []
    lets: list[tuple] = []          # (name, expr_text, expr_offset)
    ret: list[tuple] = []           # [(expr_text, expr_offset)]
    seen_return = False

    for offset, raw in _split_block_statements(source, loc):
        text, head = _py_strip_leading(raw)
        text = text.rstrip()
        if not text:
            continue
        base = offset + head
        stmt_loc = _py_pos_in(source, base, loc)

        let_m = _LET_RE.match(text)
        if let_m is not None:
            if seen_return:
                raise Unsupported(
                    "return 之后还有语句", stmt_loc, "`return EXPR` 必须是块的最后一条语句"
                )
            name = let_m.group(1)
            expr = text[let_m.end():].strip()
            if not expr:
                raise Unsupported(f"let {name} 缺少初始化表达式", stmt_loc, "写成 `let NAME = EXPR`")
            expr_off = base + text.index(expr, let_m.end())
            lets.append((name, expr, expr_off))
            continue

        if _RETURN_RE.match(text):
            if seen_return:
                raise Unsupported(
                    "代码块有多个 return", stmt_loc, "块的返回值只能有一处（契约 §3.7 第 5 条）"
                )
            expr = text[len("return"):].strip()
            if not expr:
                raise Unsupported("return 缺少表达式", stmt_loc, "块的值就是 `return EXPR` 的 EXPR")
            ret.append((expr, base + text.index(expr, len("return"))))
            seen_return = True
            continue

        if seen_return:
            raise Unsupported(
                "return 之后还有语句", stmt_loc, "`return EXPR` 必须是块的最后一条语句"
            )

        kw = _BLOCK_IDENT_RE.match(text)
        if kw is not None and kw.group() in BLOCK_DENIED:
            feature, msg = BLOCK_DENIED[kw.group()]
            raise Unsupported(feature, stmt_loc, msg)
        if _ASSIGN_RE.match(text):
            name = _BLOCK_IDENT_RE.match(text).group()
            raise Unsupported(
                f"赋值给非 let 变量 {name!r}",
                stmt_loc,
                "块内局部必须写 `let NAME = EXPR;`（契约 §3.7 第 4 条）",
            )
        raise Unsupported(
            "块内不是合法语句",
            stmt_loc,
            "块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成（契约 §3.7）",
        )

    if not seen_return:
        raise Unsupported(
            "代码块缺少 return",
            _py_pos_in(source, len(source), loc),
            "`py{ ... }` 的值就是 `return EXPR` 的 EXPR（契约 §3.7 第 5 条）",
        )

    mapping = {name: f"{RESERVED_PREFIX}_{block_index}_{name}" for name, _, _ in lets}
    let_names = [name for name, _, _ in lets]
    lines = [
        f"{mapping[name]} = {_py_block_expr(expr, off, source, loc, mapping, let_names, free)}"
        for name, expr, off in lets
    ]
    lines.append(
        "return " + _py_block_expr(ret[0][0], ret[0][1], source, loc, mapping, let_names, free)
    )
    masked_src = "\n".join(lines)

    try:                                   # 契约 §3.7 第 2 条：mode="exec"
        whole = pyast.parse(masked_src, mode="exec")
    except SyntaxError as e:
        raise Unsupported(
            "代码块 Python 语法错误", loc, f"{e.msg}（语句序列 mode=exec 校验）"
        ) from None
    for node in pyast.walk(whole):         # 契约 §3.7 第 3 条
        if isinstance(node, (pyast.Import, pyast.ImportFrom)):
            raise Unsupported(
                "块内禁止 import", loc, "需要模块请在生成码模块级 import（契约 §3.7 第 3 条）"
            )
    return _py_restore_chains(masked_src, free), free


# ---------------------------------------------------------------------------
# AST -> IR
# ---------------------------------------------------------------------------
def _short(x) -> str:
    """给错误信息用的一小段源码摘要（供 :func:`_seg` / :func:`_describe`）。"""
    if isinstance(x, list):
        return "[" + ",".join(_short(i) for i in x) + "]"
    if isinstance(x, str):
        return x
    if x is None:
        return "?"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (int, float)):
        return repr(x)
    if isinstance(x, tuple):
        return _describe(x)
    return str(x)


def _seg_show(seg) -> str:
    if isinstance(seg, str):
        return f".{seg}"
    if isinstance(seg, int):
        return f"[{seg}]"
    return f"[{_short(seg)}]"


def _describe(node) -> str:
    """给错误信息用的一行 AST 摘要。"""
    if isinstance(node, list):
        return "[" + ",".join(_describe(x) for x in node) + "]"
    if not isinstance(node, tuple) or not node:
        return _short(node)
    tag = node[0]
    if tag == "name":
        return node[1]
    if tag == "var":
        return node[1]
    if tag == "path":
        return _describe(node[1]) + _seg_show(node[2])
    if tag == "field":
        return f"$.{node[1]}"
    if tag == "index":
        return f"{_describe(node[1])}[{_describe(node[2])}]"
    if tag == "call":
        return f"{_describe(node[1])}()"
    return tag


def _higher_order(fname, args, loc, registry, state):
    """B 类高阶函数：第一个实参逐行求值，其余必须是函数字面量（编成 lambda IR）。"""
    if fname == "$sort" and len(args) == 1:
        return ("call", fname, [_emit(args[0], registry, state)], loc)
    if len(args) < 2:
        raise Unsupported(fname, loc, "需要 (数组, 函数字面量)")
    if fname != "$reduce" and len(args) != 2:
        raise Unsupported(fname, loc, "需要 (数组, 函数字面量)")
    if fname == "$reduce" and len(args) > 3:
        raise Unsupported(fname, loc, "最多接受 (数组, 函数字面量[, 初值])")
    body_state = dict(state)
    body_state["in_func"] = state.get("in_func", 0) + 1
    ir_args = []
    for i, a in enumerate(args):
        if i == 0:
            ir_args.append(_emit(a, registry, state))
            continue
        if fname == "$reduce" and i == 2:
            ir_args.append(_emit(a, registry, state))
            continue
        if a[0] != "lambda":
            raise Unsupported(
                fname, loc, "函数实参必须是函数字面量 function($x){...}")
        ir_args.append(_emit(a, registry, body_state))
    return ("call", fname, ir_args, loc)


def _emit(node, registry, state=None):
    if state is None:
        state = {"blocks": 0}
    tag = node[0]

    if tag == "lit":
        return ("lit", node[1], node[2])

    if tag == "name":
        # JSONata 裸标识符 = 上下文路径 = 字段（DCG 的行对象就是上下文）
        return ("field", node[1], node[2])

    if tag == "py":
        source, free = analyze_py_segment(node[1], node[2])
        return ("py", source, free, node[2])

    if tag == "pyblock":
        # 多语句块是**语句序列**，不是表达式：用独立节点 ("pyblock", ...)，由 codegen
        # 生成命名辅助函数（``def _py0(f1, ...): <块体>``）承载（Lead 裁决 m00841）。
        seq = state["blocks"]
        state["blocks"] += 1
        source, free = compile_py_block(node[1], node[2], seq)
        return ("pyblock", source, free, node[2])

    if tag == "var":
        raw, loc = node[1], node[2]
        if raw == "$":
            return ("root", loc)
        if raw == "$$":
            raise Unsupported("$$ 文档根", loc, "行模式没有整篇文档，只有当前行")
        return ("var", raw[1:], loc)

    if tag == "unary":
        return ("unary", node[1], _emit(node[2], registry, state), node[3])

    if tag == "bin":
        op, loc = node[1], node[4]
        return ("bin", op, _emit(node[2], registry, state), _emit(node[3], registry, state), loc)

    if tag == "cond":
        loc = node[4]
        return (
            "cond",
            _emit(node[1], registry, state),
            _emit(node[2], registry, state),
            _emit(node[3], registry, state),
            loc,
        )

    if tag == "array":
        return ("array", [_emit(x, registry, state) for x in node[1]], node[2])

    if tag == "object":
        pairs = []
        for key, val in node[1]:
            if key[0] == "lit" and isinstance(key[1], str):
                pairs.append((key[1], _emit(val, registry, state)))
            else:
                pairs.append((_emit(key, registry, state), _emit(val, registry, state)))
        return ("object", pairs, node[2])

    if tag in ("path", "index"):
        # `path` 是右结合链（`$.5.a` 解析成 `($ . (5 . a))`），`index` 是后缀链。
        # 从 `$` 起、由标识符段与常量下标段组成的路径写成下标链（`$.5.a.1`、`$.a.b`）。
        # 非 `$` 基座的下标（`a[0]` / `hour[2]`）同样走 ("index", base, idx, loc)
        # （契约 §3.6 已废弃 `$ix` call；运行时 helper 是 `_ix_get`，语义 7：标量取下标=自身）。
        head, segs = _flatten(node, registry)
        if not segs:
            return _emit(head, registry, state)
        if tag == "path" or (head[0] == "var" and head[1] == "$"):
            return _apply_segs(head, segs, registry, node[3], state)
        return _chain(_emit(head, registry, state), segs, registry, node[3], state=state)

    if tag == "call":
        loc = node[3]
        callee, args = node[1], node[2]

        if callee[0] == "var":
            raw = callee[1]
            if raw == "$":
                raise Unsupported("$() 动态求值", loc, "无法逐行编译")
            if raw == "$$":
                raise Unsupported("$$ 文档根", loc, "行模式没有整篇文档，只有当前行")
            fname = raw
        elif callee[0] == "name":
            # 真实 schema 里的 `str($.x)`：只有 policy.BARE_CALL_ALIASES 里的裸名
            # 才允许当函数；其余裸名（多半是字段名）继续按"只有 $ 函数可以被调用"拒绝。
            alias = BARE_CALL_ALIASES.get(callee[1])
            if alias is None:
                raise Unsupported(f"{callee[1]}()", loc, "只有 $ 函数可以被调用")
            fname = alias
        else:
            raise Unsupported(f"{_describe(callee)}()", loc, "只有 $ 函数可以被调用")

        # ---- 先判归属，再 emit 实参（原型坑 b）----
        if fname == "$position":
            if not state.get("in_func"):
                raise Unsupported(
                    "$position()", loc, "只在函数字面量体内可用（序列上下文）")
            return ("call", "$position", [], loc)

        reason = rejection_reason(fname)
        if reason is not None:
            raise Unsupported(fname, loc, reason)

        if fname in HIGHER_ORDER:
            # `$sort(array)` 是 A 类行内函数；只有带比较器的两参形态才是高阶。
            if not (fname == "$sort" and len(args) == 1):
                return _higher_order(fname, args, loc, registry, state)

        if fname not in ROW_FUNCS and fname not in NONDETERMINISTIC:
            if registry is not None and fname in registry:
                ir_args = [_emit(a, registry, state) for a in args]
                return ("rfunc", registry.qualname(fname), ir_args, loc)
            raise Unsupported(
                f"未知函数 `{fname}`", loc,
                "不在任何能力表内（接口实现细节文档 §3.5）")

        ir_args = [_emit(a, registry, state) for a in args]
        return ("call", fname, ir_args, loc)

    if tag == "range":
        raise Unsupported(STRUCTURAL["range"][0], node[3], STRUCTURAL["range"][1])

    if tag == "lambda":
        params, body, loc = node[1], node[2], node[3]
        body_state = dict(state)
        body_state["in_func"] = state.get("in_func", 0) + 1
        return ("lambda", list(params), _emit(body, registry, body_state), loc)

    raise Unsupported(f"未支持的 AST 节点 {tag!r}", node[-1] if node else None, None)


def _flatten(node, registry):
    """把 `path` / 后缀 `index` 链拉平成 ``(基座节点, [段])``，段按源码顺序。

    `$.a[0]` -> ``(("var","$"), ["a", 0])``；`hour[2]` -> ``(("name","hour"), [2])``。
    符号段保留为 ``str``（字段键），数值段保留为 ``int``（常量下标），运行期下标保留为
    ``("dyn", node)``——由 :func:`_apply_segs` 决定是常量下标还是拒绝。
    """
    tag = node[0]
    if tag == "path":
        head, segs = _flatten(node[1], registry)
        rhs = node[2]
        if rhs[0] in ("path", "index"):
            _m, more = _flatten(rhs, registry)
            if _m[0] == "lit" and isinstance(_m[1], int):  # `$.5.a`：数值里的符号路径
                more = [_m[1]] + more
            elif _m[0] == "name":
                more = [_m[1]] + more
            segs = segs + more
        elif rhs[0] == "name" or (rhs[0] == "lit" and not isinstance(rhs[1], (list, dict))):
            segs = segs + [rhs[1]]
        else:
            raise Unsupported(f"不支持的路径 {_describe(node)}", node[3], STRUCTURAL["path"][1])
        return head, segs
    if tag == "index":
        head, segs = _flatten(node[1], registry)
        idx = node[2]
        if idx[0] == "lit" and isinstance(idx[1], int) and not isinstance(idx[1], bool):
            segs = segs + [idx[1]]
        elif idx[0] == "var" and idx[1] in ("$", "$$"):
            raise Unsupported(f"不支持的路径 {_describe(node)}", node[3], STRUCTURAL["path"][1])
        else:
            # 字面量下标（含带引号的字符串键）与**裸名**下标一律走动态下标：
            # 裸名是"本 schema 的字段引用"（Lead 裁决 m00204），故 `$.a[b]` ≡ `$.a[$.b]`，
            # 落成 ("index", base, <任意 IR>, loc)（契约 §3.6 第 2 条）。
            # 修复前裸名分支把下标当"符号段"：`$.a[b]` 撞上 §6.1 的 ≥2 符号段判据被
            # 误报为"深路径 [a,b]"，而 `a[b]` 被静默编成字符串键 `a["b"]`（违反 §3.5
            # 绝不静默降级）。裸名与引号名由此分离，正是 JSONata 语义。
            segs = segs + [("dyn", idx)]
        return head, segs
    return node, []


def _apply_segs(head, segs, registry, loc, state=None):
    """从 `$` 基座按段重建下标链（`$.5.a.1`、`$.a.b`、`$.5.a.1.b` 都允许）。

    路径长度不限：标识符段编成字符串键下标，数值段编成常量下标，逐段叠成
    ``("index", 基座, ("lit", 段), loc)`` 链，由生成器直接连成下标表达式。
    """
    if not (head[0] == "var" and head[1] == "$"):
        raise Unsupported(f"不支持的路径 ${_short(segs)}", loc, STRUCTURAL["path"][1])
    return _chain(("root", head[2]), segs, registry, loc, state=state)


def _chain(base, segs, registry, loc, state=None):
    """按段把 `index(base, lit key)` 链叠起来（路径长度不限）。"""
    cur = base
    for seg in segs:
        if isinstance(seg, bool):
            raise Unsupported(f"路径下标非法 [{_short(seg)}]", loc, STRUCTURAL["path"][1])
        if isinstance(seg, str):
            cur = ("index", cur, ("lit", seg, loc), loc)
        elif isinstance(seg, int):
            cur = ("index", cur, ("lit", seg, loc), loc)
        elif isinstance(seg, tuple) and seg and seg[0] == "dyn":
            # 契约 §3.6 第 2 条：动态下标统一成 ("index", base, <任意 IR>, loc)，
            # codegen 对非 lit 下标翻成 `_ix_get`。`("call","$ix",...)` 已废弃：
            # 它**不是**错值 bug（codegen 一直路由到 _ix_get），而是 `$ix`/`_ix`
            # （$indexOf）差一字符的**命名歧义** + §1 节点表不完备。
            cur = ("index", cur, _emit(seg[1], registry, state), loc)
        else:
            raise Unsupported(f"路径段非法 {_short(seg)}", loc, STRUCTURAL["path"][1])
    return cur


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------
def compile_ast(node, *, registry=None) -> tuple:
    """jx AST（``parser.parse_expr`` 的产物）-> 契约 §1 的 IR。"""
    return _emit(node, registry, {"blocks": 0})


def compile_expr(src: str, *, loc_base=(1, 1), registry=None) -> tuple:
    """JSONata 源码 -> 契约 §1 的 IR。

    遇到不支持的结构抛 :class:`Unsupported`（带列号）；词法/语法错误也统一翻译成
    :class:`Unsupported`（``feature="语法错误"``），保证调用方只需要捕获一种异常。
    """
    if not isinstance(src, str):
        raise TypeError(f"compile_expr() 需要 str，收到 {type(src).__name__}")
    try:
        tokens = lex_tokens(src, loc_base=loc_base)
        tree = parse(tokens)
    except JxSyntaxError as e:
        raise Unsupported("语法错误", e.loc, e.msg) from None
    return _emit(tree, registry, {"blocks": 0})
