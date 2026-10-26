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
#: 反引号段里的裸 `$`（= 当前行本身）。Python 写不出裸 `$`，用占位名过 `ast.parse`，
#: 由 codegen 在发射时还原成当前行的局部名（契约 §3.7）。
_PY_ROW_PLACEHOLDER = "__jx_row"
#: 行模式里的 `$变量` 简写（`$upper(x)`）：没有变量作用域，占位后由下面统一报错。
#: 占位名以双下划线收尾（``__jx__var``），避开 PEP 8 里「`__x` 名字改写」的边界形态，
#: 同时保证它不会与 ``_PY_ROW_PLACEHOLDER``（``__jx_row``）粘连成假名字（task-14）。
_PY_VAR_MARK = "__jx__var"
#: 报错时把上面那个占位符换回来的映射（占位符本身是合法标识符，能过 ``ast.parse``）。
_PY_VAR_MARK_TXT = "${"

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


def _target_names(target) -> set:
    """收集赋值目标里的全部标识符名（推导式 `for a, b in ...` 这类解包也算）。"""
    out = set()
    for node in pyast.walk(target):
        if isinstance(node, pyast.Name):
            out.add(node.id)
    return out


def _args_names(args) -> set:
    """收集 lambda 形参名（含 posonly / kwonly / vararg / kwarg）。"""
    out = set()
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        out.add(a.arg)
    if args.vararg is not None:
        out.add(args.vararg.arg)
    if args.kwarg is not None:
        out.add(args.kwarg.arg)
    return out


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

    返回 ``(source_str, free_field_names)``；``source_str`` **原样**保留（codegen 负责
    把 ``$.name`` 换成生成局部名、把裸 ``$`` 换成当前行）。裸 ``$`` 在 Python 里不是
    合法表达式，这里只**校验期**换成 :data:`_PY_ROW_PLACEHOLDER`，返回值不受影响。
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

    # 裸 `$`（当前行本身）在 Python 里不是合法表达式，换成占位符；发射时 codegen
    # 把它还原成当前行的局部名。JSONata 语境里 `$` 就是行对象，`` `json.loads($)` ``
    # 是最常见的行级前处理写法，不能因为"Python 里写不出裸 $"就拒绝。
    #
    # 只换**后面不是标识符字符**的 `$`：`$upper(x)` 里的 `$` 紧跟 `upper`，那是变量的
    # 简写形态，不是「当前行」，原样留着会被当成非法语法 / 未声明裸名报错；而 `$.链`
    # 已经在上面换成字段占位符了。独立 `$`（后面是括号、运算符、空白或串尾）才换成
    # 一个合法 Python 名字，词边界因此永远成立，且永远不会让占位符粘上标识符。
    parse_src = re.sub(r"\$(?=[A-Za-z_])", _PY_VAR_MARK, parse_src)
    parse_src = parse_src.replace("$", _PY_ROW_PLACEHOLDER)

    try:
        tree = pyast.parse(parse_src, mode="eval")
    except SyntaxError as e:
        raise Unsupported(
            "反引号段 Python 语法错误",
            loc,
            f"{e.msg}（段内偏移 {e.offset}）—— 必须是单个 Python 表达式",
        ) from None

    placeholders = {f"{_PY_PLACEHOLDER}{i}" for i in range(len(names))}
    # 反向兜底：`$` 后面若跟了一个**不在安全全局里**的标识符，说明用户在反引号段里写了
    # `$foo` 这种变量简写。行模式没有变量作用域，必须报"未声明的名字"而不是让占位名
    # （`__jx__varfoo`）漏进 free_field_names 变成"裸名 '__jx__varfoo' 不是字段"。
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
            if node.id.startswith(_PY_VAR_MARK):
                raise Unsupported(
                    f"反引号段里的 ${node.id[len(_PY_VAR_MARK):]} 不可逐行编译",
                    loc,
                    "行模式没有变量作用域：字段写 $.名称，当前行本身就是裸 $",
                )
    # 推导式 / lambda 的**局部绑定名**不是字段引用：`[r for r in $]` 里的 `r` 是
    # 循环变量。行级写法里它们极少出现，但 `before`/`after` 钩子拿的是全量数据，
    # `= `[{k: ...} for r in $]`` 这种写法很自然，必须放行（task-13）。
    shadowed = set()
    for node in pyast.walk(tree):
        if isinstance(node, (pyast.ListComp, pyast.SetComp, pyast.GeneratorExp,
                             pyast.DictComp)):
            for gen in node.generators:
                shadowed |= _target_names(gen.target)
        elif isinstance(node, pyast.Lambda):
            shadowed |= _args_names(node.args)
    bare: list[tuple[int, int, str]] = []
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
            if node.id in placeholders or node.id in PY_SAFE_GLOBALS:
                continue
            if node.id in shadowed:
                continue
            if node.id == _PY_ROW_PLACEHOLDER:
                # 裸 `$` 不是字段引用，也不该被当成裸名登记（它由 codegen 还原成行对象）。
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
#: 块内反引号段的占位符（``__jxbt0``/``__jxbacktick``）：能通过 ``ast.parse``，
#: 名字校验时被 ``RESERVED_PREFIX`` 放行，发射前原样换回 ``` `...` ```。
_BT_TOKEN_RE = re.compile(rf"{RESERVED_PREFIX}(?:backtick|bt\d+)")
#: 块内裸 ``$``（= 当前行 / 整份数据本身）的占位符：名字校验放行，发射时还原成
#: 反引号段 `` `$` `` —— 反引号段的自有编译器会把它落到正确的根局部名（``R``/``D``）。
_PY_BLOCK_DOLLAR = "__jxdollar"
#: 块内 `$` 的三种形态：`$$`（整篇文档，行模式无意义）、`$变量`（行模式无作用域）、
#: 独立 `$`（当前行 / 整份数据本身 —— 唯一放行的一种）。
_DOUBLE_DOLLAR_RE = re.compile(r"\$\$")
_VAR_DOLLAR_RE = re.compile(r"\$[A-Za-z_]")
_BARE_DOLLAR_RE = re.compile(r"\$(?![A-Za-z_0-9$])")
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
        if c == "`":
            # 反引号段有自己的语法（裸 `$` = 当前行/整份数据本身），段内的 `;`
            # 与引号都不是块级结构（task-13：`let k = [r for r in $ if r[0]]`）。
            seg, end = _scan_backtick_segment(body, i + 1)
            if seg is None:
                raise Unsupported(
                    "代码块里的反引号段未闭合", _py_pos_in(body, i, loc),
                    "反引号段必须成对出现（契约 §3.3）")
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


