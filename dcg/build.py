# -*- coding: utf-8 -*-
"""`python -m dcg.build <schema.json> -o <outdir>` 的入口。
"""
from .codegen import _main

if __name__ == "__main__":
    raise SystemExit(_main())
