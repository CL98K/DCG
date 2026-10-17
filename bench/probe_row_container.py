"""行容器（list vs tuple）逐形状判定 —— verifier-dev, task-3。

回答 Lead m00803 第 2 条：D1「行容器是 tuple 不是 list」是**只有** `shape=list` 的
`return(a,b,)` 尾逗号一处，还是**所有形状**都发生？

输出：每个 shape 的产物源码 + 运行结果的容器类型（外层 / 行 / 单条）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402

ROWS = [("r0", 2, 3), ("r1", 6, 7)]


def schema(shape, single=False):
    return {"name": f"shape_{shape}_{int(single)}",
            "output": {"shape": shape, "single": single, "filter": False},
            "out": {"type": "object", "fields": {
                "id": {"type": "str", "value": "$.id"},
                "x": {"type": "int64", "expr": "$.a + $.b"},
                "y": {"type": "int64", "value": "$.a"},
            }}}


def rows():
    return [{"id": r[0], "a": r[1], "b": r[2]} for r in ROWS]


def main():
    for shape in ("list", "dict", "index"):
        for single in ((False, True) if shape == "list" else (False,)):
            tag = f"{shape}{'+single' if single else ''}"
            print("=" * 78)
            print(f"shape={tag}")
            print("=" * 78)
            try:
                src = dcg.generate(schema(shape, single))
            except Exception as exc:  # noqa: BLE001
                print(f"  generate 失败: {type(exc).__name__}: {exc}\n")
                continue
            print(src)
            ns = {}
            exec(compile(src, "<shape>", "exec"), ns)
            data = rows() if not single else rows()[:1]
            try:
                out = ns["run"](data)
            except Exception as exc:  # noqa: BLE001
                print(f"  run 失败: {type(exc).__name__}: {exc}\n")
                continue
            print(f"  外层容器: {type(out).__name__}  值={out!r}")
            if isinstance(out, dict):
                sample = next(iter(out.values()), None)
            elif isinstance(out, list):
                sample = out[0] if out else None
            else:
                sample = out
            print(f"  行容器  : {type(sample).__name__}  值={sample!r}")
            if isinstance(sample, (list, tuple)):
                print(f"  行元素  : {[type(e).__name__ for e in sample]}")
                print(f"  行是 list? {isinstance(sample, list)} | 是 tuple? "
                      f"{isinstance(sample, tuple)}")
            print()

    print("=" * 78)
    print("判定")
    print("=" * 78)
    print("  `return(a,b,)` 尾逗号 → tuple；契约 §4.1 `shape=list` 要求 list of row。")
    print("  dict/index 形状的行值构造器单独看，用来区分「尾逗号一处」vs「构造器系统性」。")


if __name__ == "__main__":
    main()
