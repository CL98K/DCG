# -*- coding: utf-8 -*-
"""DCG v2 · JSONata 词法分析器。
"""
from __future__ import annotations

import re
from collections import namedtuple

__all__ = ["TOKEN", "REGEX_LITERAL", "Token", "JxSyntaxError", "lex", "lex_tokens"]


#: PRATT/IR 共用的记号载体。``col`` / ``line`` 已经是**绝对**坐标（含 loc_base 偏移）。
Token = namedtuple("Token", "kind value col line")

#: 定位坐标。契约 只承诺错误串里带列号，这里额外保留行号以便 loc_base 偏移。
Loc = tuple  # (line, col)


class JxSyntaxError(SyntaxError):
    """词法/语法错误（不是政策拒绝）。``compile_expr`` 会把它翻译成 ``Unsupported``。"""

    def __init__(self, msg: str, loc=None):
        super().__init__(msg)
        self.msg = msg
        self.loc = loc

    def __str__(self) -> str:  # pragma: no cover - 只影响调试输出
        if self.loc:
            return f"第 {self.loc[0]} 行第 {self.loc[1]} 列: {self.msg}"
        return self.msg


# ---------------------------------------------------------------------------
TOKEN = re.compile(
    r"""
      (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<var>\$\$|\$[A-Za-z_][A-Za-z0-9_]*|\$)
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op>!=|<=|>=|~>|\.\.|\?\?|\?:|[-+*/%&<>=.,()\[\]{}:?#@^|])
    """,
    re.X,
)

#: 正则字面量 `/.../flags`。只在"期望操作数"的位置尝试匹配。
REGEX_LITERAL = re.compile(r"/(?:\\.|[^/\\\n])+/[a-zA-Z]*")

#: 能结束一个表达式的记号 —— 其后出现的 `/` 是除法而不是正则起始。
_ENDS_EXPR_KINDS = frozenset({"num", "str", "re", "var", "name", "py", "pyblock"})
_ENDS_EXPR_OPS = frozenset({")", "]", "}"})

_WHITESPACE = " \t\r\n\f\v"


def _advance(line: int, col: int, text: str):
    """给定一段已消费文本，返回其后的 (line, col)。"""
    nl = text.count("\n")
    if nl:
        return line + nl, len(text) - text.rfind("\n")
    return line, col + len(text)


def _scan_py_block(src: str, i: int, loc) -> int:
    """``src[i] == '{'``：返回与它配平的 ``}`` 下标；不配平抛 :class:`JxSyntaxError`。
"""
    depth = 0
    j = i
    n = len(src)
    while j < n:
        c = src[j]
        if c == "#":
            nl = src.find("\n", j)
            j = n if nl < 0 else nl + 1
            continue
        if c in "\"'":
            quote = c * 3 if src.startswith(c * 3, j) else c
            k = j + len(quote)
            while True:
                if k >= n:
                    raise JxSyntaxError("py{} 代码块里的字符串未闭合", loc)
                if src[k] == "\\":
                    k += 2
                    continue
                if src.startswith(quote, k):
                    k += len(quote)
                    break
                if len(quote) == 1 and src[k] == "\n":
                    raise JxSyntaxError("py{} 代码块里的字符串未闭合", loc)
                k += 1
            j = k
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise JxSyntaxError("py{} 代码块未闭合（缺少配对的 }）", loc)


def _ends_expr(tok) -> bool:
    if tok is None:
        return False
    if tok.kind in _ENDS_EXPR_KINDS:
        return True
    return tok.kind == "op" and tok.value in _ENDS_EXPR_OPS


def _tokenize(src: str, loc_base=(1, 1)):
    if not isinstance(src, str):
        raise TypeError(f"lex() 需要 str，收到 {type(src).__name__}")
    base_line, base_col = loc_base
    out = []
    pos = 0
    n = len(src)
    line = base_line
    col = base_col
    prev = None
    while pos < n:
        ch = src[pos]

        # /* ... */ 注释（JSONata 原生语法，整体当空白）
        if src.startswith("/*", pos):
            end = src.find("*/", pos + 2)
            if end < 0:
                raise JxSyntaxError("块注释未闭合（缺少 */）", (line, col))
            piece = src[pos : end + 2]
            line, col = _advance(line, col, piece)
            pos = end + 2
            continue

        if ch in _WHITESPACE:
            line, col = _advance(line, col, ch)
            pos += 1
            continue

        start_line, start_col = line, col

        # 反引号内联 Python 段：原样捕获，段内不解析 JSONata
        if ch == "`":
            j = pos + 1
            buf = []
            while j < n:
                if src[j] == "\\" and j + 1 < n and src[j + 1] == "`":
                    buf.append("`")
                    j += 2
                    continue
                if src[j] == "`":
                    break
                buf.append(src[j])
                j += 1
            if j >= n:
                raise JxSyntaxError("反引号段未闭合", (start_line, start_col))
            seg = "".join(buf)
            line, col = _advance(line, col, src[pos : j + 1])
            pos = j + 1
            tok = Token("py", seg, start_col, start_line)
            out.append(tok)
            prev = tok
            continue

        # py{ ... } 多语句代码块：与反引号段**共存**，互不干扰。
        if ch == "p" and src.startswith("py", pos):
            k = pos + 2
            while k < n and src[k] in _WHITESPACE:
                k += 1
            if k < n and src[k] == "{":
                end = _scan_py_block(src, k, (start_line, start_col))
                body_line, body_col = _advance(line, col, src[pos : k + 1])
                line, col = _advance(line, col, src[pos : end + 1])
                pos = end + 1
                tok = Token("pyblock", src[k + 1 : end], body_col, body_line)
                out.append(tok)
                prev = tok
                continue

        # `/`：先看能否是正则字面量，再退回除法
        if ch == "/" and not _ends_expr(prev):
            m = REGEX_LITERAL.match(src, pos)
            if m:
                text = m.group()
                line, col = _advance(line, col, text)
                pos = m.end()
                tok = Token("re", text, start_col, start_line)
                out.append(tok)
                prev = tok
                continue

        m = TOKEN.match(src, pos)
        if not m:
            raise JxSyntaxError(
                f"无法识别的字符 {src[pos:pos + 12]!r}", (start_line, start_col)
            )
        text = m.group()
        kind = m.lastgroup
        line, col = _advance(line, col, text)
        pos = m.end()
        tok = Token(kind, text, start_col, start_line)
        out.append(tok)
        prev = tok

    out.append(Token("eof", "", col, line))
    return out


def lex_tokens(src: str, loc_base=(1, 1)):
    """词法分析，返回 :class:`Token` 列表（末元素为 ``eof``），带绝对行列号。"""
    return _tokenize(src, loc_base)


def lex(src: str):
    """契约 的公开签名：``[(kind, value, col), ...]``（末元素为 ``('eof', '', col)``）。
"""
    return [(t.kind, t.value, t.col) for t in _tokenize(src)]
