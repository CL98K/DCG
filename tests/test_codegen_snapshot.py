"""Codegen snapshot + runtime single-source-of-truth tests (parser-dev, task-1 deliverable).

Two jobs, both guarding things no other test file guards:

A. `dcg.runtime` has exactly **one** implementation of every helper.
   `HELPERS` is the real callables the artifact runs; `SOURCE` must be a *derived*
   source view of it (`inspect.getsource`).  The pre-derivation hand-copied version
   had 1942 drifted results out of 16094 probed combinations (verifier-dev, m01158),
   including `_cn` with inverted `$contains` semantics and `_N` referencing an
   undefined `_NNN`.  Derivation makes drift structurally impossible; these tests
   keep the derivation honest and behaviourally equivalent.

B. `dcg.generate()` output is checked as a *snapshot contract*: the six zero-redundancy
   conditions of CONTRACT §4.2, the CAP/M0 reference shapes (char budget retired), and
   `type`-is-declaration-not-coercion (§4.2 rule 6).
"""
from __future__ import annotations

import ast
import builtins
import inspect
import itertools
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402

import workload as W  # noqa: E402
import codegen_rules as CR  # noqa: E402

# 产物字符数 —— **报告项，不是门禁**（用户 m00975：「那这个两个指标没啥用，我要的是
# 与手写的性能对比，不是大小对比」）。字符数只是"产物像手写代码"的代理指标，不保护
# 任何运行期性质。这里仍做上限断言，但上限取"明显异常"的量级，只用来抓"产物里冒出
# 一大段多余代码"这种真事故。
CAP = 540
M0 = 630


def _schema(fields, name="snap", shape="list", hook=None):
    """CONTRACT §2 的最小合法 schema（`output` 是必填块）。

    `hook` 是 `output.before`（数据级前置钩子的 JSONata 表达式）；`None` 表示
    不改写输入。
    """
    out = {"shape": shape}
    if hook is not None:
        out["before"] = hook
    return {"name": name,
            "output": out,
            "out": {"type": "object", "fields": fields}}


def _one_field(spec, name="snap", **kw):
    return _schema({"v": spec}, name=name, **kw)


