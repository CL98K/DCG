# -*- coding: utf-8 -*-
"""`JSONata 语法.md` 与语料/实现的一致性。

**文档是手写正文**（可直接编辑），本文件只做「内容守卫」：

* 文档是否覆盖了全部语料文件与条数；
* 文档里的 state 计数是否与语料实际一致；
* 语料里声明为 reject / codegen-reject 的条目，引擎是否真的拒绝。

**不再有「磁盘文档 == 渲染器输出」的逐字节比对**——那会让文档的每一句话都被
`tests/gen_jsonata_manual.py` 里的硬编码文案绑死，改一句话要改 Python 字符串再重生成。
渲染器保留作文档回填工具（`python tests/gen_jsonata_manual.py`），不是唯一真相源。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS = pathlib.Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from jx_corpus import ALL, observe, parse_file, sample_files  # noqa: E402

DOC_PATH = ROOT / "JSONata 语法.md"


def _doc():
    assert DOC_PATH.is_file(), f"缺少 {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def test_corpus_rejections_behave_as_declared():
    """语料里声明为 reject / codegen-reject 的条目，执行结果必须真的被拒绝。

    文档暂不收录当前拒绝消息（后续补充），这里只校验语料预期与实现一致。
    """
    for path, meta, i, case in ALL:
        if case["expect"] not in ("reject", "codegen-reject"):
            continue
        where = f"{path.name} #{i} {case['expr']!r}"
        obs = observe(case)
        if case["expect"] == "reject":
            assert obs.kind == "unsupported", f"{where}: 应被 jx 拒绝，实际 {obs.kind}"
            assert obs.exc.loc and obs.exc.loc[1] >= 1, f"{where}: 拒绝缺列号"
        else:
            # 生成器层拒绝有两类：Unsupported（能力缺口）与 SchemaError（语义不成立，
            # 如 `$x` 变量在逐行模式下没有作用域），两者都只在 build 期抛出（契约 §7.3）。
            assert obs.kind in ("codegen-unsupported", "codegen-schema-error"), \
                f"{where}: 应被 codegen 拒绝，实际 {obs.kind}"


def test_doc_covers_whole_corpus():
    doc = _doc()
    for path in sample_files():
        meta, cases = parse_file(path)
        assert meta["file"] in doc, f"{meta['file']} 未出现在覆盖统计"
        assert meta["title"] in doc, f"{meta['title']} 未出现在覆盖统计"
    assert f"**{len(ALL)}**" in doc, "语料总数未出现在覆盖统计"


def test_doc_state_counts_are_complete():
    doc = _doc()
    counts = {"ok": 0, "gap": 0, "defect": 0, "fixed": 0}
    for _p, _m, _i, case in ALL:
        counts[case["state"]] = counts.get(case["state"], 0) + 1
    assert f"正常 {counts['ok']}" in doc
    assert f"未实现(gap) {counts['gap']}" in doc
    assert f"仍存缺陷(defect) {counts['defect']}" in doc
    assert f"已修复(fixed) {counts['fixed']}" in doc
