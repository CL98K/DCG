"""例2（`format.py:626-697`）的**异构文档输出**：v2 组装结果必须与 v1 冻结产物逐值相等。

v1 的 `struct` 可以是一篇多段文档（行式列表 + 逐行标量 + 顶层常量 + 嵌套子文档），
v2 的一个 schema 只产出一个行列表，所以这一例的迁移方式是「多个 v2 产物 + 一层
组装函数」。`bench/compose_v1_document.py` 是那层组装的可执行样板，本测试把它锁进
门禁：任何一段产物漂了都会红。

v1 侧不重跑，读 `evidence/examples/v1_examples_frozen.json` 的 chunk 2
（`cython_off_trye_off` 变体 = 关掉 CYTHON 缺陷与 TRYE 二元组后的裸 dict）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

import compose_v1_document as C  # noqa: E402

FROZEN = os.path.join(ROOT, "evidence", "examples", "v1_examples_frozen.json")


def test_frozen_evidence_is_present():
    assert os.path.exists(FROZEN), "v1 冻结证据缺失：%s" % FROZEN


def test_ex2_whole_document_matches_v1():
    expected, datas = C.load_v1()
    actual = C.assemble(datas)
    assert C.diff(expected, actual) is None


def test_ex2_covers_every_top_level_section():
    """组装的段落集合必须覆盖 v1 文档的全部顶层键，不能悄悄少写一段。"""
    expected, datas = C.load_v1()
    actual = C.assemble(datas)
    assert set(actual) == set(expected), (sorted(actual), sorted(expected))


def test_ex2_nested_sections_are_present():
    """嵌套子文档（docsx.B.B3.C.D.E）也要真的组装出来，而不是被当成常量吞掉。"""
    _expected, datas = C.load_v1()
    doc = C.assemble(datas)
    e = doc["docsx"]["B"]["B3"]["C"]["D"]["E"]
    assert e == [[100]] * 10, e
    assert doc["docsx"]["B"]["B1"] == 100
    assert doc["docsx"]["A"] == 100


def test_ex2_row_sections_have_the_v1_row_count():
    _expected, datas = C.load_v1()
    doc = C.assemble(datas)
    for path, rows in (("docs", doc["docs"]),
                       ("docsx.docs", doc["docsx"]["docs"]),
                       ("docsx.B.B2", doc["docsx"]["B"]["B2"]),
                       ("xdocs", doc["xdocs"]),
                       ("ext1", doc["ext1"]),
                       ("ext.keys", doc["ext"]["keys"])):
        assert isinstance(rows, list) and len(rows) == 10, (path, rows)


def test_ex2_flat_sections_are_flat_lists_not_rows():
    """v1 单字段段落产出的是标量列表；组装时必须展平，不能留成 `[[x], ...]`。"""
    _expected, datas = C.load_v1()
    doc = C.assemble(datas)
    for path, values in (("docids", doc["docids"]),
                         ("docsx.docids", doc["docsx"]["docids"]),
                         ("lasttime", doc["lasttime"]),
                         ("count", doc["count"]),
                         ("ext.docids", doc["ext"]["docids"])):
        assert len(values) == 10, path
        assert not any(isinstance(v, list) for v in values), (path, values)


def test_ex2_constant_sections_are_literals():
    _expected, datas = C.load_v1()
    doc = C.assemble(datas)
    assert doc["format"] == [1, 2, 3]
    assert doc["ext"]["index"] == 1
    assert doc["ext"]["list"] == [3]
    assert doc["extx"] == [1]
