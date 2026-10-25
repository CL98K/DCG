"""同步契约：改代码后，「哪些文件必须跟着改」由测试强制，不靠人记。

用户 m02306 的原话：「还有 README 中的信息，是不是也需要同步更新？性能，生成代码啥的，
这些工作不用我提醒吧？该的啥，那些受影响，相关文件内容是否要同步更新，这些事应该是
流程性的」。

这条要求的本质是：**同步不是一次性动作，而是一条可执行的规则**。所以这里把「谁引用了
谁」写成断言 —— 任意一处改了而另一处没改，pytest 立刻红，并指名该改哪里。

已有的三份守护各管一摊，不重复：
* `tests/test_docs_consistency.py` —— 硬数字（helper 数、函数并集、门禁数、语料数）。
* `tests/test_doc_products.py` —— 产物代码块必须等于真实 `dcg.generate()` 输出。
* `tests/test_doc_error_messages.py` —— 文档里的报错文案必须等于真实异常文本。

本文件补的是它们没管的三类**跨文件一致性**：
1. **测试计数**：`README.md` 的两处、`验收报告.md` 的三处必须等于真实收集数；
2. **性能数字**：文档里引用的比值必须能在 `evidence/perf_protocol.json` 找到同一个数；
3. **文件清单**：`验收报告.md` 的工作区清单必须与实际 `git status` 的未跟踪文件对上。
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_README = os.path.join(_ROOT, "README.md")
_REPORT = os.path.join(_ROOT, "验收报告.md")
_PROTOCOL = os.path.join(_ROOT, "evidence", "perf_protocol.json")


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _collect_counts() -> tuple:
    """真实收集数：返回 `(passed_total, skipped, collected)`。

    用 `--collect-only -q` 的汇总行拿总数，再单独跑一次拿到 passed / skipped 的准确
    分布代价太高，所以这里只做**静态**校验：拿收集总数与文档自述比对（passed 与
    skipped 之和必须等于总数，且两者都由文档给出）。
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only",
         "-p", "no:cacheprovider", os.path.join(_ROOT, "tests")],
        capture_output=True, text=True, encoding="utf-8", cwd=_ROOT,
    ).stdout
    # 末行形如 "1146 tests collected in 0.36s"（注意别加 `-q`：conftest 的
    # `pytest_terminal_summary` 会先打一行 COUNT_SUMMARY，`-q` 下就看不到汇总了）
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    assert m, "无法从 collect-only 输出解析收集总数：\n" + out[-800:]
    return int(m.group(1))


def _git_untracked() -> list:
    """`git status` 的未跟踪文件，路径已归一成正斜杠、无引号转义。"""
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "status", "--porcelain",
         "--untracked-files=all"],
        capture_output=True, text=True, encoding="utf-8", cwd=_ROOT,
    ).stdout
    paths = []
    for line in out.splitlines():
        if not line.startswith("??"):
            continue
        path = line[3:].strip().strip('"')
        paths.append(path.replace("\\", "/"))
    return paths


# --------------------------------------------------------------------------- #
# 1. 测试计数：文档自述必须等于真实收集数
# --------------------------------------------------------------------------- #

def test_readme_test_count_matches_collection():
    """`README.md` 的两处测试计数必须是真实的收集数。"""
    total = _collect_counts()
    text = _read(_README)

    # 目录树一行：「1129 tests（1125 passed / 4 skipped，…）」
    m = re.search(r"(\d+)\s+tests（(\d+)\s+passed\s+/\s+(\d+)\s+skipped", text)
    assert m, "README 目录树里找不到「N tests（P passed / S skipped）」形态的行"
    doc_total, doc_passed, doc_skipped = (int(x) for x in m.groups())
    assert doc_total == total, (
        "README 目录树写的 %d tests 与真实收集数 %d 不符；改了测试就同步这一行"
        % (doc_total, total)
    )
    assert doc_passed + doc_skipped == doc_total, (
        "README 目录树自相矛盾：%d passed + %d skipped != %d total"
        % (doc_passed, doc_skipped, doc_total)
    )

    # 验证表一行：「**1125 passed / 4 skipped / 0 failed**（1129 collected…）」
    m2 = re.search(
        r"\*\*(\d+)\s+passed\s+/\s+(\d+)\s+skipped\s+/\s+(\d+)\s+failed\*\*（(\d+)\s+collected",
        text,
    )
    assert m2, "README 验证表里找不到「**P passed / S skipped / F failed**（N collected」形态的行"
    p2, s2, f2, c2 = (int(x) for x in m2.groups())
    assert c2 == total, (
        "README 验证表写的 %d collected 与真实收集数 %d 不符" % (c2, total)
    )
    assert (p2, s2) == (doc_passed, doc_skipped), (
        "README 目录树（%d/%d）与验证表（%d/%d）的 passed/skipped 不一致"
        % (doc_passed, doc_skipped, p2, s2)
    )
    assert f2 == 0, "README 验证表宣称 0 failed，但本次验收要求全绿；若确有失败请同步此处与结论"


