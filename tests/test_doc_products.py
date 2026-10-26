# -*- coding: utf-8 -*-
"""把文档里的**产物代码块**钉死到真实 `dcg.generate()` 输出上。

为什么需要
----------
`tests/test_docs_consistency.py` 只钉数字，`tests/test_jsonata_manual.py` 只保证
「`JSONata 语法.md` == 渲染器输出」——两者都拦不住「文档里手写的产物样例过期」。
历史上出过多次：接口文档 §4.5 的 CAP/M0 样例声称 `expr` 不复用 `_a1`/`_a2`
（实测复用）、架构文档同款样例、README §4.1 的 before 钩子样例（`D['rows']`
后来改成 `_ix_get`）。这类漂移是**文字与代码对不上**，但谁也没跑过文档里的代码。

本文件的做法
------------
把文档按 ```python 围栏切块，对每一块带方言标记的样例：
* `` ```python  `` 后紧跟 `# dcg: <schema 名>` 注释行 —— 用 `tests/workload.py`
  里同名 schema 重新 `generate()`，与代码块正文逐字符比对。
这样文档里就不可能再贴过期的产物：改 codegen 而忘了改文档，这里立刻红。

为什么不扫全部 python 块
------------------------
文档里还有「手写等价实现」「反例」「报错演示」等代码块，它们**本来**就不该等于
`generate()` 输出。只用显式标记选择要守护的块，避免误报。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import pytest                                          # noqa: E402
import dcg                                             # noqa: E402
import workload as W                                   # noqa: E402

DOCS = (
    "README.md",
    "DCG 接口实现细节文档.md",
    "DCG 整体设计方案与架构文档.md",
)

_MARK = re.compile(r"^#\s*dcg:\s*(\w+)\s*$")
_FENCE = re.compile(r"^```python\s*$")


def _read(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as f:
        return f.read().splitlines()


def _product_blocks(doc_name):
    """取出所有带 `# dcg: <schema>` 标记的产物块 -> [(schema名, 正文, 行号)]。"""
    lines = _read(doc_name)
    out = []
    i = 0
    while i < len(lines):
        if _FENCE.match(lines[i]):
            start = i + 1
            j = start
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            body = lines[start:j]
            if body:
                m = _MARK.match(body[0].strip())
                if m:
                    text = "\n".join(body[1:]).strip("\n")
                    out.append((m.group(1), text, start + 1))
            i = j + 1
        else:
            i += 1
    return out


def _all_blocks():
    cases = []
    for doc in DOCS:
        for label, text, line in _product_blocks(doc):
            cases.append(pytest.param(doc, label, text, line,
                                      id=f"{doc}:{line}:{label}"))
    return cases


BLOCKS = _all_blocks()


def test_docs_have_at_least_one_guarded_product():
    """守卫不能退化成空循环：至少要有 3 个被钉住的产物样例。"""
    assert len(BLOCKS) >= 3, f"文档里的产物样例太少（{len(BLOCKS)} 个），守卫形同虚设"


#: 需要非默认 `generate()` 参数的标记 -> 生成函数。
#: 目前只有一个：`inline_optional=False` 的产物形态（README §A.9）。
_ALT_GEN = {
    "DOC_INLINE_OPT_OFF": W.inline_optional_off_src,
}


@pytest.mark.parametrize("doc,label,text,line", BLOCKS)
def test_documented_product_matches_generate(doc, label, text, line):
    """文档里的产物必须逐字符等于 `dcg.generate(<workload 里的同名 schema>)`。"""
    if label in _ALT_GEN:
        actual = _ALT_GEN[label]().strip("\n")
    else:
        schema = getattr(W, label, None)
        assert schema is not None, f"{doc}:{line} 引用了不存在的 workload schema {label!r}"
        actual = dcg.generate(schema).strip("\n")
    documented = text.strip("\n")
    if actual == documented:
        return
    # 给出逐行定位，避免让人对着两大段代码肉眼找差异
    a = actual.splitlines()
    d = documented.splitlines()
    for k in range(max(len(a), len(d))):
        la = a[k] if k < len(a) else "<缺失>"
        ld = d[k] if k < len(d) else "<缺失>"
        if la != ld:
            raise AssertionError(
                f"{doc}:{line} 的产物样例过期（第 {k + 1} 行起不同）\n"
                f"  文档: {ld!r}\n  实测: {la!r}")
    raise AssertionError(f"{doc}:{line} 的产物样例过期（尾部差异）")

# ------------------------------------------------------------ 产物字符数
_CHAR_CLAIM = re.compile(
    r"产物（(\d+) 行 / (\d+) 字符；口径 = 各行以换行符连接，不含末尾换行）：")
_ROWS_CLAIM = re.compile(
    r"的完整产物（(\d+) 行 / (\d+) 字符；")


def _text_blocks(doc_name):
    """取出 ```text 围栏块 -> [(正文, 起始行号)]。

    正文取「行以换行符连接」的口径（与文档里声明的口径一致，不含末尾换行）。
    """
    lines = _read(doc_name)
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "```text":
            start = i + 1
            j = start
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append(("\n".join(lines[start:j]), start + 1))
            i = j + 1
        else:
            i += 1
    return out


