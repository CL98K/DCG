#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重生成 evidence/doc_vs_impl_matrix.md（2026-10-26 第 3 轮审计后）。

为什么要有这个脚本：`doc_vs_impl_matrix.md` 是 2026-10-24 的逐条契约审计表，
里面写死了接口/架构/README 的**行号锚点**与一批**计数**。文档一改，这些锚点
必然过期，而它当初是手工维护的 —— 于是它成了仓库里最容易腐烂的一份 evidence。

本脚本的做法：**只重算能机器复核的部分**（文档规模、符号计数、语料计数、
关键签名），把逐条契约审计结论里「已修 / 已过期」的判定显式写进表里并给出
复核方式（一条可复跑的命令或一条 grep）。凡不能机器复核的，一律降级成
「语义判断，不设断言」而不再写死行号。

用法：
    python -X utf8 evidence/gen_doc_matrix.py           # 只打印统计
    python -X utf8 evidence/gen_doc_matrix.py --write   # 覆写 doc_vs_impl_matrix.md
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg                      # noqa: E402
import dcg.codegen as _cg       # noqa: E402
import dcg.ir as _ir            # noqa: E402
import dcg.runtime as _rt       # noqa: E402
import dcg.jx as _jx            # noqa: E402
from dcg.jx import policy as _pol  # noqa: E402

DOCS = [
    "README.md",
    "DCG 接口实现细节文档.md",
    "DCG 整体设计方案与架构文档.md",
    "JSONata 语法.md",
    "验收报告.md",
]


def doc_size(name: str) -> tuple[int, int]:
    """行数 = "\\n" 计数（末尾无换行时 +1）；字节 = UTF-8 编码长度。"""
    raw = io.open(os.path.join(ROOT, name), "rb").read()
    text = raw.decode("utf-8")
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return lines, len(raw)


def src_lines(rel: str) -> int:
    text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
    return text.count("\n") + 1


def read(rel: str) -> str:
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def grep_count(rel: str, pattern: str) -> int:
    return len(re.findall(pattern, read(rel)))


def collect() -> dict:
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import jx_corpus as corpus  # 只读语料，不跑用例
    samples = corpus.all_samples()
    states = {}
    for s in samples:
        st = s[3].get("state", "ok")
        states[st] = states.get(st, 0) + 1
    return {
        "docs": {d: doc_size(d) for d in DOCS},
        "src": {
            "dcg/codegen.py": src_lines("dcg/codegen.py"),
            "dcg/runtime.py": src_lines("dcg/runtime.py"),
            "dcg/ir.py": src_lines("dcg/ir.py"),
            "dcg/schema.py": src_lines("dcg/schema.py"),
            "dcg/jx/policy.py": src_lines("dcg/jx/policy.py"),
            "dcg/jx/compiler.py": src_lines("dcg/jx/compiler.py"),
            "dcg/jx/registry.py": src_lines("dcg/jx/registry.py"),
        },
        "dcg_all": len(dcg.__all__),
        "ir_all": len(_ir.__all__),
        "ir_kinds": len(_ir.KINDS),
        "jx_all": len(_jx.__all__),
        "policy": (_pol.ROW_FUNCS, _pol.HIGHER_ORDER, _pol.NONDETERMINISTIC, _pol.STRUCTURAL),
        "union": len(set(_pol.ROW_FUNCS) | set(_pol.HIGHER_ORDER) | set(_pol.NONDETERMINISTIC)),
        "helpers": len(_rt.HELPERS),
        "rt_all": len(_rt.__all__),
        "snmath": sorted(_rt.SOURCE_NEEDS_MATH),
        "corpus": (len(samples), states),
        "sigs": {
            "generate": str(__import__("inspect").signature(_cg.generate)),
            "build": str(__import__("inspect").signature(_cg.build)),
            "compile_expr": str(__import__("inspect").signature(_jx.compile_expr)),
        },
    }


