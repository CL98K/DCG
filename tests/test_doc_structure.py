"""文档结构完整性守卫（防止编辑事故造成的物理损坏）。

背景：批量替换曾误吞 `DCG 接口实现细节文档.md` 的开头三行，把标题覆盖成别的
标题、正文留下悬空文案，而当时全量测试全绿 —— 没有测试守护文档的物理结构。

本文件只做结构断言，不逐字比内容（内容由 test_doc_* 其余文件守护）。四类断言：

1. **标题下有正文**：标题不能被吞掉正文后紧跟另一个标题（事故的直接形态）；
2. **编号连续、无重号**：同一父编号下子标题必须 1..N 连续（事故的另一形态：
   3.8 之后直接 3.10，或两个 3.9）；
3. **标题形态正常**：反引号成对、不以纯标点结尾（防止正文碎片被提到标题位）；
4. **目录锚点可达**：每个 `](#...)` 链接都能在同文件（或指向的兄弟文档）里
   找到能生成该锚点的标题——标题改了名而目录没跟着改，就是这个断言在红。

这些断言的共同点是**正常修订不会触发**：只要标题下有内容、编号连续，改文案/加内容
都不会红；而「吞掉几行」「错位粘贴」这类事故一定会红。
"""

from __future__ import annotations

import re
import unicodedata
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
# 所有级别的标题（锚点按 1..6 级都可能出现）
_ANY_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
# `[文字](#锚点)` / `[文字](<文件.md#锚点>)`；只认纯锚点与同仓 md 的相对链接
_ANCHOR_LINK = re.compile(r"\]\((?:<)?([^)\s>]*#[^)\s>]*)(?:>)?\)")
# 目录行：`- [文字](#锚点)`
_TOC_LINE = re.compile(r"^\s*-\s*\[([^\]]*)\]\((?:<)?([^)\s>]*#[^)\s>]*)(?:>)?\)\s*$")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\((?:<)?[^)\s>]*(?:>)?\)")
_INLINE_CODE = re.compile(r"`[^`]*`")


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


def _plain(title: str) -> str:
    """标题文本 → 参与锚点计算的纯文本（去掉行内链接与行内代码的标记）。"""
    title = _INLINE_LINK.sub(lambda m: m.group(1), title)
    title = title.replace("`", "")
    return title


def _slug(title: str) -> str:
    """按 GitHub 的标题锚点规则算 slug。

    实证依据（对真实渲染页比对得出）：小写化；空格 → `-`；保留 `-` 与 `_`；
    **删除**所有 Unicode 标点（`P*`）、符号（`S*`）、控制/格式字符（`C*`）——
    这一步同时吃掉半角与全角的 `（）§、：，。` `` ` `` `.` `/` `$`；
    中文（`Lo`）与字母数字保留。
    """
    out = []
    for ch in _plain(title).strip().lower():
        if ch.isspace():
            out.append("-")
            continue
        if ch in "-_":
            out.append(ch)
            continue
        if unicodedata.category(ch)[0] in "PSC":
            continue
        out.append(ch)
    return "".join(out)


def _anchor_ids(text: str) -> set[str]:
    """该文档全部标题能生成的锚点集合（含 GitHub 的重名后缀 `-1`/`-2`）。"""
    seen: dict[str, int] = {}
    ids = set()
    for _, _, title in _headings_any(text):
        base = _slug(title)
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.add(base if n == 0 else f"{base}-{n}")
    return ids


def _headings_any(text: str):
    """1..6 级标题（锚点断言要看全级别，与只看 2..4 级的结构断言不同）。"""
    out = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ANY_HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _blank_code_and_headings(text: str) -> list[str]:
    """把行内代码、代码围栏、标题行抹成空，只留下真正会产生链接的正文行。

    文档里常有「锚点该长什么样」的示例（写在代码段里）与「旧文本」引用
    （写在表格单元格的反引号里），它们不是链接，不能算死链。
    """
    out = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence or _ANY_HEADING.match(line):
            out.append("")
            continue
        out.append(_INLINE_CODE.sub("", line))
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
    从 1 开始连续——事故里 被吞掉后就出现 3.8 → 3.10 的跳号。
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


@pytest.mark.parametrize("doc", DOCS)
def test_anchor_links_resolve_to_a_heading(doc: str):
    """每个 `](#锚点)` 链接都必须能落到某个标题上。

    事故形态：标题从「5. 表连接（`$lookup`）完整案例」改成「5. 表连接（`$lookup`）」，
    正文与目录里的 `#5-表连接lookup完整案例` 就全成了死链——点进去什么也不发生。
    这类问题肉眼极难发现（链接看起来完全正常），只有算一遍 slug 才看得见。

    锚点规则见 `_slug`：全角括号、`§`、`、`、`：`、`` ` ``、`.`、`/`、`$` 都删除，
    空格 → `-`，中文保留。跨文档链接（`其他.md#锚点`）在目标文档里校验。
    """
    texts = {d: _read(d) for d in DOCS}
    ids = {d: _anchor_ids(t) for d, t in texts.items()}
    bad = []
    for i, line in enumerate(_blank_code_and_headings(texts[doc]), 1):
        for m in _ANCHOR_LINK.finditer(line):
            target = m.group(1)
            path, _, frag = target.partition("#")
            if not frag:
                continue
            tgt = doc if path in ("", None) else path.lstrip("./")
            if tgt not in ids:
                # 指向仓库外的链接（http/https 等）不归本断言管
                continue
            if frag not in ids[tgt]:
                bad.append(
                    f"第 {i} 行 `{target}` 在 `{tgt}` 里没有对应标题"
                    f"（算出的锚点集合不含 `#{frag}`）")
    assert not bad, (
        f"{doc} 存在失效的目录锚点（标题改名后目录没跟着改）：\n" + "\n".join(bad))


def test_anchor_slugs_match_known_github_cases():
    """用几个真实的 GitHub 页面反推出来的例子钉住 slug 规则，防止规则被改松。

    前两条来自 `mzlogin/awesome-adb` 与 `jackfrued/Python-100-Days` 的渲染页
    `id="user-content-..."`，后两条是本仓库自己的标题。
    """
    cases = [
        # 全角括号被删除、空格 → `-`（awesome-adb 的真实 id）
        ("无线连接（Android11 及以上）", "无线连接android11-及以上"),
        # 全角冒号被删除，且链接语法只留链接文字（Python-100-Days 的真实 id）
        ("第91天：[团队项目开发的问题和解决方案](./Day91-100/91.团队项目开发的问题和解决方案.md)",
         "第91天团队项目开发的问题和解决方案"),
        # 本仓库：行内代码的反引号与 `$` 删除，全角括号删除
        ("5. 表连接（`$lookup`）", "5-表连接lookup"),
        # 本仓库：全角括号 + 书名号 + `§` 删除
        ("2. schema 规格（契约见《DCG 接口实现细节文档》§2）",
         "2-schema-规格契约见dcg-接口实现细节文档2"),
    ]
    for title, expected in cases:
        assert _slug(title) == expected, (
            f"{title!r} 的锚点算成了 {_slug(title)!r}，期望 {expected!r}")
