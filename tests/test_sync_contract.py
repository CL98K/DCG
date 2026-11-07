"""同步契约：改代码后，「哪些文件必须跟着改」由测试强制，不靠人记。

背景：「还有 README 中的信息，是不是也需要同步更新？性能，生成代码啥的，
这些工作不用我提醒吧？该的啥，那些受影响，相关文件内容是否要同步更新，这些事应该是
流程性的」。

这条要求的本质是：**同步不是一次性动作，而是一条可执行的规则**。所以这里把「谁引用了
谁」写成断言 —— 任意一处改了而另一处没改，pytest 立刻红，并指名该改哪里。

已有的三份守护各管一摊，不重复：
* `tests/test_docs_consistency.py` —— 硬数字（helper 数、函数并集、门禁数、语料数）。
* `tests/test_doc_products.py` —— 产物代码块必须等于真实 `dcg.generate` 输出。
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
_ARCH = os.path.join(_ROOT, "DCG 整体设计方案与架构文档.md")

#: 允许的「收集数 - passed」跳过区间。
#:
#: * 正常机器：8 —— `test_codegen_snapshot.py` 的 4 条非确定 helper 对拍
#:   + `test_doc_samples.py` 的 4 条非确定函数样例（`$now`/`$millis`/`$random`/
#:   `$shuffle`），与机器无关。
#: * 干净树再加 1 —— `test_report_lists_every_untracked_evidence_file` 在没有
#:   未跟踪证据文件时跳过，于是 9。
#:
#: 曾经还有一档「DSH 文件沙箱 +10」：`tests/conftest.py` 探测到
#: `tempfile.mkdtemp` 子目录不可列举后，给 `_NEEDS_LISTABLE_TMPDIR` 里的用例打
#: 环境性 skip。那些用例现已改为显式使用 `_tmp/build/<用例名>` 目录，
#: `_NEEDS_LISTABLE_TMPDIR` 置空，沙箱不再多跳 —— 故区间收紧成 8~9。
#:
#: 区间而不是定值：唯一会浮动的是「干净树 +1」这一条，取决于工作区状态。
_SKIP_RANGE = range(8, 10)


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _collect_counts() -> tuple:
    """真实收集数：返回 `(passed_total, skipped, collected)`。

    用 `--collect-only -q` 的汇总行拿总数，再单独跑一次拿到 passed / skipped 的准确
    分布代价太高，所以这里只做**静态**校验：拿收集总数与文档自述比对（passed 与
    skipped 之和必须等于总数，且两者都由文档给出）。
    """
    # 子进程必须与这里的 `encoding="utf-8"` 对齐：`tests/conftest.py` 的
    # `pytest_terminal_summary` 会打中文 `SANDBOX_REASON`，Windows 上子进程默认按
    # GBK 写 stdout，父进程按 utf-8 解码会在词中间撞上 0xcf 之类的字节
    # （`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xcf in position 109291`），
    # 读取线程挂掉后 `stdout` 变成 None，测试报一个与被测行为无关的 TypeError。
    # 用 PYTHONUTF8=1 而不是 `-X utf8`：环境变量会传给子进程里的所有进程。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only",
         "-p", "no:cacheprovider", os.path.join(_ROOT, "tests")],
        capture_output=True, text=True, encoding="utf-8", cwd=_ROOT, env=env,
    ).stdout
    # 末行形如 "1146 tests collected in 0.36s"（注意别加 `-q`：conftest 的
    # `pytest_terminal_summary` 会先打一行 COUNT_SUMMARY，`-q` 下就看不到汇总了）
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    assert m, "无法从 collect-only 输出解析收集总数：\n" + out[-800:]
    return int(m.group(1))


#: 由工具 `生成` 的中间产物，不是证据、不该登记进 `验收报告.md`。
#:
#: 为什么需要这一条：CI（`.github/workflows/ci.yml`）里跑 `pip install -e .`
#: 会在仓库根生成 `dcg.egg-info/`，pytest 步骤又把输出重定向到 `pytest.log`；
#: 这两样在工作区里表现为"未跟踪文件"，断言会把它们当漏登记的证据报红，
#: 于是**每个版本、每一次 CI 都必然失败**（真机上不存在，所以本机测不出来）。
#: 判定成"产物"而不是"证据"的依据：删掉它们不影响任何结论，且 `.gitignore`
#: 之外再生成一次即可复现。
_ARTIFACT_PREFIXES = ("_tmp/", "build/", "dist/")
_ARTIFACT_DIR_SUFFIX = ".egg-info"
_ARTIFACT_NAMES = ("pytest.log",)


def _is_generated_artifact(path: str) -> bool:
    """该未跟踪路径是否属于工具生成的中间产物（不参与证据清单断言）。"""
    if path.startswith(_ARTIFACT_PREFIXES):
        return True
    parts = path.split("/")
    if any(part.endswith(_ARTIFACT_DIR_SUFFIX) for part in parts[:-1]):
        return True
    if parts[0].endswith(_ARTIFACT_DIR_SUFFIX):
        return True
    return len(parts) == 1 and parts[0] in _ARTIFACT_NAMES


#: 项目根目录下的**运行时状态**文件，不是交付物、不进证据清单。
#:
#: `AGENTS.md` §7「状态持久化」把 `PROGRESS.md` / `PROGRESS_LOG.md` 定义为
#: 「只放项目根目录」的运行时状态（task / step / lead_poll_at / poll_count 等），
#: 由 Lead 每轮覆盖写入，随会话变化而变化。它们的内容是**某一时刻的调度状态**，
#: 不是任何结论的证据 —— 删掉不影响任何验收结论，重新跑一次会话即可再生成，
#: 与 `_ARTIFACT_NAMES` 里的 `pytest.log` 同类。
#:
#: 为什么不登记进 `验收报告.md`：登记等于把「每轮都会变的调度快照」写进交付文档，
#: 报告会随轮询不断过期，而它并不承载任何可验收的结论。所以这里按「非交付物」
#: 排除，而不是把它塞进报告的工作区清单。
_RUNTIME_STATE_NAMES = ("PROGRESS.md", "PROGRESS_LOG.md")


def _is_runtime_state(path: str) -> bool:
    """该未跟踪路径是否属于 AGENTS.md §7 的根目录运行时状态文件。"""
    return path in _RUNTIME_STATE_NAMES


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

def test_docs_other_than_report_have_no_test_counts():
    """测试通过/失败/收集数只允许出现在 `验收报告.md` 里。

    其余文档（、JSONata）是给用户看的规格与手册，写死
    「N passed / M failed」这类某一次运行的结果，一改测试就过期，而且会把
    「测试跑没跑过」混进「能力是什么」里。计数唯一权威是。
    """
    docs = [
        _README,
        os.path.join(_ROOT, "DCG 接口实现细节文档.md"),
        os.path.join(_ROOT, "DCG 整体设计方案与架构文档.md"),
        os.path.join(_ROOT, "JSONata 语法.md"),
    ]
    bad = []
    for path in docs:
        text = _read(path)
        for pat in (r"\d+\s+tests（", r"passed\s*/\s*\d+\s*failed", r"\d+\s+collected"):
            m = re.search(pat, text)
            if m:
                bad.append("%s 命中 %r" % (os.path.basename(path), m.group(0)))
    assert not bad, "除验收报告外，文档里不允许出现测试计数：%s" % bad


def test_report_test_count_matches_collection():
    """`验收报告.md` 是测试计数的唯一出处，且必须与真实收集数一致。"""
    total = _collect_counts()
    report = _read(_REPORT)

    pairs, colle = set(), set()
    for line in report.splitlines():
        # 演进对照行取「→」右侧的最新值
        cur = line.split("→")[-1] if "→" in line else line
        for a, b in re.findall(r"(\d+)\s+passed\s*/\s*(\d+)\s+failed", cur):
            pairs.add((int(a), int(b)))
        colle |= {int(x) for x in re.findall(r"(\d+)\s+collected", cur)}

    assert pairs, "验收报告里找不到「N passed / F failed」形态的计数"
    assert colle, "验收报告里找不到「N collected」形态的计数"

    bad_c = {c for c in colle if c != total}
    assert not bad_c, "验收报告里的 collected 数 %s 与真实收集数 %d 不一致" % (sorted(bad_c), total)

    bad_f = {p for p in pairs if p[1] != 0}
    assert not bad_f, "验收报告宣称有失败 %s，但验收要求全绿" % sorted(bad_f)

    for passed, _failed in pairs:
        n_skipped = total - passed
        assert n_skipped in _SKIP_RANGE, (
            "验收报告里 %d tests - %d passed = %d 条跳过，落在预期区间 %d~%d 之外"
            % (total, passed, n_skipped, _SKIP_RANGE[0], _SKIP_RANGE[-1])
        )


def test_report_test_file_table_sum_matches_collection():
    """`验收报告.md` 的逐文件收集表必须与实际 `tests/test_*.py` 一致。

    这张表曾长期「各行之和 ≠ 合计」：漏列一个文件、另一个文件数字过期，合计行却是
    对的，于是没有守护时谁也不会发现。这里做两件事：

    * 行集合必须等于磁盘上的 `tests/test_*.py` 集合（不许多、不许少）；
    * 各行 collected 之和必须等于真实收集总数（与 ``_collect_counts`` 同一口径）。

    下一层的「passed / skipped 之和 == collected」由
    ``test_report_test_count_matches_collection`` 负责，两条互补。
    """
    total = _collect_counts()
    report = _read(_REPORT).splitlines()
    start = next((i for i, l in enumerate(report) if l.startswith("### 3.1 ")), -1)
    assert start >= 0, "验收报告里找不到 §3.1 测试文件表（标题被改名了？）"
    end = next(
        (i for i in range(start + 1, len(report))
         if report[i].startswith(("## ", "### "))),
        len(report),
    )

    rows = {}
    for line in report[start:end]:
        # 单元格可能是 `244`、**200**、*65* 三种写法（侧重项用粗体强调）。
        m = re.match(
            r"\|\s*`?(tests/[\w./-]+\.py)`?\s*\|\s*[*_]*\s*(\d+)\s*[*_]*\s*\|",
            line.strip(),
        )
        if m:
            rows[m.group(1).split("/")[-1]] = int(m.group(2))
    assert rows, "§3.1 表格里没有解析到任何 `tests/*.py` 行（表结构变了？）"

    tests_dir = os.path.join(_ROOT, "tests")
    actual = sorted(
        name for name in os.listdir(tests_dir)
        if name.startswith("test_") and name.endswith(".py")
    )
    listed = sorted(rows)
    missing = sorted(set(actual) - set(listed))
    extra = sorted(set(listed) - set(actual))
    assert not missing and not extra, (
        "§3.1 的测试文件表与磁盘不一致：\n  漏列: %s\n  多列: %s"
        % (missing, extra)
    )

    got = sum(rows.values())
    assert got == total, (
        "§3.1 各行 collected 之和是 %d，但真实收集数是 %d；"
        "改测试后要同步这张表（合计行也要跟着改）" % (got, total)
    )



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
    """验收报告 表格里的每个比值必须等于 `perf_protocol.json` 的实测值。"""
    protocol = _protocol_ratios()
    assert name in protocol, "perf_protocol.json 里没有用例 %s" % name
    want = round(protocol[name], 2)

    got = _ratio_cell(_read(_REPORT), name)
    assert got is not None, "验收报告 §5.1 表格里找不到用例 %s 的比值单元格" % name
    assert abs(got - want) < 0.005 + 1e-9, (
        "验收报告里 %s 的比值写成 %s×，实测（perf_protocol.json）是 %.4f×；"
        "重跑 evidence/bench_run.py 后同步此表" % (name, got, protocol[name])
    )


def test_readme_ratios_are_from_protocol():
    """README 里出现的每个比值单元格（``N.NN×``）必须能在 `perf_protocol.json` 找到同一个数。

    README 附录 B.7（分项实测表）已删除，性能数字统一收敛到
    `验收报告.md`。这条守护保留形状：以后
    谁在 README 里再写比值表格，数字必须来自同一次实测。
    """
    protocol = {round(v, 2) for v in _protocol_ratios().values()}
    text = _read(_README).splitlines()
    ratios = set()
    for line in text:
        if not line.lstrip().startswith("|"):
            continue
        for cell in line.split("|"):
            m = re.fullmatch(r"(\d+\.\d+)×", cell.strip().strip("*").strip())
            if m:
                ratios.add(round(float(m.group(1)), 2))
    stray = {r for r in ratios if r not in protocol}
    assert not stray, (
        "README 里这些比值在 perf_protocol.json 里找不到：%s。"
        "性能数字必须来自同一次实测（见 evidence/bench_run.py）" % sorted(stray)
    )


def _prose_ratio_pairs(text: str) -> list:
    """抽出「反引号用例名 + 空格 + `N.NN×`」形态的散文件比值对。

    返回 `[(行号, 用例名, 比值), ...]`。只认这一种形态，因为架构文档里
    还有大量**微基准**比值（`$zip` 1.45–1.61×、`hires_map` 1.12× 的 itype
    收益等），它们本来就不在 `perf_protocol.json` 里，扫所有 `N.NN×` 会
    大面积误报。
    """
    pat = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`\s+(\d+\.\d+)×")
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in pat.finditer(line):
            out.append((lineno, m.group(1), float(m.group(2))))
    return out


def _is_protocol_attributed(line: str) -> bool:
    """这一行是否把比值归因到验收报告（= 协议口径），而不是微基准。

    这是区分「协议口径」与「微基准」的唯一可靠信号：
    `:542` 写「协议口径复测见验收报告 §5.1（`hires_map` 1.30×…）」，
    而 `:548` 的 `hires_filter` 1.008× 是局部绑定 on/off 的微基准、
    `:559` 的 `hires_map` 1.12× 是 itype 的微基准 —— 三者都用反引号
    包了用例名，**光看「反引号用例名 + 比值」分不开**。
    """
    return "验收报告 §5" in line


def test_arch_doc_prose_ratios_are_from_protocol():
    """架构文档里**归因到验收报告**的散文件比值，必须是 `perf_protocol.json` 的同一个数。

    背景：`:542` 曾长期写「`hires_map` 1.85×」，而协议与验收报告早已是
    1.30×（`perf_protocol.json` `ratio: 1.30018`）。三条既有守护
    （`test_report_ratio_matches_protocol`、`test_readme_ratios_are_from_protocol`、
    `test_report_conclusion_matches_protocol_directions`）只读
    `验收报告.md` / `README.md`，且只认 `|` 表格单元格，管不到架构文档
    散文里的括号数字 —— 于是这个陈旧副本活了 20 多个 commit。

    口径与 `test_report_ratio_matches_protocol` 一致：±0.005。
    """
    protocol = _protocol_ratios()
    text = _read(_ARCH)
    lines = text.splitlines()

    checked, bad, skipped = [], [], []
    for lineno, name, got in _prose_ratio_pairs(text):
        if not _is_protocol_attributed(lines[lineno - 1]):
            skipped.append((lineno, name, got, "非协议口径（微基准/其它）"))
            continue
        if name not in protocol:
            skipped.append((lineno, name, got, "用例名不在 perf_protocol.json"))
            continue
        want = round(protocol[name], 2)
        checked.append((lineno, name, got, want))
        if abs(got - want) >= 0.005 + 1e-9:
            bad.append(
                "%s:%d 写 `%s` %.3f×，协议是 %.4f×"
                % (os.path.basename(_ARCH), lineno, name, got, protocol[name])
            )

    skip_note = "；".join(
        "%d:%s %.3f×（%s）" % (n, nm, v, why) for n, nm, v, why in skipped
    ) or "无"
    assert not bad, (
        "架构文档里归因到验收报告的比值与 perf_protocol.json 不一致：\n  %s\n"
        "跳过清单（不参与判定，仅供审计）：%s"
        % ("\n  ".join(bad), skip_note)
    )
    # 防止这条守护退化成「什么都没扫到所以永远绿」。
    assert checked, (
        "架构文档里没有扫到任何「验收报告 §5」归因的比值，这条守护已失效；"
        "若确实删掉了这类引用，请一并删掉本测试。跳过清单：%s" % skip_note
    )


def test_report_ratios_match_protocol_names():
    """验收报告 的用例名集合必须与 `perf_protocol.json` 的 12 项**双向**一致。

    协议共 13 项： 只放 12 项主基准，第 13 项完整样例 `order_pipeline`
    在 单列（它的比值在本函数末尾单独核对，不增加一条参数化用例、
    从而不改动「收集数 1425」这个见诸多份文档的契约数字），所以这里按
    「协议 − {order_pipeline}」比对名字集合。

    原来的守护比的是「README B.7 的比值 ⊂ 报告」；README 附录 B 删除后改成正向
    契约：报告 与协议是同一批实测，既不许多也不许少。比值本身由
    `test_report_ratio_matches_protocol` 逐项核对，这里只管名字集合的完整性。
    """
    proto = set(_protocol_ratios()) - {"order_pipeline"}
    report = _read(_REPORT).splitlines()
    start = next((i for i, l in enumerate(report) if l.startswith("### 5.1 ")), -1)
    end = next((i for i, l in enumerate(report) if l.startswith("#### 5.1.1 ")), -1)
    assert start > 0 and end > start, (
        "验收报告里找不到 §5.1 / §5.1.1 两节（性能表结构变了？）")
    section = report[start:end]

    names = set()
    for line in section:
        m = re.match(r"\|\s*`([A-Za-z_]+)`\s*\|", line.strip())
        if m:
            names.add(m.group(1))
    missing, extra = proto - names, names - proto
    assert not missing and not extra, (
        "验收报告 §5.1 的用例名与 perf_protocol.json 不一致：\n  缺: %s\n  多: %s"
        % (sorted(missing), sorted(extra)))

    # 第 13 项（完整样例 order_pipeline）单列在，比值同样要等于协议快照。
    proto_all = _protocol_ratios()
    want = round(proto_all["order_pipeline"], 2)
    got = _ratio_cell(_read(_REPORT), "order_pipeline")
    assert got is not None, "验收报告 §5.2 表里找不到 order_pipeline 的比值单元格"
    assert abs(got - want) < 0.005 + 1e-9, (
        "验收报告里 order_pipeline 的比值写成 %s×，实测（perf_protocol.json）是 %.4f×；"
        "重跑 evidence/bench_run.py 后同步此表" % (got, proto_all["order_pipeline"]))

# --------------------------------------------------------------------------- #
# 3. 证据文件清单：报告里列的未跟踪文件必须与实际一致
# --------------------------------------------------------------------------- #

def test_report_lists_every_untracked_evidence_file():
    """`验收报告.md` 里列出的未跟踪文件必须覆盖实际 `git status` 的未跟踪文件。

    反过来不要求（报告可以多列将要入库但尚未创建的文件），但**实际存在的新文件
    不能在报告里缺项** —— 否则交付时漏提交。

    由工具生成的中间产物（`dcg.egg-info/`、`pytest.log` 等，见
    `_is_generated_artifact`）与项目根目录的运行时状态文件（`PROGRESS.md` /
    `PROGRESS_LOG.md`，见 `_is_runtime_state` 与 `AGENTS.md` §7）不算缺项：
    它们不是证据，前者 CI 每次都会生成，后者每轮轮询都会重写。
    """
    try:
        untracked = _git_untracked()
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 无 git 环境
        pytest.skip("当前环境没有可用的 git")
    untracked = [p for p in untracked
                 if not _is_generated_artifact(p) and not _is_runtime_state(p)]
    if not untracked:
        pytest.skip("没有需要入库的未跟踪文件")

    report = _read(_REPORT)
    missing = [p for p in untracked if p not in report]
    assert not missing, (
        "验收报告的文件清单缺了这些实际存在的未跟踪文件：%s。"
        "新增证据/测试文件后要同步报告的工作区清单" % missing
    )
