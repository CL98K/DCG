# -*- coding: utf-8 -*-
"""README 曾引用的那份复杂示例的回归守护（示例本体现保存在 examples/readme_complex.py）。

它曾在 README 里贴出产物与结果，改 codegen 时很容易让它悄悄漂移。这里把**不变的部分**
钉住：字段形态的语义（哪些是列、哪些是具名行、哪些是位置行）与实跑结果。
字符数只做上界检查，不作为门禁（字符预算已按用户裁决取消）。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg                                    # noqa: E402

# `examples/` 不是包（也没有 __init__.py），且环境里可能有同名包遮蔽它，
# 所以按文件路径加载，不写 `import examples.xxx`。
_spec = importlib.util.spec_from_file_location(
    "readme_complex", os.path.join(ROOT, "examples", "readme_complex.py"))
EX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EX)


def _gen():
    dcg.validate(EX.SCHEMA)
    src = dcg.generate(EX.SCHEMA)
    ns = {}
    exec(compile(src, "<readme_complex>", "exec"), ns)
    return src, ns["run"]({"index": EX.ROWS})


def test_example_runs_and_shape_is_as_documented():
    src, got = _gen()
    # 顶层：dict 形状，7 个字段各一列
    assert list(got) == ["flight_id", "route", "hours", "total", "delayed",
                         "leg", "stats", "pos"]
    assert got["flight_id"] == [101, 202, 303]
    assert got["route"] == ["URC-PEK", "KMG-SHA", "HGH-CTU"]
    assert got["total"] == [6, 6, 1]
    assert got["delayed"] == [True, True, False]
    # 不标 aggregate 的 object：一列**具名行**，不是子键列
    assert got["leg"] == [{"from": "URC", "to": "PEK", "span": 6},
                          {"from": "KMG", "to": "SHA", "span": 6},
                          {"from": "HGH", "to": "CTU", "span": 1}]
    # 标 aggregate：逐层展开成子键列；嵌套常量只留一份
    assert got["stats"] == {
        "count": 1,
        "span": [6, 6, 1],
        "deep": {"ids": [[101, 102], [202, 203], [303, 304]],
                 "routes": [["URC", "PEK"], ["KMG", "SHA"], ["HGH", "CTU"]]},
    }
    # 位置行：按声明序,optional 缺键落 None 保位
    assert got["pos"] == [[101, 6, None], [202, 6, None], [303, 1, "x"]]
    # 生成码里的结构性事实（README 正文引用它们）
    assert "_d=_d['index']" in src
    assert "_a=[r[6]for r in _o]" in src
    assert "_a2=[r['deep']for r in _a]" in src
    assert "_rl([_fid,total,_ix_get(R,'maybe')])" in src
    assert len(src) <= 1200          # 只抓成倍膨胀，不是预算


def test_example_is_deterministic():
    assert dcg.generate(EX.SCHEMA) == dcg.generate(EX.SCHEMA)
