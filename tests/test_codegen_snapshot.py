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
   conditions of CONTRACT §4.2, the char budgets of §7 (CAP<=180, M0<=210), and
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
sys.path.insert(0, os.path.join(ROOT, "bench"))

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402

try:
    import _workload as W  # noqa: E402
except Exception:  # pragma: no cover - bench/ not shipped
    W = None

requires_bench = pytest.mark.skipif(W is None, reason="bench/_workload.py 不可用")

CAP = 180
M0 = 210


def _schema(fields, name="snap", shape="list", single=False, filt=True):
    """CONTRACT §2 的最小合法 schema（`output` 是必填块）。"""
    return {"name": name,
            "output": {"shape": shape, "single": single, "filter": filt},
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
        assert set(rt.SOURCE_NEEDS_MATH) == {n for n, t in rt.SOURCE.items()
                                             if "math" in t}
        # 曾经的漂移：_fl/_ce/_sq 用 _m 但依赖集合漏报 -> 内联即 NameError
        assert {"_fl", "_ce", "_sq"} <= set(rt.SOURCE_NEEDS_MATH)

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
            pytest.skip("非确定 helper：结果不可复现（DCG 接口实现细节文档 §3.5.3）")
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


@requires_bench
class TestZeroRedundancy:
    """CONTRACT §4.2 六条零冗余 + §7 字符预算 —— 逐条断言。"""

    BASES = (("V2_CAP", CAP), ("V2_SCHEMA", M0), ("V2_M0", M0))

    def _schemas(self):
        out = []
        for label, cap in self.BASES:
            sch = getattr(W, label, None)
            if sch is not None:
                out.append((label, sch, cap))
        return out

    def test_char_budget(self):
        for label, sch, cap in self._schemas():
            src = dcg.generate(sch)
            assert len(src) <= cap, (
                f"{label}: 产物 {len(src)} 字符 > §7 预算 {cap}\n{src}")

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
        """③ 不得有 buffer 中转。"""
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            assert "buffer" not in live, f"{label}: 出现 buffer 命名"
            assert not re.search(r"^\s*\w+\s*=\s*\w+\.append\s*$", live, re.M), \
                f"{label}: `x = y.append` 别名（§4.2 规则 3 明禁）"

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

    def test_row_is_a_list_via_append_not_extend(self):
        for label, sch, cap in self._schemas():
            live = _live_code(dcg.generate(sch))
            assert "_o.append([" in live, f"{label}: 行容器不是 `_o.append([...])`"
            assert "_o+=" not in live, f"{label}: `+=` 是 extend，会把行拍平"

    def test_filter_guard_follows_the_declaration(self):
        """`if R:` 只在 output.filter=true 时生成，行为随之（Lead m01917 §3）。"""
        rows = list(W.make_rows(4))
        on = json.loads(json.dumps(W.V2_CAP))
        on["output"] = dict(on["output"], filter=True)
        off = json.loads(json.dumps(W.V2_CAP))
        off["output"] = dict(off["output"], filter=False)
        src_on, src_off = dcg.generate(on), dcg.generate(off)
        assert re.search(r"^\s*if R:", _live_code(src_on), re.M), \
            "filter=true 应生成行守卫"
        assert not re.search(r"^\s*if R:", _live_code(src_off), re.M), \
            "filter=false 不得生成行守卫（不为未声明的选项付费）"

        def runner(src):
            ns = {}
            exec(compile(src, "<snapshot-filter>", "exec"), ns)
            return ns["run"]

        assert len(runner(src_on)([rows[0], None, {}, rows[1]])) == 2, \
            "filter=true 应丢弃 falsy 行"
        assert len(runner(src_off)(rows)) == len(rows), \
            "filter=false 应保留全部行"


@requires_bench
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
        sch = dict(base, output=dict(base["output"], shape="dict", single=False))
        run, _ = W.load_v2(sch)
        res = run(W.make_rows(5))
        assert isinstance(res, dict) and list(res) == fields
        assert all(len(v) == 5 for v in res.values())

    def test_index_shape_uses_int_row_keys(self):
        base = W.V2_SCHEMA
        sch = dict(base, output=dict(base["output"], shape="index", single=False))
        run, _ = W.load_v2(sch)
        res = run(W.make_rows(5))
        assert list(res) == [0, 1, 2, 3, 4]
        assert all(isinstance(v, list) for v in res.values())

    def test_single_flattens_only_list_shape(self):
        base = W.V2_SCHEMA
        n_fields = len(base["out"]["fields"])
        one = dict(base, output=dict(base["output"], shape="list", single=True))
        run, _ = W.load_v2(one)
        res = run(W.make_rows(3))
        # single + list = 直接返回**那一条行**（扁平的一行，不是行的列表）
        assert isinstance(res, list) and len(res) == n_fields
        assert not any(isinstance(x, list) and len(x) > 1 for x in res[1:])
        for shape in ("dict", "index"):
            bad = dict(base, output=dict(base["output"], shape=shape, single=True))
            with pytest.raises(dcg.SchemaError):
                dcg.validate(bad)


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
        assert _run_of(src)([{"a": 1}, {"a": 5}, None, {}]) == [[1, 3], [5, 11]]

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

    def test_list_items_path(self):
        sch = _schema({"a": {"type": "int64", "value": "$.a"},
                       "v": {"type": "list", "items": {
                           "type": "any", "expr": "missing + 1"}}})
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(sch)
        assert ei.value.path == "out.fields.v.items.expr"

    def test_reserved_field_name_is_located(self):
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_schema({"_ad": {"type": "int64", "value": "$.a"}}))
        assert ei.value.path == "out.fields._ad"


# ================================================  J. 嵌套结构（对象 / 数组）取值
class TestNestedContainers:
    """契约 §2 line 127-128：`fields` 与 `items` 都是**单个** FieldSpec。"""

    def test_nested_object_is_a_dict(self):
        src = dcg.generate(_schema({"n": {"type": "object", "fields": {
            "x": {"type": "int64", "value": "$.a"}}}}))
        ns = {}
        exec(compile(src, "<n>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[{"x": 7}]]

    def test_nested_list_items_is_a_one_element_list(self):
        src = dcg.generate(_schema(
            {"v": {"type": "list", "items": {"type": "int64", "value": "$.a"}}}))
        ns = {}
        exec(compile(src, "<l>", "exec"), ns)
        assert ns["run"]([{"a": 7}]) == [[[7]]]

    def test_nested_list_of_objects_composes(self):
        src = dcg.generate(_schema(
            {"v": {"type": "list", "items": {"type": "object", "fields": {
                "x": {"type": "int64", "value": "$.a"},
                "y": {"type": "int64", "value": "$.b"}}}}}))
        ns = {}
        exec(compile(src, "<lo>", "exec"), ns)
        assert ns["run"]([{"a": 7, "b": 8}]) == [[[{"x": 7, "y": 8}]]]
