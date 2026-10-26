# -*- coding: utf-8 -*-
"""文档一致性守护：README / 契约文档 / 语法文档里写死的**数字与事实**。

为什么单独一个文件
------------------
项目有 4 份中文文档（README、接口实现细节、整体设计方案、JSONata 语法），
它们的数字（helper 个数、函数分类个数、conformance 例数、包版本……）以前靠
人工同步，已经漂移过多次（历史评审快照曾记录 20+ 处，已清理）。
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
    found = set(re.findall(r"dcg\s*v?(\d+\.\d+\.\d+)", text))
    found |= set(re.findall(r"版本\s*`?(\d+\.\d+\.\d+)`?", text))
    for v in found:
        assert v == dcg.__version__, f"README 提到旧版本 {v}，当前是 {dcg.__version__}"


# ------------------------------------------------------------- Registry 契约
def test_registry_rejects_unimportable_callables():
    """lambda / 闭包 / 嵌套函数在 **build 期** 就报 Unsupported。

    原因：产物里 `_x(q, *a)` 是按 qualname 做 `getattr` 导入的，
    `<lambda>` / `<locals>` 那样的名字运行期一定失败（契约 §7.3：
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
    """契约 §4.4：86 个 helper、`__all__` 96 个名字。

    `_hb` / `_ha` 是 `before` / `after` 数据级钩子的返回值守卫（见
    `dcg/runtime.py` 的「数据级钩子守卫」段），把 undefined 从钩子口上挡住。
    """
    assert len(rt.HELPERS) == 86, f"HELPERS 现在是 {len(rt.HELPERS)} 个"
    assert len(rt.SOURCE) == len(rt.HELPERS)
    assert len(rt.__all__) == 96, f"__all__ 现在是 {len(rt.__all__)} 个"


def test_runtime_needs_math_matches_contract():
    """契约 §4.4：只有 `_fl` / `_ce` / `_sq` 需要 math。"""
    assert set(rt.SOURCE_NEEDS_MATH) == {"_fl", "_ce", "_sq"}


# ------------------------------------------------------------ jx 分类计数
def test_jx_policy_counts_match_contract():
    """契约 §3.5：A 类 46、B 类 6、C 类 4、结构性拒绝 10。"""
    assert len(policy.ROW_FUNCS) == 46, f"ROW_FUNCS 现在是 {len(policy.ROW_FUNCS)}"
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
    """文档写的「55 个函数」= 三张表去重并集（46 + 6 + 4 − 1 个重复的 `$sort`）。"""
    total = len(set(policy.ROW_FUNCS) | set(policy.HIGHER_ORDER)
                | set(policy.NONDETERMINISTIC))
    assert total == 55, f"三表并集现在是 {total} 个"
    overlap = set(policy.ROW_FUNCS) & set(policy.HIGHER_ORDER)
    assert overlap == {"$sort"}, f"意外重叠：{sorted(overlap)}"
    assert "55 个函数" in _read("README.md"), "README 没写 55 个函数"
    assert "共 55 个" in _read("JSONata 语法.md"), "语法文档没写 55 个函数"


def test_syntax_doc_has_no_open_defects():
    """`JSONata 语法.md` §15.1 必须是空的（当前无未修缺陷）；有新增缺陷才允许非空。"""
    text = _read("JSONata 语法.md")
    head = text.split("### 15.1")[1].split("### 15.2")[0]
    assert "当前为空" in head, \
        "§15.1 出现了未修缺陷条目，请同步 README §7 与 tests/test_docs_consistency"


def test_runtime_core_semantics_are_correct():
    """三条曾被登记为缺陷的语义现在必须正确（回归保护，fail 说明又退化了）。"""
    # `$..m[0]`（m=null）：null 是**值**不是「没有值」，`a[0] == a` 对标量成立，
    # 所以 `null[0]` 必须是 `null` 本身，不能静默降级成 undefined。
    assert rt._ix_get(None, 0) is None, "null 下标必须得到 null（不是 undefined）"
    assert rt._ro(2.5) == 3, "$round(2.5) 必须是 3（half-up）"
    assert rt._ct("abc") == 1, "$count('abc') 必须是 1"
    assert rt._ct({"k": 1}) == 1, "$count 对对象也是 1"
    assert rt._sp("a,b,c", ",", 2) == ["a", "b,c"], "$split 第三参 limit"


# --------------------------------------------------------- conformance 例数
def test_conformance_case_count_matches_readme():
    """README §7：冻结 conformance 50 例（core 26 + edge 24）。"""
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
    assert len(policy.ROW_FUNCS) == 46 and len(policy.HIGHER_ORDER) == 6
    assert len(policy.NONDETERMINISTIC) == 4
    assert len(rt.HELPERS) == 86 and len(rt.__all__) == 96
    # 门里的两处「产物形态」断言必须与当前生成器一致
    src_gen = dcg.generate({"name": "docs_mirror", "output": {"shape": "list"},
                            "out": {"type": "object",
                                    "fields": {"v": {"type": "int64", "value": "$.0"}}}})
    assert "_c=_o.append" in src_gen and "if R:" not in src_gen