# =====================================================================  A. SOURCE
class TestSingleSourceOfTruth:
    """`SOURCE` 必须是 `HELPERS` 的派生物，且逐名行为一致。"""

    def test_source_covers_exactly_helpers(self):
        assert set(rt.SOURCE) == set(rt.HELPERS), (
            "SOURCE 与 HELPERS 的键集必须完全相同（多一个=幽灵实现，少一个=缺失实现）")

    def test_source_is_derived_by_inspect_getsource(self):
        """逐名对拍：文本必须就是 `inspect.getsource(HELPERS[name])`。"""
        for name, fn in rt.HELPERS.items():
            assert rt.SOURCE[name] == inspect.getsource(fn), (
                f"{name}: SOURCE 不是 inspect.getsource(HELPERS[{name!r}]) 的结果 "
                f"—— 手抄漂移又回来了")

    def test_every_fragment_defines_its_own_name(self):
        """碎片必须自己定义同名函数：否则内联进产物就是一个不存在的名字。"""
        for name, text in rt.SOURCE.items():
            tree = ast.parse(text)
            defs = [n.name for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            assert defs == [name], f"{name}: 碎片定义了 {defs}"

    def test_math_dependency_set_is_derived(self):
        """`SOURCE_NEEDS_MATH` 必须等于「源码里真的引用了 math 的 helper」全集。

        注意**不要**把断言写成 `n for n, t in rt.SOURCE.items() if "math" in t`
        —— 那正是被测实现自己的派生式，等于拿实现证明实现：`_fl`/`_ce` 改成
        `_M.floor` 后派生式跟着一起缩小，测试照样绿（2026-10 就是这样漏掉的）。
        这里必须**独立**判定：扫源码里的两种拼法 `math.` / `_M.`，再按契约排除
        从不被内联的 `_pw`（`$power` 内联直接发 `a**b`，见 codegen `_inline_text`）。
        """
        def uses_math(text):
            return "math." in text or "_M." in text

        assert set(rt.SOURCE_NEEDS_MATH) == {n for n, t in rt.SOURCE.items()
                                             if uses_math(t)} - {"_pw"}
        # 集合里每个名字都必须**真的**引用 math，否则产物会多一条无用 import
        for name in rt.SOURCE_NEEDS_MATH:
            assert uses_math(rt.SOURCE[name]), \
                f"{name} 进了 SOURCE_NEEDS_MATH 却不引用 math"
        # 曾经的漂移：_fl/_ce/_sq 用 _M. 但依赖集合漏报 -> 内联即 NameError
        assert {"_fl", "_ce", "_sq"} == set(rt.SOURCE_NEEDS_MATH)

    def test_undefined_sentinel_is_a_unique_object(self):
        assert rt._U is rt.UNDEF
        assert rt.UNDEF is not None and rt.UNDEF is not False and rt.UNDEF is not 0
        assert not isinstance(rt.UNDEF, (int, float, bool, str, list, dict, tuple))

    def test_import_star_exports_every_helper(self):
        """产物用 `from dcg.runtime import*` 取 helper；下划线名必须在 `__all__` 里。"""
        missing = (set(rt.HELPERS) | {"_U", "UNDEF"}) - set(rt.__all__)
        assert not missing, f"`__all__` 缺 {sorted(missing)}，产物会 NameError"


#: 对拍取值电池：覆盖 undefined/null/bool/数值边界/字符串/容器。
_BATTERY = (rt.UNDEF, None, True, False, 0, 1, -1, 2.5, 3.0, "", "ab", "ABC",
            [], [1, 2], [1, "a"], {}, {"a": 1}, {"a": [1, 2]})


def _call(fn, args):
    """调用并归一化结果：异常只比类型，`UNDEF` 比身份（它是单例）。"""
    try:
        v = fn(*args)
    except Exception as exc:  # noqa: BLE001 - 对拍就是要比异常
        return ("raise", type(exc).__name__)
    return ("value", "<UNDEF>" if v is rt.UNDEF else v)


@pytest.fixture(scope="module")
def exec_ns():
    """整个 runtime 模块命名空间打底（`_cmp_ok`/`_NN` 这类非 HELPERS 依赖有着落），
    再用 exec 出来的 SOURCE 版本**覆盖**同名 helper。"""
    space = dict(vars(rt))
    space.update({"_U": rt.UNDEF, "UNDEF": rt.UNDEF})
    for name, text in rt.SOURCE.items():
        exec(compile(text, f"<SOURCE:{name}>", "exec"), space)
    return space


class TestHelperBehaviourParity:
    """把每个 SOURCE 碎片 exec 出来，与真实 HELPERS 逐组取值对拍。"""

    @pytest.mark.parametrize("name", sorted(rt.HELPERS))
    def test_fragment_matches_callable(self, name, exec_ns):
        if name in ("_nw", "_ms", "_rn", "_sh"):
            pytest.skip("非确定 helper：结果不可复现（DCG 接口实现细节文档 §3.5「C 类」/ §3.7）")
        fn = rt.HELPERS[name]
        derived = exec_ns[name]
        params = [p for p in inspect.signature(fn).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        arity = len(params)
        if arity == 0:
            assert _call(derived, ()) == _call(fn, ()), f"{name}() 行为不一致"
            return
        if arity >= 3:  # pragma: no cover - 多参数 helper 只抽样
            combos = itertools.islice(itertools.product(_BATTERY, repeat=arity), 400)
        else:
            combos = itertools.product(_BATTERY, repeat=arity)
        for args in combos:
            got, want = _call(derived, args), _call(fn, args)
            assert got == want, (
                f"{name}{args}: SOURCE 派生版 {got!r} != HELPERS {want!r}（源码漂移）")


# ============================================================  B. 产物快照契约
def _no_comments(src):
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def _live_code(src):
    """去掉注释与缩进空白，只留活代码（快照断言用）。"""
    return "\n".join(l.strip() for l in _no_comments(src).splitlines() if l.strip())


class TestZeroRedundancy:
    """CONTRACT §4.2 六条零冗余 + CAP/M0 形态 —— 逐条断言（字符预算已取消）。"""

    BASES = (("V2_CAP", CAP), ("V2_SCHEMA", M0), ("V2_M0", M0))

    def _schemas(self):
        out = []
        for label, cap in self.BASES:
            sch = getattr(W, label, None)
            if sch is not None:
                out.append((label, sch, cap))
        return out

    def test_char_size_is_reported_not_gated(self):
        """字符数只报告，不作为达成条件（用户 m00975）。

        这里把实测字符数打进报告，
        上限只拦"产物里冒出成倍多余代码"这种真事故。
        """
        report = []
        for label, sch, cap in self._schemas():
            src = dcg.generate(sch)
            report.append(f"{label}={len(src)}")
            assert len(src) <= cap, (
                f"{label}: 产物 {len(src)} 字符，超出异常阈值 {cap}"
                f"（§7 预算已降级为报告项，此阈值只抓成倍膨胀）\n{src}")
        print("CHAR_REPORT: " + ", ".join(report))

    def test_rule1_row_prefix_hoisted_once(self):
        """① 行首同一前缀只取一次（`R[5]['a']` 不得出现两遍）。"""
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            prefixes = []
            for chain in re.finditer(r"R((?:\[[^\[\]]*\])+)", live):
                parts = re.findall(r"\[[^\[\]]*\]", chain.group(1))
                for i in range(len(parts)):
                    prefixes.append("R" + "".join(parts[:i + 1]))
            dupes = {p for p in prefixes if prefixes.count(p) > 1}
            assert not dupes, f"{label}: 行前缀重复取值 {sorted(dupes)}"

    def test_rule2_no_identity_or_return_only_temporaries(self):
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            assert not re.search(r"^\s*(\w+)\s*=\s*\1\s*$", live, re.M), \
                f"{label}: 恒等赋值 x = x"
            assert not re.search(r"^\s*\w+\s*=\s*None\s*$", live, re.M), \
                f"{label}: `x = None` 空桩/中转"

    def test_rule3_no_passthrough_buffer(self):
        """③ 不得有 buffer 中转；唯一豁免是 `_c=_o.append` 方法绑定。

        §4.2 规则 3 原文禁「append 别名」，本意是禁**多余中转 buffer**
        （`_buf={'doc':[]}; _doc=_buf['doc']` 这类）。把**唯一的容器方法**
        `_o.append` 提到循环外只绑定一次不是中转，而是 method hoisting，
        用户 m00828 明确要求（"append = doc.append 需要绑定"）。
        因此这里逐行判定：允许形如 `_c=_o.append` 的**方法**绑定，
        其余任何 `x = y.append` / buffer 命名仍然硬禁。
        """
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            assert "buffer" not in live, f"{label}: 出现 buffer 命名"
            for m in re.finditer(r"^\s*(\w+)\s*=\s*(\w+)\.append\s*$", live, re.M):
                lhs, rhs = m.group(1), m.group(2)
                assert rhs == "_o", \
                    f"{label}: `{lhs}={rhs}.append` 别名（§4.2 规则 3 明禁，"
                assert lhs == "_c", \
                    f"{label}: 方法绑定必须叫 `_c`（§4.2 规则 3 豁免条款），" \
                    f"实际是 `{lhs}`"
                assert live.count(f"{lhs}(") >= 1, \
                    f"{label}: 绑定了 `{lhs}` 却没有调用，是纯中转"

    def test_rule4_output_shape_fixed_at_compile_time(self):
        """⑤ 形状编译期定型：活代码里不得出现运行期形状分支。"""
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            for bad in ("shape", "itertools", "chain(", ".items()", "isinstance("):
                assert bad not in live, f"{label}: 活代码含运行期形状判断 `{bad}`"

    def test_rule5_no_per_row_try(self):
        for label, sch, cap in self._schemas():
            for ln in _live_code(dcg.generate(sch)).splitlines():
                assert not ln.startswith("try"), f"{label}: 行级 try/except"

    def test_rule6_no_runtime_coercion_or_dispatch(self):
        """④/§4.2 规则 6：`type` 是声明不是转换 —— 不得凭空转换或 isinstance 分派。"""
        for label, sch, cap in self._schemas():
            src = dcg.generate(sch)
            assert "isinstance(" not in src, f"{label}: isinstance 分派链"
            assert "int(" not in src and "float(" not in src, \
                f"{label}: 凭空类型转换（type 是声明，§4.2 规则 6）"

    def test_rule7_guard_count_equals_optional_fields(self):
        guard = re.compile(r"\bis\s+not\s+(?:_U|UNDEF)\b|\bis\s+_U\b")

        def n_optional(node):
            if isinstance(node, dict):
                return ((1 if node.get("optional") else 0)
                        + sum(n_optional(v) for v in node.values()))
            if isinstance(node, list):
                return sum(n_optional(v) for v in node)
            return 0

        for label, sch, cap in self._schemas():
            n_opt, n_guard = n_optional(sch), len(guard.findall(dcg.generate(sch)))
            assert n_guard == n_opt, (
                f"{label}: undefined 守卫 {n_guard} 处，optional 字段 {n_opt} 个"
                f"（§4.2 规则 7：只有 optional 才付 undefined 语义的钱）")

    def test_rule8_deterministic_and_reproducible(self):
        for label, sch, cap in self._schemas():
            a, b = dcg.generate(sch), dcg.generate(sch)
            assert a == b, f"{label}: 两次 generate 字节不同"
            assert "random" not in a, f"{label}: 产物含 random"
            assert not re.search(r"_[a-z]{8,}", a), f"{label}: 随机/长后缀命名"

    def test_rule9_cython_declares_every_local(self):
        for label, sch, cap in self._schemas():
            py, cy = dcg.generate(sch), dcg.generate(sch, cython=True)
            assert "cdef" in cy, f"{label}: cython 产物无 cdef"
            # 参数是 `_d`（不是局部）；其余每个局部都必须有 cdef 声明
            locals_ = set(re.findall(r"^\s*(\w+)\s*=", py, re.M)) - {"_d"}
            for name in sorted(locals_):
                assert f"cdef object {name}" in cy, f"{label}: 缺 cdef {name}"

    def test_compiles_exactly_once_without_none_stub(self):
        """规则 ⑥（B2 收窄口径）：**产物主体**（含 `def run(` 的那段）
        恰好被 compile 一次，且不含 `x = None` 空桩。

        反引号段/py{} 块的语法校验属 build 期成本，不计入本条判定。
        """
        for label, sch, cap in self._schemas():
            real, calls = builtins.compile, []

            def spy(src, *a, **k):
                calls.append(src)
                return real(src, *a, **k)

            builtins.compile = spy
            try:
                src = dcg.generate(sch)
            finally:
                builtins.compile = real
            product = [c for c in calls if isinstance(c, str) and "def run(" in c]
            assert len(product) == 1, \
                f"{label}: 产物主体 compile() {len(product)} 次（应为 1）"
            assert product[0] == src
            assert "= None" not in src, f"{label}: 出现空桩"

    def test_artifact_parses_and_has_no_assignment_in_lambda(self):
        for label, sch, cap in self._schemas():
            src = dcg.generate(sch)
            tree = ast.parse(src)  # 必须是合法 Python
            for node in ast.walk(tree):
                if isinstance(node, ast.Lambda):
                    for sub in ast.walk(node):
                        assert not isinstance(
                            sub, (ast.Assign, ast.AugAssign, ast.AnnAssign,
                                  ast.NamedExpr)), \
                            f"{label}: lambda 体内有赋值（§4.2 规则 ① 要求语句形式）"

    def test_row_is_a_list_via_cpend_not_extend(self):
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            # 绑定形式 `_c=_o.append` + `_c([...])`：方法查找提到循环外，
            # 每行只做一次局部名调用（Lead m00828 要求）。
            assert "_c=_o.append" in live, f"{label}: 未把 `_o.append` 绑定成局部名"
            assert "_c([" in live, f"{label}: 行容器不是 `_c([...])`"
            assert "_o.append([" not in live, \
                f"{label}: 循环内仍是 `_o.append(...)`，绑定没生效"
            assert "_o+=" not in live, f"{label}: `+=` 是 extend，会把行拍平"

    def test_cpend_binding_does_not_change_output(self):
        """绑定的语义中立性：同一 schema、绑定版与逐行 `_o.append` 版逐值相同。

        直接按文本把绑定拆掉（`_c=_o.append` 行删掉、`_c(` 还原成
        `_o.append(`）重建一个"未绑定"产物，与真实产物对同一批行比结果。
        输入形状跟 schema 的 `input.source` 路径走（`W.v2_input`）。
        """
        rows = list(W.make_rows(50))
        for label, sch in (("CAP", W.V2_CAP), ("V2_SCHEMA", W.V2_SCHEMA),
                           ("V2_M0", W.V2_M0)):
            src = dcg.generate(sch)
            plain = "\n".join(
                ln for ln in src.splitlines() if "_c=_o.append" not in ln)
            plain = plain.replace("_c(", "_o.append(")
            ns_a, ns_b = {}, {}
            exec(compile(src, "<bound>", "exec"), ns_a)
            exec(compile(plain, "<plain>", "exec"), ns_b)
            feed = W.v2_input(rows, sch)
            assert ns_a["run"](feed) == ns_b["run"](feed), \
                f"{label}: 绑定 append 改变了输出"

    def test_before_hook_follows_the_declaration(self):
        """数据级前置钩子：写了 `output.before` 才在行循环之前挂一次调用。

        「丢弃 falsy 行」这类逐行判断，现在靠 `before` 返回一份新数据来实现。
        """
        rows = list(W.make_rows(4))
        on = json.loads(json.dumps(W.V2_CAP))
        on["output"] = dict(on["output"], before="= `[r for r in $ if r]`")
        off = json.loads(json.dumps(W.V2_CAP))

        def runner(src):
            ns = {}
            exec(compile(src, "<snapshot-hook>", "exec"), ns)
            return ns["run"]

        src_on, src_off = dcg.generate(on), dcg.generate(off)
        # 钩子在 `_o=[]` 之前、只求值一次（不逐行）
        pre = src_on.split("_o=[]")[0]
        assert "(lambda D:" in pre, f"before 钩子应挂在行循环之前:\n{src_on}"
        assert "_d=_hb((lambda D:" in pre, \
            f"before 钩子应改写输入数据（返回的数据替换输入，并经 _hb 守卫）:\n{src_on}"
        # 不写钩子不得生成任何额外调用（不为未声明的选项付费）
        assert "(lambda D:" not in src_off, src_off
        assert "_d=_hb(" not in src_off, src_off

        fed_on = [rows[0], None, {}, rows[1]]
        assert len(runner(src_on)(fed_on)) == 2, "before 过滤后应只剩 2 行"
        assert len(runner(src_off)(rows)) == len(rows), \
            "不写 before 应保留全部行"


class TestGeneratedShapes:
    """形状契约（§4.1）：编译期定型，列式不得运行期物化。"""

    def test_list_shape_returns_list_of_lists(self):
        run, _ = W.load_v2(W.V2_SCHEMA)
        res = run(W.make_rows(5))
        assert isinstance(res, list) and len(res) == 5
        assert all(isinstance(r, list) for r in res)

    def test_dict_shape_is_columnar(self):
        base = W.V2_SCHEMA
        fields = list(base["out"]["fields"])
        sch = dict(base, output=dict(base["output"], shape="dict"))
        run, _ = W.load_v2(sch)
        res = run(W.make_rows(5))
        assert isinstance(res, dict) and list(res) == fields
        assert all(len(v) == 5 for v in res.values())

    def test_index_shape_uses_int_row_keys(self):
        base = W.V2_SCHEMA
        sch = dict(base, output=dict(base["output"], shape="index"))
        run, _ = W.load_v2(sch)
        res = run(W.make_rows(5))
        assert list(res) == [0, 1, 2, 3, 4]
        assert all(isinstance(v, list) for v in res.values())


class TestOutputAggregate:
    """`shape:"dict"` 下 `aggregate` 的列容器语义（§4.1）。

    `object` 字段在列式输出里有两种用法，结构上无从分辨，由 `aggregate` 显式
    声明：标了就是「每个子键各聚合成一列」（可逐层嵌套），不标就是「该键下是
    一列具名行」。
    """

    @staticmethod
    def _sch(fields):
        # 加一个标量列：单字段会走 `_flat_row` 的裸存快路径，测不到聚合
        allf = {"n": {"type": "any", "value": "$.0"}}
        allf.update(fields)
        return {"name": "agg", "input": {},
                "output": {"shape": "dict"},
                "out": {"type": "object", "fields": allf}}

    @staticmethod
    def _run(schema, rows):
        ns = {}
        exec(compile(dcg.generate(schema), "<agg>", "exec"), ns)
        return ns["run"](rows)

    def test_aggregate_object_becomes_columns(self):
        sch = self._sch({
            "col": {"type": "object", "aggregate": True, "fields": {
                "a": {"type": "any", "value": "$.1"},
            }},
        })
        res = self._run(sch, [[1, 10], [2, 20]])
        assert res == {"n": [1, 2], "col": {"a": [10, 20]}}

    def test_aggregate_nests_recursively(self):
        sch = self._sch({
            "col": {"type": "object", "aggregate": True, "fields": {
                "a": {"type": "any", "value": "$.1"},
                "deep": {"type": "object", "aggregate": True, "fields": {
                    "b": {"type": "any", "value": "$.2"},
                }},
            }},
        })
        res = self._run(sch, [[0, 10, 100], [0, 20, 200]])
        assert res["col"] == {"a": [10, 20], "deep": {"b": [100, 200]}}

    def test_object_without_aggregate_stays_a_row_list(self):
        sch = self._sch({
            "kid": {"type": "object", "fields": {
                "c": {"type": "any", "value": "$.3"},
            }},
        })
        res = self._run(sch, [[0, 0, 0, 1000], [0, 0, 0, 2000]])
        # 不标 aggregate：该键下是一列**具名行**，不是子键列
        assert res["kid"] == [{"c": 1000}, {"c": 2000}]

    def test_aggregate_and_row_list_can_coexist(self):
        sch = self._sch({
            "col": {"type": "object", "aggregate": True, "fields": {
                "a": {"type": "any", "value": "$.1"},
                "kid": {"type": "object", "fields": {
                    "c": {"type": "any", "value": "$.3"},
                }},
            }},
        })
        res = self._run(sch, [[0, 10, 0, 1000], [0, 20, 0, 2000]])
        assert res["col"] == {"a": [10, 20],
                              "kid": [{"c": 1000}, {"c": 2000}]}

    def test_aggregate_requires_object_with_fields(self):
        for bad in (
            {"type": "any", "value": "$.0", "aggregate": True},
            {"type": "object", "value": "$.0", "aggregate": True},
            {"type": "list", "fields": {"a": {"type": "any", "value": "$.0"}},
             "aggregate": True},
        ):
            with pytest.raises(dcg.SchemaError):
                dcg.validate(self._sch({"x": bad}))

    def test_shared_parent_column_is_hoisted_once(self):
        """父列被 >=2 个子键复用时提取成短名，不在 return 里原地展开多遍。"""
        sch = self._sch({
            "col": {"type": "object", "aggregate": True, "fields": {
                "a": {"type": "any", "value": "$.1"},
                "b": {"type": "any", "value": "$.1"},
                "deep": {"type": "object", "aggregate": True, "fields": {
                    "c": {"type": "any", "value": "$.2"},
                    "d": {"type": "any", "value": "$.2"},
                }},
            }},
        })
        src = dcg.generate(sch)
        ret = src.rsplit(" return ", 1)[1]
        # 父列那串 `[r[1]for r in _o]` 在 return 里出现 0 次（已提出去）
        assert "[r[1]for r in _o]" not in ret
        assert "\n _a=" in src
        res = self._run(sch, [[0, 10, 100], [0, 20, 200]])
        assert res["col"] == {"a": [10, 20], "b": [10, 20],
                              "deep": {"c": [100, 200], "d": [100, 200]}}

    def test_single_use_column_is_not_hoisted(self):
        """只被一个子键用的列不提取——原地写更短。"""
        sch = self._sch({
            "col": {"type": "object", "aggregate": True, "fields": {
                "only": {"type": "any", "value": "$.1"},
            }},
        })
        src = dcg.generate(sch)
        assert "_a=" not in src
        assert self._run(sch, [[0, 10], [0, 20]])["col"] == {"only": [10, 20]}

    def test_hoisting_does_not_change_result_for_three_levels(self):
        """三层 aggregate：中间两层各被多路复用，结果仍逐值正确。"""
        sch = self._sch({
            "l1": {"type": "object", "aggregate": True, "fields": {
                "x": {"type": "any", "value": "$.1"},
                "y": {"type": "any", "value": "$.1"},
                "l2": {"type": "object", "aggregate": True, "fields": {
                    "p": {"type": "any", "value": "$.2"},
                    "q": {"type": "any", "value": "$.2"},
                    "l3": {"type": "object", "aggregate": True, "fields": {
                        "rr": {"type": "any", "value": "$.3"},
                        "ss": {"type": "any", "value": "$.3"},
                    }},
                }},
            }},
        })
        res = self._run(sch, [[0, 10, 100, 1000], [0, 20, 200, 2000]])
        assert res["l1"] == {
            "x": [10, 20], "y": [10, 20],
            "l2": {"p": [100, 200], "q": [100, 200],
                   "l3": {"rr": [1000, 2000], "ss": [1000, 2000]}},
        }


class TestTypeIsDeclarationNotCoercion:
    """§4.2 规则 6：`type` 只用于声明/校验（cython 的 cdef），绝不插运行期转换。"""

    @pytest.mark.parametrize("decl", ["int64", "int32", "float64", "str", "bool"])
    def test_declared_type_adds_no_conversion(self, decl):
        src = dcg.generate(_one_field({"type": decl, "value": "$.v"},
                                      name="t_" + decl))
        assert "int(" not in src and "float(" not in src, (
            f"type={decl} 凭空插入了运行期转换：\n{src}")
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        # 声明不转换：int 进来还是 int，字符串进来还是字符串
        assert ns["run"]([{"v": 7}]) == [[7]]
        assert ns["run"]([{"v": "7"}]) == [["7"]]


class TestBuildSnapshot:
    def test_build_is_byte_reproducible_and_sha1_stamped(self, tmp_path):
        sch = _schema({"v": {"type": "any", "value": "$.v"}}, name="snapbuild")
        m1 = dcg.build(sch, str(tmp_path))
        first = (tmp_path / "gen" / "snapbuild.py").read_text(encoding="utf-8")
        # 同一 name + 同一 schema → 内容逐字节相同，`build` 不视为「schema 已变更」，
        # 因此无需 overwrite=True 也能重跑（overwrite 守卫只管**内容不同**的覆盖）。
        m2 = dcg.build(sch, str(tmp_path))
        second = (tmp_path / "gen" / "snapbuild.py").read_text(encoding="utf-8")
        assert first == second
        assert m1 == m2
        assert m1["schema_sha1"] == dcg.codegen.schema_sha1(dcg.validate(sch))

    def test_gen_name_is_deterministic_and_human_readable(self):
        sch = _schema({"v": {"type": "any", "value": "$.v"}}, name="readable_name")
        got = dcg.gen_name(dcg.validate(sch))
        assert got == "readable_name" == dcg.gen_name(dcg.validate(sch))
        assert "gen/" not in got and "\\" not in got


# =====================================================  H. py{...} 代码块（任务 7）
def _py_schema(expr, extra=None, name="pysnap"):
    fields = {"a": {"type": "int64", "value": "$.a"}}
    fields.update(extra or {})
    fields["v"] = {"type": "any", "expr": expr}
    return _schema(fields, name=name)


def _run_of(src):
    ns = {}
    exec(compile(src, "<pysnap>", "exec"), ns)
    return ns["run"]


class TestPyBlock:
    """`py{ ... }` 被编译成 `run` 之外的**模块级** helper（任务 7 契约）。"""

    def test_block_builds_and_runs(self):
        src = dcg.generate(_py_schema("py{ let t = $.a * 2; return t + 1 }"))
        # `if R:` 行守卫仍在（空行在字段求值前整行丢掉），这里只喂有效行
        assert _run_of(src)([{"a": 1}, {"a": 5}]) == [[1, 3], [5, 11]]

    def test_helper_is_module_level_and_defined_exactly_once(self):
        src = dcg.generate(_py_schema("py{ let t = $.a * 2; return t + 1 }"))
        tree = ast.parse(src)
        defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        assert defs.count("run") == 1
        py = [n for n in defs if n.startswith("_py")]
        assert py == ["_py0"], f"helper 命名/去重不对: {defs}"
        # `run` 必须在 helper 之后定义，且 helper 不接收行容器参数（`_d`）
        names = [n.name if isinstance(n, ast.FunctionDef) else None for n in tree.body]
        assert names.index("_py0") < names.index("run")
        helper = next(n for n in tree.body
                      if isinstance(n, ast.FunctionDef) and n.name == "_py0")
        params = [a.arg for a in helper.args.args]
        assert params == ["_p0"], params
        assert "_d" not in params and not isinstance(helper.args.vararg, ast.arg)

    def test_identical_blocks_share_one_helper(self):
        src = dcg.generate(_py_schema(
            "py{ let t = $.a * 2; return t + 1 }",
            extra={"w": {"type": "any",
                         "expr": "py{ let t = $.a * 2; return t + 1 }"}}))
        tree = ast.parse(src)
        assert src.count("def _py0(") == 1
        assert src.count("_py0(") == 3           # 1 次定义 + 2 次调用
        assert not [n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_py1"]

    def test_free_chains_are_all_passed_to_the_helper(self):
        sch = _py_schema("py{ let t = $.a + $.b; return t }",
                         extra={"b": {"type": "int64", "value": "$.b"}})
        src = dcg.generate(sch)
        helper = next(n for n in ast.parse(src).body
                      if isinstance(n, ast.FunctionDef) and n.name == "_py0")
        assert len(helper.args.args) == 2, "两个 free 链必须各占一个形参"
        assert _run_of(src)([{"a": 4, "b": 9}]) == [[4, 9, 13]]

    def test_positional_free_chains_are_raw_subscripts(self):
        sch = _schema({"v": {"type": "any",
                             "expr": "py{ let t = $.5.a.1 + $.5.a.2; return t }"}},
                      name="pysnap_pos")
        src = dcg.generate(sch)
        rows = [("x", 0, 0, 0, 0, {"a": [11, 22, 33]})]
        assert _run_of(src)(rows) == [[55]]

    def test_undeclared_free_name_raises_schema_error_with_available_fields(self):
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_py_schema("py{ let t = $.missing + 1; return t }"))
        msg = str(ei.value)
        assert "missing" in msg and "'a'" in msg and "'v'" in msg, msg

    def test_missing_return_is_unsupported_with_real_position(self):
        with pytest.raises(dcg.Unsupported) as ei:
            dcg.generate(_py_schema("py{\n let t = $.a + 1\n }"))
        msg = str(ei.value)
        assert "return" in msg and "第 3 行第 2 列" in msg, msg

    def test_block_body_is_emitted_verbatim_and_parses(self):
        src = dcg.generate(_py_schema("py{ let t = $.a; return t }"))
        assert "let" not in src                      # JSONata 语法不留痕
        assert "_.=" not in src and "?=" not in src
        ast.parse(src)                               # 整产物仍是合法 Python


# ============================================  I. SchemaError 的 path 精度（可定位）
class TestErrorPathPrecision:
    """`SchemaError.path` 必须指到**具体字段的具体键**，不是笼统的 `out.fields`。"""

    def test_undeclared_name_in_expr_field(self):
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_py_schema("py{ let t = $.missing + 1; return t }"))
        assert ei.value.path == "out.fields.v.expr"

    def test_undeclared_bare_name_in_jsonata_expr(self):
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_py_schema("missing + 1"))
        assert ei.value.path == "out.fields.v.expr"

    def test_forward_reference_is_resolved_by_dependency(self):
        """前向引用合法：计算按依赖序，输出仍按声明序（§2.5）。"""
        sch = _schema({"v": {"type": "any", "expr": "later + 1"},
                       "later": {"type": "int64", "value": "$.a"}})
        src = dcg.generate(sch)
        run = _run_of(src)
        assert run([{"a": 3}]) == [[4, 3]]

    def test_dependency_cycle_is_reported_as_schema_error(self):
        sch = _schema({"v": {"type": "any", "expr": "later + 1"},
                       "later": {"type": "any", "expr": "v + 1"}})
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(sch)
        assert ei.value.path == "out.fields"
        assert "成环" in str(ei.value)

    def test_nested_field_path_matches_schema_convention(self):
        sch = _schema({"a": {"type": "int64", "value": "$.a"},
                       "n": {"type": "object", "fields": {
                           "x": {"type": "any", "expr": "missing + 1"}}}})
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(sch)
        assert ei.value.path == "out.fields.n.fields.x.expr"

    def test_list_items_is_rejected_at_schema_level(self):
        """`items` 不存在：错误 path 停在字段本身，报「未知键: items」。"""
        sch = _schema({"a": {"type": "int64", "value": "$.a"},
                       "v": {"type": "list", "items": {
                           "type": "any", "expr": "missing + 1"}}})
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(sch)
        assert ei.value.path == "out.fields.v"
        assert "未知键: items" in str(ei.value)

    def test_row_is_rejected_at_schema_level(self):
        """`row` 只是一个不存在的键：项目未发布，v2 不为任何旧写法留后门。

        用户指令 (2026-10-22, m01313)：所有文档与代码里的旧格式内容全部删掉，
        既没正式发布就没有兼容负担。故 `FIELD_KEYS` 不含 `"row"`，也不再有
        专属「已移除」提示——它就是普通未知键。
        """
        for bad in (
            {"v": {"row": "list", "fields": {
                "x": {"type": "int64", "value": "$.a"}}}},
            {"v": {"type": "object", "row": "list", "fields": {
                "x": {"type": "int64", "value": "$.a"}}}},
            {"v": {"type": "object", "row": "object", "fields": {
                "x": {"type": "int64", "value": "$.a"}}}},
            {"v": {"type": "list", "row": "object", "fields": {}}},
        ):
            with pytest.raises(dcg.SchemaError) as ei:
                dcg.generate(_schema(bad))
            assert ei.value.path in ("out.fields.v", "out.fields.v.fields")
            assert "未知键: row" in ei.value.msg

    def test_row_removal_does_not_break_canonical_forms(self):
        """对照：删除别名后，两种规范写法都必须照常工作（不能误伤）。"""
        pos = dcg.generate(_schema({"v": {"type": "list", "fields": {
            "x": {"type": "int64", "value": "$.a"}}}}))
        ns = {}
        exec(compile(pos, "<pos>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[[7]]]

        obj = dcg.generate(_schema({"v": {"type": "object", "fields": {
            "x": {"type": "int64", "value": "$.a"}}}}))
        ns = {}
        exec(compile(obj, "<obj>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]

    def test_reserved_field_name_is_located(self):
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_schema({"_ad": {"type": "int64", "value": "$.a"}}))
        assert ei.value.path == "out.fields._ad"


# ================================================  J. 嵌套结构（对象 / 数组）取值
class TestNestedContainers:
    """契约 §2：`fields` 是**单个** FieldSpec；数组用 value/expr，嵌套用 fields。"""

    def test_nested_object_is_a_dict(self):
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "value": "$.a"}}}}))
        # 全字段可证非 undefined → task-10 起直接发字典字面量，不过 `_ob` helper
        assert "_ob(" not in src and "{'x':R['a']}" in src
        ns = {}
        exec(compile(src, "<n>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]

    def test_nested_object_literal_is_scoped_to_its_own_field(self):
        """上一条的 `{'x':...}` 只在**该嵌套字段**的取值表达式里出现，不是全局无 `_ob`。

        收紧断言：旧写法 `"{'x':R['a']}" in src` 对「同一份产物里既有字面量又有 `_ob`」
        这种情况照样成立，分不清是哪个字段走了快路径。

        读法口径（用户裁决 2026-10-25，DEFECT-021）：`type:"any"` 也是 required，
        发裸读 `R['b']`；但它的**值仍可能是 undefined**，所以照样退回 `_ob` 包裹。
        两件事互不影响 —— 断言要同时盯住。
        """
        src = dcg.generate(_schema({
            "fast": {"type": "object", "fields": {
                "x": {"type": "int64", "value": "$.a"}}},
            "slow": {"type": "object", "fields": {
                "y": {"type": "any", "value": "$.b"}}},
        }))
        assert "{'x':R['a']}" in src
        assert "_ob([('y',R['b'])])" in src

    def test_nested_object_falls_back_to_ob_when_value_may_be_undefined(self):
        """回退路径覆盖：`type:"any"` 取值为可 undefined → 仍走 `_ob([...])`。

        `any` 在**读法**上是 required（裸读 `R['a']`、缺键 KeyError），但在**值**上
        仍可能是哨兵，于是 helper 包裹不能省。"""
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "any", "value": "$.a"}}}}))
        assert "_ob([" in src and "R['a']" in src
        ns = {}
        exec(compile(src, "<no>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]
        with pytest.raises(KeyError):
            ns["run"]([{"b": 1}])

    def test_nested_list_row_is_a_one_element_list(self):
        """位置行（type:"list" + fields）产出一层列表包裹。

        全字段可证非 undefined → 直接发列表字面量（task-10）；只要有一个字段可能
        undefined 就回退到 `_rl([...])`，由它把 undefined 落成 None 保住位置。
        """
        src = dcg.generate(_schema(
            {"v": {"type": "list", "fields": {"x": {"type": "int64", "value": "$.a"}}}}))
        assert "_rl([" not in src and "[R['a']]" in src
        ns = {}
        exec(compile(src, "<l>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[[7]]]

    def test_nested_list_row_falls_back_to_rl_when_element_may_be_undefined(self):
        """回退路径覆盖：`type:"any"` 取值为可 undefined → 仍走 `_rl([...])` 保位。

        同 `test_nested_object_falls_back_to_ob_when_value_may_be_undefined`：
        `any` 读法 required（裸读），但值可能是哨兵，`_rl` 把哨兵落成 None 保位。
        """
        src = dcg.generate(_schema(
            {"v": {"type": "list", "fields": {"x": {"type": "any", "value": "$.a"}}}}))
        assert "_rl([" in src and "R['a']" in src
        ns = {}
        exec(compile(src, "<lr>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[[7]]]
        # 缺键是真实的 required 读错误
        with pytest.raises(KeyError):
            ns["run"]([{"b": 1}])

    def test_nested_list_of_objects_composes(self):
        src = dcg.generate(_schema(
            {"v": {"type": "list", "fields": {
                "o": {"type": "object", "fields": {
                    "x": {"type": "int64", "value": "$.a"},
                    "y": {"type": "int64", "value": "$.b"}}}}}}))
        ns = {}
        exec(compile(src, "<lo>", "exec"), ns)
        assert ns["run"]([{"a": 7, "b": 8}]) == [[[{"x": 7, "y": 8}]]]


class TestNestedOptionalValueSemantics:
    """§2.3：`optional:true` 的字段缺键必须得到 None，**在嵌套作用域里同样成立**。

    回归背景（Lead, 2026-10-21）：`raw_mode()` 在 optional 且无 default 时已返回
    None，但 `field_value()` 的 `value` 分支只看 `type`——`type` 声明了具体类型的
    optional 字段仍走 `emit_path()`，发的是裸读 `R['x']`。结果是
    `{"a":{"type":"int64","optional":true,"value":"$.x"}}` 缺键时抛
    `KeyError: 'x'` 而不是给 None，与 §2.3 的承诺直接矛盾。

    修法有**两道防线，缺一不可**：
      1. `field_value()` 的 `value` 分支：optional 且无 default → 走 `safe_text()`
         （发 `_ix_get` 链，缺键得哨兵）。只改这里不够——哨兵会被裸字面量写进产物。
      2. `_never_undef()` 的 `value` 分支：optional 且无 default → 返回 False
         （必须包 `_ob`/`_rl` 把哨兵落成丢键/None）。只改这里也不够——表达式本身
         就是裸读，包起来照样在求参时抛。
    """

    def test_named_object_optional_value_gives_none_not_keyerror(self):
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "optional": True, "value": "$.a"}}}}))
        # 两道防线都要在产物里留痕：safe 读 + helper 包裹。
        # safe 读可以是 `dict.get(k, _U)` 快路径（类型可判定的 optional 专用，
        # 见 `field_value` 的三选一），也可以是 `_ix_get` 链 —— 两者语义等价，
        # 这里只断言「不是裸下标」，别把实现细节钉死（钉死后性能优化会被误判成回归）。
        assert "_ix_get(R,'a')" in src or "R.get('a',_U)" in src
        assert "R['a']" not in src
        assert "_ob([" in src
        ns = {}
        exec(compile(src, "<nov>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]
        # 缺键：具名对象丢键（不是 KeyError，也不是把哨兵写出去）
        assert ns["run"]([{"b": 1}]) == [[{}]]

    def test_positional_row_optional_value_keeps_position(self):
        """位置行里 optional+value 缺键要保位成 None（不是 KeyError、不是缩短）。"""
        src = dcg.generate(_schema({"v": {"type": "list", "fields": {
            "x": {"type": "int64", "optional": True, "value": "$.a"}}}}))
        assert "_ix_get(R,'a')" in src or "R.get('a',_U)" in src
        assert "_rl([" in src
        ns = {}
        exec(compile(src, "<pov>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[[7]]]
        assert ns["run"]([{"b": 1}]) == [[[None]]]

    def test_required_value_in_nested_scope_still_reads_bare(self):
        """对照：required 的 `value` 仍然发裸读、缺键仍抛 KeyError——**不能被放宽**。

        这是上面两条用例的边界守卫：修 optional 不能顺手把 required 也变成
        "缺键给 None"，那会掩盖真实的 schema/数据不匹配。
        """
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "value": "$.a"}}}}))
        assert "R['a']" in src and "_ix_get(R,'a')" not in src
        ns = {}
        exec(compile(src, "<req>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]
        with pytest.raises(KeyError):
            ns["run"]([{"b": 1}])

    def test_optional_expr_takes_the_same_safe_path(self):
        """对照：optional+`expr` 本来就走 `_ix_get`，修复前后行为一致（防回归）。"""
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "optional": True, "expr": "$.a"}}}}))
        assert "_ix_get(R,'a')" in src
        ns = {}
        exec(compile(src, "<oe>", "exec"), ns)
        assert ns["run"]([{"b": 1}]) == [[{}]]

    def test_optional_value_with_default_still_uses_default(self):
        """对照：`optional` + `default` 走 `_df` 兜底，缺键给默认值而不是丢键。"""
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "optional": True, "value": "$.a",
                  "default": 9}}}}))
        assert "_df(" in src
        ns = {}
        exec(compile(src, "<od>", "exec"), ns)
        assert ns["run"]([{"b": 1}]) == [[{"x": 9}]]
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]

    def test_top_level_optional_value_is_unchanged(self):
        """对照：顶层 optional+value 在修复前就是对的（`safe` 模式已生效），不能改坏。"""
        src = dcg.generate(_schema({"a": {"type": "int64", "optional": True,
                                         "value": "$.a"}}))
        ns = {}
        exec(compile(src, "<tov>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[7]]
        assert ns["run"]([{"b": 1}]) == [[None]]

