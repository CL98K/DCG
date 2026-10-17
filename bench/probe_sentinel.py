"""哨兵（undefined 单例）对抗探针 —— verifier-dev, task-3（只读 dcg/，不修改任何实现）。

用法：python -X utf8 bench/probe_sentinel.py [> evidence/v2_sentinel_probe.txt]

回答 Lead m00727 第 1 条要我补的问题：**除 `0` 之外，还有哪些值会被产物误判为 undefined？**
本探针从三个方向取证：
  A. 产物里哨兵的**绑定形态**（是否被绑成字面量）+ 比较方式（`is` vs `==`）
  B. 合法假值（0 / 0.0 / False / None / "" / [] / {} / 自定义 __eq__）在算术与 $exists 下的行为
  C. 真 undefined（缺字段）是否仍能被表示（若缺失直接 KeyError，则哨兵形同虚设）
"""
from __future__ import annotations

import os
import re
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402


class AlwaysEq:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0

    def __repr__(self):
        return "AlwaysEq()"


def schema(fields, name="sent"):
    return {"name": name,
            "output": {"shape": "list", "single": False, "filter": False},
            "out": {"type": "object", "fields": fields}}


ADD = schema({"a": {"type": "any", "value": "$.a"},
              "b": {"type": "any", "value": "$.b"},
              "s": {"type": "any", "expr": "$.a + $.b"}})
EXI = schema({"a": {"type": "any", "value": "$.a"},
              "e": {"type": "bool", "expr": "$exists($.a)"}})


def load(sch):
    src = dcg.generate(sch)
    ns = {}
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        code = compile(src, "<probe>", "exec")
    exec(code, ns)
    return src, ns


def sentinel(ns):
    for n in ("_U", "UNDEF", "_UNDEF", "UNDEFINED"):
        if n in ns:
            return n, ns[n]
    return None, None


def call(ns, row):
    try:
        return ns["run"]([row])
    except Exception as exc:  # noqa: BLE001 - 探针要如实报告异常
        return f"{type(exc).__name__}: {exc}"


def main():
    print("=" * 78)
    print("A. 哨兵绑定与比较方式（静态）")
    print("=" * 78)
    src, ns = load(ADD)
    print(src)
    binds = re.findall(r"^\s*(_U|UNDEF)\s*=\s*(.+?)\s*$", src, re.M)
    print("绑定:", binds)
    name, s = sentinel(ns)
    print("哨兵对象:", name, "=", repr(s), "type=", type(s).__name__)
    print("is 0:", s is 0, "| is 0.0:", s is 0.0, "| is False:", s is False,
          "| is None:", s is None, "| is '':", s is "", "| is []:", s is [],
          "| bool():", bool(s))
    print("相等性比较违规 (==/!= 对哨兵):",
          re.findall(r"(_U|UNDEF)\s*[=!]=|[=!]=\s*(_U|UNDEF)", src) or "无")
    bad_literal = [f"{n} = {r}" for n, r in binds
                   if re.match(r"^(0|0\.0|-0\.0|False|True|None|''|\"\")\s*$", r)]
    print("字面量绑定违规:", bad_literal or "无")

    print()
    print("=" * 78)
    print("B. 合法假值 vs undefined —— 算术 ($.a + $.b)")
    print("=" * 78)
    cases = [(0, 5), (5, 0), (0, 0), (0.0, 5.0), (-0.0, 5.0), (256, -256),
             (False, 5), (None, 5), ("", 5), ([], 5), ({}, 5), (AlwaysEq(), 5)]
    for a, b in cases:
        out = call(ns, {"a": a, "b": b})
        note = ""
        if isinstance(out, list):
            got = out[0][2]
            if got is s:
                note = "  <-- 被判为 undefined"
            elif a == 0 and not (isinstance(a, bool)) and got != a + b:
                note = f"  <-- 值错（应 {a + b}）"
        print(f"  a={a!r:>12} b={b!r:<4} -> {out!r}{note}")

    print()
    print("=" * 78)
    print("B2. 哨兵=0 的第二处静默错值：显式 `length=0` 被当成「缺省参数」")
    print("=" * 78)
    SUB = schema({"a": {"type": "any", "value": "$.a"},
                  "sub": {"type": "any", "expr": "$substring($.a, 0, $.l)"}})
    src3, ns3 = load(SUB)
    print(src3)
    for l in (0, 2, 3):
        print(f"  $substring('hello', 0, {l}) -> {call(ns3, {'a': 'hello', 'l': l})}"
              f"   (JSONata 期望 {chr(39) + 'hello'[0:l] + chr(39)})")

    print()
    print("=" * 78)
    print("C. $exists 对合法假值 / 缺失字段")
    print("=" * 78)
    src2, ns2 = load(EXI)
    print(src2)
    for v in (0, 0.0, "", [], {}, False, None):
        print(f"  $exists($.a)  a={v!r:>8} -> {call(ns2, {'a': v})}")
    print(f"  $exists($.a)  缺字段     -> {call(ns2, {})}")
    print(f"  $.a 直接取    缺字段     -> {call(ns2, {'missing': 1})}")

    print()
    print("=" * 78)
    print("D. CONTRACT §4.4 的 miss 用例（缺失路径）与「文档化的 undefined」来源")
    print("=" * 78)
    C44 = schema({"c": {"type": "any", "expr": "'x' & $.zz"},        # §4.4-1: miss 当空串
                  "eq": {"type": "bool", "expr": "$.zz = $.zz"},     # §4.4-3: 任一侧 undef -> False
                  "sub": {"type": "any", "expr": "$substring($.a, 0)"}})  # 非字符串 -> UNDEF
    src4, ns4 = load(C44)
    print(src4)
    print("  行 {} (zz 缺失):")
    print("    'x' & $.zz        ->", call(ns4, {}))
    print("    $.zz = $.zz       ->", call(ns4, {}))
    print("  行 {'a': 123} ($substring 非字符串 → UNDEF):")
    out = call(ns4, {"a": 123})
    print("    $substring($.a,0) ->", out)
    if isinstance(out, list):
        got = out[0][2]
        print("      是哨兵?", got is sentinel(ns4)[1], "| 是 0?", got is 0)

    print()
    print("=" * 78)
    print("E. 结论")
    print("=" * 78)
    print("  见 tests/test_sentinel_integrity.py（同一断言的 pytest 形态）")


if __name__ == "__main__":
    main()
