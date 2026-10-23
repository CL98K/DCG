# -*- coding: utf-8 -*-
"""五个真实业务用例的等价性测试。

判据：**运行结果逐值等于业务真跑的期望值**，不要求生成代码形态一致。

基准 ``examples/baseline.json`` 是这五个用例的实测输出（冻结的期望值，
不是为 v2 手写的），测试校验「v2 产物是否复现业务实测行为」。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXAMPLES = os.path.join(ROOT, "examples")
if EXAMPLES not in sys.path:
    sys.path.insert(0, EXAMPLES)

import legacy_cases as L  # noqa: E402

BASELINE_PATH = os.path.join(EXAMPLES, "baseline.json")

with open(BASELINE_PATH, encoding="utf-8") as _f:
    BASELINE = json.load(_f)

# 案例名 → 输入数据（与真跑时的输入逐字节相同）
DATAS = L.DATAS

CASE_NAMES = tuple(L.RUNNERS)


def _norm(obj):
    """tuple → list，便于与 JSON 基准比较。"""
    if isinstance(obj, dict):
        return {k: _norm(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(v) for v in obj]
    return obj


def test_baseline_covers_every_case():
    assert set(BASELINE) == set(CASE_NAMES)


@pytest.mark.parametrize("name", CASE_NAMES)
def test_handwritten_schema_matches_business_output(name):
    """手写 v2 schema 的产物与业务实测结果逐值相同。"""
    got = _norm(L.RUNNERS[name](DATAS[name]))
    assert got == BASELINE[name]


def test_case2_returns_bare_dict_not_try_wrapper():
    """多组时返回裸 dict（不套 try 二元组）。"""
    result = L.case2(DATAS["case2"])
    assert isinstance(result, dict)
    assert len(result) == 11


def test_case1_returns_try_wrapper():
    """单组时返回 (buffer, try_errors) 二元组。"""
    result = L.case1(DATAS["case1"])
    assert isinstance(result, tuple) and len(result) == 2
    assert result[1] == []


def test_case5_third_column_is_null():
    """f-string 动态路径实测取不到值，第三列是 None —— 照抄行为，不"修"它。"""
    out, errors = L.case5(DATAS["case5"])
    assert errors == []
    assert out == [[14528086, "W", None]]
