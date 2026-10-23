"""End-to-end verification of DCG v2 (task-3 deliverable; verifier-dev).

Independent acceptance tests -- written from DCG 接口实现细节文档.md only, never from the
implementation's self-report.  Scope:

  1. 民航 ACDM/flight 端到端：schema -> validate -> generate -> run
  2. 输出形状裁决（Lead，CONTRACT §4.1）：list / dict(列式) / index(int 键) / single
  3. 六条零冗余（CONTRACT §4.2）—— 字符预算已按用户裁决删除
  4. `type` 是声明不是转换（CONTRACT §4.2 type 裁决）：不得出现凭空 `int(...)`
  5. 确定性：两次 generate 字节相同；无 random；build 可复现
  6. 生成值与手写参照语义一致：逐行逐字段比**值**（形状是有意变更，不比容器）
  7. build() / CLI / SchemaError / Unsupported

Tests that need a working `dcg.generate` are marked `requires_v2`; the rest guard
pure-Python invariants.  Once `dcg.generate` exists, all of them run and any failure
is a real defect.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import codegen_rules as CR  # noqa: E402
import workload as W  # noqa: E402

try:
    import dcg  # noqa: E402
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - implementation not landed
    dcg = None
    _IMPORT_ERROR = exc

# Gate: while the v2 engine has not landed, every v2-dependent test is *collected
# and reported as skipped* (exit 0).  A module-level `pytest.skip` would instead
# make pytest exit 5 ("no tests collected"), which is indistinguishable from a
# broken test file -- found by verifier-dev while checking the gate.
V2_REASON = ""
if dcg is None:
    V2_REASON = f"dcg v2 包不可导入: {type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}"
elif not callable(getattr(dcg, "generate", None)):
    _public = sorted(n for n in dir(dcg) if not n.startswith("_"))
    V2_REASON = (f"dcg v2 引擎尚未落地（缺 dcg.generate）: "
                 f"dcg.__file__={getattr(dcg, '__file__', None)}, 公开名={_public}")
V2_READY = not V2_REASON
requires_v2 = pytest.mark.skipif(not V2_READY, reason=V2_REASON or "dcg v2 未就绪")

API = ["validate", "SchemaError", "generate", "build", "gen_name",
       "compile_expr", "Unsupported", "Registry"]


# ==========================================================================
# helpers
# ==========================================================================
def make_runner(schema, **kw):
    """generate -> exec -> callable run (the real deploy path)."""
    src = dcg.generate(schema, **kw)
    ns = {}
    exec(compile(src, "<gen/%s.py>" % schema["name"], "exec"), ns)
    return ns["run"], src


def _shape_schema(shape, name=None):
    return {
        "name": name or f"sh_{shape}",
        "output": {"shape": shape},
        "out": {"type": "object", "fields": {
            "a": {"type": "int64", "value": "$.a"},
            "b": {"type": "int64", "value": "$.b"},
        }},
    }


# ==========================================================================
# 1. flight / ACDM end-to-end
# ==========================================================================
FLIGHT_SCHEMA = {
    "name": "flight",
    "output": {"shape": "list", "trace": False},
    "out": {"type": "object", "fields": {
        "id":    {"type": "int64", "value": "$.ID"},
        "sum":   {"type": "int64", "expr": "$.a + $.b"},
        "grade": {"type": "str", "expr": "$.score >= 90 ? 'A' : 'B'",
                  "optional": True},
        "tag":   {"type": "str", "const": "flight"},
    }},
}

FLIGHT_ROWS = [
    {"ID": 10, "a": 3, "b": 4, "score": 95},
    {"ID": 11, "a": 1, "b": 2, "score": 8},
    {"ID": 12, "a": 0, "b": 0, "score": 90},
    # 第 4 行专门钉 P0：0 是合法操作数（曾经 a==0 会被当成 undefined 而静默返回 0）
    {"ID": 13, "a": 0, "b": 5, "score": 50},
]
FLIGHT_EXPECT = [[10, 7, "A", "flight"],
                 [11, 3, "B", "flight"],
                 [12, 0, "A", "flight"],
                 [13, 5, "B", "flight"]]


@requires_v2
class TestPublicSurface:
    def test_contract_5_exports(self):
        missing = [n for n in API if not hasattr(dcg, n)]
        assert not missing, f"CONTRACT §5 公开面缺失: {missing}"

    def test_generate_signature(self):
        import inspect
        sig = inspect.signature(dcg.generate)
        assert list(sig.parameters) == ["schema", "cython", "inline_optional"]
        assert sig.parameters["cython"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["inline_optional"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["cython"].default is False
        assert sig.parameters["inline_optional"].default is True


@requires_v2
class TestFlightE2E:
    def test_flight_rows_exact(self):
        run, src = make_runner(FLIGHT_SCHEMA)
        got = run(FLIGHT_ROWS)
        assert got == FLIGHT_EXPECT, f"got {got}\nsource:\n{src}"

    def test_flight_is_validated_first(self):
        # validate() must accept the canonical schema (build path validates too)
        dcg.validate(FLIGHT_SCHEMA)

    def test_before_hook_drops_falsy_rows(self):
        """数据级前置钩子：`before` 用反引号段把 falsy 行从**输入数据**里剔掉。

        逐行判断全部搬到 `before`：钩子拿到 input 定位后的全量数据，返回的数据
        替换它（用户 m00292 的钩子模型）。
        """
        schema = dict(FLIGHT_SCHEMA, output={
            "shape": "list",
            "before": "= `[r for r in $ if r]`",
        })
        run, src = make_runner(schema)
        rows = [{"ID": 1, "a": 1, "b": 1, "score": 99}, {}, None]
        got = run(rows)
        assert got == [[1, 2, "A", "flight"]], f"{got}\nsource:\n{src}"

    def test_optional_field_missing_is_guarded(self):
        """CONTRACT §4.2 rule 7: only `optional: true` gets an undefined guard.

        注意（verifier-dev 修正）：不能只在整个模块里 grep `is _U` —— 算术 helper
        自带的 undefined 预检会喂饱那种计数，从而在"optional 字段根本没产出"时假 PASS
        （D9 就是这样漏过去的）。这里用字段级的判定器。
        """
        run, src = make_runner(FLIGHT_SCHEMA)
        ok, detail, obs = CR.rule7_optional_guard_only(src, FLIGHT_SCHEMA)
        assert ok, f"rule 7 违规: {detail} | {obs}\n{src}"
        # 有 score 时必须真的产出 grade 值（不是静默丢字段）
        out = run([{"ID": 1, "a": 1, "b": 1, "score": 95}])
        assert len(out) == 1 and out[0][:2] == [1, 2]
        assert out[0][2] in ("A", "B"), f"optional 字段 grade 未产出: {out}"
        assert "flight" in out[0]
        # 缺 score 时不应抛异常
        out2 = run([{"ID": 2, "a": 1, "b": 1}])
        assert len(out2) == 1, out2

    def test_zero_operand_is_not_undefined(self):
        """P0 回归守卫（verifier-dev 独立发现，2026-10-17 00:3x）。

        曾经产物把 undefined 哨兵编成字面量 `_U=0`，而 CPython 小整数缓存使
        `0 is 0` 为 True -> `$.a + $.b` 在 a==0 或 b==0 时被判为 undefined，
        静默返回 0。实测 a=0,b=5 得 sum=0（应为 5）。这里钉死：0 是合法值。
        """
        run, src = make_runner(_one_field_schema("undef_zero", {"expr": "$.a + $.b"}))
        cases = [({"a": 0, "b": 5}, [5]),
                 ({"a": 5, "b": 0}, [5]),
                 ({"a": 0, "b": 0}, [0]),
                 ({"a": 2, "b": 3}, [5])]
        for row, expect in cases:
            got = run([row])
            assert got == [expect], (
                f"UNDEF 哨兵与整数 0 撞身份: {row} -> {got}, 期望 [{expect}]\n{src}")

    def test_undefined_sentinel_is_not_a_literal_zero(self):
        """产物里的 undefined 哨兵不得是字面量 0（应来自 runtime 的单例）。"""
        _, src = make_runner(_one_field_schema("undef_lit", {"expr": "$.a + $.b"}))
        live = _live_code(src)
        assert not any(l.strip() in ("_U=0", "_U = 0") for l in live.splitlines()), \
            f"产物把 undefined 哨兵写成字面量 0:\n{live}"

    def test_nested_fields_compile_and_run(self):
        schema = {
            "name": "nested",
            "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "n": {"type": "object", "fields": {
                    "x": {"type": "int64", "value": "$.x"}}},
                "top": {"type": "int64", "value": "$.y"},
            }},
        }
        run, src = make_runner(schema)
        got = run([{"x": 5, "y": 7}])
        assert len(got) == 1
        row = got[0]
        assert 7 in row, f"标量字段丢失: {row}\nsource:\n{src}"
        flat = [v for v in row if not isinstance(v, (dict, list))]
        assert 7 in flat
        nested = [v for v in row if isinstance(v, (dict, list))]
        assert nested, f"嵌套字段未产出嵌套容器: {row}\nsource:\n{src}"


# ==========================================================================
# 2. output shapes -- CONTRACT §4.1 (Lead's ruling)
# ==========================================================================
SHAPE_ROWS = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


@requires_v2
class TestOutputShapes:
    def test_shape_list(self):
        run, _ = make_runner(_shape_schema("list"))
        assert run(SHAPE_ROWS) == [[1, 2], [3, 4]]

    def test_shape_dict_is_columnar(self):
        run, src = make_runner(_shape_schema("dict"))
        got = run(SHAPE_ROWS)
        assert isinstance(got, dict), got
        assert got == {"a": [1, 3], "b": [2, 4]}, f"shape=dict 必须列式: {got}\n{src}"

    def test_shape_index_int_keys(self):
        run, src = make_runner(_shape_schema("index"))
        got = run(SHAPE_ROWS)
        assert isinstance(got, dict), got
        assert set(got) == {0, 1} and all(isinstance(k, int) for k in got), got
        assert got == {0: [1, 2], 1: [3, 4]}, f"{got}\n{src}"

    def test_shape_rows_emits_per_row_dicts(self):
        """`rows`：每行一个 {字段: 值} dict（不是列式）。"""
        run, src = make_runner(_shape_schema("rows"))
        got = run(SHAPE_ROWS)
        assert got == [{"a": 1, "b": 2}, {"a": 3, "b": 4}], f"shape=rows: {got}\n{src}"
        assert all(isinstance(r, dict) for r in got), got
        assert [list(r) for r in got] == [["a", "b"], ["a", "b"]], \
            "键序必须是字段声明序"

    def test_single_is_removed_from_every_shape(self):
        """`output.single` 已删除（用户 m03599）：任何形状下都是 SchemaError。"""
        for shape in ("list", "rows", "dict", "index"):
            bad = _shape_schema(shape)
            bad["output"]["single"] = True
            with pytest.raises(dcg.SchemaError) as ei:
                dcg.validate(bad)
            assert ei.value.path == "output.single"

    def test_row_container_is_list_in_every_shape(self):
        """D1（Lead m00803 确认为真缺陷）：契约 §4.1 的 `shape=list` 是 **list of row**。

        实测（evidence/v2_row_container.txt）：`_f` 的 `return(id,x,y,)` 尾逗号造出 tuple，
        `shape=list` 与 `single` 直接暴露该 tuple；`dict`/`index` 形状因为外面套了
        `list(...)` 所以行值是 list。本断言在所有形状上钉住「行必须是 list」。
        """
        run, src = make_runner(_shape_schema("list"))
        rows = run(SHAPE_ROWS)
        assert all(isinstance(r, list) for r in rows), \
            f"shape=list 的行必须是 list，实得 {[type(r).__name__ for r in rows]}\n{src}"

        run, src = make_runner(_shape_schema("dict"))
        col = run(SHAPE_ROWS)
        assert all(isinstance(v, list) for v in col.values()), f"{col}\n{src}"

        run, src = make_runner(_shape_schema("index"))
        idx = run(SHAPE_ROWS)
        assert all(isinstance(v, list) for v in idx.values()), \
            f"shape=index 的行值必须是 list: {idx}\n{src}"

    def test_no_runtime_shape_switch(self):
        """CONTRACT §4.1/§4.2 rule 4: shape is a compile-time constant."""
        for shape in ("list", "dict", "index"):
            _, src = make_runner(_shape_schema(shape))
            # docstrings 可能包含 schema JSON（其中就有 "shape" 字样），先剥掉再查代码体
            body = CR._strip_docstrings(src)
            code = "\n".join(l for l in body.splitlines()
                             if not l.strip().startswith("#"))
            assert "shape" not in code, \
                f"shape={shape} 的产物里出现运行期 shape 判断:\n{code}"
            assert "itertools" not in code and ".items()" not in code
            assert "isinstance(" not in code, "形状不得运行期判断类型"


@requires_v2
class TestCodegenRules:
    def test_zero_redundancy(self):
        """零冗余规则逐条判定 —— 只看**结构**，不看字符数。

        只按结构判定，不看字符数（字符上限已按用户裁决删除，见 m00975/m01036）。
        """
        for label, schema in (("CAP", W.V2_CAP), ("M0", W.V2_SCHEMA)):
            src = dcg.generate(schema)
            cy = None
            try:
                cy = dcg.generate(schema, cython=True)
            except Exception as exc:
                cy = None
            report = CR.run_all(src, schema, cy)
            bad = [(n, d) for n, ok, d, o in report if not ok]
            assert not bad, "规则未过:\n" + \
                "\n".join(f"  {n}: {d}" for n, d in bad) + \
                f"\n--- source ---\n{CR.render(src)}"

    def test_product_is_self_contained(self):
        """产物必须自足：exec 到空 globals 后能跑（helper 少 import 就会 NameError）。

        codegen 新引入「只在需要时才 `from dcg.runtime import *`」的优化，这条测试
        同时钉住两个方向：调用了 runtime helper 就必须有 import 行；反之不得多 import。
        """
        import re as _re
        import dcg.runtime as rt
        helpers = {n for n in dir(rt)
                   if n.startswith("_") and callable(getattr(rt, n))}
        for label, schema in (("CAP[ext]", W.V2_CAP_EXT), ("M0[ext]", W.V2_M0),
                              ("CAP[native]", W.V2_CAP), ("M0[native]", W.V2_SCHEMA)):
            src = dcg.generate(schema)
            used = {m.group(1) for m in _re.finditer(r"\b(_[A-Za-z]\w*)\s*\(", src)} & helpers
            if used:
                assert "from dcg.runtime import" in src, (
                    f"{label}: 产物调用了 {sorted(used)} 但没有 import runtime\n{src}")
            ns = {}
            exec(compile(src, f"<{label}>", "exec"), ns)
            assert callable(ns["run"]), f"{label}: 产物未定义 run()"

    def test_inline_for_statically_provable_typed_fields(self):
        """CONTRACT §4.3.1：required 且 type 明确的字段走裸运算，不套 `_ad`/`_S`，也不加守卫。

        注意：**不要**断言具体的别名拼写（`hourA+hourB` vs `_a1+hourB`）——codegen 正按
        「别名能否消除」迭代，绑死拼写会造出一个只在命名变化时变红的假失败（这类
        "过度精确的测试"我在本报告 §5.13 记过两次教训）。只断言可证安全的裸运算形态。
        """
        import re as _re
        src = dcg.generate(W.V2_CAP_EXT)   # hourE = hourA + hourB，int64 required
        assert "_ad(" not in src, f"可证类型安全的加法仍走 helper:\n{src}"
        assert "is _U" not in src, f"required 字段不得有 undefined 守卫:\n{src}"
        assert _re.search(r"\w+\s*\+\s*\w+", src), f"未内联为裸加法:\n{src}"

    def test_inline_leaves_no_helper_for_statically_provable_index(self):
        """CONTRACT §4.3.1：`hour` 声明为 list 且下标是常量 → 必须内联成裸 `hour[2]`。

        历史上此测试以 `xfail` 过渡（当时仍发 `_ix_get(hour,2)`），内联落地后
        已 XPASS，按 Lead m01316 ①「xfail 只是过渡、落地后必须转真 PASS 并删除」
        改为真断言。
        """
        src = dcg.generate(W.V2_M0)
        assert "_ix_get(" not in src, f"M0[ext] 仍用 helper 取常量下标:\n{src}"
        assert "_a[2]" in src.replace(" ", ""), f"未内联为裸下标:\n{src}"

    def test_cython_product_is_pure_source_plus_cdef_lines(self):
        """Lead 裁决 ⑤：cython 产物必须恰好是「纯 Python 产物 + 每局部一行 cdef」。

        因此剥掉所有以 `cdef` 开头的行之后必须与纯 Python 产物**逐字节相同**。
        """
        for label, schema in (("CAP[ext]", W.V2_CAP_EXT), ("M0[ext]", W.V2_M0),
                              ("CAP[native]", W.V2_CAP), ("M0[native]", W.V2_SCHEMA)):
            pure = dcg.generate(schema)
            cy = dcg.generate(schema, cython=True)
            cdef_lines = [ln for ln in cy.splitlines() if ln.strip().startswith("cdef ")]
            stripped = "\n".join(ln for ln in cy.splitlines()
                                 if not ln.strip().startswith("cdef "))
            assert stripped == pure.rstrip("\n"), (
                f"{label}: 剥掉 cdef 行后与纯 Python 产物不一致\n"
                f"--- stripped ---\n{stripped}\n--- pure ---\n{pure}")
            assert cdef_lines, f"{label}: cython 产物里没有任何 cdef 行"

    def test_cython_product_is_longer_than_pure(self):
        """cython 产物因 cdef 行而更长；这条只钉结构与可解析性。

        字符预算已删除（用户 m00975/m01036），故这里不再断言任何字符上限。
        """
        pure = dcg.generate(W.V2_CAP)
        cy = dcg.generate(W.V2_CAP, cython=True)
        assert len(cy) > len(pure), "cython 产物应当因 cdef 行而更长"
        # `cdef` 不是合法 Python，所以预算绝不能用 AST 分析 cython 产物。
        # （注意 CR.stats 自己吞掉 SyntaxError，因此这里直接问 ast。）
        import ast as _ast
        with pytest.raises(SyntaxError):
            _ast.parse(cy)

    def test_product_compiled_exactly_once(self):
        """规则 ⑥ 的口径：**产物**（含 `def run(`）只 compile 一次。

        反引号段的编译期校验（ast.parse / compile(seg,'...','eval')）不算产物编译，
        但它是可省成本：实测每个 ext schema 会重复校验同一段 4 次。
        """
        import builtins
        for label, schema in (("CAP[ext]", W.V2_CAP_EXT), ("M0[ext]", W.V2_M0),
                              ("M0[native]", W.V2_SCHEMA)):
            grabbed = []
            orig = builtins.compile

            def spy(s, *a, **k):
                if isinstance(s, str):
                    grabbed.append(s)
                return orig(s, *a, **k)

            builtins.compile = spy
            try:
                dcg.generate(schema)
            finally:
                builtins.compile = orig
            products = [s for s in grabbed if "def run(" in s]
            segments = [s for s in grabbed if "def run(" not in s]
            assert len(products) == 1, (
                f"{label}: 产物被 compile {len(products)} 次（必须恰好 1 次）")
            assert not any("datas = None" in s or "buffer_" in s for s in products), \
                f"{label}: 产物里出现空桩"
            assert len(segments) <= 4, (
                f"{label}: 反引号段编译期校验 {len(segments)} 次（已知为 4 次冗余，若变化请复核）")

    def test_no_isinstance_dispatch_and_no_runtime_int_cast(self):
        """CONTRACT §4.2 type 裁决: type is a declaration, not a conversion."""
        src = dcg.generate(W.V2_SCHEMA)
        assert "isinstance(" not in src
        assert "int(" not in src, \
            "type=int64 不得凭空插入 int(...)（type 仅是声明：值只由输入决定）"
        assert "float(" not in src

    def test_generated_source_is_valid_python(self):
        """产物必须是合法 Python：`lambda R: x = 1` 是硬文法错误（Lead 实测过该形态）。"""
        import ast as _ast
        for label, schema in (("CAP", W.V2_CAP), ("M0", W.V2_SCHEMA)):
            src = dcg.generate(schema)
            try:
                tree = _ast.parse(src)
            except SyntaxError as exc:
                pytest.fail(f"{label} 产物不是合法 Python: line {exc.lineno}: {exc.msg}\n"
                            f"{CR.render(src)}")
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Lambda):
                    for sub in _ast.walk(node.body):
                        if isinstance(sub, (_ast.Assign, _ast.AugAssign, _ast.AnnAssign)):
                            pytest.fail(f"{label} 产物在 lambda body 里赋值（非法）: "
                                        f"line {sub.lineno}\n{CR.render(src)}")

    def test_names_deterministic_no_random(self):
        a = dcg.generate(W.V2_SCHEMA)
        b = dcg.generate(W.V2_SCHEMA)
        assert a == b, "两次 generate() 必须字节相同"
        assert "random" not in a
        assert dcg.gen_name(W.V2_SCHEMA) == dcg.gen_name(W.V2_SCHEMA)
        assert "gen/" not in dcg.gen_name(W.V2_SCHEMA)
        assert "\\" not in dcg.gen_name(W.V2_SCHEMA)


# ==========================================================================
# 4. build() / CLI
# ==========================================================================
@requires_v2
class TestBuild:
    def test_build_writes_artifact_and_manifest(self, tmp_path):
        man = dcg.build(FLIGHT_SCHEMA, str(tmp_path))
        assert isinstance(man, dict), man
        blobs = [str(p) for p in tmp_path.rglob("flight.py")]
        assert blobs, f"未写出 gen/flight.py: {sorted(map(str, tmp_path.rglob('*')))}"
        text = tmp_path.joinpath("gen", "flight.py")
        assert text.exists() or blobs
        assert any("sha1" in k.lower() for k in man), \
            f"manifest 需记录 schema_sha1（CONTRACT §4.1）: {sorted(man)}"
        assert any(k in man for k in ("lines", "chars", "n_lines", "n_chars")) or \
            any("line" in k.lower() or "char" in k.lower() for k in man), \
            f"manifest 需含行数/字符数: {sorted(man)}"

    def test_build_is_reproducible(self, tmp_path):
        dcg.build(FLIGHT_SCHEMA, str(tmp_path))
        p = next(tmp_path.rglob("flight.py"))
        first = p.read_text(encoding="utf-8")
        dcg.build(FLIGHT_SCHEMA, str(tmp_path))
        assert p.read_text(encoding="utf-8") == first, "同 schema 重建必须字节相同"

    def test_build_refuses_silent_staleness(self, tmp_path):
        dcg.build(FLIGHT_SCHEMA, str(tmp_path))
        changed = json.loads(json.dumps(FLIGHT_SCHEMA))
        changed["out"]["fields"]["tag"]["const"] = "flight2"
        with pytest.raises(dcg.SchemaError):
            dcg.build(changed, str(tmp_path))
        dcg.build(changed, str(tmp_path), overwrite=True)
        p = next(tmp_path.rglob("flight.py"))
        assert "flight2" in p.read_text(encoding="utf-8")

    def test_cli_builds(self, tmp_path):
        schema_file = tmp_path / "flight.json"
        schema_file.write_text(json.dumps(FLIGHT_SCHEMA), encoding="utf-8")
        out = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "dcg.build", str(schema_file), "-o", str(out)],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        assert proc.returncode == 0, f"CLI 失败:\n{proc.stdout}\n{proc.stderr}"
        assert list(out.rglob("*.py")), f"CLI 未产出模块: {proc.stdout}"


# ==========================================================================
# 5. errors: SchemaError / Unsupported
# ==========================================================================
@requires_v2
class TestErrors:
    def test_two_value_keys_rejected_with_path(self):
        bad = json.loads(json.dumps(FLIGHT_SCHEMA))
        bad["out"]["fields"]["id"]["const"] = 1
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.validate(bad)
        assert "out.fields.id" in str(ei.value) or \
            getattr(ei.value, "path", "") == "out.fields.id", str(ei.value)

    def test_missing_type_rejected(self):
        bad = json.loads(json.dumps(FLIGHT_SCHEMA))
        del bad["out"]["fields"]["id"]["type"]
        with pytest.raises(dcg.SchemaError):
            dcg.validate(bad)

    def test_external_shape_reports_path_not_keyerror(self):
        other = {"out": {"type": "object", "fields": {"sum": {"描述": "x"}}}}
        with pytest.raises((dcg.SchemaError, ValueError)) as ei:
            dcg.validate(other)
        assert not isinstance(ei.value, KeyError)

    @pytest.mark.parametrize("expr", ["$sum($.a)", "$now()", "$average($.a)",
                                      "$random()", "$sum([1, 2, 3])"])
    def test_batch_functions_are_supported(self, expr):
        """A/C 类函数已支持（DCG 接口实现细节文档 §3.4 / §3.5「C 类」）。"""
        good = json.loads(json.dumps(FLIGHT_SCHEMA))
        good["out"]["fields"]["sum"] = {"type": "any", "expr": expr}
        src = dcg.generate(good)
        assert "def run" in src

    @pytest.mark.parametrize("expr", ["$map($.a, $string)", "$eval($.a)"])
    def test_unsupported_expressions_still_refused(self, expr):
        bad = json.loads(json.dumps(FLIGHT_SCHEMA))
        bad["out"]["fields"]["sum"] = {"type": "any", "expr": expr}
        with pytest.raises(dcg.Unsupported) as ei:
            dcg.generate(bad)
        assert "列" in str(ei.value) or "col" in str(ei.value).lower(), \
            f"Unsupported 需带列号: {ei.value}"

    def test_py_inline_segment_validated_at_build(self):
        bad = json.loads(json.dumps(FLIGHT_SCHEMA))
        bad["out"]["fields"]["sum"] = {"type": "int64", "expr": "`1 +`"}
        with pytest.raises((dcg.SchemaError, dcg.Unsupported)):
            dcg.generate(bad)


# ==========================================================================
# 6. generated values vs an independent hand-written reference
# ==========================================================================
def _cap_reference_row(r):
    """One CAP row computed by hand (no code generation)."""
    a = r[5]["a"]
    return [a[1], a[2], str(a[1]) + str(a[2]), a[1] + a[2]]


@requires_v2
class TestFrozenRowEquivalence:
    def test_m0_row_values_match_hand_reference(self):
        rows = W.make_rows(300)
        expected = W.ref_tight({"index": rows})
        run, src = make_runner(W.V2_SCHEMA)
        v2_out = run(rows)
        assert len(v2_out) == len(expected) == 300
        for i, (a, b) in enumerate(zip(expected, v2_out)):
            assert b == a, (
                f"第 {i} 行值不一致:\n ref={a}\n v2={b}\n rows={rows[i]}\n"
                f"--- v2 source ---\n{src}")
        assert not isinstance(v2_out, dict)

    def test_cap_row_values_match_hand_reference(self):
        rows = W.make_rows(50)
        expected = [_cap_reference_row(r) for r in rows]
        run, src = make_runner(W.V2_CAP)
        v2_out = run(rows)
        for i, (a, b) in enumerate(zip(expected, v2_out)):
            assert b == a, f"第 {i} 行 CAP 不一致:\n ref={a}\n v2={b}\n{src}"


# ==========================================================================
# 7. CONTRACT §3.6 动态下标 (Lead 裁决 2026-10-17)
#    背景（按 Lead 修正后的口径，勿写成"已证明的静默错值 bug"）：
#    jx 曾把动态下标编成 ("call","$ix",...)，而运行期 `_ix` == $indexOf
#    (runtime.py:289 `s.find(...)`)；真实下标 helper 是 `_ix_get` (runtime.py:391)。
#    即这是一个 **IR 命名歧义**（$ix vs _ix），Lead 已裁决废弃 `$ix`、
#    统一用 ("index", base, idx, loc)。本类断言目标：
#      ① 动态下标取到**行内元素**（端到端）；② 产物/IR 中不出现 `$ix` 节点。
# ==========================================================================
def _live_code(src):
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def _one_field_schema(name, spec, out_type="int64"):
    return {"name": name,
            "output": {"shape": "list"},
            "out": {"type": "object", "fields": {"v": dict(spec, type=out_type)}}}


def _walk(node):
    """Yield every tuple node of an IR (contract §1 trees are plain tuples)."""
    if isinstance(node, tuple):
        yield node
        for item in node:
            yield from _walk(item)
    elif isinstance(node, (list,)):
        for item in node:
            yield from _walk(item)


def _analyze_py_segment():
    try:
        import dcg.jx as jx
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"dcg.jx 不可导入: {type(exc).__name__}: {exc}")
    ana = getattr(jx, "analyze_py_segment", None)
    if ana is None:
        ana = getattr(getattr(jx, "compiler", None), "analyze_py_segment", None)
    if ana is None:
        pytest.skip("analyze_py_segment 未导出（非契约 §5 公开面；以端到端断言为准）")
    return ana


def _dyn_schema(name, expr, out_type="int64", idx_type="int64"):
    """§3.6 动态下标的 schema：裸名下标 `b` 是**已声明字段引用**（Lead m00803 裁定 (i)），
    所以除了目标字段 `v` 还必须声明 `b`；且 codegen 要求被引用的裸名**已在前面取值**
    （`dcg/codegen.py:310`：`defined` 只含声明顺序更早的字段）→ `b` 必须排在 `v` 前。"""
    return {"name": name,
            "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "b": {"type": idx_type, "value": "$.b"},
                "v": {"type": out_type, "expr": expr}}}}


def _dyn_row(row, names=("b", "v")):
    """把产物的行（list 或 tuple，取决于 D1）映成 {字段名: 值}，与容器类型无关。"""
    return dict(zip(names, list(row)))


@requires_v2
class TestContract36DynamicIndex:
    def test_dynamic_index_takes_row_element(self):
        """§3.6 测试要求: $.a[b] 取到的是行内元素（不是 $indexOf/find 结果）。

        `b` 已声明 -> 值为行内的 b（Lead m00803 裁定：`$.a[b]` ≡ `$.a[$.b]`）。
        """
        run, src = make_runner(_dyn_schema("dyn_list", "$.a[b]"))
        got = run([{"a": [7, 8, 9], "b": 2}, {"a": [4, 5], "b": 0}])
        assert [_dyn_row(r)["v"] for r in got] == [9, 4], f"动态下标取错元素: {got}\n{src}"
        code = _live_code(src)
        assert "_ix(" not in code, f"下标被路由到 $indexOf(_ix):\n{code}"

    def test_dynamic_index_via_dollar_path_needs_no_declaration(self):
        """`$.a[$.b]` 不依赖声明，必须与 `$.a[b]`（声明 b 后）等价。"""
        run, src = make_runner(_one_field_schema("dyn_dollar", {"expr": "$.a[$.b]"}))
        got = run([{"a": [7, 8, 9], "b": 2}])
        assert list(got[0]) == [9], f"{got}\n{src}"

    def test_undeclared_bare_index_name_is_rejected_at_build(self):
        """Lead m00803 第 1 条（保护「错误在 build 期暴露」这一性质）。

        未声明的裸名 `b` → build 期 `SchemaError`，且消息必须**列出可用字段名**。
        """
        with pytest.raises(dcg.SchemaError) as ei:
            dcg.generate(_one_field_schema("dyn_undeclared", {"expr": "$.a[b]"}))
        msg = str(ei.value)
        assert "b" in msg and "v" in msg, \
            f"SchemaError 必须点名非法裸名并列出可用字段: {msg}"

    def test_dynamic_index_on_string_is_not_string_find(self):
        """$indexOf 语义是 `"hello".find(2)` -> TypeError；正确语义是下标取字符。"""
        run, src = make_runner(_dyn_schema("dyn_str", "$.a[b]", out_type="str"))
        got = run([{"a": "hello", "b": 2}])
        assert _dyn_row(got[0])["v"] == "l", f"字符串动态下标错误: {got}\n{src}"
        assert "_ix(" not in _live_code(src)

    def test_dynamic_index_ir_is_index_node(self):
        """§3.6 裁决 2: 动态下标必须走 ("index", base, idx)，不得伪装成 ("call","$ix")。"""
        ir = dcg.compile_expr("$.a[b]")
        nodes = list(_walk(ir))
        kinds = [n[0] for n in nodes if n]
        assert "index" in kinds, f"未见 index 节点: {ir}"
        assert not any(n[0] == "call" and len(n) > 1 and n[1] == "$ix" for n in nodes), \
            f"动态下标仍伪装成 $ix 调用: {ir}"
        assert "$ix" not in repr(ir), f"IR 里不得出现 $ix: {ir}"

    def test_bare_name_index_is_declared_field_not_string_key(self):
        """jx-dev 修复点回归：裸名下标是 `("field","b")`，**不是** `("lit","b")` 字符串键。"""
        ir = dcg.compile_expr("$.a[b]")
        assert ("lit", "b") not in list(_walk(ir)), \
            f"裸名下标退化成字符串键查找（契约 §3.5 禁止静默降级）: {ir}"

    def test_constant_index_unchanged(self):
        """§3.6 裁决 1: 常量下标保持既有 ("index", base, ("lit", k)) 行为。"""
        run, src = make_runner(_one_field_schema("const_idx", {"value": "$.5.a.1"}))
        got = run([("x", 0, 0, 0, 0, {"a": [11, 22, 33]})])
        assert list(got[0]) == [22], f"常量下标行为变了: {got}\n{src}"


# ==========================================================================
# 8. CONTRACT §3.7 反引号段里的字段访问 (Lead 裁决 2026-10-17)
#    缺陷背景: _PY_FIELD 只认 \$.ident，不认数字段 -> 生产 schema 的 ext
#    (`str($.5.a.1) + str(hourB)`) 曾直接 Unsupported，深路径取数被打断。
# ==========================================================================
# CAP 工作负载的**原始显式 ext 写法**（不是 JSONata 改写版）：
#   对应行式产物: [hourA, hourB, str(a[1]) + str(hourB), hourA + hourB]
CAP_BACKTICK_SCHEMA = {
    "name": "cap_bt",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "hourA": {"type": "int64", "value": "$.5.a.1"},
        "hourB": {"type": "int64", "value": "$.5.a.2"},
        "hourD": {"type": "str",   "expr": "`str($.5.a.1) + str(hourB)`"},
        "hourE": {"type": "int64", "expr": "`hourA + hourB`"},
    }},
}


@requires_v2
class TestContract37PySegment:
    def test_min_test_backtick_ext_matches_hand_reference(self):
        """§3.7 最小测试: ``str($.5.a.1) + str(hourB)`` 端到端 == 手写参照。"""
        rows = W.make_rows(50)
        expected = [_cap_reference_row(r) for r in rows]
        run, src = make_runner(CAP_BACKTICK_SCHEMA)
        v2_out = run(rows)
        assert len(v2_out) == len(expected) == 50, f"{len(v2_out)} vs {len(expected)}"
        for i, (a, b) in enumerate(zip(expected, v2_out)):
            assert b == a, (f"第 {i} 行 CAP(原始 ext 反引号写法) 与手写参照不一致:\n"
                            f" ref={a}\n v2={b}\n rows={rows[i]}\n--- source ---\n{src}")

    def test_numeric_chain_alone_in_backticks(self):
        """`$.5.a.1` 单独成段也必须编译（段内偏移 5 的那个 bug）。"""
        run, src = make_runner(
            _one_field_schema("bt_num", {"expr": "`$.5.a.1`"}))
        got = run([("x", 0, 0, 0, 0, {"a": [11, 22, 33]})])
        assert got == [[22]], f"{got}\n{src}"

    def test_analyze_py_segment_accepts_numeric_chain(self):
        """§3.7 裁决 2: free_field_names 是完整链（"5.a.1"，不含前导 `$.`）。"""
        ana = _analyze_py_segment()
        seg, names = ana("str($.5.a.1) + str(hourB)")
        assert "str($.5.a.1)" in seg or "$.5.a.1" not in seg, seg
        assert any(n.strip() in ("5.a.1", "5.a.1.b") for n in names), \
            f"free_field_names 应为完整链: {names}"

    def test_backtick_and_jsonata_path_emit_same_code(self):
        """§3.7 裁决 3: 反引号里的 $.5.a.1 与 JSONata 的 $.5.a.1 共用同一实现。

        判定（比裸文本比对更强）：两者产物除生成名/sha1 头 + **冗余括号**外逐行相同。
        规范化只做一件事：把包住单个标识符的多余括号去掉（`v=(_a1)` -> `v=_a1`）。
        限定「前一个字符不是标识符/点」以免误伤调用 `f(_a1)`。
        （verifier-dev 修正：初版要求逐字节相同，实测唯一差异是反引号段保留了
        源码里的显式括号，属正当差异；契约裁决的对象是"共用实现"，不是文本相等。）
        """
        import re as _re
        _, src_jsonata = make_runner(_one_field_schema("shared_a", {"value": "$.5.a.1"}))
        _, src_py = make_runner(_one_field_schema("shared_b", {"expr": "`$.5.a.1`"}))

        def _drop_redundant_parens(text):
            return _re.sub(r"(?<![\w.])\((\w+)\)", r"\1", text)

        def norm(s):
            name = None
            for line in s.splitlines():
                if line.startswith("def "):
                    name = line.split("(")[0][4:]
                    break
            lines = []
            for line in s.splitlines():
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                if name:
                    t = t.replace(name, "<N>")
                lines.append(_drop_redundant_parens(t))
            return lines

        a, b = norm(src_jsonata), norm(src_py)
        assert a == b, ("反引号段与 JSONata 路径未共用同一实现（产物不同）:\n"
                        f"--- JSONata ---\n" + "\n".join(a) +
                        "\n--- backtick ---\n" + "\n".join(b))

    def test_jsonata_chain_ir_is_index_chain(self):
        """§3.7 裁决 3 的结构面: $.5.a.1 的 IR 是 index 链，反引号段必须复用它。"""
        ir = dcg.compile_expr("$.5.a.1")
        kinds = [n[0] for n in _walk(ir) if n]
        assert kinds.count("index") >= 3, f"$.5.a.1 应为多层 index 链: {ir}"
        assert "lit" in kinds, f"每层下标应为 ('lit', k): {ir}"

    def test_double_identifier_path_compiles_to_index_chain(self):
        """§6.1 修订：$.a.b（多层标识符路径）是普通 index 链，不再拒绝。"""
        ir = dcg.compile_expr("$.a.b")
        assert ir[0] == "index"
        assert dcg.compile_expr("$.5.a.1.b")[0] == "index"



class TestFunctionLiteralAndOptionalRules:
    """§3.6 函数字面量规则、§4.2 比较器语义、§4.3 inline_optional、§4.5 index 输入。"""

    def test_lambda_outside_higher_order_is_schema_error(self):
        sch = {"name": "lam0", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "any", "expr": "function($x){$x}"}}}}
        with pytest.raises(dcg.SchemaError):
            dcg.generate(sch)

    def test_duplicate_lambda_parameters_is_schema_error(self):
        sch = {"name": "lam1", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "list",
                         "expr": "$map([1], function($x,$x){$x})"}}}}
        with pytest.raises(dcg.SchemaError):
            dcg.generate(sch)

    def test_lambda_body_may_contain_py_block(self):
        sch = {"name": "lam2", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "list",
                         "expr": "$map([1,2], function($x){ py{ return 7 } })"}}}}
        run, _ = make_runner(sch)
        assert run([{"x": 1}]) == [[[7, 7]]]

    def test_sort_comparator_true_means_swap(self):
        """JSONata 1.8：比较器返回 true = 交换，`$a > $b` 因此是升序。"""
        for cmp_, want in (("$a > $b", [1, 2, 3]), ("$a < $b", [3, 2, 1])):
            sch = {"name": "sortcmp", "output": {"shape": "list"},
                   "out": {"type": "object", "fields": {
                       "v": {"type": "list",
                             "expr": "$sort([3,1,2], function($a,$b){ %s })"
                                     % cmp_}}}}
            run, _ = make_runner(sch)
            assert run([{"x": 1}]) == [[want]], cmp_

    def test_inline_optional_switches_guard_form(self):
        sch = {"name": "optguard", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "int64", "value": "$.0", "optional": True},
                   "b": {"type": "int64", "const": 1}}}}
        run1, src1 = make_runner(sch, inline_optional=True)
        run2, src2 = make_runner(sch, inline_optional=False)
        assert src1 != src2
        assert "is _U" in src1 and "is_u(" in src2
        assert run1([[5]]) == run2([[5]]) == [[5, 1]]

    def test_input_source_path_selects_the_row_array(self):
        """`input.source` 是行集合在输入里的路径：裸下标取，不套 `_ix_get`。"""
        sch = {"name": "idxin", "input": {"source": "$.a.b"},
               "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "int64", "value": "$.0"}}}}
        run, src = make_runner(sch)
        assert "_d=_d['a']['b']" in src
        assert "_ix_get" not in src
        assert run({"a": {"b": [[1], [2]]}}) == [[1], [2]]

    def test_missing_input_source_path_raises(self):
        """`input.source` 是声明好的固定结构：缺了就 KeyError，不静默降级。"""
        sch = {"name": "idxmiss", "input": {"source": "$.a.b"},
               "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "int64", "value": "$.0"}}}}
        run, _src = make_runner(sch)
        with pytest.raises(KeyError):
            run({})

    def test_default_input_source_is_the_whole_input(self):
        sch = {"name": "noinp", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "int64", "value": "$.0"}}}}
        run, src = make_runner(sch)
        assert "_d=" not in src
        assert run([[1], [2]]) == [[1], [2]]

    def test_input_source_numeric_segment_is_an_index(self):
        """`$.a.0.b` 的 `0` 是整数下标，产物必须是 `[0]` 而不是 `['0']`。"""
        sch = {"name": "idxnum", "input": {"source": "$.a.0.b"},
               "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "int64", "value": "$.0"}}}}
        run, src = make_runner(sch)
        assert "_d=_d['a'][0]['b']" in src
        assert "['0']" not in src
        assert run({"a": [{"b": [[1], [2]]}]}) == [[1], [2]]

    def test_input_source_quoted_segment_is_a_key(self):
        sch = {"name": "idxq", "input": {"source": "$['x y'].k"},
               "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "int64", "value": "$.0"}}}}
        run, src = make_runner(sch)
        assert "_d=_d['x y']['k']" in src
        assert run({"x y": {"k": [[3], [4]]}}) == [[3], [4]]

    def test_input_source_leading_bracket_needs_no_dot(self):
        sch = {"name": "idxbr", "input": {"source": "$[0].rows"},
               "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "any", "value": "$.0"}}}}
        run, src = make_runner(sch)
        assert "_d=_d[0]['rows']" in src
        assert run([{"rows": [["a"], ["b"]]}]) == [["a"], ["b"]]


class TestInlineEmissionRules:
    """§4.2 九条规则：类型可静态证明时发裸代码，否则保留 helper 守卫。"""

    @staticmethod
    def _sch(a_type, b_type, out_type, expr, name):
        return {"name": name, "output": {"shape": "list"},
                "out": {"type": "object", "fields": {
                    "a": {"type": a_type, "value": "$.a"},
                    "b": {"type": b_type, "value": "$.b"},
                    "v": {"type": out_type, "expr": expr}}}}

    def test_typed_numeric_operands_inline_arithmetic(self):
        src = dcg.generate(self._sch("int64", "int64", "int64", "a + b", "inl1"))
        assert "from dcg.runtime import" not in src
        assert "(a+b)" in src

    def test_typed_numeric_unary_minus_inlines(self):
        src = dcg.generate(self._sch("int64", "int64", "int64", "-a", "inl2"))
        assert "from dcg.runtime import" not in src
        assert "(-a)" in src

    def test_typed_string_concat_inlines(self):
        sch = {"name": "inl3", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "str", "value": "$.a"},
                   "b": {"type": "str", "value": "$.b"},
                   "v": {"type": "str", "expr": "a & b"}}}}
        run, src = make_runner(sch)
        assert "from dcg.runtime import" not in src
        assert "(a+b)" in src
        assert run([{"a": "x", "b": "y"}]) == [["x", "y", "xy"]]

    def test_numeric_fields_concat_keeps_string_helper(self):
        """`&` 是字符串连接：int64 字段不能裸发数值 `+`（曾错误发成 `a+b`）。"""
        run, src = make_runner(
            self._sch("int64", "int64", "str", "a & b", "inl4"))
        assert "_cc(a,b)" in src
        assert run([{"a": 1, "b": 2}]) == [[1, 2, "12"]]

    def test_untyped_operands_keep_helper_guards(self):
        sch = {"name": "inl5", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "any", "value": "$.a"},
                   "b": {"type": "any", "value": "$.b"},
                   "v": {"type": "int64", "expr": "a + b"}}}}
        src = dcg.generate(sch)
        assert "from dcg.runtime import" in src
        assert "_ad(a,b)" in src


class TestStringMethodInline:
    """字符串函数实参类型可证时发内建方法调用（DCG 接口实现细节文档 §4.2）。"""

    def test_string_functions_inline_to_builtin_methods(self):
        sch = {"name": "sm_inline", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "s": {"type": "str", "value": "$.s"},
                   "u": {"type": "str", "expr": "$uppercase(s)"},
                   "l": {"type": "str", "expr": "$lowercase(s)"},
                   "t": {"type": "str", "expr": "$trim(s)"}}}}
        run, src = make_runner(sch)
        assert "from dcg.runtime import" not in src
        assert ".upper()" in src and ".lower()" in src and ".strip()" in src
        assert run([{"s": "  Ab  "}]) == [["  Ab  ", "  AB  ", "  ab  ", "Ab"]]

    def test_string_functions_keep_helpers_when_type_unproven(self):
        sch = {"name": "sm_helper", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "any", "value": "$.a"},
                   "u": {"type": "any", "expr": "$uppercase(a)"}}}}
        src = dcg.generate(sch)
        assert "_up(" in src


class TestBareLiteralAndOperandProof:
    """数组/对象字面量与运算的裸发条件：操作数类型可证即可（§4.2）。"""

    def test_array_literal_with_definite_elements_is_bare(self):
        sch = {"name": "lit1", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "v": {"type": "list", "expr": "[1, 2, 3]"}}}}
        run, src = make_runner(sch)
        assert "from dcg.runtime import" not in src
        assert "[1,2,3]" in src

    def test_array_with_maybe_undefined_element_keeps_ar(self):
        sch = {"name": "lit2", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "int64", "value": "$.a"},
                   "v": {"type": "list", "expr": "[$.m, a]"}}}}
        run, src = make_runner(sch)
        assert "_ar(" in src
        assert run([{"a": 1, "m": None}]) == [[1, [None, 1]]]

    def test_arithmetic_inside_array_is_bare_when_operands_proven(self):
        sch = {"name": "lit3", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "int64", "value": "$.a"},
                   "b": {"type": "int64", "value": "$.b"},
                   "v": {"type": "list", "expr": "[a, b, a + b]"}}}}
        run, src = make_runner(sch)
        assert "(a+b)" in src and "_ad(" not in src
        assert run([{"a": 1, "b": 2}]) == [[1, 2, [1, 2, 3]]]

    def test_object_literal_with_definite_values_is_bare(self):
        sch = {"name": "lit4", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "int64", "value": "$.a"},
                   "v": {"type": "object", "expr": "{'k': a}"}}}}
        run, src = make_runner(sch)
        assert "{'k':a}" in src and "_ob(" not in src
        assert run([{"a": 7}]) == [[7, {"k": 7}]]


class TestComparisonLogicConditionInline:
    """§5：比较 / 逻辑 / 条件在操作数类型可证且无 JSONata 特例时发裸表达式。"""

    @staticmethod
    def _sch(a_type, b_type, out_type, expr, name):
        return {"name": name, "output": {"shape": "list"},
                "out": {"type": "object", "fields": {
                    "a": {"type": a_type, "value": "$.a"},
                    "b": {"type": b_type, "value": "$.b"},
                    "v": {"type": out_type, "expr": expr}}}}

    def test_typed_numeric_comparison_inlines(self):
        src = dcg.generate(self._sch("int64", "int64", "bool", "a < b", "cmp1"))
        assert "from dcg.runtime import" not in src
        assert "(a<b)" in src
        assert "_lt(" not in src

    def test_typed_string_equality_inlines(self):
        src = dcg.generate(self._sch("str", "str", "bool", "a = b", "cmp2"))
        assert "(a==b)" in src

    def test_typed_booleans_and_not_inline(self):
        src = dcg.generate(self._sch("bool", "bool", "bool", "a and not b", "cmp3"))
        assert "(a and (not b))" in src
        assert "_and(" not in src
        assert "_tru(" not in src

    def test_typed_ternary_inlines_and_runs(self):
        sch = self._sch("int64", "int64", "str",
                        "a > 1 ? 'big' : 'small'", "cmp4")
        src = dcg.generate(sch)
        assert "('big' if (a>1) else 'small')" in src
        assert "_co(" not in src
        g = {}
        exec(src, g)
        assert g["run"]([{"a": 5, "b": 0}]) == [[5, 0, "big"]]
        assert g["run"]([{"a": 0, "b": 0}]) == [[0, 0, "small"]]

    def test_typed_in_inlines(self):
        src = dcg.generate(self._sch("int64", "int64", "bool", "a in [1,2,3]", "cmp5"))
        assert "(a in [1,2,3])" in src
        assert "_in(" not in src

    def test_mixed_type_equality_keeps_helper(self):
        # JSONata：`1 = true` 为 false（数字与布尔不同类型），不能发 `(1==True)`
        src = dcg.generate(self._sch("int64", "int64", "bool", "1 = true", "cmp6"))
        assert "_eq(" in src

    def test_untyped_operands_keep_helper(self):
        # 未声明类型（any）可能 undefined，必须保留 helper 的 undefined 传播
        sch = {"name": "cmp7", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "any", "value": "$.a"},
                   "b": {"type": "any", "value": "$.b"},
                   "v": {"type": "bool", "expr": "a < b"}}}}
        src = dcg.generate(sch)
        assert "_lt(" in src

    def test_undefined_condition_keeps_co(self):
        sch = {"name": "cmp8", "output": {"shape": "list"},
               "out": {"type": "object", "fields": {
                   "a": {"type": "any", "value": "$.a"},
                   "v": {"type": "str", "expr": "a ? 'y' : 'n'"}}}}
        src = dcg.generate(sch)
        assert "_co(" in src


class TestBuiltinFunctionInline:
    """§4.2：有语义一致的 Python 内建等价的列函数直接发内建表达式。"""

    @staticmethod
    def _one(expr, out_type, name, extra=None):
        fields = {"v": {"type": out_type, "expr": expr}}
        if extra:
            fields.update(extra)
        return {"name": name, "output": {"shape": "list"},
                "out": {"type": "object", "fields": fields}}

    @staticmethod
    def _run(src, rows):
        g = {}
        exec(src, g)
        return g["run"](rows)

    def _check(self, expr, out_type, expect, needle, name):
        src = dcg.generate(self._one(expr, out_type, name))
        assert needle in src, (expr, src)
        assert self._run(src, [{"x": 1}]) == [[expect]], (expr, src)

    def test_string_builtins(self):
        self._check("$length('abc')", "int64", 3, "len('abc')", "bi1")
        self._check("$uppercase('ab')", "str", "AB", ".upper()", "bi2")
        self._check("$trim('  x  ')", "str", "x", ".strip()", "bi3")
        self._check("$contains('abc', 'b')", "bool", True, "('b' in 'abc')", "bi4")
        self._check("$split('a,b', ',')", "list", ["a", "b"], ".split(',')", "bi5")
        self._check("$substring('abcde', 1, 2)", "str", "bc", "[1:3]", "bi6")
        self._check("$substringBefore('a-b', '-')", "str", "a", ".partition('-')[0]", "bi7")
        self._check("$substringAfter('a-b', '-')", "str", "b", ".partition('-')[2]", "bi8")
        self._check("$replace('a-b', '-', '+')", "str", "a+b", ".replace('-','+')", "bi9")
        self._check("$pad('ab', 5)", "str", "ab   ", ".ljust(5)", "bi10")

    def test_numeric_builtins(self):
        self._check("$abs(-3)", "int64", 3, "abs(", "bi11")
        self._check("$sum([1, 2, 3])", "int64", 6, "sum([1,2,3])", "bi12")
        self._check("$max([1, 2, 3])", "int64", 3, "max([1,2,3])", "bi13")
        self._check("$min([1, 2, 3])", "int64", 1, "min([1,2,3])", "bi14")
        self._check("$average([1, 2])", "float64", 1.5, "(sum([1,2])/len([1,2]))", "bi15")
        self._check("$count([1, 2, 3])", "int64", 3, "len([1,2,3])", "bi16")

    def test_boolean_builtins(self):
        self._check("$boolean(1)", "bool", True, "bool(1)", "bi17")
        self._check("$boolean(0)", "bool", False, "bool(0)", "bi18")
        self._check("$not(true)", "bool", False, "(not True)", "bi19")

    def test_math_builtins_inline(self):
        self._check("$floor(3.7)", "int64", 3, "math.floor(3.7)", "bi20")
        self._check("$ceil(3.2)", "int64", 4, "math.ceil(3.2)", "bi21")
        self._check("$round(3.5)", "int64", 4, "round(3.5)", "bi22")
        self._check("$round(2.5)", "int64", 2, "round(2.5)", "bi23")
        self._check("$power(2, 10)", "int64", 1024, "(2**10)", "bi24")
        src = dcg.generate(self._one("$sqrt(9)", "float64", "bi25"))
        assert "math.sqrt(9)" in src, src
        assert src.startswith("import math\n"), src[:40]
        assert self._run(src, [{"x": 1}]) == [[3.0]]

    def test_sqrt_negative_or_unprovable_keeps_helper(self):
        # math.sqrt 对负数抛 ValueError，而 helper 把负数映射成 undefined -> 不能盲目内联
        neg = dcg.generate(self._one("$sqrt(-4)", "float64", "bi26"))
        assert "_sq(" in neg, neg
        field = dcg.generate(self._one(
            "$sqrt(n)", "float64", "bi27",
            extra={"n": {"type": "float64", "value": "$.n"}}))
        assert "_sq(" in field, field

    def test_functions_without_builtin_equivalence_keep_helper(self):
        cases = [
            ("$number('42')", "int64", "_N(", "bi28"),
            ("$type(1)", "str", "_ty(", "bi29"),
            ("$exists(1)", "bool", "_ex(", "bi30"),
            ("$join(['a', 'b'], '-')", "str", "_jn(", "bi31"),
        ]
        for expr, out_type, needle, name in cases:
            src = dcg.generate(self._one(expr, out_type, name))
            assert needle in src, (expr, src)

class TestArrayLiteralInline:
    """§4.2 明细内联：数组字面量上的 $sum/$reverse/$sort 发内建。"""

    @staticmethod
    def _one(expr, out_type="any", name="ai"):
        return {"name": name, "output": {"shape": "list"},
                "out": {"type": "object",
                        "fields": {"v": {"type": out_type, "expr": expr}}}}

    @staticmethod
    def _run(src, rows):
        g = {}
        exec(src, g)
        return g["run"](rows)

    def test_sum_of_empty_array_inline(self):
        src = dcg.generate(self._one("$sum([])", "int64", "ai1"))
        assert "sum([])" in src and "import" not in src, src
        assert self._run(src, [{"x": 1}]) == [[0]]

    def test_reverse_literal_array_inline(self):
        src = dcg.generate(self._one("$reverse([1, 2, 3])", "list", "ai2"))
        assert "([1,2,3])[::-1]" in src and "import" not in src, src
        assert self._run(src, [{"x": 1}]) == [[[3, 2, 1]]]

    def test_sort_literal_arrays_inline(self):
        src = dcg.generate(self._one("$sort([3, 1, 2])", "list", "ai3"))
        assert "sorted([3,1,2])" in src and "import" not in src, src
        assert self._run(src, [{"x": 1}]) == [[[1, 2, 3]]]
        src = dcg.generate(self._one("$sort(['b', 'a'])", "list", "ai4"))
        assert "sorted(['b','a'])" in src, src
        assert self._run(src, [{"x": 1}]) == [[["a", "b"]]]

    def test_reverse_and_sort_on_path_keep_helper(self):
        src = dcg.generate(self._one("$reverse($.xs)", "list", "ai5"))
        assert "_rv(" in src, src
        src = dcg.generate(self._one("$sort($.xs)", "list", "ai6"))
        assert "_so(" in src, src

    def test_mixed_sort_and_empty_max_keep_helper(self):
        src = dcg.generate(self._one("$sort([3, 'a'])", "list", "ai7"))
        assert "_so(" in src, src
        assert self._run(src, [{"x": 1}])[0][0] is dcg.runtime.UNDEF
        for expr, needle, name in [("$max([])", "_mx(", "ai8"),
                                   ("$average([])", "_av(", "ai9")]:
            src = dcg.generate(self._one(expr, "any", name))
            assert needle in src, (expr, src)

    def test_sum_with_unprovable_element_keeps_helper(self):
        src = dcg.generate(self._one("$sum([1, $.n])", "any", "ai10"))
        assert "_sm(" in src, src

class TestPyDollarRefsDoNotCreateDependencies:
    """`py{}` / 反引号里的 `$.字段名` 只校验声明、读原始行，不构成依赖边（§4.5.1）。"""

    @staticmethod
    def _gen(fields):
        return dcg.generate({"name": "pyref", "output": {"shape": "list"},
                             "out": {"fields": fields}})

    @staticmethod
    def _run(src, rows):
        ns = {}
        exec(src, ns)
        return ns["run"](rows)

    def test_pyblock_dollar_ref_is_not_a_dependency(self):
        src = self._gen({"v": {"type": "int64", "expr": "py{ return $.a }"},
                         "a": {"type": "int64", "expr": "v + 1"}})
        assert self._run(src, [{"a": 99}]) == [[99, 100]]

    def test_backtick_dollar_ref_is_not_a_dependency(self):
        src = self._gen({"v": {"type": "int64", "expr": "`$.a`"},
                         "a": {"type": "int64", "expr": "v + 1"}})
        assert self._run(src, [{"a": 99}]) == [[99, 100]]

    def test_backtick_bare_name_is_a_dependency(self):
        src = self._gen({"v": {"type": "int64", "expr": "`a + 1`"},
                         "a": {"type": "int64", "expr": "$.x + 1"}})
        assert "a=_ad(" in src, src
        assert "_ix_get(R,'x')" in src, src
        assert self._run(src, [{"x": 5}]) == [[7, 6]]

    def test_real_cycle_between_fields_is_still_reported(self):
        with pytest.raises(Exception) as ei:
            self._gen({"v": {"type": "int64", "expr": "`a + 1`"},
                       "a": {"type": "int64", "expr": "v + 1"}})
        assert "成环" in str(ei.value)

class TestFieldDefault:
    """字段级 default：算出 undefined 时替换成编译期常量（§2.3）。"""

    @staticmethod
    def _gen(fields):
        return dcg.generate({"name": "dflt", "output": {"shape": "list"},
                             "out": {"fields": fields}})

    @staticmethod
    def _run(src, rows):
        ns = {}
        exec(src, ns)
        return ns["run"](rows)

    def test_default_replaces_undefined(self):
        src = self._gen({"v": {"type": "int64", "expr": "$uppercase(1)",
                               "default": 0}})
        assert self._run(src, [{"x": 1}]) == [[0]]

    def test_default_wins_over_optional_none(self):
        src = self._gen({"v": {"type": "any", "optional": True,
                               "expr": "$uppercase(1)", "default": "n/a"}})
        assert self._run(src, [{"x": 1}]) == [["n/a"]]

    def test_value_present_ignores_default(self):
        src = self._gen({"v": {"type": "int64", "expr": "$.x", "default": 42}})
        assert self._run(src, [{"x": 7}]) == [[7]]

    def test_default_in_nested_object_and_row(self):
        """`items` 移除后，等价写法是位置行（type:"list" + fields）：default 作用在行内字段上。"""
        fields = {
            "o": {"type": "object", "fields": {
                "a": {"type": "int64", "expr": "$uppercase(1)", "default": -1}}},
            "xs": {"type": "list", "fields": {
                "x": {"type": "int64", "expr": "$uppercase(1)", "default": 9}}},
        }
        src = self._gen(fields)
        assert self._run(src, [{"x": 1}]) == [[{"a": -1}, [9]]]

    def test_default_with_fields_is_rejected(self):
        with pytest.raises(dcg.SchemaError) as ei:
            self._gen({"o": {"type": "object", "default": {},
                             "fields": {"a": {"type": "int64", "expr": "$.x"}}}})
        assert "default" in str(ei.value)

    def test_default_type_mismatch_is_rejected(self):
        with pytest.raises(dcg.SchemaError) as ei:
            self._gen({"v": {"type": "int64", "expr": "$.x", "default": "x"}})
        assert "default" in str(ei.value)


class TestDynamicIndexInline:
    """动态下标：required 字段内联裸下标；optional/default 走 _ix_get。"""

    def _src(self, spec, ftype="any"):
        import dcg
        sch = {"name": "t", "output": {"shape": "list"},
               "out": {"type": "object",
                       "fields": {"i": {"type": "int64", "value": "$.i"},
                                  "v": dict(spec, type=ftype)}}}
        return dcg.generate(sch)

    def test_required_dynamic_index_is_bare(self):
        src = self._src({"expr": "$.a[$i]"})
        assert "R['a'][i]" in src
        assert "_ix_get" not in src
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        assert ns["run"]([{"a": [10, 20], "i": 1}]) == [[1, 20]]

    def test_required_dynamic_index_raises_on_oob(self):
        src = self._src({"expr": "$.a[$i]"})
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        try:
            ns["run"]([{"a": [10], "i": 5}])
        except IndexError:
            pass
        else:
            raise AssertionError("越界应抛 IndexError")

    def test_optional_dynamic_index_uses_ix_get(self):
        src = self._src({"expr": "$.a[$i]", "optional": True})
        assert "_ix_get" in src
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        assert ns["run"]([{"i": 0}]) == [[0, None]]

    def test_default_dynamic_index_falls_back(self):
        src = self._src({"expr": "$.a[$i]", "default": 0})
        assert "_ix_get" in src
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        assert ns["run"]([{"i": 0}]) == [[0, 0]]


class TestPathUndefinedSemantics:
    """B 口径：单段路径 / any 读值缺键 -> undefined；多段链仍裸链（缺键=KeyError）。"""

    def _gen(self, fields):
        import dcg
        sch = {"name": "p", "output": {"shape": "list"},
               "out": {"type": "object", "fields": fields}}
        return dcg.generate(sch)

    def test_single_symbol_expr_path_missing_is_undefined(self):
        src = self._gen({"o": {"type": "str", "expr": "'x' & $.zz"}})
        assert "_ix_get(R,'zz')" in src
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        assert ns["run"]([{"other": 1}]) == [["x"]]

    def test_exists_missing_path_single_use_is_false(self):
        src = self._gen({"e": {"type": "bool", "expr": "$exists($.missing)"}})
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        assert ns["run"]([{"other": 1}]) == [[False]]

    def test_value_any_missing_path_is_undefined(self):
        import dcg.runtime as rt
        src = self._gen({"v": {"type": "any", "value": "$.a"}})
        assert "_ix_get" in src
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        got = ns["run"]([{"other": 1}])[0][0]
        assert got is rt.UNDEF

    def test_multi_segment_paths_keep_bare_reads(self):
        src = self._gen({"v": {"type": "int64", "expr": "$.a.b + 1"}})
        assert "_ix_get" not in src
        assert "R['a']['b']" in src


class TestNestedSiblingFields:
    """嵌套 fields 是独立作用域：同层兄弟可裸名互引、前向引用合法、环报错、内层遮蔽外层。"""

    def _gen(self, fields, shape="list"):
        import dcg
        sch = {"name": "n", "output": {"shape": shape},
               "out": {"type": "object", "fields": fields}}
        return dcg.generate(sch)

    def _run(self, src, rows):
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)
        return ns["run"](rows)

    def test_sibling_reference_inside_nested_object(self):
        src = self._gen({
            "doc": {"type": "object", "fields": {
                "a": {"type": "int64", "value": "$.a"},
                "b": {"type": "int64", "value": "$.b"},
                "s": {"type": "int64", "expr": "a + b"},
            }},
        })
        assert self._run(src, [{"a": 5, "b": 7}]) == [[{"a": 5, "b": 7, "s": 12}]]

    def test_nested_forward_reference(self):
        src = self._gen({
            "doc": {"type": "object", "fields": {
                "s": {"type": "int64", "expr": "a + b"},
                "a": {"type": "int64", "value": "$.a"},
                "b": {"type": "int64", "value": "$.b"},
            }},
        })
        assert self._run(src, [{"a": 2, "b": 3}]) == [[{"s": 5, "a": 2, "b": 3}]]

    def test_nested_cycle_is_schema_error(self):
        from dcg.schema import SchemaError
        with pytest.raises(SchemaError) as ei:
            self._gen({"doc": {"type": "object", "fields": {
                "a": {"type": "int64", "expr": "b + 1"},
                "b": {"type": "int64", "expr": "a + 1"},
            }}})
        assert "成环" in str(ei.value)
        assert "out.fields.doc.fields" in str(ei.value)

    def test_inner_shadows_outer(self):
        src = self._gen({
            "x": {"type": "int64", "value": "$.outer"},
            "doc": {"type": "object", "fields": {
                "x": {"type": "int64", "value": "$.inner"},
                "y": {"type": "int64", "expr": "x + 1"},
            }},
        })
        assert self._run(src, [{"outer": 100, "inner": 7}]) == [[100, {"x": 7, "y": 8}]]

    def test_inner_references_outer_field(self):
        src = self._gen({
            "x": {"type": "int64", "value": "$.a"},
            "doc": {"type": "object", "fields": {
                "y": {"type": "int64", "expr": "x + $.b"},
            }},
        })
        assert self._run(src, [{"a": 5, "b": 6}]) == [[5, {"y": 11}]]

    def test_two_level_nesting(self):
        src = self._gen({
            "doc": {"type": "object", "fields": {
                "mid": {"type": "object", "fields": {
                    "a": {"type": "int64", "value": "$.a"},
                    "b": {"type": "int64", "value": "$.b"},
                    "s": {"type": "int64", "expr": "a * b"},
                }},
            }},
        })
        assert self._run(src, [{"a": 5, "b": 7}]) == [[{"mid": {"a": 5, "b": 7, "s": 35}}]]

    def test_nested_default_fills_undefined(self):
        src = self._gen({"doc": {"type": "object", "fields": {
            "n": {"type": "int64", "value": "$.missing", "default": 0},
            "x": {"type": "int64", "value": "$.a"},
        }}})
        assert self._run(src, [{"a": 8}]) == [[{"n": 0, "x": 8}]]

    def test_undeclared_sibling_is_schema_error(self):
        from dcg.schema import SchemaError
        with pytest.raises(SchemaError) as ei:
            self._gen({"doc": {"type": "object", "fields": {
                "s": {"type": "int64", "expr": "zzz + 1"},
            }}})
        assert "裸名 'zzz'" in str(ei.value)


# ==========================================================================
# 位置行（type:"list" + fields）：v1 文档键 {"doc": [行, ...]} 的结构化表达
# ==========================================================================
POSITIONAL_SCHEMA = {
    "name": "positional",
    "input": {"source": "$.index"},
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        "doc": {"type": "list", "fields": {
            "hour": {"type": "str", "value": "$.0"},
            "hourA": {"type": "int64", "value": "$.1"},
        }},
    }},
}


class TestPositionalRow:
    def test_dict_shape_returns_document_with_positional_rows(self):
        run, src = make_runner(POSITIONAL_SCHEMA)
        # 全字段可证（str/int64 + value）→ task-10 起直接发列表字面量，无 `_rl(` helper
        assert "_rl([" not in src and "[R[0],R[1]]" in src
        assert run({"index": [("a0", 0), ("a1", 1)]}) == {
            "doc": [["a0", 0], ["a1", 1]]}

    def test_undefined_becomes_none_to_keep_positions(self):
        schema = {
            "name": "positional_undef",
            "output": {"shape": "dict"},
            "out": {"type": "object", "fields": {
                "doc": {"type": "list", "fields": {
                    "a": {"type": "any", "value": "$.zz"},
                    "b": {"type": "int64", "value": "$.b"},
                }},
            }},
        }
        run, _ = make_runner(schema)
        assert run([{"b": 1}]) == {"doc": [[None, 1]]}

    def test_single_uses_rows_shape(self):
        schema = {
            "name": "positional_single",
            "input": {"source": "$.index"},
            "output": {"shape": "rows"},
            "out": {"type": "object", "fields": {
                "doc": {"type": "list", "fields": {
                    "hour": {"type": "str", "value": "$.0"},
                }},
            }},
        }
        run, _ = make_runner(schema)
        assert run({"index": [("a0",), ("a1",)]}) == [
            {"doc": ["a0"]}, {"doc": ["a1"]}]

    def test_list_shape_gives_per_row_document_lists(self):
        schema = {
            "name": "positional_list",
            "input": {"source": "$.index"},
            "output": {"shape": "list"},
            "out": {"type": "object", "fields": {
                "doc": {"type": "list", "fields": {
                    "hour": {"type": "str", "value": "$.0"},
                }},
            }},
        }
        run, _ = make_runner(schema)
        assert run({"index": [("a0",), ("a1",)]}) == [[["a0"]], [["a1"]]]
