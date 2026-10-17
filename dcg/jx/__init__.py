# -*- coding: utf-8 -*-
"""DCG v2 · JSONata 编译器（CONTRACT.md §3）。

公开面三个符号（契约 §3.1 / §5）::

    from dcg.jx import compile_expr, Unsupported, Registry

依赖方向：``jx/*`` 只 import 同级模块，**不 import dcg 的其他模块** ——
IR 是纯 tuple（契约 §1），不需要 ir.py 的符号，因此 jx 可以独立单测。
运行期**绝不** import ``jsonata``（契约 D4）；``jsonata-python`` 只作为
``tests/`` 里带 ``importorskip`` 保护的对照探针。
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
from .policy import BATCH_ONLY, ROW_FUNCS, Unsupported
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
    "BATCH_ONLY",
    # 链解析的唯一权威（契约 §3.7；codegen 也走它）
    "parse_path",
    "parse_chain",
    "parse_path_chain",
    "split_chain",
    "chain_ir",
    "FIELD_REF",
    "count_symbolic",
    # jx 保留给 codegen 的命名空间前缀 ``__jx``：用户反引号段里出现即 Unsupported，
    # 因此 codegen 可以放心用 ``__jx_p0``/``__jx_f0`` 做别名，绝不与用户源码撞车。
    "RESERVED_PREFIX",
]