def test_readme_line_and_char_claims_are_true():
    """README 里「N 行 / M 字符」的自述必须与其后代码块的真实尺寸一致。

    历史上这两处（§1.1 与 §4.3）长期各差 1~23 个字符——因为没人真的数过。
    现在数：行数 = 块内行数，字符数 = 各行以换行符连接的长度（不含末尾换行）。
    """
    lines = _read("README.md")
    checked = 0
    for idx, line in enumerate(lines):
        m = _CHAR_CLAIM.search(line) or _ROWS_CLAIM.search(line)
        if not m:
            continue
        want_lines, want_chars = int(m.group(1)), int(m.group(2))
        # 紧邻的下一个 ``` 围栏块（中间允许空行）
        k = idx + 1
        while k < len(lines) and not lines[k].startswith("```"):
            k += 1
        assert k < len(lines), f"README:{idx + 1} 声明后没有代码块"
        start = k + 1
        end = start
        while end < len(lines) and not lines[end].startswith("```"):
            end += 1
        block = lines[start:end]
        # 有些代码块在产物之后还附了「运行结果」演示（空行分隔）——
        # 声明里的行数/字符数只针对产物本身，所以只量到第一个空行为止。
        cut = block.index("") if "" in block else len(block)
        body_lines = block[:cut]
        body = "\n".join(body_lines)
        assert len(body_lines) == want_lines, (
            f"README:{idx + 1} 声明 {want_lines} 行，实测 {len(body_lines)} 行")
        assert len(body) == want_chars, (
            f"README:{idx + 1} 声明 {want_chars} 字符，实测 {len(body)} 字符")
        checked += 1
    # README 只保留 §4.4 rows 一处「N 行 / M 字符」自述：原 §1.1 的复杂示例整节
    # 已按用户裁决删除（README 只讲怎么用，不复述原理与优化过程），另一处自述随之消失。
    # 这里只要求「声明的数字确实被数过」，不再要求条数。
    assert checked >= 1, f"只校验到 {checked} 处字符数自述，守卫退化"


# ------------------------------------------------- README 附录 B 已按裁决删除
def test_readme_has_no_deleted_appendix_b():
    """README 附录 B（手写实现 vs DCG 生成代码）已按用户裁决整节删除。

    那一节是文档里唯一「完整复杂负载」的产物样例（schema + 生成码 + 手写实现 +
    性能比值），属实现细节与性能报告，不该出现在面向使用者的手册里：性能归
    `验收报告.md` §5，设计取舍归架构文档。README 只讲用户看得见的东西。
    这里做回归守护：不许再把这一节长回来。
    """
    text = "\n".join(_read("README.md")).replace("\r\n", "\n")
    for marker in ("## 附录 B.", "### B.2 ", "### B.3 ", "### B.7 ", "### B.8 "):
        assert marker not in text, (
            "README 里又出现了已删除的附录 B 标记 %r；性能与实现细节请写进 "
            "`验收报告.md` §5 或架构文档，不要放回用户手册" % marker)
