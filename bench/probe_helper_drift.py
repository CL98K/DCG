"""HELPERS（真实函数）vs SOURCE（字符串副本）差分对拍 —— 两处漂移就是隐藏缺陷。

背景（verifier-dev 独立审计）：
  * `dcg/runtime.py` 同时维护两套实现：`HELPERS`（:471，真实 callable）与
    `SOURCE`（:499，同名函数的**源码字符串**）。`runtime.py:5` 把 SOURCE 叫作产物的
    真相源，`runtime.py:498` 声称有差分对拍测试禁止两处漂移。
  * 但当前产物第一行是 `from dcg.runtime import *` → **跑的是 HELPERS**；
    而 Lead 的 `evidence/accept.py` 多处用 `dict(runtime.SOURCE)` 当 exec globals
    （accept.py:454/522/572）→ **跑的是 SOURCE 字符串**。
    两套只要有一处不同，验收门看到的行为就不是真实产物的行为。

用法：python -X utf8 bench/probe_helper_drift.py
"""
from __future__ import annotations

import hashlib
import inspect
import math
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg.runtime as rt  # noqa: E402

VALUES = [
    0, 1, -1, 2, 0.0, -0.0, 1.5, -2.5,
    float("nan"), float("inf"), float("-inf"),
    True, False, None, "", "a", "5", "0", "A", "ab",
    [1, 2], [0], [], {}, {"a": 1}, rt.UNDEF,
]

MAX_ARGS = 2
#: 这些名字是常量/别名，不参与对拍
SKIP = {"UNDEF", "_U", "is_u", "HELPERS", "SOURCE", "SOURCE_NEEDS_MATH",
        "SOURCE_PRELUDE", "UNDEFINED_SEMANTICS"}


def norm(v):
    if isinstance(v, float):
        if math.isnan(v):
            return ("float", "nan")
        return ("float", repr(v))
    if isinstance(v, bool):
        return ("bool", v)
    if v is rt.UNDEF:
        return ("UNDEF",)
    try:
        return (type(v).__name__, repr(v))
    except Exception:  # noqa: BLE001
        return (type(v).__name__, "<unreprable>")


def call(fn, args):
    try:
        return ("ok", norm(fn(*args)))
    except Exception as exc:  # noqa: BLE001
        return ("exc", type(exc).__name__, str(exc)[:60])


def arity_of(fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    params = list(sig.parameters.values())
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return None
    required = [p for p in params
                if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(required), len(params)


def main() -> int:
    print("=" * 78)
    print("HELPERS vs SOURCE 差分对拍")
    print(f"时间: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    rp = os.path.join(ROOT, "dcg", "runtime.py")
    with open(rp, "rb") as fh:
        print(f"dcg/runtime.py sha1 = {hashlib.sha1(fh.read()).hexdigest().upper()}")
    print(f"HELPERS={len(rt.HELPERS)}  SOURCE={len(rt.SOURCE)}  "
          f"HELPERS-SOURCE={sorted(set(rt.HELPERS) - set(rt.SOURCE)) or '无'}  "
          f"SOURCE-HELPERS={sorted(set(rt.SOURCE) - set(rt.HELPERS)) or '无'}")

    ns = {k: v for k, v in vars(rt).items() if not k.startswith("__")}
    # 产物/accept.py 侧需要的前导名：SOURCE 字符串引用 `_NN`（`_NNN` 是 :733 的可疑残留），
    # 而它们并不在 runtime 模块作用域里 —— 所以先补给，再看 HELPERS/SOURCE 的**真实**语义差。
    ns.update({"_NN": (int, float), "_NNN": (int, float), "math": math})
    for name, src in rt.SOURCE.items():
        try:
            exec(src, ns)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            print(f"  !! SOURCE[{name}] 无法 exec: {type(exc).__name__}: {exc}")

    mismatches = 0
    checked = 0
    for name, real in sorted(rt.HELPERS.items()):
        if name in SKIP or name not in rt.SOURCE:
            continue
        twin = ns.get(name)
        if twin is None or not callable(twin):
            print(f"  !! SOURCE 里没有可调用的 {name}")
            mismatches += 1
            continue
        info = arity_of(real)
        if info is None:
            print(f"  -- 跳过 {name}（变参，无法自动对拍）")
            continue
        nreq, nmax = info
        if nreq > MAX_ARGS:
            print(f"  -- 跳过 {name}（需要 {nreq} 个参数，超出对拍范围）")
            continue
        n = max(1, nreq)
        bad = []
        if n == 1:
            combos = [(v,) for v in VALUES]
        else:
            combos = [(a, b) for a in VALUES for b in VALUES]
        for args in combos:
            checked += 1
            got_real = call(real, args)
            got_twin = call(twin, args)
            if got_real != got_twin:
                bad.append((args, got_real, got_twin))
        if bad:
            mismatches += len(bad)
            print(f"  [DIFF] {name}: {len(bad)} 处不一致 / {len(combos)} 组；前 3 例:")
            for args, gr, gt in bad[:3]:
                print(f"      {name}{args!r}: HELPERS -> {gr}   SOURCE -> {gt}")
        else:
            print(f"  [ok]   {name}: {len(combos)} 组一致")
    print(f"\n结论: 对比 {checked} 组，{mismatches} 处 HELPERS/SOURCE 漂移")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
