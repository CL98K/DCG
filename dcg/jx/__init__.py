# -*- coding: utf-8 -*-
"""DCG v2 · JSONata 编译器。
"""
from .compiler import (
    RESERVED_PREFIX,
    analyze_py_segment,
    chain_ir,
    compile_ast,
    compile_expr,
    compile_py_block,
    parse_path_chain,
    split_chain,
)
from .lexer import JxSyntaxError, lex
from .parser import parse_expr
from .paths import FIELD_REF, count_symbolic, parse_chain, parse_path
from .policy import HIGHER_ORDER, NONDETERMINISTIC, ROW_FUNCS, Unsupported
from .registry import Registry, default_registry

__all__ = [
    "compile_expr",
    "compile_ast",
    "Unsupported",
    "Registry",
    "default_registry",
    "analyze_py_segment",
    "compile_py_block",
    "parse_expr",
    "lex",
    "JxSyntaxError",
    "ROW_FUNCS",
    "HIGHER_ORDER",
    "NONDETERMINISTIC",
    # 链解析的唯一权威；codegen 也走它）
    "parse_path",
    "parse_chain",
    "parse_path_chain",
    "split_chain",
    "chain_ir",
    "FIELD_REF",
    "count_symbolic",
    # jx 保留给 codegen 的命名空间前缀 ``__jx``：用户反引号段里出现即 Unsupported。
    "RESERVED_PREFIX",
]