def _split_backtick_segments(text: str):
    """把表达式里的反引号段抽成占位符，返回 ``(segments, masked)``。

    反引号段内的内容有独立语义（裸 ``$`` = 当前行/整份数据本身，契约 §3.3），
    块级校验（``$.链`` 改写、裸 ``$`` 检查、名字校验）都不该看进去，故先摘出来，
    校验完再用 :func:`_restore_backtick_segments` 原样放回。
    未闭合的反引号按开放处理，把余下全部内容当一个段（块内报错由后续
    ``ast.parse`` 给出真实位置）。
    """
    segments: list[str] = []
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        # Python 字符串字面量里的反引号是普通字符（`return 'a`b'`），不是段。
        if c in "\"'":
            end = _py_skip_string(text, i)
            if end < 0:
                end = len(text)
            out.append(text[i:end])
            i = end
            continue
        if c == "`":
            seg, end = _scan_backtick_segment(text, i + 1)
            if seg is None:
                seg, end = text[i + 1 :], len(text)
            segments.append(seg)
            out.append(_bt_token(len(segments) - 1))
            i = end
            continue
        out.append(c)
        i += 1
    return segments, "".join(out)


def _bt_token(i: int, kind: str = "bt") -> str:
    """反引号段的 Python 标识符形态占位符（能通过 ``ast.parse``）。"""
    return f"{RESERVED_PREFIX}{kind}{i}"


def _restore_backtick_segments(text: str, segments) -> str:
    for i, seg in enumerate(segments):
        text = text.replace(_bt_token(i), "`" + seg + "`")
    return text


def _bt_mark(text: str) -> str:
    """整条表达式就是一段反引号段时的占位符。"""
    return f"{RESERVED_PREFIX}backtick"


def _scan_backtick_segment(text: str, i: int):
    """从 ``i``（`` ` `` 之后）扫到未转义的收尾反引号，返回 ``(segment, end_index)``。

    与 :func:`dcg.jx.lexer.lex` 同口径：``\\`` `` 是字面反引号，其余字符原样保留。
    未闭合返回 ``(None, i)``。
    """
    out = []
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] == "`":
            out.append("`")
            i += 2
            continue
        if c == "`":
            return "".join(out), i + 1
        out.append(c)
        i += 1
    return None, i