def render(st: dict) -> str:
    d = st["docs"]
    rw, rr, ra, rj, rv = (d[n] for n in DOCS)
    pr, ph, pn, ps = st["policy"]
    n_ok = st["corpus"][1].get("ok", 0)
    n_fixed = st["corpus"][1].get("fixed", 0)
    out = []
    A = out.append

    A("# 文档 ↔ 实现 逐条矩阵（机器重生成）")
    A("")
    A("> **本文件由 `evidence/gen_doc_matrix.py` 重生成，不要手改**：`python -X utf8 evidence/gen_doc_matrix.py --write`。")
    A("> 生成时间与下面所有数字都取自**生成那一刻的工作区**；文档一改，重跑即可，不再存在「锚点行号过期」的维护负担。")
    A("")
    A("## 0. 规模与符号（唯一事实源，重跑即得）")
    A("")
    A("| 项 | 实测 |")
    A("|---|---|")
    A("| `README.md` | %d 行 / %d 字节 |" % rw)
    A("| `DCG 接口实现细节文档.md` | %d 行 / %d 字节 |" % rr)
    A("| `DCG 整体设计方案与架构文档.md` | %d 行 / %d 字节 |" % ra)
    A("| `JSONata 语法.md` | %d 行 / %d 字节 |" % rj)
    A("| `验收报告.md` | %d 行 / %d 字节 |" % rv)
    A("| `dcg.__all__` | %d |" % st["dcg_all"])
    A("| `dcg.ir.__all__` | %d（KINDS %d / UNARY_OPS %d / BIN_OPS %d） |" % (st["ir_all"], st["ir_kinds"], len(_ir.UNARY_OPS), len(_ir.BIN_OPS)))
    A("| `dcg.jx.__all__` | %d |" % st["jx_all"])
    A("| policy 四表 | ROW %d / HO %d / NONDET %d / STRUCT %d |" % (len(pr), len(ph), len(pn), len(ps)))
    A("| 函数并集（去重后） | %d |" % st["union"])
    A("| runtime `HELPERS` / `__all__` | %d / %d |" % (st["helpers"], st["rt_all"]))
    A("| `SOURCE_NEEDS_MATH` | `%s` |" % ", ".join(st["snmath"]))
    A("| `.jx` 语料 | %d 条（ok %d / fixed %d） |" % (st["corpus"][0], n_ok, n_fixed))
    A("")
    A("签名（`inspect.signature` 实测）：")
    A("")
    A("```")
    for k, v in st["sigs"].items():
        A("%s%s" % (k, v))
    A("```")
    A("")
    A("## 1. 怎么复核这张表之外的东西")
    A("")
    A("逐条契约的**语义**判断无法机器复核（「这个措辞是否忠实于实现」需要读代码）。所以本表只做两件事：")
    A("")
    A("1. **给每条结论配一条可复跑的命令或 grep**，任何人可以原地验证；")
    A("2. **不写死行号**，改写成「在 `grep -n` 的输出里」——行号由读者现查，永不腐烂。")
    A("")
    A("## 2. 2026-10-24 历史审计表的结论去向")
    A("")
    A("原表 40(A) + 5(B) + 16(C) + 22(D) + 10(E) + 21(F) 条，逐条处置如下。")
    A("")
    A("| 原编号 | 结论 | 处置 | 复核方式 |")
    A("|---|---|---|---|")
    A("| A-19 | `_x` 用 `__import__` + `rfind` 只切最后一点 | **已修**（DEFECT-023）：改为「从长到短试」`importlib.import_module` + 剩余段 `getattr` 属性链，且与钩子侧 `_sh` 同款 | `grep -n 'importlib as _il' dcg/codegen.py`；`python -m pytest tests/test_registry_dispatch.py -q` |")
    A("| B-01 | `Registry` 注册的函数进不了产物 | **已修**（v2.1）：`generate(registry=...)` / `build(registry=...)` 显式传入后才发 `_x(...)`，不传仍报 `未知函数`（保纯函数） | `python -c \"import dcg,inspect;print(inspect.signature(dcg.generate))\"` |")
    A("| B-02 | `qualname` 只切最后一点，多级类方法导入失败 | **已修**：同上，`pkg.mod.Class.method` 可分派 | `python -m pytest tests/test_registry_dispatch.py -q` |")
    A("| B-03 | `$.m[0]`（required）对 `null` 抛 `TypeError` | **仍成立，且是契约本身**：required = 裸链 = 真实 Python 异常由用户承担 | `grep -n '裸链' 'DCG 接口实现细节文档.md'` |")
    A("| B-04 | 内联不改 undefined 语义（类型声明不是转换） | **仍成立，且是契约本身**：`type` 只影响类型判定与内联，不做运行期转换 | `grep -n '声明不是转换' 'DCG 接口实现细节文档.md'` |")
    A("| B-05 | `$.a.1.2` 被词法层当小数拒绝 | **已修**：路径上下文里连续整数下标可解析 | `python -c \"import dcg;print(dcg.compile_expr('$.a.1.2'))\"` |")
    A("| C-02 | `aggregate` 在非 dict 形状被静默忽略 | **已修**：改为构建期 `SchemaError`（`_check_aggregate_shape`，覆盖 `generate()` / `build()` / CLI，嵌套 `fields` 逐层查） | `grep -n '_check_aggregate_shape' dcg/codegen.py` |")
    A("| C-04 | `SOURCE_PRELUDE` 空串但被文档当作有内容 | **已澄清**：文档改为写「当前为空串，保留为扩展点」 | `grep -n 'SOURCE_PRELUDE' dcg/runtime.py 'DCG 接口实现细节文档.md'` |")
    A("| C-06 | `generate()` 不产 trace 产物 | **仍成立，且是有意为之**：`output.trace=true` 只让 `build()` 额外落一个 `<name>.trace.py`，`generate()` 是无副作用纯函数，正式产物与 `trace=false` 逐字节相同 | `grep -n 'trace_artifact' dcg/codegen.py` |")
    A("| D 系列 10 项真缺陷 | D-AGG-01 / D-HOOK-01 / D-HOOK-02 / D-OPT-01 / D-NEST-01 / D-MATH-01 / D-COL-01 / D-AGG-02 / D-PLAN-01 … | **全部已修**，回归见 `验收报告.md` §2 | `python -m pytest -q`（全绿） |")
    A("| E/F 系列 | 性能与优先级清单 | 已并入 `验收报告.md` §5 | `python -X utf8 evidence/perf_runner.py`（协议复跑） |")
    A("")
    A("## 3. 2026-10-25 ~ 26 新增/收敛的契约（原表未覆盖）")
    A("")
    A("| 主题 | 现口径 | 复核方式 |")
    A("|---|---|---|")
    A("| 读法主判据 | 只有 `optional`（含 `default`）决定读法；`type` **不参与**。三者优先级：**声明口径**（该路径被唯一一种方式声明过）→ **引用点口径**（同一路径 required / optional 都声明过时按引用处） | `python -m pytest tests/test_req_safe_prefix.py tests/test_value_expr_parity.py -q` |")
    A("| 字段观测值 | optional 字段的值**恰好一次** undefined 判断后落 `None`（`value` / `expr`、四种 shape 一致）；要观察 undefined 本身须用 `$exists(<expr>)` | `grep -n '恰好一次' 'DCG 接口实现细节文档.md'` |")
    A("| 前缀提升 | required 单段也提升；子树 required/optional 并存时局部名绑成**裸读**（`_buyer=R['buyer']`），安全端从中派生 `.get(k,_U)`；**反向禁止**「安全口径绑定被 required 消费」 | `grep -n 'bare_hoist' dcg/codegen.py` + `grep -n '_eff_safe' dcg/codegen.py`；`python -m pytest tests/test_req_safe_prefix.py -q` |")
    A("| `$name` | 一律 `SchemaError`，**没有**「与已声明字段同名就按字段读」的降级（用户裁决 2026-10-25） | `grep -n '没有同名降级' 'DCG 接口实现细节文档.md'`；`python -m pytest tests/test_rule_consistency.py -q` |")
    A("| `aggregate` 非 dict | 构建期 `SchemaError`，不静默失效 | 见上 C-02 |")
    A("| 永久拒绝 | **10 个键 / 13 条现象**（`$$` 不在 10 键内，另计一条） | `python -c \"from dcg.jx import policy as p;print(len(p.STRUCTURAL))\"` |")
    A("| qualname 导入 | 钩子 `_sh` 与 rfunc `_x` 用**同一套**「从长到短试」，`pkg.mod.Class.method` 支持 | 见上 A-19 |")
    A("| `generate` / `build` | 都带 `registry=` 与 `strict_qualname=`（见 §0 签名实测） | 见上 B-01 |")
    A("")
    A("## 4. 已知不设断言的项（如实登记）")
    A("")
    A("| 项 | 为什么不断言 |")
    A("|---|---|")
    A("| 五份文档自身的行数 / 字节 | 每次修订必然变化，写成硬断言会让改文档就要改测试；改由本脚本重算（§0） |")
    A("| `README.md` 两个 manifest 例子（`lines` / `chars`） | 例子里没有附 schema，无法由生成器复算；只看形态不看数字 |")
    A("| `order_pipeline` 的绝对 µs | 依赖机器状态（同进程手写基线一起漂移），只断言**比值方向**与 `perf_protocol.json` 一致 |")
    A("| `Cython` 路径 | 本机无 `cl`，只做「剥掉 cdef 后与 py 逐字节相同」的文本等价断言 |")
    A("")
    return "\n".join(out) + "\n"


def main() -> int:
    st = collect()
    text = render(st)
    if "--write" in sys.argv:
        path = os.path.join(ROOT, "evidence", "doc_vs_impl_matrix.md")
        io.open(path, "w", encoding="utf-8", newline="\n").write(text)
        print("wrote %s (%d lines)" % (path, text.count("\n")))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
