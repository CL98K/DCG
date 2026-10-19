"""哨兵（undefined 单例）身份/相等性对抗测试 —— verifier-dev, task-3.

Lead 裁决（m00727 第 1 条）：产物里的 undefined 哨兵必须是**自建单例对象**，
禁止 `0`/`None`/`False`/`0.0` 或任何小整数缓存/全局单例字面量。Lead 的静态门
（evidence/accept.py，Lead 所有）只覆盖「哨兵被绑成文字面量」这一形态；本文件补
**静态覆盖不到**的那一类：产物用 `==` 而不是 `is` 比较哨兵时，`__eq__` 会回退到
身份比较并**静默**把合法值误判为 undefined。

设计原则（对抗性，不依赖实现内部命名）：
  * 哨兵名字不假设为 `_U` 或 `UNDEF`：两者都探测，取产物命名空间里实际存在的那个；
  * 断言只用「身份」和「精确值」，不用「truthiness」（`0`/`False`/`""` 都是假的，
    用 truthiness 断言会把本次要抓的 bug 掩盖掉）；
  * 每个用例都同时给出「合法值必须原样通过」与「哨兵必须仍能表示缺失」两侧，
    避免把「哨兵根本不存在」误判成通过。

断言分三层：
  A. 静态层  —— 产物源码里哨兵的**绑定形态**与**比较方式**（不跑代码）
  B. 行为层  —— 0 / 0.0 / False / None / "" / [] / {} / 自定义 __eq__ 对象
  C. 语义层  —— 哨兵仍能表示「字段缺失」，且与任何合法值**不是同一个对象**

期望值来源：dcg/runtime.py 的 UNDEFINED_SEMANTICS（CONTRACT §4.4）+ `_num`
（`isinstance(v,(int,float)) and not isinstance(v,bool)`：bool 不是 number）。
"""
from __future__ import annotations

import os
import re
import sys
import warnings

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

try:
    import dcg  # noqa: E402
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    dcg = None
    _IMPORT_ERROR = exc

V2_REASON = ""
if dcg is None:
    V2_REASON = f"dcg v2 包不可导入: {type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}"
elif not callable(getattr(dcg, "generate", None)):
    V2_REASON = "dcg v2 引擎尚未落地（缺 dcg.generate）"
# 与 tests/test_e2e_flight.py 同一门禁口径：收集 + skip（exit 0），不用模块级 skip
requires_v2 = pytest.mark.skipif(bool(V2_REASON), reason=V2_REASON or "dcg v2 未就绪")


# ==========================================================================
# helpers
# ==========================================================================
def _schema(fields, name="sent"):
    return {
        "name": name,
        "output": {"shape": "list", "single": False, "filter": False},
        "out": {"type": "object", "fields": fields},
    }


