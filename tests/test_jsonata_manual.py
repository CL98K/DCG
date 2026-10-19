# -*- coding: utf-8 -*-
"""`JSONata 语法.md` 与生成器/语料的一致性。

文档由 `tests/gen_jsonata_manual.py` 渲染：分类示例逐条真实执行，§12.7/§13/§14 来自
`examples/jx/*.jx` 语料。本文件断言磁盘文档与渲染结果逐字节相等，并抽查关键内容不缺失。
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

import gen_jsonata_manual as gen  # noqa: E402
from jx_corpus import ALL, observe, parse_file, sample_files  # noqa: E402

DOC_PATH = ROOT / "JSONata 语法.md"


def _doc():
    assert DOC_PATH.is_file(), f"缺少 {DOC_PATH}，先跑 python tests/gen_jsonata_manual.py"
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_is_byte_identical_to_renderer():
    on_disk = _doc()
    rendered = gen.render()
    if on_disk != rendered:
        i = next((k for k in range(min(len(on_disk), len(rendered))) if on_disk[k] != rendered[k]),
                 min(len(on_disk), len(rendered)))
        pytest.fail(f"JSONata 语法.md 与渲染结果不一致（首个差异在第 {i} 字符）：\n"
                    f"磁盘: {on_disk[i - 60:i + 60]!r}\n渲染: {rendered[i - 60:i + 60]!r}\n"
                    f"重新生成：python tests/gen_jsonata_manual.py")


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
            assert obs.kind == "codegen-unsupported", f"{where}: 应被 codegen 拒绝，实际 {obs.kind}"


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
