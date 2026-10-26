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
sys.path.insert(0, os.path.join(ROOT, "tests"))

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
        "output": {"shape": "list"},
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
    # `a` 必须声明 `optional`：契约（用户裁决 2026-10-25）里「没有声明 optional 就是
    # KeyError，声明了才是 undefined」。本组测的是 `$exists` 对 undefined 的判定，
    # 所以读法必须走安全口径，否则缺键会在读的时候就 KeyError，测不到 `$exists`。
    return _schema({"a": {"type": "any", "value": "$.a", "optional": True},
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

        判定标准：①任何本地绑定都不得是假值字面量；②必须能从
        runtime 取到哨兵（import 形态或非字面量绑定）；③runtime 里的哨兵对象
        本身与 0/None/False/"" 不同一。绝不能用「产物里出现 `_U = ...` 绑定行」
        来判定——正确做法恰恰是从 `dcg.runtime import *` 取单例。
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

    def test_sentinel_is_falsy_under_bool(self):
        """哨兵必须「假值」：`bool(UNDEF) is False`（与接口文档 §4.4 六条语义并列的契约）。

        为什么必须钉：`_tru` 可省判据（接口 §4.4 / 架构 §7.5）只对「值域 ⊆
        True/False/undefined」的节点成立，其等价性**依赖** `bool(undefined) is False`
        —— 若有人把 `_Undef.__bool__` 改成返回 True，`if <undefined>` 会变成真，
        `$filter` 之类会静默保留本应丢弃的元素，而现有测试抓不到这种漂移。
        """
        from dcg import runtime as _rt

        sentinel = None
        for name in ("_U", "UNDEF"):
            if hasattr(_rt, name):
                sentinel = getattr(_rt, name)
                break
        assert sentinel is not None, "dcg.runtime 里找不到哨兵 _U / UNDEF"
        assert bool(sentinel) is False, (
            "undefined 哨兵在 `if x` 语境下为真，_tru 白名单等价性失效")
        # 必须是 _Undef 自己定义的 __bool__，不能靠 __len__ 之类的偶然假值（防漂移）。
        assert "__bool__" in type(sentinel).__dict__, (
            f"{type(sentinel).__name__} 未显式定义 __bool__（契约依赖它为 False）")

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

    @pytest.mark.parametrize("value", [False, []])
    def test_non_number_operand_follows_python(self, value):
        """非数值操作数：Python 自己能算出来就直接返回（EAFP），算不出来才响亮失败。

        `False + 5 == 5`、`[] + 5` 抛 `TypeError` —— 后者必须冒泡成
        `TypeError`，绝不能静默返回数字或哨兵。
        """
        ns, _ = make_ns(add_schema())
        _, s = sentinel_of(ns)
        if value is False:
            out = run1(ns, {"a": value, "b": 5})
            assert out[0][2] == 5, f"a=False 参与 $add 应得 5（Python 语义），实得 {out[0][2]!r}"
            return
        with pytest.raises(TypeError):
            run1(ns, {"a": value, "b": 5})

    def test_custom_eq_object_cannot_fake_undefined(self):
        """`__eq__` 恒真对象不得被哨兵守卫当成 undefined。

        守卫一律用 `is _U`（身份比较），所以 `AlwaysEq()` 即使 `==` 任何东西都
        为真也冒充不了 undefined：`$add` 对它抛 `TypeError`（不是数值），
        `optional` 字段拿到它时落成它本身而非 `None`。
        """
        ns, s = make_ns(opt_identity_schema())
        obj = AlwaysEq()
        got = run1(ns, {"a": obj})[0][0]
        assert got is not s, "自定义 __eq__ 对象被当成了 undefined（产物用 == 比哨兵）"
        assert got is obj, f"optional 字段应原样透传输入对象，实得 {got!r}"
        assert got is not None, "__eq__ 恒真对象被守卫误判成 undefined 并落成了 None"


def opt_identity_schema():
    """单个 optional 字段，输入原样透传（用于验证守卫用 `is` 而非 `==`）。"""
    return _schema({"a": {"type": "any", "value": "$.a", "optional": True}})


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
    """CONTRACT §4.4 的 `miss` 用例（DCG 接口实现细节文档.md:435-439 明文要求）。

    **三个字段都必须声明 `optional`**：契约里的 `miss` 指「按安全口径读一条
    `optional` 路径却读不到值」得到的 undefined，不是「静默吞掉缺键」。
    没有 `optional` 时缺键必须抛 `KeyError` —— 反向对照见
    `TestStrictReadWithoutOptional`。
    """
    return _schema({"c": {"type": "str", "expr": "'x' & $.zz", "optional": True},   # §4.4-1
                    "eq": {"type": "bool", "expr": "$.zz = $.zz", "optional": True},  # §4.4-3
                    "lt": {"type": "bool", "expr": "$.zz < 5", "optional": True},    # §4.4-4
                    # 字段层只能看到 `None`（规则 7 的守卫把 undefined 落成 `None`），
                    # 要观察 **undefined 本身**必须回到表达式里问：`$exists(<undefined>)`
                    # 是 `False`，`$exists(None)` 是 `True`。这两格是 §4.4-4 与「缺键
                    # 路径读成 undefined」的唯一可观测入口。
                    #
                    # 这两格**必须也声明 `optional`**：`$.zz` 的另一侧若写成 required，
                    # 同一路径就同时有 required 与 optional 两种声明（声明冲突），
                    # 读法只能回到各引用点各自的语境 —— required 那侧裸读，缺键直接
                    # `KeyError`，这三条断言就全测不到了。
                    "lt_ex": {"type": "bool", "expr": "$exists($.zz < 5)",
                              "optional": True},
                    "p_ex": {"type": "bool", "expr": "$exists($.zz)",
                             "optional": True}})


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
    """声明了 `optional` 的路径读不到值时必须走 undefined 哨兵，而不是 `KeyError`。

    契约依据（DCG 接口实现细节文档.md:435-439，§4.4 六条语义逐条用 `miss` 表述）：
      1. `'x' & miss == 'x'`
      3. `miss = miss == False`
      4. `miss < 5 == undefined`

    口径（用户裁决 2026-10-25）：**读法由 `optional` 声明决定** ——
    没有 `optional` 就是 `KeyError`（响亮失败，见 `TestStrictReadWithoutOptional`）；
    声明了 `optional` 才是 undefined，并交给上述算子语义处理；有 `default` 时落默认值。
    本类只覆盖第二种情形，`miss` 因此必须由声明了 `optional` 的字段产生。
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
        """§4.4-4：`miss < 5` 求值成 **undefined**（既不是 `False` 也不是 `null`）。

        注意观测层：字段层看到的是 `None` —— 契约 §4.2 规则 7 规定 `optional`
        字段「恰好一次 undefined 判断」（`X=None if _t is _U else _t`），守卫把
        表达式算出的 undefined 落成了 `None`；§4.3 又要求 `value`/`expr` 产物
        逐字符相同。所以**不能拿字段值冒充 undefined**，必须回到表达式层观察：
        `$exists(<undefined>)` 是 `False`，而 `$exists(None)` 是 `True`。
        """
        ns, _ = make_ns(missing_path_schema())
        out = run1(ns, {})
        row = list(out[0])
        assert row[2] is None, (
            f"optional 字段的表达结果是 undefined，守卫应落 None，实得 {row!r}")
        assert row[3] is False, (
            f"§4.4-4 `$.zz < 5` 必须求值成 undefined（`$exists` 才为 False），实得 {row!r}")
        assert row[4] is False, (
            f"未声明 optional 的路径读出来是 undefined（`$exists` 为 False），实得 {row!r}")


@requires_v2
class TestStrictReadWithoutOptional:
    """没有声明 `optional` 就是 `KeyError`（用户裁决 2026-10-25）。

    与 `TestContract44MissingPath` 是一对反向对照：那条测「声明 `optional` → undefined」，
    这条测「没声明 `optional` → 缺键必须响亮失败」。两者的产物文本也必须不同 ——
    未声明 `optional` 的路径一律裸读（`R['zz']`），声明了才可能是 `_ix_get(R,'zz')`。
    """

    def test_value_side_missing_path_raises_keyerror(self):
        sch = _schema({"a": {"type": "any", "value": "$.a", "optional": True},
                       "v": {"type": "any", "value": "$.zz"}})
        ns, src = make_ns(sch)
        assert "R['zz']" in src, f"未声明 optional 的 `value` 路径必须裸读，产物：\n{src}"
        with pytest.raises(KeyError):
            run1(ns, {})

    def test_expr_side_missing_path_raises_keyerror(self):
        sch = _schema({"a": {"type": "any", "value": "$.a", "optional": True},
                       "c": {"type": "str", "expr": "'x' & $.zz"}})
        ns, src = make_ns(sch)
        assert "R['zz']" in src, f"未声明 optional 的 `expr` 路径必须裸读，产物：\n{src}"
        with pytest.raises(KeyError):
            run1(ns, {})
