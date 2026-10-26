"""文档结构完整性守卫（防止编辑事故造成的物理损坏）。

背景：历史上发生过一次真实事故——批量替换误吞了 `DCG 接口实现细节文档.md` §3.9 的
开头三行，把标题 `### 3.9 反引号段与 py{} 块` 覆盖成了别的标题，正文里留下一句悬空的
`**反引号段**（expr 里的 ``...``）：` 和一段孤立的报错文案。**当时 1251 条测试全绿**，
没有任何测试守护「文档的物理结构」这一层。

本文件只做结构断言，不逐字比内容（内容由 test_doc_* 其余文件守护）。三类断言：

1. **标题下有正文**：标题不能被吞掉正文后紧跟另一个标题（事故的直接形态）；
2. **编号连续、无重号**：同一父编号下子标题必须 1..N 连续（事故的另一形态：
   3.8 之后直接 3.10，或两个 3.9）；
3. **标题形态正常**：反引号成对、不以纯标点结尾（防止正文碎片被提到标题位）。

这些断言的共同点是**正常修订不会触发**：只要标题下有内容、编号连续，改文案/加内容
都不会红；而「吞掉几行」「错位粘贴」这类事故一定会红。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "DCG 接口实现细节文档.md",
    "DCG 整体设计方案与架构文档.md",
    "JSONata 语法.md",
    "验收报告.md",
]

_HEADING = re.compile(r"^(#{2,4})\s+(.*)$")
# 「3.8 xxx」「A.8 xxx」「附录 B.7 xxx」里的数字编号
_NUMBERED = re.compile(r"^(?:附录\s*)?([0-9A-Za-z]+(?:\.[0-9]+)*)\.?\s")


def _read(doc: str) -> str:
    return (ROOT / doc).read_text(encoding="utf-8")


def _headings(text: str):
    """返回 [(行号, 级别, 标题文本)]，跳过代码围栏内的伪标题。"""
    out = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


@pytest.mark.parametrize("doc", DOCS)
def test_headings_have_body(doc: str):
    """每个标题下面必须有正文，不能紧跟另一个标题。

    允许「父标题后一路排下来全是子标题」（如 `## 3. 表达式语法` 直接接
    `### 3.1`），这是正常的目录式排布；只禁止**标题后无正文且后面不是
    更深的子标题**这种孤立标题。
    """
    lines = _read(doc).splitlines()
    heads = _headings("\n".join(lines))
    bad = []
    for idx, (ln, level, title) in enumerate(heads):
        nxt_ln, nxt_level = (
            (heads[idx + 1][0], heads[idx + 1][1])
            if idx + 1 < len(heads) else (len(lines) + 1, 0))
        body = [s for s in lines[ln:nxt_ln - 1] if s.strip()]
        if body:
            continue
        # 无正文：只有当后一个标题是「更深一级」（即这一节全由子节组成）才算合法
        if nxt_level > level:
            continue
        bad.append(
            f"第 {ln} 行 `{'#' * level} {title}` 下面没有正文，"
            f"紧接着第 {nxt_ln} 行是同级或更浅的标题")
    assert not bad, f"{doc} 存在孤立标题（正文可能被吞掉）：\n" + "\n".join(bad)


@pytest.mark.parametrize("doc", DOCS)
def test_numbered_heading_sequences_are_contiguous(doc: str):
    """同一编号域下的子标题必须 1..N 连续，不允许重号/跳号。

    按「章节根」编号分组：顶层 `## 3.` 与小节 `### 3.1` 归属同一个根 `3`，
    附录 `### A.8` 与 `#### A.8.1` 归属根 `A`。每个根内的小节编号必须
    从 1 开始连续——事故里 §3.9 被吞掉后就出现 3.8 → 3.10 的跳号。
    """
    heads = _headings(_read(doc))
    problems = []

    for level in (3, 4):
        # root -> [child numbers]
        groups: dict[str, list[tuple[int, str]]] = {}
        for ln, lv, title in heads:
            if lv != level:
                continue
            m = _NUMBERED.match(title)
            if not m:
                continue
            num = m.group(1)
            if "." not in num:
                continue
            root, child = num.rsplit(".", 1)
            if not child.isdigit():
                continue
            groups.setdefault(root, []).append((ln, child))

        for root, items in groups.items():
            if len(items) < 2:
                continue
            nums = [int(c) for _, c in items]
            expected = list(range(1, len(nums) + 1))
            if nums != expected:
                detail = ", ".join(f"{c}(L{ln})" for ln, c in items)
                problems.append(
                    f"根 `{root}` 下的 `#{level}` 子标题编号为 {detail}，"
                    f"期望从 1 连续到 {len(nums)}")

    assert not problems, (
        f"{doc} 标题编号不连续（很可能是编辑时吞行/错位造成的损坏）：\n"
        + "\n".join(problems))


@pytest.mark.parametrize("doc", DOCS)
def test_no_duplicate_headings(doc: str):
    """同一级别下不能出现完全相同的标题文本（复制粘贴/错位粘贴的特征）。"""
    heads = _headings(_read(doc))
    seen = Counter((lv, title) for _, lv, title in heads)
    dup = [f"{'#' * lv} {t}（出现 {n} 次）" for (lv, t), n in seen.items() if n > 1]
    assert not dup, f"{doc} 有重复标题：\n" + "\n".join(dup)


@pytest.mark.parametrize("doc", DOCS)
def test_heading_titles_are_not_fragments(doc: str):
    """标题不能是正文碎片：反引号必须成对，且不能以纯标点结尾。"""
    bad = []
    for ln, level, title in _headings(_read(doc)):
        stripped = title.strip()
        if stripped.count("`") % 2:
            bad.append(f"第 {ln} 行 `{stripped}` 反引号不成对")
        if stripped.endswith(("：", ":", "，", ",", "；", ";", "。")):
            bad.append(f"第 {ln} 行 `{stripped}` 以标点结尾，像被截断的正文")
    assert not bad, f"{doc} 存在形态异常的标题：\n" + "\n".join(bad)


@pytest.mark.parametrize("doc", DOCS)
def test_no_unreplaced_edit_markers(doc: str):
    """不能残留批量替换的中间产物。"""
    text = _read(doc)
    bad = []
    for marker in ("<<<<<<<", ">>>>>>>", "\x00"):
        if marker in text:
            bad.append(f"残留编辑标记 {marker!r}")
    for m in re.finditer(r"\b(TODO|FIXME|XXX)_\w+", text):
        bad.append(f"残留占位符 {m.group(0)!r}")
    assert not bad, f"{doc}：\n" + "\n".join(bad)
