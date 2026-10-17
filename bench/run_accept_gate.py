"""单独运行 Lead 的 evidence/accept.py 里某一道 gate（只读，不修改 accept.py）。

用途（Lead m00803 第 4 条）：在 P0-1..P0-4 未修好时**不要**跑完整 accept.py（大面积红），
但要能单独验证某道 gate 自身确实是红的 —— 即「gate 有效」。

用法：
    python -X utf8 bench/run_accept_gate.py                       # 默认 sentinel integrity
    python -X utf8 bench/run_accept_gate.py "row container"       # 名称子串匹配
    python -X utf8 bench/run_accept_gate.py --list                # 列出全部 gate 名
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evidence"))

import accept  # noqa: E402  (Lead 的验收 gate，只 import 不修改)


def main(argv):
    if "--list" in argv:
        for name, _fn in accept.GATES:
            print(f"  - {name}")
        return 0
    needle = " ".join(a for a in argv[1:] if not a.startswith("-")).lower()
    if not needle:
        needle = "sentinel"
    hits = [(n, f) for n, f in accept.GATES if needle in n.lower()]
    if not hits:
        print(f"没有匹配 {needle!r} 的 gate；用 --list 查看全部")
        return 2
    rc = 0
    for name, fn in hits:
        try:
            status, msg = fn()
        except Exception as exc:  # noqa: BLE001
            status, msg = accept.FAIL, f"gate 抛异常: {type(exc).__name__}: {exc}"
        print(f"[{status}] {name}\n        {msg}")
        if status != accept.PASS:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
