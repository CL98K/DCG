# -*- coding: utf-8 -*-
"""`examples/flight_generated_*.py` / `.pyx` 的守护：入库产物必须与当前生成器逐字节相同。

## 为什么要专门一个文件

这 4 个文件是**文档里引用的产物样例**（README §4.2 与架构 §7.6 都指着它们说
「这就是 dcg 生成的东西」），但在本轮整改前**没有任何测试读它们**，于是它们在
codegen 换了三版产物形态之后仍是旧样子（`_o.append([...])` 直接调用、
`.pyx` 缺 `cdef object _d`），读者照着读会得到错误的印象。

之所以长期没人发现：它们既不在 `tests/` 的输入里，也不带 `# dcg: <schema>`
标记，`tests/test_doc_products.py` 的重生成比对完全够不到。

## 钉住的三件事

1. **逐字节等于重新生成的结果**（形状变体 = 同一 schema 换 `output.shape`）。
2. **`.pyx` 与 `.py` 的差值只有 cdef 块**（`tests/codegen_rules.py` 的规则 9）。
3. **它们仍然能被 import 并跑出正确结果**（防止「字节对但语义死掉」）。

本文件**不**重新定义飞行 schema —— 直接读 `examples/flight.json`，与文档同源。
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os

import pytest

import dcg
from codegen_rules import rule9_cython_same_semantics

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EX = os.path.join(_ROOT, "examples")
_SCHEMA_PATH = os.path.join(_EX, "flight.json")


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _schema() -> dict:
    with io.open(_SCHEMA_PATH, encoding="utf-8", newline="") as f:
        return json.load(f)


def _shaped(shape: str) -> dict:
    s = copy.deepcopy(_schema())
    s["output"]["shape"] = shape
    return s


_SHAPES = ("list", "dict", "index")


# -- 1. 逐字节 ---------------------------------------------------------------
@pytest.mark.parametrize("shape", _SHAPES)
def test_generated_py_matches_current_generator(shape):
    """`examples/flight_generated_<shape>.py` == `dcg.generate(同 schema, shape=X)`。"""
    path = os.path.join(_EX, "flight_generated_%s.py" % shape)
    assert os.path.exists(path), "样例产物缺失：%s" % path
    got = _read(path)
    want = dcg.generate(_shaped(shape))
    assert got == want, (
        "examples/flight_generated_%s.py 与当前生成器输出不一致；"
        "改了 codegen 就要重新生成这 4 个样例：\n--- 文件 ---\n%s\n--- 当前 ---\n%s"
        % (shape, got, want))


def test_generated_pyx_matches_current_generator():
    """`examples/flight_generated_cython.pyx` == `dcg.generate(schema, cython=True)`。"""
    path = os.path.join(_EX, "flight_generated_cython.pyx")
    assert os.path.exists(path), "样例产物缺失：%s" % path
    assert _read(path) == dcg.generate(_schema(), cython=True), (
        "examples/flight_generated_cython.pyx 与当前生成器输出不一致")


def test_pyx_differs_from_py_only_by_cdef_block():
    """规则 9：`.pyx` 剥掉 cdef 块后必须与 `.py` 逐字节相同。"""
    py = _read(os.path.join(_EX, "flight_generated_list.py"))
    pyx = _read(os.path.join(_EX, "flight_generated_cython.pyx"))
    ok, msg, bad = rule9_cython_same_semantics(py, pyx)
    assert ok, "规则 9 违规：%s（%s）" % (bad, msg)


# -- 2. 形状语义 ------------------------------------------------------------
def test_shapes_differ_only_in_return_statement():
    """三种形状的行循环必须完全相同，只有 `return` 一行不同（这是「换形状不改加工」的证据）。"""
    bodies = {}
    for shape in _SHAPES:
        src = _read(os.path.join(_EX, "flight_generated_%s.py" % shape))
        lines = src.splitlines()
        bodies[shape] = [ln for ln in lines if not ln.lstrip().startswith("return")]
    assert bodies["list"] == bodies["dict"] == bodies["index"], (
        "三种形状的加工逻辑应当完全一致，只有 return 形态不同")


# -- 3. 可执行 --------------------------------------------------------------
def _load_module(path: str):
    spec = importlib.util.spec_from_file_location("_fl_example", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _rows():
    with io.open(os.path.join(_EX, "flight_rows.json"), encoding="utf-8",
                 newline="") as f:
        return json.load(f)


def test_generated_py_is_runnable():
    """样例产物能 import 并跑出 9 列（用与文档同源的 `examples/flight_rows.json`）。"""
    mod = _load_module(os.path.join(_EX, "flight_generated_list.py"))
    rows = _rows()
    out = mod.run(rows)
    assert isinstance(out, list)
    assert len(out) == len(rows["index"])
    assert all(len(r) == 9 for r in out), "每行应有 9 列"


def test_generated_dict_shape_returns_columns():
    """`dict` 形状：返回 9 个具名列，每列长度等于行数（列式直出）。"""
    mod = _load_module(os.path.join(_EX, "flight_generated_dict.py"))
    out = mod.run(_rows())
    assert set(out) == {"hour", "hourA", "hourB", "hourC", "hourD",
                        "hourE", "hourF", "hourG", "hourH"}
    n = len(_rows()["index"])
    assert all(len(v) == n for v in out.values())


def test_generated_index_shape_returns_keyed_rows():
    """`index` 形状：返回 `{0: row0, 1: row1, ...}`，行内容与 list 形状一致。"""
    mod_idx = _load_module(os.path.join(_EX, "flight_generated_index.py"))
    mod_lst = _load_module(os.path.join(_EX, "flight_generated_list.py"))
    rows = _rows()
    got = mod_idx.run(rows)
    assert list(got) == list(range(len(rows["index"])))
    assert list(got.values()) == mod_lst.run(rows)