def test_report_test_count_matches_readme():
    """`验收报告.md` 里的**当前**测试计数必须与 `README.md` 一致。

    注意跳过形如「`1100 passed / 4 skipped` → `1142 / 4 / 0`」的历史对照行：报告里
    会保留「旧值 → 新值」的演进记录，旧值不是当前计数。
    """
    text = _read(_README)
    m = re.search(r"(\d+)\s+tests（(\d+)\s+passed\s+/\s+(\d+)\s+skipped", text)
    assert m
    total, passed, skipped = (int(x) for x in m.groups())

    report = _read(_REPORT)
    pairs = set()
    for line in report.splitlines():
        # 演进对照行：`1100 passed / 4 skipped` → ... ；只取「→」右侧的最新值
        cur = line.split("→")[-1] if "→" in line else line
        for a, b in re.findall(r"(\d+)\s+passed\s*/\s*(\d+)\s+skipped", cur):
            pairs.add((int(a), int(b)))

    assert pairs, "验收报告里找不到「N passed / S skipped」形态的计数"
    bad = {p for p in pairs if p != (passed, skipped)}
    assert not bad, (
        "验收报告里的计数 %s 与 README 的 %d passed / %d skipped 不一致；改了测试要同步报告"
        % (sorted(bad), passed, skipped)
    )

    # 「N collected」形态同样只挑当前值（演进行取右侧）
    colle = set()
    for line in report.splitlines():
        cur = line.split("→")[-1] if "→" in line else line
        colle |= {int(x) for x in re.findall(r"(\d+)\s+collected", cur)}
    bad_c = {c for c in colle if c != total}
    assert not bad_c, (
        "验收报告里的 collected 数 %s 与真实的 %d 不一致" % (sorted(bad_c), total)
    )


# --------------------------------------------------------------------------- #
# 2. 性能数字：文档引用的比值必须能在 perf_protocol.json 找到
# --------------------------------------------------------------------------- #

def _protocol_ratios() -> dict:
    with io.open(_PROTOCOL, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("rows") or []
    assert rows, "evidence/perf_protocol.json 里没有 rows；先跑 evidence/bench_run.py"
    return {r["name"]: r["ratio"] for r in rows}


def _ratio_cell(text: str, name: str):
    """从 Markdown 表格里取出某用例那一行的「N.NN×」比值单元格。

    表格行长这样：
        | `hires_map` | `$map` 带回调 | 0.4686 | 0.2017 | **2.32×** | DCG 更慢 |
    取最后一个形如 `**N.NN×**` / `N.NN×` 的比值单元格。
    """
    for line in text.splitlines():
        if "`" + name + "`" not in line or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip().strip("*").strip() for c in line.split("|")]
        for cell in cells:
            m = re.fullmatch(r"(\d+\.\d+)×", cell)
            if m:
                return float(m.group(1))
    return None


