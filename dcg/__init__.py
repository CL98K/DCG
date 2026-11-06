# -*- coding: utf-8 -*-
"""DCG v2 —— 数据驱动的 JSONata 子集编译器。
"""
from .schema import validate, SchemaError
from .codegen import generate, build, gen_name, specs_of
from .jx import compile_expr, Unsupported, Registry
from .table import Table, prepare, bind

#: 包版本。**不要**从 importlib.metadata 读：dcg 常常以"源码目录直接上 sys.path"
__version__ = "2.2.3"

__all__ = ["validate", "SchemaError", "generate", "build", "gen_name",
           "compile_expr", "Unsupported", "Registry",
           "Table", "prepare", "bind", "specs_of", "__version__"]
