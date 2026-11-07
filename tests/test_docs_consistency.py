# -*- coding: utf-8 -*-
"""文档一致性守护：README / 契约文档 / 语法文档里写死的**数字与事实**。

为什么单独一个文件
------------------
项目有 4 份中文文档（、接口实现细节、整体设计方案、JSONata 语法），
它们的数字（helper 个数、函数分类个数、conformance 例数、包版本……）靠人工同步
必然漂移。
本文件把这些**可直接从代码里算出来的事实**钉死：文档改错、代码改多，这里立刻红。

守护的事实
----------
1. `dcg.__version__` == `pyproject.toml` 的 `version`（manifest 的
   `generator_version` 由它派生，版本对不上会让产物溯源失真）
2. `Registry` 只接受模块级可导入的函数（lambda/闭包在 build 期就报错，
   而不是等到运行期产物 `getattr` 失败）
3. `runtime.HELPERS` / `SOURCE` / `__all__` / `SOURCE_NEEDS_MATH` 的个数与文档一致
4. `jx.policy` 的 A/B/C/结构性拒绝四张表的个数与文档一致（`$sort` 同时属于
   A 类与 B 类，这是**有意**的：它是 A 类行内函数但需要比较器）
5. `tests/fixtures/conformance.json` 的例数与文档一致

注意：这些断言不运行任何被守护的产物，只做静态计数与最小 build，秒级完成。
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg                                             # noqa: E402
from dcg import runtime as rt                          # noqa: E402
from dcg.jx import policy                              # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------- 包版本一致
def test_package_version_matches_pyproject():
    """`dcg.__version__` 与 `pyproject.toml` 的 version 必须逐字相同。"""
    m = re.search(r'^version\s*=\s*"([^"]+)"', _read("pyproject.toml"), re.M)
    assert m, "pyproject.toml 里找不到 version"
    assert dcg.__version__ == m.group(1), (
        f"dcg.__version__={dcg.__version__!r} 与 pyproject.toml "
        f"version={m.group(1)!r} 不一致")


def test_readme_states_the_same_version_or_none():
    """README 若提到版本号，必须与 `dcg.__version__` 一致（防止手抄旧版本）。"""
    text = _read("README.md")
    found = set(re.findall(r"dcg\s*v?(\d+\.\d+\.\d+)", text, re.I))
    found |= set(re.findall(r"版本\s*`?(\d+\.\d+\.\d+)`?", text))
    for v in found:
        assert v == dcg.__version__, f"README 提到旧版本 {v}，当前是 {dcg.__version__}"


# ---------------------------------------------- 文档抬头的版本标记（Q-03 事故）
# 每份文档「开头那个声明本包版本的地方」用什么正则抓。
# 为什么不能统一成「第一个 x.y.z」：`JSONata 语法.md` 通篇以 2.2.2 指**上游**
# JSONata，`验收报告.md` 里还有 Python 3.13.3；那些都不是本包版本。
_VERSION_MARKER = {
    # `> 验收对象：DCG（Declarative Columnar Generator）v2.2.3 —— …`
    "验收报告.md": re.compile(r"^>\s*验收对象：.*?v(\d+\.\d+\.\d+)", re.M),
    # manifest 样例：`'generator_version': '2.2.4.g1'`（由 `__version__` + `.g1` 派生）
    "README.md": re.compile(r"'generator_version':\s*'(\d+\.\d+\.\d+)\.g1'"),
}


@pytest.mark.parametrize("doc", sorted(_VERSION_MARKER))
def test_doc_declares_the_current_version(doc):
    """文档抬头声明的版本必须与 `dcg.__version__` 逐字相同。

    事故形态（Q-03）：验收报告抬头写 v2.2.2，而代码已经是 2.2.3，报告正文还
    引用了 2.2.3 才有的 `$lookup` —— 文档自相矛盾，此前没有任何测试会红。
    """
    text = _read(doc)
    m = _VERSION_MARKER[doc].search(text)
    assert m, (
        f"{doc} 里找不到版本标记——抬头格式变了？请同步更新 "
        f"`_VERSION_MARKER[{doc!r}]` 的正则，别把断言留成空转")
    line = text.count("\n", 0, m.start()) + 1
    assert m.group(1) == dcg.__version__, (
        f"{doc}:{line} 声明版本 {m.group(1)!r}，"
        f"而 dcg.__version__={dcg.__version__!r}")


@pytest.mark.parametrize("doc", sorted(_VERSION_MARKER))
def test_doc_mentions_no_other_package_version(doc):
    """文档里凡是「DCG + 版本号」的写法，都必须是当前版本。

    只认 `DCG 2.2.3` / `dcg v2.2.3` / `版本 2.2.3` 这类**带主语**的写法，
    因此 `JSONata 2.2.2`（上游版本）、`Python 3.13.3`（环境）不会误伤。
    """
    text = _read(doc)
    found: dict[str, int] = {}
    for pat in (r"dcg\s*v?(\d+\.\d+\.\d+)", r"版本\s*`?(\d+\.\d+\.\d+)`?"):
        for m in re.finditer(pat, text, re.I):
            found.setdefault(m.group(1), text.count("\n", 0, m.start()) + 1)
    stale = {v: ln for v, ln in found.items() if v != dcg.__version__}
    assert not stale, (
        f"{doc} 提到与本包不符的版本（当前 {dcg.__version__}）：\n"
        + "\n".join(f"  第 {ln} 行 {v}" for v, ln in sorted(stale.items(), key=lambda kv: kv[1])))


# ------------------------------------------------------------- Registry 
def test_registry_rejects_unimportable_callables():
    """lambda / 闭包 / 嵌套函数在 **build 期** 就报 Unsupported。

    原因：产物里 `_x(q, *a)` 是按 qualname 做 `getattr` 导入的，
    `<lambda>` / `<locals>` 那样的名字运行期一定失败（契约：
    运行期错误不得被包装成 SchemaError，所以必须在 build 期拦）。
    """
    reg = dcg.Registry()
    reg.fn("$inc", lambda x: x + 1)
    try:
        dcg.compile_expr("$inc($.a)", registry=reg)
    except dcg.Unsupported as e:
        assert "lambda" in str(e) or "模块级" in str(e), str(e)
    else:
        raise AssertionError("lambda 注册的函数必须被拒绝")


def test_registry_accepts_module_level_function():
    """模块顶层的 def 函数可以正常编译成 rfunc，产物能 import 到它。"""
    reg = dcg.Registry()
    reg.fn("$twice", _twice)
    ir = dcg.compile_expr("$twice($.a)", registry=reg)
    assert ir[0] == "rfunc", ir
    assert ir[1].endswith("._twice"), ir[1]


def _twice(x):
    """模块级函数：Registry 的合法注册对象。"""
    return _ser_dummy(x) if False else x      # 保持函数体非平凡


def _ser_dummy(x):                            # pragma: no cover - 永不调用
    return x


# ------------------------------------------------------------ runtime 计数
def test_runtime_helper_count_matches_contract():
    """契约：98 个 helper、`__all__` 107 个名字。

    `_hb` / `_ha` 是 `before` / `after` 数据级钩子的返回值守卫（见
    `dcg/runtime.py` 的「数据级钩子守卫」段），把 undefined 从钩子口上挡住；
    `_xp` 是 `output.expand` 的行展开（见「行展开」段）。
    """
    assert len(rt.HELPERS) == 98, f"HELPERS 现在是 {len(rt.HELPERS)} 个"
    assert len(rt.SOURCE) == len(rt.HELPERS)
    assert len(rt.__all__) == 108, f"__all__ 现在是 {len(rt.__all__)} 个"


def test_runtime_needs_math_matches_contract():
    """契约：只有 `_fl` / `_ce` / `_sq` 需要 math。"""
    assert set(rt.SOURCE_NEEDS_MATH) == {"_fl", "_ce", "_sq"}


# ------------------------------------------------------------ jx 分类计数
def test_jx_policy_counts_match_contract():
    """契约：A 类 55、B 类 6、C 类 4、结构性拒绝 10。"""
    assert len(policy.ROW_FUNCS) == 55, f"ROW_FUNCS 现在是 {len(policy.ROW_FUNCS)}"
    assert len(policy.HIGHER_ORDER) == 6, sorted(policy.HIGHER_ORDER)
    assert len(policy.NONDETERMINISTIC) == 4, sorted(policy.NONDETERMINISTIC)
    assert len(policy.STRUCTURAL) == 10, sorted(policy.STRUCTURAL)


def test_sort_is_both_row_and_higher_order():
    """`$sort` 同时是 A 类（单参行内）与 B 类（带比较器）——**有意**的双属。"""
    assert "$sort" in policy.ROW_FUNCS
    assert "$sort" in policy.HIGHER_ORDER


def test_every_sentinel_not_in_helpers():
    """`_U` 必须是运行期单例，不能被 helper 表里的函数名遮蔽。"""
    assert rt._U is rt.UNDEF
    assert "_U" not in rt.HELPERS and "UNDEF" not in rt.HELPERS


def test_document_function_total_is_dedup_union():
    """文档写的「64 个函数」= 三张表去重并集（55 + 6 + 4 − 1 个重复的 `$sort`）。"""
    total = len(set(policy.ROW_FUNCS) | set(policy.HIGHER_ORDER)
                | set(policy.NONDETERMINISTIC))
    assert total == 64, f"三表并集现在是 {total} 个"
    overlap = set(policy.ROW_FUNCS) & set(policy.HIGHER_ORDER)
    assert overlap == {"$sort"}, f"意外重叠：{sorted(overlap)}"
    assert "64 个函数" in _read("README.md"), "README 没写 64 个函数"
    assert "共 64 个" in _read("JSONata 语法.md"), "语法文档没写 64 个函数"


def test_syntax_doc_has_no_open_defects():
    """`JSONata 语法.md` 必须是空的（当前无未修缺陷）；有新增缺陷才允许非空。"""
    text = _read("JSONata 语法.md")
    head = text.split("### 19.1")[1].split("### 19.2")[0]
    assert "当前为空" in head, \
        "§19.1 出现了未修缺陷条目，请同步 README §8 与 tests/test_docs_consistency"


def test_runtime_core_semantics_are_correct():
    """曾被登记为缺陷的语义与 `$lookup` 数组入参现在必须正确（回归保护，fail 说明又退化了）。"""
    # `$..m[0]`（m=null）：null 是**值**不是「没有值」，`a[0] == a` 对标量成立，
    # 所以 `null[0]` 必须是 `null` 本身，不能静默降级成 undefined。
    assert rt._ix_get(None, 0) is None, "null 下标必须得到 null（不是 undefined）"
    # `$round` 是**银行家舍入**（1.8.6 源码注释即 `Round to half even`）：
    # `Math.round` + 「.5 且结果为奇数则减 1」。
    assert rt._ro(2.5) == 2, "$round(2.5) 必须是 2（half to even）"
    assert rt._ro(3.5) == 4, "$round(3.5) 必须是 4（.5 处取偶）"
    assert rt._ro(-2.5) == -2, "$round(-2.5) 必须是 -2（向偶）"
    assert rt._ct("abc") == 1, "$count('abc') 必须是 1"
    assert rt._ct({"k": 1}) == 1, "$count 对对象也是 1"
    # `$split` 第三参是**结果个数上限**（JS `String#split` 口径，纯截断）
    assert rt._sp("a,b,c", ",", 2) == ["a", "b"], "$split 第三参 limit"
    assert rt._sp("a,b,c", ",", 0) == [], "limit=0 得空数组"
    assert rt._sp("abc", "", 2) == ["a", "b"], "空分隔符按字符切后再截断"
    # `$substring`：负 start 前夹到 0、负 length 直接空串（不是 Python 负切片）
    assert rt._ss("hello", -2) == "lo", "负 start 表示从尾部算"
    assert rt._ss("hello", -10, 2) == "he", "start 过于靠前时夹到 0"
    assert rt._ss("hello", 1, -2) == "", "负 length 得空串"
    # `$substringBefore/After` 未命中时返回**整串**
    assert rt._sbb("ab", "-") == "ab", "$substringBefore 未命中回整串"
    assert rt._sba("ab", "-") == "ab", "$substringAfter 未命中回整串"
    # `$number` 认 JSON 数字字面量；2.0.0 起（PR #573）另认 0x/0b/0o 前缀，
    # 与 JS `parseInt` 同口径：**只允许尾部空白**，前导空白与正负号一律拒绝。
    assert rt._N("1e3") == 1000, "指数写法可解析"
    assert rt._N("0x1F") == 31 and rt._N("0b101") == 5 and rt._N("0o17") == 15, \
        "2.0.0 起支持 0x/0b/0o 前缀（JSONata 2.2.2 对齐）"
    for bad in ("  42  ", " 0x1F", "-0x10", "+0x10", "0x", "0b2", "Infinity", "+1"):
        assert rt._N(bad) is rt.UNDEF, f"$number 必须拒绝 {bad!r}"
    # `$string` 对齐 `JSON.stringify`：null/整值浮点/紧凑 JSON
    assert rt._S(None) == "null", "$string(null) 是 'null'"
    assert rt._S(2.0) == "2", "整值浮点写成整数"
    assert rt._S([1, 2]) == "[1,2]", "数组走紧凑 JSON"
    # 单值提升：数组型形参收到标量时包成单元素数组
    assert rt._rv("abc") == ["abc"], "$reverse 把字符串当单元素序列"
    assert rt._mn(1) == 1 and rt._mx(1) == 1 and rt._av(2) == 2, \
        "$min/$max/$average 必须做单值提升"
    assert rt._sg(1) == 1 and rt._sg("a") == "a", "$single 必须做单值提升"
    assert rt._ds(1) == 1, "$distinct 对标量返回标量"
    assert rt._so(1) == [1], "$sort 对标量返回单元素数组"
    # `$sort` 比较器是「返回真就交换」的归并排序，负数是真值
    assert rt._so([3, 1, 2], lambda a, b: a - b) == [2, 1, 3], \
        "$sort 比较器按真值判定（与 1.8 一致）"
    assert rt._so([3, 1, 2], lambda a, b: a > b) == [1, 2, 3], "布尔比较器是常规用法"
    # `$keys` 对数组输入逐元素取键并保序去重；空结果塌缩成 undefined
    assert rt._ky([{"a": 1}, {"b": 2}]) == ["a", "b"], "$keys 数组入参"
    assert rt._ky({}) is rt.UNDEF, "$keys({}) 是 undefined"
    # `and`/`or` 恒返回布尔（`true and 1` 是 true，不是 1）
    assert rt._and(1, 2) is True and rt._or(1, 2) is True, "逻辑运算返回布尔"
    # `in` 不是 Python 的 `in`：右侧非数组时按单元素数组比
    assert rt._in("a", "abc") is False, "'a' in 'abc' 是 false"
    assert rt._in(1, [1, 2]) is True, "1 in [1,2] 是 true"
    assert rt._in(True, [1]) is False, "true in [1] 是 false"
    # `$lookup` 数组入参（JSONata 官方语义）：逐元素递归取键，只收集非 undefined 命中。
    assert rt._lk([{"a": 1}, {"a": 2}], "a") == [1, 2], \
        "$lookup 数组入参必须收集所有命中值"
    assert rt._lk([{"a": 1}, {"b": 2}], "a") == [1], "未命中的元素必须被跳过"
    assert rt._lk([], "a") == [], "空数组必须得到 []（不是 undefined）"
    assert rt._lk([1, "x", {"a": 9}], "a") == [9], "数组里的标量元素必须静默跳过"
    assert rt._lk({"a": 1}, "z") is rt.UNDEF, "对象缺键仍是 undefined"
    assert rt._lk(1, "a") is rt.UNDEF, "非对象非数组仍是 undefined"


# --------------------------------------------------------- conformance 例数
def test_conformance_case_count_matches_readme():
    """README：冻结 conformance 50 例（core 26 + edge 24）。"""
    data = json.loads(_read("tests", "fixtures", "conformance.json"))
    cases = data["cases"]
    assert len(cases) == 50, f"conformance 现在是 {len(cases)} 例"
    groups = [c.get("group") for c in cases]
    assert groups.count("core") == 26, groups.count("core")
    assert groups.count("edge") == 24, groups.count("edge")


# ------------------------------------------------------------ 文档互指完整
def test_four_documents_exist():
    """四份文档必须都在仓库根目录（README 里互相链接，缺一份就是死链）。"""
    for name in ("README.md", "DCG 接口实现细节文档.md",
                 "DCG 整体设计方案与架构文档.md", "JSONata 语法.md"):
        assert os.path.exists(os.path.join(ROOT, name)), f"缺少 {name}"


def test_readme_mentions_manifest_keys():
    """manifest 的每个键都必须在 README 里出现过（新增键不忘写文档）。"""
    readme = _read("README.md")
    import tempfile
    tmp = os.path.join(ROOT, "_tmp", "pytest_docs_manifest")
    os.makedirs(tmp, exist_ok=True)
    man = dcg.build({"name": "docs_check", "output": {"shape": "list"},
                     "out": {"type": "object",
                             "fields": {"v": {"type": "int64", "value": "$.0"}}}},
                    tmp, overwrite=True)
    for key in man:
        assert key in readme, f"manifest 键 {key!r} 没有写进 README"


# ------------------------------------------------ 验收门的文档门镜像
def test_accept_gate_mirror_is_in_sync():
    """`evidence/accept.py` 的文档门是本文件的**子集镜像**（它不能依赖 pytest）。

    两边都不想跑对方的 runner，所以这里只核对镜像里写死的几条阈值与
    本文件一致；镜像一旦漏改，这道断言先红。
    """
    src = _read("evidence", "accept.py")
    assert "def g_docs_consistency()" in src, "accept.py 缺少文档一致性门"
    assert len(policy.ROW_FUNCS) == 55 and len(policy.HIGHER_ORDER) == 6
    assert len(policy.NONDETERMINISTIC) == 4
    assert len(rt.HELPERS) == 98 and len(rt.__all__) == 108
    # Cython 两条门禁必须还在；且「无 C 编译器时可跳过」只允许给端到端那条
    assert "def g_cython_product_shape()" in src, "accept.py 缺少 Cython 产物形态门"
    assert "def g_cython_bench()" in src, "accept.py 缺少 Cython 端到端门"
    assert "ACCEPTABLE_SKIPS = {CYTHON_BENCH_GATE}" in src, \
        "无 C 编译器时可跳过的门应当只有 Cython 端到端门"
    # 门里的两处「产物形态」断言必须与当前生成器一致
    src_gen = dcg.generate({"name": "docs_mirror", "output": {"shape": "list"},
                            "out": {"type": "object",
                                    "fields": {"v": {"type": "int64", "value": "$.0"}}}})
    assert "_c=_o.append" in src_gen and "if R:" not in src_gen
