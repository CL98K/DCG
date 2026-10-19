# -*- coding: utf-8 -*-
"""DCG v2 —— 数据驱动的 JSONata 子集编译器（DCG 接口实现细节文档.md §5）。

公开面只有下面 8 个符号，它们合起来就是 v2 的全部对外承诺：

* :func:`validate` / :exc:`SchemaError` —— schema 冻结与校验（§2）
* :func:`generate` / :func:`build` / :func:`gen_name` —— 产物生成与落盘（§4.1）
* :func:`compile_expr` / :exc:`Unsupported` / :class:`Registry` —— jx 表达式编译（§3）

外部只应 `import dcg` 后使用这 8 个名字；`dcg.codegen` / `dcg.ir` / `dcg.runtime` /
`dcg.jx.*` 都是实现细节，随时可能变。
"""
from .schema import validate, SchemaError
from .codegen import generate, build, gen_name
from .jx import compile_expr, Unsupported, Registry

__all__ = ["validate", "SchemaError", "generate", "build", "gen_name",
           "compile_expr", "Unsupported", "Registry"]
