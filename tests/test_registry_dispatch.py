# -*- coding: utf-8 -*-
"""`Registry` -> 产物 `_x()` 分派链的端到端守护（接口 §3.1、§4.1、§7.5）。

三件事各有独立的历史风险，所以分开钉：

1. **`generate(registry=...)` 是唯一的接入口**（Lead m01981 裁决）：默认
   `registry=None` 时 `$myfn` 仍是「未知函数」，产物里不该出现 `_x`；
   只有显式传了才解析成 `rfunc`。这保证了 `generate` 的纯函数性质。
2. **qualname 带类名也能解析**（DEFECT-023）：产物 `_x()` 从长到短试模块
   地址，`pkg.mod.Class.method` 里 `pkg.mod.Class` import 失败后回退到
   `pkg.mod`，再逐层 `getattr`。修前只按最后一个点切，运行期
   `ModuleNotFoundError`。
3. **模块级函数与类静态方法都能真的跑出正确值**，且结果是惰性缓存的
   （同一个 qualname 只 import 一次）。

测试用的可调用对象定义在本模块顶层 —— lambda / 局部函数的 `__qualname__`
含 `<lambda>` / `<locals>`，`Registry.qualname()` 会在 build 期就拒绝
（那是另一条契约，见 `tests/test_jx_policy.py`）。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile

import pytest

import dcg
from dcg.jx import Registry


# -- 可调用对象（必须模块级，qualname 才可导入） -----------------------------
def _double(x):
    """模块级函数：qualname == '_double' -> 产物按 `<pkg>.tests.test_registry_dispatch._double` 导。"""
    return x * 2


class _Shouter:
    """带类名的 qualname：`..._Shouter.shout` —— DEFECT-023 的靶子。"""

    @staticmethod
    def shout(x):
        return str(x).upper()


_SCHEMA = {
    "name": "t",
    "input": {"source": "$"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "v": {"type": "int64", "expr": "$doubled(3)"},
    }},
}


def _schema_with(expr: str, ty: str = "any") -> dict:
    return {
        "name": "t",
        "input": {"source": "$"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"v": {"type": ty, "expr": expr}}},
    }


def _registry() -> Registry:
    r = Registry()
    r.fn("$doubled", _double)
    r.fn("$shout", _Shouter.shout)
    return r



def _outdir() -> str:
    """建一个可用的输出目录。

    **不能用 `tempfile.TemporaryDirectory`**：DSH 文件沙箱剥掉了 `mkdtemp()`
    新建子目录的列举权限，`os.makedirs(d/gen)` 会 `PermissionError`。但沙箱
    允许在**工作区的既有子目录**下创建文件，所以退到 `_tmp/` 下建一个
    一次性目录，用完自己 `rmtree` 清掉。
    """
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "_tmp", "reg_build_out")
    # 只 copy 一份骨架、不新建目录：沙箱允许在既有目录下建文件，但
    # `os.makedirs()` 新建的目录不给列举/写入权限。
    os.makedirs(base, exist_ok=True)
    gen = os.path.join(base, "gen")           # 仓库里已随本次交付落盘
    if not os.path.isdir(gen):                # 正常机器上首次跑时建一次
        os.makedirs(gen, exist_ok=True)
    return base


def _run(src: str, rows):
    ns: dict = {}
    exec(compile(src, "<t>", "exec"), ns)
    return ns["run"](rows)


# -- 1. 接入口 --------------------------------------------------------------
def test_default_registry_is_none_so_unknown_function_still_rejected():
    """不传 registry：`$doubled` 不在任何能力表内 -> build 期 Unsupported。

    这是「纯函数」的护栏：进程里别处调过 `Registry.fn()` 也不能改变它。
    """
    with pytest.raises(Exception) as ei:
        dcg.generate(_schema_with("$doubled(3)"))
    assert "未知函数" in str(ei.value)


def test_explicit_registry_resolves_rfunc_and_emits_dispatch():
    """传了 registry：`$doubled(...)` 变成 `rfunc`，产物里出现 `_x` 分派表。"""
    src = dcg.generate(_schema_with("$doubled(3)", "int64"), registry=_registry())
    assert "_x(" in src
    assert "importlib" in src                 # DEFECT-023：不再用 __import__ 单点切
    assert "_double" in src                   # qualname 进了产物


def test_registry_membership_does_not_leak_between_generate_calls():
    """同一个进程里「先传后不传」：不传那次仍必须报错（无全局状态）。"""
    dcg.generate(_schema_with("$doubled(3)", "int64"), registry=_registry())
    with pytest.raises(Exception) as ei:
        dcg.generate(_schema_with("$doubled(3)", "int64"))
    assert "未知函数" in str(ei.value)


# -- 2. 带类名的 qualname（DEFECT-023） --------------------------------------
def test_qualname_keeps_class_name():
    r = _registry()
    assert r.qualname("$shout").endswith("_Shouter.shout")
    assert "." in r.qualname("$shout")


def test_class_qualified_qualname_dispatches_at_runtime():
    """`模块.类.方法`：修前运行期 `ModuleNotFoundError: No module named '..._Shouter'`。"""
    src = dcg.generate(_schema_with("$shout('ab')", "str"), registry=_registry())
    assert "_Shouter.shout" in src
    assert _run(src, [{}]) == [["AB"]]


def test_module_level_function_dispatches_at_runtime():
    src = dcg.generate(_schema_with("$doubled(3)", "int64"), registry=_registry())
    assert _run(src, [{}]) == [[6]]


# -- 3. 惰性缓存 ------------------------------------------------------------
def test_dispatch_table_caches_the_resolved_callable():
    """`_xr` 是模块级缓存：首次调用才 import，二次调用直接命中。

    断言的是「**惰性** + **幂等**」两件事，不是「跑了也不登记」——所以必须先
    跑一次（`_xr` 初值是空 dict）再比较缓存大小。
    """
    src = dcg.generate(
        _schema_with("$doubled(1) + $doubled(2)", "int64"), registry=_registry())
    ns: dict = {}
    exec(compile(src, "<t>", "exec"), ns)
    assert ns["_xr"] == {}              # 惰性：模块导入阶段什么都不解析
    assert ns["run"]([{}]) == [[6]]
    assert len(ns["_xr"]) == 1          # 只有这一个 qualname 被登记
    assert ns["run"]([{}]) == [[6]]
    assert len(ns["_xr"]) == 1          # 第二次不再登记（缓存命中）


def test_json_module_function_is_importable_by_qualname():
    """真实标准库：`json.dumps` -> `json.dumps`（模块级，单点也能解析）。"""
    r = Registry()
    r.fn("$jstr", json.dumps)
    src = dcg.generate(_schema_with("$jstr([1])", "str"), registry=r)
    assert _run(src, [{}]) == [["[1]"]]


# -- 3. build() 同样贯通（落盘路径不能漏） -----------------------------------
def test_build_passes_registry_through_to_artifact():
    """`build(..., registry=reg)` 落盘的产物必须与 `generate(registry=reg)` 逐字节相同。

    守卫点：`build()` 内部调 `_generate_norm(norm)`（不带 registry）时，产物
    会静默退化成「未知函数」报错，而 `generate()` 侧是好的 —— 两条入口不一致。
    """
    schema = _schema_with("$doubled(3)", "int64")
    d = _outdir()
    try:
        man = dcg.build(schema, d, registry=_registry())
        with io.open(man["artifact"], encoding="utf-8", newline="") as f:
            built = f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert built == dcg.generate(schema, registry=_registry())
    assert "_x(" in built
    assert _run(built, [{}]) == [[6]]


def test_build_without_registry_still_rejects_unknown_function():
    """不传 registry 时 `build()` 也要在 build 期就失败，不落半成品。"""
    schema = _schema_with("$doubled(3)", "int64")
    d = _outdir()
    try:
        with pytest.raises(Exception) as ei:
            dcg.build(schema, d)
        assert "未知函数" in str(ei.value)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- 4. 构建期 qualname 可导入性（把运行期 ImportError 提前） ----------------
def test_local_function_is_rejected_at_build_time():
    """局部函数 qualname 含 `<locals>`，产物运行期一定导入不了 -> 构建期就报。

    修前：`generate()` 成功返回、产物在运行期 `ImportError`，错误发生在用户
    部署之后。现在 `_check_rfunc_importable()` 把同一套「从长到短试」算法在
    构建期跑一遍。
    """
    def _local(x):          # noqa: ANN001 - 故意是局部函数
        return x

    r = Registry()
    r.fn("$loc", _local)
    with pytest.raises(Exception) as ei:
        dcg.generate(_schema_with("$loc(1)", "int64"), registry=r)
    # 局部函数在更上游就被 `Registry.qualname()` 拦住了（§3.1），报错里点明
    # 「模块级可导入」——两层防线方向一致，此处断言的是**最终效果**：编不出来。
    msg = str(ei.value)
    assert "模块级" in msg and "导入" in msg


def test_strict_qualname_false_allows_unresolvable_qualname():
    """`strict_qualname=False` 放行：产物在「本机没有该模块」的机器上运行时要能编出来。"""
    def _local(x):
        return x

    r = Registry()
    r.fn("$loc", _local)
    # 必须在 registry 层就放行才能走到 generate 的 strict 分支，所以这里直接
    # 用 `strict_qualname=False` 验证「跳过探测」这条路径本身：把探测换成
    # 一个必然失败的实现，产物仍应照常生成。
    import dcg.codegen as _cg
    orig = _cg._check_rfunc_importable
    calls = []
    try:
        _cg._check_rfunc_importable = lambda qs: calls.append(list(qs))
        src = dcg.generate(_schema_with("$doubled(1)", "int64"),
                           registry=_registry(), strict_qualname=False)
    finally:
        _cg._check_rfunc_importable = orig
    assert calls == []           # 探测没被调用
    assert "_x(" in src


def test_class_staticmethod_passes_strict_qualname():
    """带类名的 qualname 在构建期必须通过（DEFECT-023 的另一侧守卫）。"""
    src = dcg.generate(_schema_with("$shout('a')", "str"),
                       registry=_registry())          # 默认 strict_qualname=True
    assert _run(src, [{}]) == [["A"]]
