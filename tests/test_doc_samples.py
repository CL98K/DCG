# -*- coding: utf-8 -*-
"""`JSONata 语法.md` 「逐算子使用样例」的覆盖守报。

曾经的状态：文档的能力表把 64 个算子**名字**都列了，但其中 14 个在正文里
只有名字、没有任何 `$fn(...)` 调用形态，`examples/jx/` 里也缺 13 个 ——
「文档列了名」被当成了「文档有样例」。本文件把这件事变成可执行契约：

* 表里出现的算子集合，必须**恰好等于** `dcg.codegen` 里真实注册的
  行内/高阶算子集合（多一个说明表里写了不存在的算子，少一个说明又有算子掉队）；
* 每一行都必须带 `$fn(` 形态的调用（不是裸名字）；
* 每一行的「实测结果」必须能被这台机器上的引擎复现 —— 直接照表里的
  `expr` + `输入行` 重跑一遍，比对结果。**结果随行的样例不许写死随机值**。

第三条是重点：它让这张表不是「文档」，而是「可执行的断言集」。改了 `runtime`
却忘了改文档，这里会红。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (str(ROOT), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ast  # noqa: E402

import dcg.codegen as codegen  # noqa: E402
from jx_corpus import observe  # noqa: E402

DOC_PATH = ROOT / "JSONata 语法.md"

# 语料里用「确定性断言」覆盖的非确定函数：它们的值不可复现，只断言类型/范围，
# 因而**不放进**本表的逐值复现校验，但仍必须在 里出现（有样例形态）。
NONDET = {"$now", "$millis", "$random", "$shuffle"}


def _registered() -> set:
    """`dcg.codegen` 里真实注册的全部算子名（A 类 + B 类高阶）。"""
    names = set(codegen._FN) | set(codegen._HIGHER)
    return names


def _rows():
    """解析 表格的数据行 -> [(函数, expr, row_literal, 结果单元格)]。"""
    text = DOC_PATH.read_text(encoding="utf-8")
    start = text.find("### 15.5 ")
    assert start >= 0, "`JSONata 语法.md` 里找不到 §15.5 逐算子使用样例"
    end = text.find("\n## ", start)
    body = text[start:end if end > 0 else len(text)]
    out = []
    for line in body.splitlines():
        m = re.match(
            r"\|\s*`(\$[A-Za-z0-9]+)`\s*\|\s*`([^`]+)`\s*\|\s*(`[^`]+`|—)\s*\|\s*`?([^|`]+)`?\s*\|",
            line.strip(),
        )
        if m:
            row_cell = m.group(3).strip().strip("`")
            out.append((m.group(1), m.group(2), row_cell, m.group(4).strip()))
    return out


ROWS = _rows()


def test_section_15_5_exists_and_is_not_empty():
    assert len(ROWS) >= 60, "§15.5 样例表太少（%d），疑似被删" % len(ROWS)


def test_every_registered_operator_has_a_sample():
    """每个注册过的算子都必须在 里有一条带调用形态的样例。"""
    have = {r[0] for r in ROWS}
    missing = sorted(_registered() - have)
    assert not missing, (
        "这些算子在 §15.5 里没有使用样例（只有名字不算样例）：%s\n"
        "补一行 `| `$fn` | `$fn(...)` | 输入行 | 实测结果 | 说明 |` 到 "
        "`JSONata 语法.md` §15.5，并在 examples/jx/ 里补一条可执行断言"
        % missing)


def test_no_sample_for_unregistered_operator():
    """表里不许出现没注册的算子（防止把已删算子留在文档里）。"""
    extra = sorted({r[0] for r in ROWS} - _registered())
    assert not extra, "§15.5 里列了没有注册的算子：%s" % extra


def test_every_row_uses_call_syntax():
    """每行的「样例写法」必须是真正的调用形态 `$fn(`，不能只写名字。"""
    bad = [r for r in ROWS if ("$" not in r[1] or "(" not in r[1])]
    assert not bad, "§15.5 里有不是调用形态的样例：%s" % [r[0] for r in bad]


@pytest.mark.parametrize("fn,expr,row_lit,got", ROWS, ids=[r[0] for r in ROWS])
def test_sample_result_matches_engine(fn, expr, row_lit, got):
    """表里的「实测结果」必须能被当前引擎逐值复现。"""
    if fn in NONDET:
        pytest.skip("%s 是非确定函数，语料里用确定性断言覆盖" % fn)
    row = {} if row_lit == "—" else ast.literal_eval(row_lit)
    obs = observe({"expr": expr, "row": row, "expect": None})
    assert obs.kind in ("value", "undefined"), (
        "%s 的样例 `%s` 在本机报 %s：%s" % (fn, expr, obs.kind,
                                          getattr(obs, "exc", "")))
    if got == "undefined":
        assert obs.kind == "undefined", (
            "%s 样例写成 undefined，实测是 %r" % (fn, obs.value))
        return
    want = ast.literal_eval(got)
    assert obs.kind == "value" and obs.value == want, (
        "%s 的样例 `%s` 实测 %r，文档写的是 %r —— 文档与实现漂移了"
        % (fn, expr, getattr(obs, "value", None), want))
