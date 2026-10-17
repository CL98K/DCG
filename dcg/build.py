# -*- coding: utf-8 -*-
"""`python -m dcg.build <schema.json> -o <outdir>` 的入口（CONTRACT §4.1）。

真正的实现放在 :mod:`dcg.codegen`（`generate` / `build` / `gen_name` 的唯一实现处），
这里只做模块入口，保证 `dcg/` 里没有第二份生成逻辑。
"""
from .codegen import _main

if __name__ == "__main__":
    raise SystemExit(_main())
