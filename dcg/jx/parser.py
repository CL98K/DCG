# -*- coding: utf-8 -*-
"""DCG v2 · JSONata Pratt 解析器（DCG 接口实现细节文档.md §3.3）。

按早期原型（已从仓库移除）的 Pratt 骨架重写，四处**原型已实测的坑**必须保留修法：

a. 调用/下标/路径统一在**中缀循环的后缀位置**处理。原型早期只在 prefix 识别
   函数调用，导致 ``$not(miss)``、``a[0]`` 解析到一半报"多余内容"。
b. ``and`` / ``or`` / ``in`` 必须进结合力表，否则 ``KeyError``。
c. 这里的 AST 不做政策判定；归属判定在 ``compiler`` 里**先于实参 emit**（见 policy）。
d. 输出是 jx 内部 AST（``("path", ...)`` 之类的未决形状），由 ``compiler`` 落成
   契约 §1 的 IR。AST 里的裸标识符是 ``("name", ...)``，是"上下文路径"而不是字段，
   最终由 compiler 决定它是不是字段。

结合力（契约 §3.3，数字越大越紧）::

    or 10 · and 20 · 比较/in 30 · & 40 · +- 50 · */% 60 · 一元 - + not 70
    · 后缀 . [ ( 90 · .. 45 · 三元 ? 5
"""
from __future__ import annotations

import ast as pyast

from .lexer import JxSyntaxError, lex_tokens
from .policy import STRUCTURAL, Unsupported

__all__ = ["BP", "Parser", "parse", "parse_expr"]

#: 中缀结合力表（契约 §3.3）。
BP = {
    "or": 10,
    "and": 20,
    "=": 30, "!=": 30, "<": 30, "<=": 30, ">": 30, ">=": 30, "in": 30,
    "&": 40,
    "+": 50, "-": 50,
    "*": 60, "/": 60, "%": 60,
}

POSTFIX_BP = 90          # . [ (      （契约 §3.3）
RANGE_BP = 45            # ..
UNARY_BP = 70            # 一元 - + not
COND_BP = 5              # 三元 ? :

#: 词法上是 name、语法上是运算符的关键字。
WORD_OPS = frozenset({"and", "or", "in"})

_LITERALS = {"true": True, "false": False, "null": None}

#: 契约 §3.5 要求结构性拒绝的单字符/双字符运算符 -> STRUCTURAL 的键。
_STRUCT_OPS = {
    "#": "bind_pos",
    "@": "bind_join",
    "^": "sort",
    "|": "transform",
    "~>": "transform",
}


def _structural(key: str, loc) -> Unsupported:
    feature, msg = STRUCTURAL[key]
    return Unsupported(feature, loc, msg)


