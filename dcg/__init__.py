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

#: 包版本。**不要**从 importlib.metadata 读：dcg 常常以"源码目录直接上 sys.path"
#: 的方式被使用（本仓库的测试与 evidence 脚本就是这样），那时没有安装分发，
#: importlib.metadata.version("dcg") 会抛 PackageNotFoundError。这里的字面量
#: 与 pyproject.toml 的 `version` 必须一致（tests/test_readme_* 会核对，
#: manifest 的 `generator_version` 也由它派生）。
__version__ = "2.1.0"

__all__ = ["validate", "SchemaError", "generate", "build", "gen_name",
           "compile_expr", "Unsupported", "Registry", "__version__"]
