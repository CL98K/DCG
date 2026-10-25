# -*- coding: utf-8 -*-
"""把接口文档里的**报错文案样例**钉死到真实异常上。

为什么需要
----------
`DCG 接口实现细节文档.md` §2.6 / §7.1 / §7.2 各有一段「报错形态」代码块，列出
`"{path}: {msg}"` 与 `str(Unsupported)` 的真实文本。这些文案被用户、日志、测试
大量引用，却从来没人核对过——历史上至少 8 条与实现不符（audit 报告 D-08~D-13）。

本文件的做法
------------
对每条样例写一个**最小复现**（构造会触发它的 schema / 表达式），断言
`str(exc)` 与文档里那一行**完全相等**。这样改实现文案时忘记改文档，这里立刻红。

约定
----
* 文档样例里的 `'` `"` 混排保持原样，比对用精确相等（不 strip 内部空白）。
* 只钉「稳定文案」：不钉含文件路径、内存地址、列号会随输入变化的片段。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest                                          # noqa: E402
import dcg                                             # noqa: E402
from dcg.jx import Unsupported, compile_expr           # noqa: E402


def _doc():
    with open(os.path.join(ROOT, "DCG 接口实现细节文档.md"),
              "r", encoding="utf-8") as f:
        return f.read()


def _fields(**kw):
    return {"name": "e", "output": {"shape": "list"},
            "out": {"type": "object", "fields": kw}}


# ---------------------------------------------------------------- §2.6 / §7.1
def test_schemaerror_documents_reported():
    """每条 SchemaError 样例都必须出现在文档里，且与实测逐字符相同。"""
    cases = []

    # 1 name 非法
    try:
        dcg.validate({"name": "cap-1", "output": {"shape": "list"},
                      "out": {"type": "object", "fields": {}}})
    except dcg.SchemaError as e:
        cases.append(str(e))

    # 2 value/const 互斥
    try:
        dcg.validate(_fields(sum={"type": "int64", "value": "$.a", "const": 1}))
    except dcg.SchemaError as e:
        cases.append(str(e))

    # 3 type 非法
    try:
        dcg.validate(_fields(hourD={"type": "number", "value": "$.a"}))
    except dcg.SchemaError as e:
        cases.append(str(e))

    # 4 裸名必须已声明
    try:
        dcg.generate(_fields(a={"type": "int64", "value": "$.a"},
                             z={"type": "int64", "expr": "foo"}))
    except dcg.SchemaError as e:
        cases.append(str(e))

    # 5 before 不接受布尔
    try:
        dcg.validate({"name": "x",
                      "output": {"shape": "list", "before": True},
                      "out": {"type": "object", "fields": {}}})
    except dcg.SchemaError as e:
        cases.append(str(e))

    doc = _doc()
    assert cases, "没有采到任何 SchemaError 样例"
    for text in cases:
        assert text in doc, f"文档缺少这条实测报错文案：\n  {text}"


def test_unsupported_documents_reported():
    """每条 Unsupported 样例都必须出现在文档里，且与实测逐字符相同。"""
    cases = []
    for src in ("/abc/", "`1 +`", "$eval('1')"):
        try:
            compile_expr(src)
        except Unsupported as e:
            cases.append(str(e))
        else:                                          # pragma: no cover
            raise AssertionError(f"{src!r} 应当被拒绝")

    doc = _doc()
    for text in cases:
        assert text in doc, f"文档缺少这条实测 Unsupported 文案：\n  {text}"


def test_unsupported_str_shapes_match_contract():
    """`str(e)` 三种形态（单行/多行/无 loc）必须与 §7.2 表格一致。"""
    # 单行有 loc
    e1 = Unsupported("正则字面量", (1, 3), "原因")
    assert str(e1) == "第 3 列: 正则字面量 不可逐行编译（原因）"
    # 多行
    e2 = Unsupported("正则字面量", (2, 5), "原因")
    assert str(e2) == "第 2 行第 5 列: 正则字面量 不可逐行编译（原因）"
    # 无 loc
    e3 = Unsupported("正则字面量", None, "原因")
    assert str(e3) == "正则字面量 不可逐行编译（原因）"


@pytest.mark.parametrize("attr", ["loc", "col"])
def test_unsupported_exposes_location_attributes(attr):
    e = Unsupported("x", (2, 7), "y")
    assert getattr(e, attr) == ((2, 7) if attr == "loc" else 7)