def _compile_src(src):
    """按 Lead 要求：`SyntaxWarning` 必须是错误（产物里有 `is <int 字面量>` 时炸）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        return compile(src, "<sentinel-probe>", "exec")


def make_ns(schema):
    """generate -> compile -> exec -> 产物命名空间（哨兵就在里面）。"""
    src = dcg.generate(schema)
    ns = {}
    exec(_compile_src(src), ns)
    return ns, src


def sentinel_of(ns):
    """产物里的 undefined 哨兵绑定：(`名字`, 对象)；找不到返回 (None, None)。"""
    for name in ("_U", "UNDEF", "_UNDEF", "UNDEFINED"):
        if name in ns:
            return name, ns[name]
    return None, None


def run1(ns, row):
    return ns["run"]([row])


def add_schema():
    return _schema({"a": {"type": "any", "value": "$.a"},
                    "b": {"type": "any", "value": "$.b"},
                    "s": {"type": "any", "expr": "$.a + $.b"}})


def exists_schema():
    return _schema({"a": {"type": "any", "value": "$.a"},
                    "e": {"type": "bool", "expr": "$exists($.a)"},
                    "n": {"type": "bool", "expr": "$not($.a)"}})


class AlwaysEq:
    """最阴的对手：与任何对象都「相等」。若产物用 `==` 比哨兵，这里必然静默误判。"""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0


# ==========================================================================
# A. 静态层
# ==========================================================================
@requires_v2
class TestSentinelStatic:
    def test_binding_is_not_a_literal(self):
        """哨兵必须来自 dcg.runtime（真实单例），绝不能自己绑成假值字面量。

        修正记录（verifier-dev, 2026-10-17）: 旧版本要求产物里必须出现
        `_U = ...` 绑定行，于是把「`from dcg.runtime import *` 引入 runtime 的
        `_U` 单例」这一**正确**做法误判为失败（Lead 的 accept.py 哨兵 gate 有
        同一个盲点）。判定改为：①任何本地绑定都不得是假值字面量；②必须能从
        runtime 取到哨兵（import 形态或非字面量绑定）；③runtime 里的哨兵对象
        本身与 0/None/False/"" 不同一。
        """
        _, src = make_ns(add_schema())
        bad = re.findall(r"^\s*(_U|UNDEF)\s*=\s*(.+?)\s*$", src, re.M)
        offenders = [f"{name} = {rhs}" for name, rhs in bad
                     if re.match(r"^(0|0\.0|-0\.0|False|True|None|''|\"\")\s*$", rhs.strip())]
        assert not offenders, (
            "哨兵被绑成假值字面量（会被 is 身份比较误判为 undefined）: %s" % offenders)
        imported = re.search(
            r"from\s+dcg\.runtime\s+import\s+\*|from\s+dcg\.runtime\s+import[^\n]*\b(_U|UNDEF)\b",
            src)
        assert bad or imported, (
            "产物既没有哨兵绑定，也没有从 dcg.runtime 取哨兵 —— 契约要求 undefined 单例")
        # runtime 侧的真实性：`_U` 必须在 __all__ 里（否则 `import *` 拿不到）且不是假值。
        from dcg import runtime as _rt
        star = {}
        exec("from dcg.runtime import *", star)  # noqa: S102
        sentinel = None
        for name in ("_U", "UNDEF"):
            if hasattr(_rt, name):
                sentinel = (name, getattr(_rt, name))
                break
        assert sentinel is not None, "dcg.runtime 里找不到哨兵 _U / UNDEF"
        name, obj = sentinel
        if hasattr(_rt, "__all__"):
            assert name in _rt.__all__, (
                f"runtime.{name} 不在 __all__ 里，产物的 `import *` 取不到它")
        assert name in star, f"`from dcg.runtime import *` 没有带入 {name}"
        assert obj is not 0 and obj is not None and obj is not False and obj != "", (
            f"runtime.{name} 与假值同一/相等（{obj!r}）→ is 身份比较会误判")

    def test_compared_by_identity_only(self):
        """产物必须用 `is` / `is not` 比哨兵，绝不能出现 `==` / `!=`。"""
        _, src = make_ns(add_schema())
        offenders = [m.group(0) for m in
                     re.finditer(r"(_U|UNDEF)\s*[=!]=|[=!]=\s*(_U|UNDEF)", src)]
        assert not offenders, (
            "哨兵用相等性比较（`__eq__` 对任意对象可能为真 → 静默误判）: %s" % offenders)

    def test_compiles_under_syntaxwarning_as_error(self):
        """`-W error::SyntaxWarning` 下产物必须能编译（`is <int 字面量>` 会炸）。"""
        src = dcg.generate(add_schema())
        _compile_src(src)  # 抛出即失败

    @pytest.mark.parametrize("name", ["int64", "float64", "str", "bool", "any"])
    def test_no_runtime_coercion_of_declared_type(self, name):
        """`type` 是声明不是转换（CONTRACT §4.2）：产物里不得凭空出现 int()/float()。"""
        src = dcg.generate(_schema({"a": {"type": name, "value": "$.a"}}))
        assert not re.search(r"\b(?:int|float|bool|str)\s*\(", src), (
            "type=%s 的声明字段产生了运行期转换调用: %s" % (name, src))


# ==========================================================================
# B. 行为层 —— 合法假值不得被当成 undefined
# ==========================================================================
@requires_v2
class TestFalsyValuesAreRealValues:
    def test_zero_arithmetic(self):
        ns, _ = make_ns(add_schema())
        # 容器类型是另一个缺陷（D1 行容器 tuple）；这里只判定 Sentinel 语义，
        # 所以统一 list() 归一，避免一个断言同时踩两个缺陷。
        def out(row):
            return [list(r) for r in run1(ns, row)]

        assert out({"a": 0, "b": 5}) == [[0, 5, 5]]
        assert out({"a": 5, "b": 0}) == [[5, 0, 5]]
        assert out({"a": 0, "b": 0}) == [[0, 0, 0]]

    def test_zero_like_values(self):
        ns, _ = make_ns(add_schema())

        def out(row):
            return [list(r) for r in run1(ns, row)]

        assert out({"a": 0.0, "b": 5}) == [[0.0, 5, 5.0]]
        assert out({"a": -0.0, "b": 5}) == [[-0.0, 5, 5.0]]
        assert out({"a": 256, "b": -256}) == [[256, -256, 0]]

    def test_sentinel_is_a_distinct_object(self):
        ns, _ = make_ns(add_schema())
        name, s = sentinel_of(ns)
        assert s is not None, "产物里没有哨兵绑定"
        for other, label in [(0, "0"), (0.0, "0.0"), (False, "False"), (None, "None"),
                             ("", "''"), ([], "[]"), ({}, "{}"), ((), "()")]:
            assert s is not other, f"哨兵与 {label} 是同一对象（is 比较会误判）"
        assert not isinstance(s, (int, float, bool, str, list, dict, tuple, type(None))), (
            f"哨兵 {name} 是内建假值类型 {type(s).__name__}，小整数/字面量缓存会破坏身份比较")

    def test_exists_predicate_on_falsy_values(self):
        """`$exists` 对 0/''/[]/{}/false/null 必须为 True（它们都存在）。"""
        ns, _ = make_ns(exists_schema())
        for v in (0, 0.0, "", [], {}, False, None):
            out = run1(ns, {"a": v})
            assert out[0][1] is True, f"$exists 对合法值 {v!r} 返回 {out[0][1]!r}，应为 True"

    def test_exists_predicate_on_missing_field(self):
        """缺失字段（真 undefined）必须为 False —— 反向对照，防止「一律 True」蒙过。"""
        ns, _ = make_ns(exists_schema())
        out = run1(ns, {})
        assert out[0][1] is False, f"$exists 对缺失字段返回 {out[0][1]!r}，应为 False"

    @pytest.mark.parametrize("value", [False, None, "", [], {}, AlwaysEq()])
    def test_non_number_operand_is_loud_not_silent(self, value):
        """非数值操作数：必须显式失败（TypeError），绝不能静默返回一个数字或哨兵。"""
        ns, _ = make_ns(add_schema())
        _, s = sentinel_of(ns)
        try:
            out = run1(ns, {"a": value, "b": 5})
        except TypeError:
            return  # 契约 §4.4：$add 对非数值报 TypeError（响亮失败）
        assert out[0][2] is s, (
            f"a={value!r} 参与 $add 未报错且返回了 {out[0][2]!r}（既非 undefined 也非数值）")
        assert out[0][2] is not 0, f"a={value!r} 被静默当成 undefined 之外的东西"

    def test_custom_eq_object_cannot_fake_undefined(self):
        """`__eq__` 恒真对象：若产物用 `==` 比哨兵，它会被静默当成 undefined。"""
        ns, _ = make_ns(add_schema())
        _, s = sentinel_of(ns)
        obj = AlwaysEq()
        try:
            out = run1(ns, {"a": obj, "b": 5})
        except TypeError:
            return
        assert not (out[0][2] is s), (
            "自定义 __eq__ 对象被当成了 undefined（产物用 == 比哨兵）")


# ==========================================================================
# C. 语义层 —— 哨兵仍要能表示缺失
# ==========================================================================
def substring_schema():
    """`$substring` 对非字符串返回 UNDEF（dcg/runtime.py:282-283）—— 文档化的 undefined 来源。"""
    return _schema({"a": {"type": "any", "value": "$.a"},
                    "sub": {"type": "any", "expr": "$substring($.a, 0)"}})


def substring_len_schema():
    return _schema({"a": {"type": "any", "value": "$.a"},
                    "n": {"type": "any", "value": "$.n"},
                    "sub": {"type": "any", "expr": "$substring($.a, 0, $.n)"}})


def missing_path_schema():
    """CONTRACT §4.4 的 `miss` 用例（接口实现细节文档.md:435-439 明文要求）。"""
    return _schema({"c": {"type": "str", "expr": "'x' & $.zz"},        # §4.4-1
                    "eq": {"type": "bool", "expr": "$.zz = $.zz"},     # §4.4-3
                    "lt": {"type": "bool", "expr": "$.zz < 5"}})       # §4.4-4


@requires_v2
class TestSentinelStillWorks:
    def test_undefined_from_documented_operator_is_the_sentinel(self):
        """文档化的 undefined（$substring 遇非字符串）必须产出哨兵本身。"""
        ns, _ = make_ns(substring_schema())
        name, s = sentinel_of(ns)
        out = run1(ns, {"a": 123})
        got = list(out[0])[1]
        assert got is s, f"$substring(123,0) 未产出哨兵 {name}，实得 {got!r}"
        assert got is not 0, "哨兵就是 0 —— undefined 与合法 0 无法区分"

    def test_explicit_length_zero_is_not_the_default(self):
        """哨兵=0 的第二条静默错值：`length=0` 被 `length is _U` 误判成「缺省参数」。

        实测 evidence/v2_sentinel_probe.txt: `$substring('hello',0,0)` -> 'hello'（应为 ''）。
        """
        ns, _ = make_ns(substring_len_schema())
        assert list(run1(ns, {"a": "hello", "n": 0})[0])[2] == "", \
            "$substring('hello',0,0) 应为空串（显式 0 被当成缺省 length）"

    def test_present_zero_in_optional_field_is_kept(self):
        """optional 字段存在且值为 0 时，必须保留 0（不能与 undefined 混淆、也不能丢）。"""
        sch = _schema({"a": {"type": "any", "value": "$.a"},
                       "g": {"type": "int64", "value": "$.b", "optional": True}})
        ns, src = make_ns(sch)
        out = run1(ns, {"a": 1, "b": 0})
        assert list(out[0]) == [1, 0], (
            "optional 字段在值为 0 时被丢弃或替换；产物:\n%s" % src)


@requires_v2
class TestContract44MissingPath:
    """缺失路径必须走 undefined 哨兵，而不是 `KeyError`。

    契约依据（接口实现细节文档.md:435-439，§4.4 六条语义逐条用 `miss` 表述）：
      1. `'x' & miss == 'x'`
      3. `miss = miss == False`
      4. `miss < 5 == undefined`

    实测（evidence/v2_sentinel_probe.txt §D）：今天的产物是 `_zz=R['zz']` → 三例**全部
    KeyError**，即 §4.4 的六条语义在产物里**不可达**。注意 CONTRACT §4.2 的示例码用的是
    `_a=r[5]["a"]`（直接下标），与 §4.4 的 `miss` 用例存在文本冲突 → 归属标为
    「实现缺陷（parser-dev），需 Lead 确认口径」。
    """

    def test_concat_with_missing_path_is_empty_string(self):
        ns, _ = make_ns(missing_path_schema())
        out = run1(ns, {})
        assert list(out[0])[0] == "x", f"§4.4-1 'x' & miss 应为 'x'，实得 {out[0]!r}"

    def test_equality_with_missing_path_is_false(self):
        ns, _ = make_ns(missing_path_schema())
        out = run1(ns, {})
        assert list(out[0])[1] is False, f"§4.4-3 miss = miss 应为 False，实得 {out[0]!r}"

    def test_comparison_with_missing_path_is_undefined(self):
        ns, _ = make_ns(missing_path_schema())
        _, s = sentinel_of(ns)
        out = run1(ns, {})
        assert list(out[0])[2] is s, f"§4.4-4 miss < 5 应为 undefined，实得 {out[0]!r}"