class Parser:
    """JSONata 表达式 -> jx 内部 AST（不含政策判定）。"""

    def __init__(self, tokens):
        self.t = list(tokens)
        self.i = 0

    # -- 记号流 -----------------------------------------------------------
    def peek(self, k: int = 0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else self.t[-1]

    def next(self):
        tok = self.t[self.i]
        if tok.kind != "eof":
            self.i += 1
        return tok

    def eat(self, value: str):
        tok = self.peek()
        if tok.value != value:
            raise JxSyntaxError(f"期望 {value!r}，实际 {tok.value!r}", (tok.line, tok.col))
        return self.next()

    # -- 入口 -------------------------------------------------------------
    def parse(self):
        node = self.expr(0)
        tok = self.peek()
        if tok.kind != "eof":
            raise JxSyntaxError(f"多余内容: {tok.value!r}", (tok.line, tok.col))
        return node

    # -- Pratt 中缀循环 ---------------------------------------------------
    def expr(self, minbp: int):
        left = self.prefix()
        while True:
            tok = self.peek()
            kind, val = tok.kind, tok.value
            loc = (tok.line, tok.col)

            # 后缀：调用 / 下标 / 路径 —— 全部在这里处理（坑 a）
            if kind == "op" and val == "(":
                if POSTFIX_BP < minbp:
                    break
                self.next()
                args = self._arglist(")")
                left = ("call", left, args, loc)
                continue
            if kind == "op" and val == "[":
                if POSTFIX_BP < minbp:
                    break
                self.next()
                idx = self.expr(0)
                self.eat("]")
                left = ("index", left, idx, loc)
                continue
            if kind == "op" and val == ".":
                if POSTFIX_BP < minbp:
                    break
                self.next()
                nxt = self.peek()
                if nxt.kind == "op" and nxt.value == "{":
                    # a.{...} —— Reduce 路径阶段
                    raise _structural("reduce", (nxt.line, nxt.col))
                right = self.expr(POSTFIX_BP)
                left = ("path", left, right, loc)
                continue

            if kind == "op" and val == "..":
                if RANGE_BP < minbp:
                    break
                self.next()
                right = self.expr(RANGE_BP)
                left = ("range", left, right, loc)
                continue

            if kind == "op" and val == "?":
                if COND_BP < minbp:
                    break
                self.next()
                then = self.expr(0)
                self.eat(":")
                orelse = self.expr(COND_BP)
                left = ("cond", left, then, orelse, loc)
                continue

            # 结构性拒绝：出现在中缀位置才是它本来的语义
            if kind == "op" and val == "{":
                # expr{...} —— 分组/聚合
                raise _structural("group", loc)
            if kind == "op" and val in _STRUCT_OPS:
                raise _structural(_STRUCT_OPS[val], loc)

            if kind == "op":
                key = val
            elif kind == "name" and val in WORD_OPS:
                key = val
            else:
                key = None
            if key not in BP:
                break
            bp = BP[key]
            if bp < minbp:
                break
            self.next()
            right = self.expr(bp + 1)
            left = ("bin", key, left, right, loc)
        return left

    # -- 前缀 -------------------------------------------------------------
    def prefix(self):
        tok = self.next()
        kind, val, loc = tok.kind, tok.value, (tok.line, tok.col)

        if kind == "num":
            return ("lit", _to_number(val), loc)
        if kind == "str":
            return ("lit", pyast.literal_eval(val), loc)
        if kind == "re":
            raise _structural("regex", loc)
        if kind == "py":
            return ("py", val, loc)
        if kind == "pyblock":
            # 多语句代码块：这里只承载原始块体，块内规则由 compiler.compile_py_block 判定
            return ("pyblock", val, loc)

        if kind == "name":
            if val in _LITERALS:
                return ("lit", _LITERALS[val], loc)
            if val == "not":
                # 契约 §3.3 把 not 列为一元运算符（JSONata 本身只有 $not()）
                return ("unary", "not", self.expr(UNARY_BP), loc)
            if val == "function" and self.peek().value == "(":
                return self._lambda(loc)
            return ("name", val, loc)

        if kind == "var":
            return ("var", val, loc)

        if kind == "op":
            if val == "(":
                node = self.expr(0)
                self.eat(")")
                return node
            if val == "[":
                return ("array", self._arglist("]"), loc)
            if val == "{":
                return ("object", self._pairs(), loc)
            if val in ("-", "+"):
                return ("unary", val, self.expr(UNARY_BP), loc)
            if val in _STRUCT_OPS:
                raise _structural(_STRUCT_OPS[val], loc)
            if val == "..":
                raise _structural("range", loc)

        raise JxSyntaxError(f"不支持的起始记号 {val!r}", loc)

    # -- 函数字面量 --------------------------------------------------------
    def _lambda(self, loc):
        """`function($x, $y){ 表达式 }` -> `("lambda", [参数名...], body, loc)`。"""
        self.eat("(")
        params = []
        if self.peek().value != ")":
            while True:
                t = self.next()
                if t.kind != "var" or not t.value.startswith("$") \
                        or t.value in ("$", "$$"):
                    raise JxSyntaxError("函数字面量参数必须是 $变量",
                                        (t.line, t.col))
                params.append(t.value[1:])
                if self.peek().value == ",":
                    self.next()
                    continue
                break
        self.eat(")")
        self.eat("{")
        body = self.expr(0)
        self.eat("}")
        return ("lambda", params, body, loc)

    # -- 参数表 / 字面量表 / 对象 ------------------------------------------
    def _arglist(self, closer: str):
        items = []
        if self.peek().value == closer:
            self.next()
            return items
        items.append(self.expr(0))
        while self.peek().value == ",":
            self.next()
            items.append(self.expr(0))
        self.eat(closer)
        return items

    def _pairs(self):
        pairs = []
        if self.peek().value == "}":
            self.next()
            return pairs
        while True:
            key = self.expr(0)
            self.eat(":")
            val = self.expr(0)
            pairs.append((key, val))
            if self.peek().value != ",":
                break
            self.next()
        self.eat("}")
        return pairs


def _to_number(text: str):
    if "." in text or "e" in text or "E" in text:
        return float(text)
    return int(text)


def parse(tokens):
    """记号流 -> jx AST。"""
    return Parser(tokens).parse()


def parse_expr(src: str, *, loc_base=(1, 1)):
    """``src`` -> jx AST（词法+语法一体，便于单测）。"""
    return parse(lex_tokens(src, loc_base=loc_base))