@pytest.mark.parametrize(
    "name",
    ["hires_reduce", "hires_map", "hires_filter", "hires_sort",
     "shape_list", "shape_dict", "shape_index", "shape_rows",
     "agg_container", "agg_direct", "optwide_hit", "optwide_missing"],
)
def test_report_ratio_matches_protocol(name):
    """验收报告 §5.1 表格里的每个比值必须等于 `perf_protocol.json` 的实测值。"""
    protocol = _protocol_ratios()
    assert name in protocol, "perf_protocol.json 里没有用例 %s" % name
    want = round(protocol[name], 2)

    got = _ratio_cell(_read(_REPORT), name)
    assert got is not None, "验收报告 §5.1 表格里找不到用例 %s 的比值单元格" % name
    assert abs(got - want) < 0.005 + 1e-9, (
        "验收报告里 %s 的比值写成 %s×，实测（perf_protocol.json）是 %.4f×；"
        "重跑 evidence/bench_run.py 后同步此表" % (name, got, protocol[name])
    )


def test_readme_appendix_b_ratios_are_from_protocol():
    """README 附录 B.7 表格里的每个比值必须能在 `perf_protocol.json` 找到同一个数。

    注意别用「裸 N.NN×」正则去扫整节：µs 列的数字（0.1666）也会被 `×` 前面的字符
    误伤。这里只认表格行里**独立成单元格**的比值。
    """
    protocol = {round(v, 2) for v in _protocol_ratios().values()}
    text = _read(_README)
    start = text.find("### B.7 ")
    end = text.find("### B.8 ")
    assert start > 0 and end > start, "README 里找不到 B.7 / B.8 两节"
    section = text[start:end]

    ratios = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for cell in line.split("|"):
            m = re.fullmatch(r"(\d+\.\d+)×", cell.strip().strip("*").strip())
            if m:
                ratios.add(round(float(m.group(1)), 2))

    assert ratios, "README B.7 表格里没有找到任何比值单元格"
    stray = {r for r in ratios if r not in protocol}
    assert not stray, (
        "README B.7 里这些比值在 perf_protocol.json 里找不到：%s。"
        "附录 B 的性能数字必须来自同一次实测（见 evidence/bench_run.py）" % sorted(stray)
    )


def test_report_and_readme_agree_on_direction():
    """同一个用例在 README B.7 与验收报告 §5.1 里的比值必须一致。

    两份文档都引用同一份 `perf_protocol.json`，若出现分歧就是有人只改了一边。
    报告里额外出现的比值可以来自 §5.4 的「优化前」列，不算冲突；但 B.7 里出现的
    比值必须同时出现在报告里（两份文档说的是一批实测）。
    """
    report = _read(_REPORT)
    readme = _read(_README)
    start = readme.find("### B.7 ")
    end = readme.find("### B.8 ")
    assert start > 0 and end > start
    b7 = readme[start:end]

    def ratios_of(section, pat):
        found = set()
        for line in section.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            for cell in line.split("|"):
                m = re.fullmatch(pat, cell.strip().strip("*").strip())
                if m:
                    found.add(round(float(m.group(1)), 2))
        return found

    proto = {round(v, 2) for v in _protocol_ratios().values()}
    b7_ratios = ratios_of(b7, r"(\d+\.\d+)×")
    rep_ratios = ratios_of(report, r"(\d+\.\d+)×")

    missing = b7_ratios & proto - rep_ratios
    assert not missing, (
        "README B.7 用的比值 %s 在验收报告里找不到；两份文档应引用同一批实测"
        % sorted(missing)
    )


# --------------------------------------------------------------------------- #
# 3. 证据文件清单：报告里列的未跟踪文件必须与实际一致
# --------------------------------------------------------------------------- #

def test_report_lists_every_untracked_evidence_file():
    """`验收报告.md` §7.2 列的未跟踪文件必须覆盖实际 `git status` 的未跟踪文件。

    反过来不要求（报告可以多列将要入库但尚未创建的文件），但**实际存在的新文件
    不能在报告里缺项** —— 否则交付时漏提交。
    """
    try:
        untracked = _git_untracked()
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 无 git 环境
        pytest.skip("当前环境没有可用的 git")
    untracked = [p for p in untracked
                 if not p.startswith("_tmp/") and not p.startswith("build/")]
    if not untracked:
        pytest.skip("没有需要入库的未跟踪文件")

    report = _read(_REPORT)
    missing = [p for p in untracked if p not in report]
    assert not missing, (
        "验收报告 §7.2 的文件清单缺了这些实际存在的未跟踪文件：%s。"
        "新增证据/测试文件后要同步报告的工作区清单" % missing
    )
