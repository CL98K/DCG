"""`docs/examples_v1_to_v2.md` 与真实可跑测试之间的可追溯性门。

验收要求「docs 里每个样例都能定位到真实可跑的测试」。这条要求如果只靠人读保证，
迟早会漂移：文档写了一个测试名、测试被改名或删掉，没人会发现。

所以把它变成机器检查：
1. 文档里出现的**每一个** `test_xxx` 名字必须真的定义在 `tests/examples/test_v1_parity.py` 里；
2. **例1..例6 每一例**都必须被一个 `test_ex<N>_*` 引用；
3. 文档声明的对等测试数量必须和文件里的实际数量一致。

文档缺引用、或引用了不存在的测试，都直接 FAIL —— 不是 skip。
"""

from __future__ import annotations

import ast
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOC = os.path.join(ROOT, "docs", "examples_v1_to_v2.md")
PARITY = os.path.join(HERE, "test_v1_parity.py")

# 文档里还会出现**文件名**（`tests/examples/test_v1_parity.py`）和**通配写法**（`test_ex<N>_*`、
# `test_ex5_...`）。它们不是测试名，扫描前必须先把这两种形状剔掉，否则门会把文档自身的
# 文件路径报成"引用了不存在的测试"（这是本门第一次跑起来时真实发生的 FAIL）。
PATH_RE = re.compile(r"[\w./\\-]*\.py\b")
PARITY_DECLARED = 15  # 文档开头声明「15 passed, 0 skipped」，这里核对它没有脱节


def _doc_text() -> str:
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def _cited_test_names(text: str) -> set[str]:
    """只看**真的当测试名引用**的写法，剔掉文件名与 `test_ex<N>_*` / `test_xxx_...` 通配。"""
    stripped = PATH_RE.sub(" ", text)
    names = set()
    for match in re.finditer(r"\btest_[a-z0-9_]+\b", stripped):
        tail = stripped[match.end():match.end() + 3]
        if tail.startswith(("<", "._", "_*", "_.")):
            continue
        names.add(match.group(0))
    return names


def _parity_test_names() -> set[str]:
    with open(PARITY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=PARITY)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }


def test_doc_and_parity_file_exist():
    assert os.path.isfile(DOC), DOC
    assert os.path.isfile(PARITY), PARITY


def test_every_test_name_cited_in_the_doc_really_exists():
    defined = _parity_test_names()
    cited = _cited_test_names(_doc_text())
    # 硬断言：门自己不能退化成空转。文档必须真的引用全部 13 个对等测试名 ——
    # 只写「有引用就检查」是软检查，文档被改空时门照样绿。
    assert len(cited) >= PARITY_DECLARED, (
        f"文档只引用了 {len(cited)} 个测试名（应 ≥{PARITY_DECLARED}）：{sorted(cited)} —— "
        f"引用表被删空会让门退化成空转"
    )
    missing = sorted(cited - defined)
    assert not missing, f"文档引用了不存在的测试: {missing}；实际定义的是 {sorted(defined)}"


def test_each_example_has_a_cited_real_test():
    text = _doc_text()
    defined = _parity_test_names()
    cited = _cited_test_names(text)
    absent = []
    for n in range(1, 7):
        cited_here = {name for name in cited if name.startswith(f"test_ex{n}_")}
        if not cited_here:
            absent.append(f"例{n}: 文档没引用任何 test_ex{n}_* 测试")
        elif not (cited_here & defined):
            absent.append(f"例{n}: 引用 {sorted(cited_here)} 但文件里都不存在")
    assert not absent, "; ".join(absent)


def test_doc_test_count_matches_the_real_file():
    text = _doc_text()
    assert f"{PARITY_DECLARED} passed" in text, f"文档应声明 {PARITY_DECLARED} passed"
    defined = _parity_test_names()
    assert len(defined) == PARITY_DECLARED, (
        f"文档写 {PARITY_DECLARED} 个对等测试，实际 {len(defined)} 个: {sorted(defined)}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