def _py_block_expr(text: str, offset: int, body: str, loc, mapping, let_names, free) -> str:
    """校验块内一条表达式，返回**占位符形态**（已验证）的可发射源码。

    块内 ``$.链`` 被换成 ``__jxq<i>`` 占位符交给 codegen 回填；**反引号段
    （`` `...` ``）原样保留**——它有自己的编译器，裸 ``$`` 在段内表示当前行 /
    整份数据本身（契约 §3.3），块内同样适用。
    """
    segments, masked = _split_backtick_segments(text)
    whole_segment = False
    if segments and masked.strip() == _bt_token(0):
        whole_segment = True
        masked = _bt_mark(text)
    # 段内容本身可能是 `$`（= 当前行/整份数据本身），掩码阶段不该看进去：
    # 先把段里的内容当成不透明串，等校验完再原样放回。
    chunks = []
    pos = 0
    for m in FIELD_REF.finditer(masked):
        chunks.append(masked[pos : m.start()])
        chain = m.group(1)
        chain_loc = _py_pos_in(body, offset + m.start(), loc)
        split_chain(chain, chain_loc)   # 链语法校验；路径长度不限
        if chain not in free:
            free.append(chain)
        chunks.append(f"{_BLOCK_PLACEHOLDER}{free.index(chain)}")
        pos = m.end()
    chunks.append(masked[pos:])
    masked = "".join(chunks)

    # 裸 `$`：块内表示「当前行 / 整份数据本身」，与反引号段（§3.3）同口径。
    # 只认**独立**的 `$`（前后都不是标识符字符）：`$x` 是 JSONata 变量引用，
    # 行模式没有变量作用域，仍按契约 §3.7 拒绝。`$$` 在行模式下没有意义。
    if _DOUBLE_DOLLAR_RE.search(masked):
        raise Unsupported(
            "代码块里的 $ 引用",
            _py_pos_in(body, offset + _DOUBLE_DOLLAR_RE.search(masked).start(), loc),
            "行模式下没有整篇文档，只有当前行（契约 §3.7 第 1 条）",
        )
    if _VAR_DOLLAR_RE.search(masked):
        raise Unsupported(
            "代码块里的 $ 引用",
            _py_pos_in(body, offset + _VAR_DOLLAR_RE.search(masked).start(), loc),
            "行模式没有变量作用域：字段写 `$.名称`，当前行本身就是裸 `$`（契约 §3.7 第 1 条）",
        )
    dollars = bool(_BARE_DOLLAR_RE.search(masked))
    if dollars:
        masked = _BARE_DOLLAR_RE.sub(_PY_BLOCK_DOLLAR, masked)
    backticks = [m.group() for m in _BT_TOKEN_RE.finditer(masked)]

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
    out = _py_rename_locals(masked, mapping)
    # 反引号段一律还原成 `` `段内容` ``（含空段 / `$` 段），交给 codegen 的
    # :meth:`_Gen._expand_backticks_in_block` 落到正确的根局部名。
    out = _restore_backtick_segments(out, segments)
    if whole_segment:
        out = out.replace(_bt_mark(text), "`" + segments[0] + "`", 1)
    # 裸 `$` 统一还原成等价的反引号段 `` `$` ``：块内它就是「当前行 / 整份数据本身」，
    # 与段内 `$` 同义，这样 codegen 只需处理一种形态（task-13）。
    return out.replace(_PY_BLOCK_DOLLAR, "`$`")


def _restore_dollar_in_block(text: str) -> str:
    """把 ``_PY_BLOCK_DOLLAR`` 占位符还原成反引号包裹的裸 ``$`` 段。"""
    return text.replace(_PY_BLOCK_DOLLAR, "`$`")


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

    # 反引号段不是合法 Python，`mode="exec"` 校验前要先换成标识符占位符
    # （与逐条表达式校验同一口径：段内是反引号段自己的语法）。
    _segs, for_check = _split_backtick_segments(masked_src)
    try:                                   # 契约 §3.7 第 2 条：mode="exec"
        whole = pyast.parse(for_check, mode="exec")
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
            # 键必须是字符串字面量：`{expr: expr}` 是 JSONata 的分组构造（永久拒绝，
            # 契约 §3.5）。parser 已先一步拦住，这里是第二道防线 —— 若放行，
            # `_emit(key, ...)` 返回的是 IR 元组，会被当成字典键原样泄漏进产物。
            if key[0] != "lit" or not isinstance(key[1], str):
                raise Unsupported(
                    "分组/聚合 {..}",
                    key[-1] if isinstance(key[-1], tuple) else node[2],
                    STRUCTURAL["group"][1])
            pairs.append((key[1], _emit(val, registry, state)))
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
                "不在任何能力表内（DCG 接口实现细节文档 §3.5）")

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
