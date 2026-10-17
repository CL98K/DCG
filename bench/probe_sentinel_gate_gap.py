"""证明 Lead 的 `evidence/accept.py::g_sentinel_integrity` 目前**因为错误的原因**变红，
即：它的两半都抓不到它要抓的东西。只读 accept.py，不修改它。

发现（verifier-dev，见 evidence/v2_accept_gate_gap.txt）：
  (a) 静态半边：`accept.py:485` 在 `_body(schema)` 上扫 `^\\s*(_U|UNDEF)\\s*=`，
      而 `_body()`（accept.py:268-277）**只返回 `def run(...)` 之后的部分**，
      哨兵绑定 `_U=0` 位于共享 helper 前导段（`def run` 之前）→ 永远扫不到。
  (b) 行为半边：`accept.py:500` 用 `rows = [{"a": 0, "b": 5, "i": 1}, ...]`（dict 行），
      而 CAP 的表达式是 `$.5.a.1` → 产物做 `R[5]` → `KeyError: 5`，
      于是它想要的 `a+b -> [5, 5]` 断言**从未执行**。
  后果：parser-dev 修好哨兵之后这道 gate **仍然红**（还是一样 KeyError: 5），
        既不能变绿也不能定位；而修好前它也没有真的验证过 0 的语义。

用法：python -X utf8 bench/probe_sentinel_gate_gap.py
"""
from __future__ import annotations

import importlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evidence"))

import accept  # noqa: E402


def main() -> int:
    src = accept._src(accept.CAP)
    body, off = accept._body(accept.CAP)
    print("== (a) 静态半边的扫描范围 ==")
    print(f"CAP 产物全长 {len(src)} 字符；_body() 起点 {off}（= 前导段长度）")
    print(f"_U=0 的实际位置: {src.find('_U=0')}   （<0 表示不在产物里）")
    in_body = "_U=0" in body
    print(f"_U=0 是否在 _body() 返回的文本里: {in_body}  <- 静态半边只会看到这里")
    pat = r"^\s*(_U|UNDEF)\s*=\s*(.+)$"
    hits_src = re.findall(pat, src, re.M)
    hits_body = re.findall(pat, body, re.M)
    print(f"对**整个产物**扫哨兵绑定: {hits_src}")
    print(f"对 _body() 扫哨兵绑定: {hits_body}")

    print("\n== (b) 行为半边的输入布局 ==")
    print(f"CAP schema: shape={accept.CAP['output']['shape']} filter={accept.CAP['output']['filter']}")
    print(f"CAP 字段: {list(accept.CAP['out']['fields'])}  hourA=$.5.a.1 hourB=$.5.a.2 hourE=hourA+hourB")
    bad_rows = [{"a": 0, "b": 5, "i": 1}, {"a": 5, "b": 0, "i": 1}]
    print(f"accept.py 实际喂的行: {bad_rows}  (dict 行)")
    print(f"accept.py 自己定义的正确布局常量: ROW = {accept.ROW}")

    from dcg import runtime
    g: dict = dict(runtime.SOURCE)
    exec(compile(src, "<gen:gap-probe>", "exec"), g)  # noqa: S102
    run = g["run"]
    # CAP: hourA=$.5.a.1 -> R[5]['a'][1]；hourB=$.5.a.2 -> R[5]['a'][2]；hourE=hourA+hourB 在下标 3
    rows = [(0, 0, 0, 0, 0, {"a": [0, 0, 5, 0]}), (1, 0, 0, 0, 0, {"a": [0, 5, 0, 0]})]
    got = run(rows)
    print(f"用**正确布局**跑 a=0,b=5 / a=5,b=0 -> {got}")
    first = got[0] if got else None
    if first is not None and not isinstance(first, (list, tuple)):
        print("  <- 返回的是**扁平 list**（2 行 × 4 字段 = 8 个标量）；"
              "shape=list 要求 list of row，说明行被摊平了（D1 变体）")
    else:
        print(f"  hourE（下标 3）= {[r[3] for r in got]}  期望 [5, 5]；全为 0 即哨兵串味")
    try:
        run([{"a": 0, "b": 5, "i": 1}])
    except Exception as exc:  # noqa: BLE001
        print(f"用 accept.py 的 dict 行走 -> {type(exc).__name__}: {exc}  <- 行为半边就死在这里")
    print("\n结论：静态半边看不到哨兵（在 _body 之外）；行为半边喂错布局 → 修好哨兵后此 gate 仍红。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
