# -*- coding: utf-8 -*-
"""task-6：`examples/jx/*.jx` 的**可执行**样例套件 + `docs/jsonata_syntax.md` 生成器。

设计（"文档与样例同源"从根上防住文档腐化）：

- `examples/jx/*.jx` 是**唯一真理源**，每个 `=== <标题>` 记录是一条例样；
- 本文件既**执行**每条例样（`compile_expr` -> `dcg.generate` -> `exec` -> `run`，断言真实结果），
  又**渲染** `docs/jsonata_syntax.md`，渲染结果里逐字包含样例源文件；
- `test_doc_is_byte_identical_to_renderer` 断言磁盘文档与渲染结果逐字节相等 —— 文档只能由样例生成。
- **不得 skip**：任何"不支持"都以可执行断言的形式记录（`expect: reject` 断言抛 `Unsupported`，
  `expect: codegen-reject` 断言 `generate` 抛 `Unsupported`，`state: defect` 断言当前错误行为，
  `state: fixed` 断言"曾经错误、现已修复"的正确值），所以不存在"跑不起来的样例被静默跳过"。
- **缺陷有两种生命周期**：`state: defect` 钉住**当前**错误行为（引擎修好即变红，充当复核触发器）；
  一旦修好就必须迁到 `state: fixed`（回归保护，只放 `14_fixed_regressions.jx`）并断言**正确值**，
  将来再退化就会变红。禁止把已修好的条目改回"继续断言错误行为"，禁止用 `skip`/`xfail` 盖过去。

重新生成文档：

    python tests/test_jx_examples.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dcg  # noqa: E402
from dcg.jx import Unsupported, compile_expr  # noqa: E402

SAMPLES_DIR = ROOT / "examples" / "jx"
DOC_PATH = ROOT / "docs" / "jsonata_syntax.md"
REPRO_DATE = "2026-10-17 01:04 (Asia/Shanghai)"
_E = "`"


# ---------------------------------------------------------------------------
# 样例文件解析
# ---------------------------------------------------------------------------
def sample_files():
    return sorted(SAMPLES_DIR.glob("*.jx"))


def parse_file(path):
    meta = {"file": path.name, "title": path.stem, "desc": []}
    cases = []
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("=== "):
            cur = {"title": line[4:].strip(), "expr": None, "row": {}, "expect": None,
                   "type": None, "note": None, "state": None}
            cases.append(cur)
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if body.startswith("file:"):
                meta["file"] = body.split(":", 1)[1].strip()
            elif body.startswith("title:"):
                meta["title"] = body.split(":", 1)[1].strip()
            elif body.startswith("desc:"):
                meta["desc"].append(body.split(":", 1)[1].strip())
            continue
        if not line.strip() or cur is None:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "expr":
            cur["expr"] = val
        elif key == "row":
            cur["row"] = ast.literal_eval(val)
        elif key == "note":
            cur["note"] = val
        elif key in ("expect", "type", "state"):
            cur[key] = val
        else:
            raise AssertionError(f"{path.name}: 未知键 {key!r}")
    for case in cases:
        if not case["state"]:
            case["state"] = "gap" if case["expect"] == "codegen-reject" else "ok"
    return meta, cases


def all_samples():
    out = []
    for path in sample_files():
        meta, cases = parse_file(path)
        for i, case in enumerate(cases, 1):
            out.append((path, meta, i, case))
    return out


ALL = all_samples()


def _cid(item):
    path, _meta, i, case = item
    return f"{path.stem}:{i}:{case['title']}"


# ---------------------------------------------------------------------------
# 真实执行
# ---------------------------------------------------------------------------
class Obs:
    __slots__ = ("kind", "value", "exc")

    def __init__(self, kind, value=None, exc=None):
        self.kind, self.value, self.exc = kind, value, exc


def build(expr, row, out_type="any"):
    """按 DCG 真实管线建 schema -> generate -> exec，返回 (run, 列号, 源码)。"""
    fields = {}
    for k in row:
        if isinstance(k, str) and k.isidentifier() and k.isascii():
            fields[k] = {"type": "any", "value": "$." + k}
    fields["v"] = {"type": out_type, "expr": expr}
    schema = {"name": "ex", "output": {"shape": "list", "single": False, "filter": False},
              "out": {"type": "object", "fields": fields}}
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<gen/examples>", "exec"), ns)  # noqa: S102 - 被测产物就是代码
    return ns, list(fields).index("v"), src


def observe(case):
    expr = case["expr"]
    expect = case["expect"] or ""
    if expect == "reject":
        try:
            compile_expr(expr)
        except Unsupported as exc:
            return Obs("unsupported", exc=exc)
        return Obs("not-rejected")
    if expect == "codegen-reject":
        compile_expr(expr)                      # jx 必须接受（这是"gap 而非语法错"的前提）
        try:
            build(expr, case["row"])
        except Unsupported as exc:
            return Obs("codegen-unsupported", exc=exc)
        return Obs("not-rejected")
    ns, ix, _src = build(expr, case["row"])
    try:
        out = ns["run"]([case["row"]])
    except Exception as exc:  # noqa: BLE001 - defect 例要求观测到真实异常
        return Obs("raise", exc=exc)
    res = out[0][ix]
    sentinel = ns.get("_U")
    if sentinel is not None and res is sentinel:
        return Obs("undefined", value=res)
    return Obs("value", value=res)


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
        assert obs.kind == "codegen-unsupported", f"{where}: 应被 codegen 拒绝，实际 {obs.kind}"
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


# ---------------------------------------------------------------------------
# 渲染 docs/jsonata_syntax.md
# ---------------------------------------------------------------------------
def _code(s):
    s = str(s).replace("\n", " ")
    if "`" in s or s.startswith(" ") or s.endswith(" "):
        return "`` " + s + " ``"
    return "`" + s + "`"


def _cell(s):
    return _code(s).replace("|", "\\|")


def _observed(item):
    _path, _meta, _i, case = item
    obs = observe(case)
    if obs.kind == "unsupported":
        return "Unsupported · " + str(obs.exc)
    if obs.kind == "codegen-unsupported":
        return "Unsupported · " + str(obs.exc)
    if obs.kind == "undefined":
        return "undefined（模块内 _U，其值 0）"
    if obs.kind == "raise":
        return f"{type(obs.exc).__name__}: {obs.exc}"
    if obs.kind == "not-rejected":
        return "**未被拒绝（测试会失败）**"
    return repr(obs.value)


def _states():
    counts = {"ok": 0, "gap": 0, "defect": 0, "fixed": 0}
    for _p, _m, _i, case in ALL:
        counts[case["state"]] = counts.get(case["state"], 0) + 1
    return counts


def render_doc():
    L = []
    add = L.append
    counts = _states()
    add("# DCG v2 · JSONata 语法样例与实测行为（可执行文档）")
    add("")
    add(f"> 任务：task-6（语法样例）＋ task-7 的 jx 侧（`py{{}}` 编译）｜ 生成源：`examples/jx/*.jx`"
        f"（{len(sample_files())} 个文件、{len(ALL)} 条例样）"
        f" ｜ **复现日期：{REPRO_DATE}** ｜ 生成器：`tests/test_jx_examples.py`")
    add("")
    add("这份文档的**每一条例样都被真实执行**：`dcg.generate(schema)` 生成 Python 源码 -> `exec` -> "
        "`run([row])`，断言的是运行期真实结果。文档由样例渲染而来，`tests/test_jx_examples.py` 断言"
        "磁盘文档与渲染结果**逐字节相等**，所以文档不可能与样例漂移。")
    add("")
    add("重新生成：")
    add("")
    add("```sh")
    add("python tests/test_jx_examples.py      # 重新渲染 docs/jsonata_syntax.md")
    add("python -m pytest tests/test_jx_examples.py -q   # 逐条例样执行 + 文档一致性")
    add("```")
    add("")
    add("## 时效说明（必读）")
    add("")
    add(f"以下分类是 **{REPRO_DATE}** 在 `dcg/` 当前实现上**实测**的结果，**不是**契约保证的语义。")
    add("")
    add("**已修复（回归保护，`state: fixed`）** —— 2026-10-17 的引擎修复把原先 21 条缺陷里的 **17 条修好了**，"
        "它们已按**正确值**重新冻结在 `14_fixed_regressions.jx`，并与官方 `jsonata-python` **逐例交叉复核一致**。"
        "最重要的一条：内部 undefined 哨兵不再是字面量 `0`（因此不再被 CPython 小整数缓存骗到），"
        "**合法的 `0` 与布尔 `false` 不再被当成 undefined**；`null` 字面量、`? :` 的假分支、`$number`、"
        "`in` 运算符也都恢复正常。**这些条目断言的是正确值：将来再退化就会变红。**")
    add("- 布尔字面量现在是真正的 Python `bool`（`True`/`False`），不再退化成 int `1`/`0`："
        "`$type(true)` = 'boolean'、`$string(true)` = 'true'（`01_literals.jx` 的两例带 `type: bool`）。")
    add("")
    add("**仍未修复（`state: defect`）** —— 只剩 4 条，全部在 `13_known_defects.jx`，"
        "那里**故意断言当前的错误行为**，修好后就会变红：反引号段里的 `math.*`（生成码没有 import）、"
        "`null` 做下标、`$round` 的 banker's rounding、`$count` 对标量返回 undefined。")
    add("")
    add("**未实现（`state: gap`）** —— jx 接受但 `dcg.generate` 抛 `Unsupported` 的，"
        "属于 codegen 端尚未实现（见附录 A），不是语法错。")
    add("")
    add("## 两条最容易踩的坑")
    add("")
    add("1. **下标里的裸名是字段引用，不是字符串键**：`$.a[b]` ≡ `$.a[$.b]`（`b` 需在 schema 中声明，"
        "否则 **build 期** `SchemaError` 并列出可用字段）；要按下标字符串取键请写 `$.a['b']`。"
        "同一条规则在顶层就成立：裸名 `hourB` = 已声明字段，`$.hourB` = 直接读行。")
    add("2. **`$` 变量不绑定**：jx 不实现 JSONata 的变量绑定，`$i` 退化为同名字段 `i`（M0/CAP 工作负载依赖这一点）。")
    add("")
    add("## `py{}` 多语句代码块（task-7）")
    add("")
    add("`py{ let t = $.a * 1.2; return int(t) }` 是**行内多语句 Python**，已**端到端可用**。"
        "`compile_expr` 产出 IR 节点 `(\"pyblock\", stmts_source, free_field_names, loc)`："
        "`let` 局部被改名为 `__jx_<块序号>_<名字>`（`__jx` 是保留前缀，用户不能自己取）；"
        "codegen 把每个块落成**模块级命名辅助函数** `def _pyN(f1, f2, ...)`"
        "（契约 §4.3 指定形状，**不是** lambda/IIFE，定义在 `run()` 之外、每行只调用一次）。")
    add("")
    add(f"IR 层节点形状、位置与全部拒绝项（含列号）的断言见 `tests/test_jx_py_block.py`，"
        f"可执行样例见 `15_py_block.jx`（复现日期 {REPRO_DATE}）。")
    add("")
    add("块内规则（契约 §3.7）：只能 `let NAME = EXPR;` + 结尾唯一一条 `return EXPR`；"
        "不引入循环/条件/异常语句（条件请用 Python 条件表达式 `a if c else b` 或 JSONata 的 `? :`）；"
        "不能 `import`/`def`/`class`/`lambda`；**不暴露裸字段名**（字段写 `$.名称`，块内局部必须先 `let`）；"
        "块内 `$.` 只支持单层字段或常量下标（与 JSONata 侧同一判据，契约 §6.1）。"
        "全部边界与确切消息、列号见 `15_py_block.jx` 与附录 A。")
    add("")
    add("**`py{}` 不是沙箱**：它执行任意 Python 语句，只做**语法**约束，不做安全隔离。"
        "它与反引号单表达式段互补：一条表达式用 `` `EXPR` ``（见 `10_backtick.jx`），"
        "需要中间变量时用 `py{ let ...; return ... }`。")
    add("")
    add("## 覆盖统计")
    add("")
    add("| 文件 | 主题 | 例数 |")
    add("|---|---|---|")
    for path in sample_files():
        meta, cases = parse_file(path)
        add(f"| [{meta['file']}](../examples/jx/{path.name}) | {meta['title']} | {len(cases)} |")
    add(f"| **合计** | 正常 {counts['ok']} ｜ 未实现(gap) {counts['gap']} ｜ 仍存缺陷(defect) {counts['defect']} "
        f"｜ 已修复(fixed) {counts['fixed']} | **{len(ALL)}** |")
    add("")
    add("---")
    add("")

    for path in sample_files():
        meta, cases = parse_file(path)
        add(f"### {meta['file']} — {meta['title']}")
        add("")
        for line in meta["desc"]:
            add(line)
            add("")
        add("```text")
        add(path.read_text(encoding="utf-8").rstrip("\n"))
        add("```")
        add("")
        add("| # | 表达式 | row | 实测 | 说明 |")
        add("|---|---|---|---|---|")
        for i, case in enumerate(cases, 1):
            item = (path, meta, i, case)
            note = case["note"] or ""
            if case["state"] == "gap":
                note = ("**未实现（codegen 拒绝）** " + note).strip()
            elif case["state"] == "defect":
                note = ("**已知缺陷（当前错误行为）** " + note).strip()
            elif case["state"] == "fixed":
                note = ("**已修复（回归保护）** " + note).strip()
            add(f"| {i} | {_cell(case['expr'])} | {_cell(case['row'] or '{}')} | {_observed(item)} | {note} |")
        add("")

    # 附录：不支持项汇总
    add("---")
    add("")
    add("## 附录 A：不支持项汇总（jx 编译期与 codegen 端到端）")
    add("")
    add("用户可用这一节判断「我的表达式能不能用」。`jx 拒绝` = `compile_expr` 抛 `Unsupported`（带列号）；"
        "`codegen 拒绝` = jx 接受但 `dcg.generate` 抛 `Unsupported`（**未实现**，不是语法错）。")
    add("")
    add("| 表达式 | 阶段 | 实测消息 |")
    add("|---|---|---|")
    for path, meta, i, case in ALL:
        if case["expect"] not in ("reject", "codegen-reject"):
            continue
        stage = "jx 拒绝" if case["expect"] == "reject" else "codegen 拒绝"
        add(f"| {_cell(case['expr'])} | {stage} | {_observed((path, meta, i, case))} |")
    add("")
    add("## 附录 B：仍未修复的缺陷汇总（运行期）")
    add("")
    add("| 表达式 | row | 实测（错误行为） | 正确语义 |")
    add("|---|---|---|---|")
    for path, meta, i, case in ALL:
        if case["state"] != "defect":
            continue
        note = (case["note"] or "").replace("**应当", "应当")
        add(f"| {_cell(case['expr'])} | {_cell(case['row'] or '{}')} | {_observed((path, meta, i, case))} | {note} |")
    add("")
    add("## 附录 C：已修复（回归保护）")
    add("")
    add("这些条目断言的是**正确值**（源：`14_fixed_regressions.jx`）。它们曾经是错误行为，"
        f"在 2026-10-17 的引擎修复之后重新冻结，并与官方 `jsonata-python` 逐例交叉复核一致"
        f"（复现日期 {REPRO_DATE}）。**再次退化就会变红。**")
    add("")
    add("| 表达式 | row | 实测（正确值） | 修复前 / 说明 |")
    add("|---|---|---|---|")
    for path, meta, i, case in ALL:
        if case["state"] != "fixed":
            continue
        note = (case["note"] or "").replace("原缺陷：", "")
        add(f"| {_cell(case['expr'])} | {_cell(case['row'] or '{}')} | {_observed((path, meta, i, case))} | {note} |")
    add("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 文档一致性
# ---------------------------------------------------------------------------
def test_doc_is_byte_identical_to_renderer():
    assert DOC_PATH.is_file(), f"缺少 {DOC_PATH}，先跑 python tests/test_jx_examples.py"
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    rendered = render_doc()
    if on_disk != rendered:
        i = next((k for k in range(min(len(on_disk), len(rendered))) if on_disk[k] != rendered[k]),
                 min(len(on_disk), len(rendered)))
        pytest.fail(f"docs/jsonata_syntax.md 与样例渲染结果不一致（首个差异在第 {i} 字符）：\n"
                    f"磁盘: {on_disk[i - 60:i + 60]!r}\n渲染: {rendered[i - 60:i + 60]!r}\n"
                    f"重新生成：python tests/test_jx_examples.py")


def test_doc_embeds_every_sample_file_verbatim():
    doc = render_doc()
    for path in sample_files():
        assert path.read_text(encoding="utf-8").rstrip("\n") in doc, f"{path.name} 未逐字进入文档"


def test_doc_lists_every_unsupported_message_with_location():
    doc = render_doc()
    for _path, _meta, _i, case in ALL:
        if case["expect"] == "codegen-reject":
            continue
        if case["expect"] != "reject":
            continue
        obs = observe(case)
        assert obs.kind == "unsupported"
        assert str(obs.exc) in doc, f"{case['expr']}: 确切消息未进文档"
        assert f"第 {obs.exc.loc[1]} 列" in doc


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


if __name__ == "__main__":
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(render_doc(), encoding="utf-8", newline="\n")
    print(f"wrote {DOC_PATH} ({len(ALL)} samples from {len(sample_files())} files)")
