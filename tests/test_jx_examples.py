# -*- coding: utf-8 -*-
"""`examples/jx/*.jx` 的**可执行**样例套件（语料解析与执行层见 `tests/jx_corpus.py`）。

设计（"文档与样例同源"从根上防住文档腐化）：

- `examples/jx/*.jx` 是**唯一真理源**，每个 `=== <标题>` 记录是一条例样；
- 本文件逐条**执行**每条例样（`compile_expr` -> `dcg.generate` -> `exec` -> `run`，断言真实结果）；
- 文档 `JSONata 语法.md` 是手写正文，其中的语料覆盖统计由本文件与
  `tests/test_jsonata_manual.py` 守护（注入器 `tests/gen_jsonata_manual.py` 仅作回填工具），
  `tests/test_jsonata_manual.py` 断言磁盘文档与渲染结果逐字节相等 —— 文档只能由语料生成。
- **不得 skip**：任何"不支持"都以可执行断言的形式记录（`expect: reject` 断言抛 `Unsupported`，
  `expect: codegen-reject` 断言 `generate` 抛 `Unsupported`，`state: defect` 断言当前错误行为，
  `state: fixed` 断言"曾经错误、现已修复"的正确值），所以不存在"跑不起来的样例被静默跳过"。
- **缺陷有两种生命周期**：`state: defect` 钉住**当前**错误行为（引擎修好即变红，充当复核触发器）；
  一旦修好就必须迁到 `state: fixed`（回归保护，只放 `14_fixed_regressions.jx`）并断言**正确值**，
  将来再退化就会变红。禁止把已修好的条目改回"继续断言错误行为"，禁止用 `skip`/`xfail` 盖过去。

"""
from __future__ import annotations

import ast

import pytest

from jx_corpus import ALL, _cid, observe


@pytest.mark.parametrize("item", ALL, ids=_cid)
def test_sample_runs_as_documented(item):
    path, _meta, _i, case = item
    where = f"{path.name} #{_i} [{case['title']}] {case['expr']!r}"
    expect = case["expect"]
    assert expect, f"{where}: 缺少 expect"
    assert case["expr"], f"{where}: 缺少 expr"
    assert "v" not in case["row"], f"{where}: row 不得含保留输出名 v"

    obs = observe(case)
    if expect == "reject":
        assert obs.kind == "unsupported", f"{where}: 应被 jx 拒绝，实际 {obs.kind}"
        assert obs.exc.loc is not None, f"{where}: 拒绝必须带列号"
        assert f"第 {obs.exc.loc[1]} 列" in str(obs.exc), where
    elif expect == "codegen-reject":
        # 生成器层的两类拒绝都算"编译期拒绝"：Unsupported（能力缺口）与
        # SchemaError（语义不成立，如 `$x` 变量没有作用域），见契约 §7.3。
        assert obs.kind in ("codegen-unsupported", "codegen-schema-error"), \
            f"{where}: 应被 codegen 拒绝，实际 {obs.kind}"
    elif expect == "undefined":
        assert obs.kind == "undefined", f"{where}: 应为 undefined，实际 {obs.kind} {obs.value!r}"
    elif expect.startswith("raise:"):
        _, tname, sub = expect.split(":", 2)
        assert obs.kind == "raise", f"{where}: 应抛 {tname}，实际 {obs.kind} {obs.value!r}"
        assert type(obs.exc).__name__ == tname, f"{where}: {type(obs.exc).__name__} != {tname}"
        assert sub in str(obs.exc), f"{where}: {sub!r} 不在 {obs.exc}"
    else:
        want = ast.literal_eval(expect)
        assert obs.kind == "value", f"{where}: 期望值 {want!r}，实际 {obs.kind} {obs.value!r}"
        assert obs.value == want, f"{where}: {obs.value!r} != {want!r}"
        if case["type"]:
            got = type(obs.value).__name__
            assert got == case["type"], f"{where}: 类型 {got} != {case['type']}"


def test_no_sample_is_silently_skipped():
    """每条例样都必须被 test_sample_runs_as_documented 覆盖（不依赖 pytest 的 skip 机制）。"""
    assert len(ALL) >= 100, f"样例太少（{len(ALL)}）"
    for _p, _m, _i, case in ALL:
        assert case["expect"], f"{case['expr']}: 无 expect"
        assert case["state"] in ("ok", "gap", "defect", "fixed")
    for _p, _m, _i, case in ALL:
        if case["state"] == "defect":
            assert case["note"], f"缺陷例 {case['expr']!r} 必须写 note 说明正确语义"
        if case["state"] == "fixed":
            assert _p.name == "14_fixed_regressions.jx", \
                f"已修复例 {case['expr']!r} 必须放在 14_fixed_regressions.jx"


def test_fixed_regressions_are_frozen_as_correct_values():
    """引擎修好之后，缺陷例必须转为"断言正确值"的回归保护，而不是删掉、也不是继续断言错误。

    防止两类退化：① 有人把已修好的条目删掉（回归保护消失）；② 有人把它们改回断言旧缺陷
    （那会把"修复"重新钉死成"必须坏"，等于阻止后续修复被验证）。
    """
    fixed = [c for _p, _m, _i, c in ALL if c["state"] == "fixed"]
    assert len(fixed) >= 17, f"已修复(回归保护)条目太少（{len(fixed)}），疑似把缺陷例删掉了"
    for c in fixed:
        assert not str(c["expect"]).startswith("raise:NameError"), \
            f"{c['expr']!r}: 回归保护不得断言旧缺陷（NameError）"


