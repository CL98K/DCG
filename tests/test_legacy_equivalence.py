# -*- coding: utf-8 -*-
"""v1 老用例的 v2 等价性测试。

判据（用户 m01664）：**运行结果与 v1 逐值一致**，不要求生成代码形态一致。

基准 ``examples/legacy_v1_baseline.json`` 由 v1 旧实现真跑得到（``V1/`` 存档
不入库），不是手写期望值 —— 这样测试校验的是「v2 是否等价于 v1 的实测行为」，
而不是「v2 是否符合我们对 v1 的理解」。
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

BASELINE_PATH = os.path.join(EXAMPLES, "legacy_v1_baseline.json")

with open(BASELINE_PATH, encoding="utf-8") as _f:
    BASELINE = json.load(_f)

# 案例名 → 输入数据（v1 真跑时的输入与 ``legacy_cases.DATAS`` 逐字节相同）
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
def test_handwritten_schema_matches_v1(name):
    """手写 v2 schema 的产物与 v1 真跑结果逐值相同。"""
    got = _norm(L.RUNNERS[name](DATAS[name]))
    assert got == BASELINE[name]


@pytest.mark.parametrize("name", L.MIGRATABLE)
def test_migrator_output_matches_v1(name):
    """``dcg.migrate`` 自动迁移的 schema 产物也与 v1 逐值相同。"""
    desc = dict(L.CASES_BY_NAME[name])
    ok, rep, got = L.migrated_equivalent(name, desc, DATAS[name], BASELINE[name])
    assert rep.counts()["unsupported"] == 0
    assert ok, "migrated output differs from v1 baseline: %r" % (rep.counts(),)
    assert _norm(got) == (
        BASELINE[name][0] if L._has_try_wrapper(BASELINE[name]) else BASELINE[name])


def test_case2_returns_bare_dict_not_try_wrapper():
    """v1 实测：protocol=False 多组时返回裸 dict（不套 try 二元组）。"""
    result = L.case2(DATAS["case2_protocol_false_multigroup"])
    assert isinstance(result, dict)
    assert len(result) == 11


def test_case1_returns_try_wrapper():
    """v1 实测：protocol=True 单组时返回 (buffer, try_errors) 二元组。"""
    result = L.case1(DATAS["case1_protocol_true_types_ext"])
    assert isinstance(result, tuple) and len(result) == 2
    assert result[1] == []


def test_case5_third_column_is_null_like_v1():
    """v1 的 f-string 动态路径实测取不到值，第三列是 None —— 照抄行为，不"修"它。"""
    out, errors = L.case5(DATAS["case5_protocol_true_flight_real"])
    assert errors == []
    assert out == [[14528086, "W", None]]
