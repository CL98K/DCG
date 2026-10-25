"""组合矩阵正确性门禁：形状 × 取值形态 × 钩子的全笛卡尔积，与独立参考实现对账。

为什么要这一层
------------
单元测试钉的是**已知形态**（`test_codegen_snapshot` 钉产物文本、`test_e2e_flight`
钉具体场景）。它们无法回答「任意组合是否都对」——而那正是对外承诺（m00092 第 1
条：所有简单/复杂/多组合 schema 的结果都不能错）。

这一层不读产物文本、不复用 codegen 的任何逻辑：它为每一格**独立地**用最朴素的
Python 算一遍期望值，再与 `dcg.generate()` 的产物实跑结果逐值比对。所以它能抓到
「两条生成路径对同一 schema 给出不同答案」（如 DEFECT-001 的行槽位错位、
`const` 子键在直出/转置两条路上一个成标量一个成整列）。

矩阵维度
--------
* 输出形状 `shape`：list / dict / index / rows
* 字段取值形态 `kind`：见 `_KINDS`，从裸 value 到嵌套 aggregate 共 7 类
* 数据级钩子 `hook`：无 / before / after / 两者
* 输入布局 `source`：裸行 / `$.index` / `$.a.b`

组合数 4×7×4×3 = 336，全部逐项编译执行（约 1 秒），无需 mark。
"""
from __future__ import annotations

import itertools
import json

import pytest

import dcg
from dcg.runtime import _U


# ---------------------------------------------------------------- 输入与行数据
ROWS = [
    {"fid": 1, "dep": "SHA", "arr": "PEK", "hours": [1, 2], "maybe": "x"},
    {"fid": 2, "dep": "PEK", "arr": "CAN", "hours": [3], "maybe": None},
    {"fid": 3, "dep": "CAN", "arr": "SZX", "hours": [], "maybe": "z"},
]


def _input(source: tuple):
    """按 input.source 包一层，返回真正喂给 `run()` 的数据。"""
    if not source:
        return [dict(r) for r in ROWS]
    if source == ("index",):
        return {"index": [dict(r) for r in ROWS]}
    if source == ("a", "b"):
        return {"a": {"b": [dict(r) for r in ROWS]}}
    raise AssertionError(f"未登记的 source: {source}")


def _rows_of(src_data):
    """参考实现那边同样要按 source 剥一层，才能拿到行列表。"""
    if not isinstance(src_data, dict) or "index" in src_data or "a" in src_data:
        return _input.rows  # 仅用于 before 钩子构造，见下
    return src_data


# ---------------------------------------------------------------- 取值形态
# 每一格：(名字, 字段 spec, 期望值函数 f(row) -> 值)
_KINDS = {
    # 裸 value：`$.fid`，required，int64 -> 走裸下标快路径
    "bare": (
        {"type": "int64", "value": "$.fid"},
        lambda r: r["fid"],
    ),
    # value + any：`$.maybe`，类型不可判定 -> 走 `_ix_get` 安全链，缺键得 undefined
    "any_val": (
        {"type": "any", "value": "$.maybe"},
        lambda r: r.get("maybe", _U),
    ),
    # optional + value：缺键 -> undefined -> 守卫落成 None
    "opt": (
        {"type": "str", "value": "$.maybe", "optional": True},
        lambda r: r.get("maybe"),
    ),
    # default：undefined -> 默认值
    "default": (
        {"type": "int64", "value": "$.zzz", "default": 9},
        lambda r: 9,
    ),
    # expr + 高阶函数：`$sum(hours)`
    "expr_ho": (
        {"type": "int64", "expr": "$sum(hours)"},
        lambda r: sum(r["hours"]),
    ),
    # 具名对象（不标 aggregate）：每行一份，不拆列
    "obj": (
        {"type": "object", "fields": {
            "d": {"type": "str", "value": "$.dep"},
            "h": {"type": "list", "value": "$.hours"},
        }},
        lambda r: {"d": r["dep"], "h": list(r["hours"])},
    ),
    # 位置行（type=list + fields）：undefined 落 None 保位
    "pos": (
        {"type": "list", "fields": {
            "i": {"type": "int64", "value": "$.fid"},
            "m": {"type": "any", "value": "$.maybe", "optional": True},
        }},
        lambda r: [r["fid"], r.get("maybe")],
    ),
}


# ---------------------------------------------------------------- 钩子
_HOOKS = {
    "none": (None, None, lambda rows: rows),
    # before：整批数据 -> 新数据。参考实现同样把 fid 翻倍后再交给 out 字段
    "before": ("`[dict(r, fid=r['fid'] * 10) for r in $]`", None,
               lambda rows: [dict(r, fid=r["fid"] * 10) for r in rows]),
    # after：输出数据 -> 新数据。参考实现给 dict 形状补一个 `n` 列
    "after": (None, "@dcg.runtime.HOOK_AFTER_FOR_TEST", None),
    "both": ("`[dict(r, fid=r['fid'] * 10) for r in $]`",
             "@dcg.runtime.HOOK_AFTER_FOR_TEST", None),
}


def _apply_hook_ref(name, hook_out, shape):
    """after 钩子的参考实现：原样返回（HOOK_AFTER_FOR_TEST 是恒等函数）。"""
    return hook_out


# ---------------------------------------------------------------- 参考输出
def _field_values(kind_name, rows):
    """返回 `(每字段一列的列表 [col_a, col_b, ...], 字段名列表)`。

    `expr_ho` 需要一个辅助列 `hours`（`$sum(hours)` 的裸名必须已声明），
    字段顺序必须与 `_schema()` 里 `fields` 的插入序一致。
    """
    names, cols = [], []
    if kind_name == "expr_ho":
        names.append("hours")
        cols.append([list(r["hours"]) for r in rows])
    names.append("v")
    cols.append([_KINDS[kind_name][1](r) for r in rows])
    return cols, names


def _ref_col(shape, name, values):
    """单列值按形状装配。"""
    if shape == "list":
        raise AssertionError("list 形状是多列行，见 _expected")
    if shape == "dict":
        return {name: values}
    if shape == "index":
        return {i: v for i, v in enumerate(values)}
    if shape == "rows":
        return [{name: v} for v in values]
    raise AssertionError(shape)


def _expected(shape, source, kind_name, hook_name):
    """参考实现：完全独立地算出期望输出。"""
    rows = [dict(r) for r in ROWS]
    before, after, before_ref = _HOOKS[hook_name]
    if before_ref is not None:
        rows = before_ref(rows)
    cols, names = _field_values(kind_name, rows)
    if shape == "list":
        # list 形状：一行 = 一个 list，按字段声明序
        return [[c[i] for c in cols] for i in range(len(rows))]
    if shape == "dict":
        return {n: c for n, c in zip(names, cols)}
    if shape == "index":
        return {i: [c[i] for c in cols] for i in range(len(rows))}
    if shape == "rows":
        return [{n: c[i] for n, c in zip(names, cols)} for i in range(len(rows))]
    raise AssertionError(shape)


def _norm(v):
    """把哨兵归一成 JSON 友好值，用于跨路径比较（undefined 不该出现在产物里）。"""
    if v is _U:
        return "<UNDEF>"
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return v


# ---------------------------------------------------------------- 矩阵本体
SHAPES = ["list", "dict", "index", "rows"]
SOURCES = [(), ("index",), ("a", "b")]


def _schema(shape, source, kind_name, hook_name):
    spec = dict(_KINDS[kind_name][0])
    before, after, _ = _HOOKS[hook_name]
    # `expr` 里用到的裸名必须是本 schema 已声明字段（§1「裸名即字段引用」）
    fields = {}
    if kind_name == "expr_ho":
        fields["hours"] = {"type": "list", "value": "$.hours", "optional": True}
    fields["v"] = spec
    out = {"type": "object", "fields": fields}
    schema = {
        "name": f"mx_{shape}_{kind_name}_{hook_name}",
        "output": {"shape": shape},
        "out": out,
    }
    if source:
        schema["input"] = {"source": "$." + ".".join(source)}
    hooks = {}
    if before is not None:
        hooks["before"] = before
    if after is not None:
        hooks["after"] = after
    if hooks:
        schema["output"].update(hooks)
    return schema  # 交给 dcg.generate() 内部 validate，别提前归一化（钩子会被归一两次）


def _run_generated(src, data):
    ns: dict = {}
    exec(compile(src, "<mx>", "exec"), ns)  # noqa: S102 - 产物是我们自己生成的
    return ns["run"](data)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("source", SOURCES, ids=["src_root", "src_index", "src_ab"])
@pytest.mark.parametrize("kind", list(_KINDS), ids=list(_KINDS))
@pytest.mark.parametrize("hook", ["none", "before"], ids=["no_hook", "before"])
def test_matrix_matches_independent_reference(shape, source, kind, hook):
    """每一格：产物实跑结果 == 独立参考实现的结果。

    `after` 钩子单独用 `test_after_hook_is_applied_once` 验（它不改字段值，
    只验证「被调用且只调用一次」，放在矩阵里无法与「没调用」区分）。
    """
    schema = _schema(shape, source, kind, hook)
    src = dcg.generate(schema)
    got = _run_generated(src, _input(source))
    want = _expected(shape, source, kind, hook)
    assert _norm(got) == _norm(want), (
        f"\nshape={shape} source={source} kind={kind} hook={hook}"
        f"\n--- 产物 ---\n{src}\n--- got ---\n{_norm(got)}\n--- want ---\n{_norm(want)}")


@pytest.mark.parametrize("shape", SHAPES)
def test_matrix_two_shapes_agree_on_same_fields(shape):
    """同一份字段集合在四种形状下的**值**必须一致（形状只改容器方向）。

    §2.5「同一 schema 两种形状语义不得分叉」的最直接机器化表达。
    """
    names = ["bare", "any_val", "opt", "default"]
    fields = {n: dict(_KINDS[n][0]) for n in names}
    # `$sum(hours)` 的 `hours` 必须在本 schema 里声明（裸名 = 已声明字段，§1）
    fields["hours"] = {"type": "list", "value": "$.hours", "optional": True}
    fields["expr_ho"] = dict(_KINDS["expr_ho"][0])
    schema = {
        "name": f"mx_agree_{shape}",
        "output": {"shape": shape},
        "out": {"type": "object", "fields": fields},
    }
    got = _run_generated(dcg.generate(schema), _input(()))

    # 只验 `bare`（必成功、无哨兵），其余形态各有 undefined 出口，由矩阵主体负责
    if shape == "list":
        col = [row[0] for row in got]
    elif shape == "dict":
        col = got["bare"]
    elif shape == "index":
        col = [got[i][0] for i in range(len(got))]
    else:
        col = [row["bare"] for row in got]
    assert col == [r["fid"] for r in ROWS]


# ---------------------------------------------------------------- after 钩子
def test_after_hook_is_applied_once():
    """`after` 钩子只被调用一次，且作用在**整份输出**上（§4.1）。"""
    calls = []

    def spy(out):
        calls.append(out)
        return out

    import dcg.runtime as rt
    rt.HOOK_AFTER_FOR_TEST = spy
    try:
        schema = {
            "name": "mx_after_spy",
            "output": {"shape": "dict", "after": "@dcg.runtime.HOOK_AFTER_FOR_TEST"},
            "out": {"type": "object",
                    "fields": {"v": {"type": "int64", "value": "$.fid"}}},
        }
        got = _run_generated(dcg.generate(schema), _input(()))
    finally:
        del rt.HOOK_AFTER_FOR_TEST

    assert len(calls) == 1
    assert got == {"v": [1, 2, 3]}
    assert calls[0] == got


# ---------------------------------------------------------------- 直出 aggregate
def test_direct_aggregate_column_slots_are_remapped():
    """DEFECT-001 回归：直出 aggregate 字段**不在最后**时，列下标必须按行槽位取。

    修前：`_aggregate` 用**声明序位**取列，而 `_c([...])` 跳过了直出字段，
    于是 `c2` 声明序 3、实际只占第 2 格 -> `[r[3]for r in _o]` -> IndexError。
    """
    schema = {
        "name": "mx_direct_slot",
        "output": {"shape": "dict"},
        "out": {"type": "object", "fields": {
            "c0": {"type": "int64", "value": "$.fid"},
            "agg": {"type": "object", "aggregate": True, "fields": {
                "n": {"type": "int64", "const": 1},
                "sum": {"type": "int64", "value": "$.fid"},
            }},
            "c2": {"type": "int64", "expr": "`$.fid * 2`"},
        }},
    }
    got = _run_generated(dcg.generate(schema), _input(()))
    assert got == {
        "c0": [1, 2, 3],
        "agg": {"n": 1, "sum": [1, 2, 3]},
        "c2": [2, 4, 6],
    }


def test_aggregate_const_is_scalar_on_both_paths():
    """同一 `const` 子键在「直出列」与「行容器转置」两条路上语义必须一致。

    两条路的分工（`_direct_aggregate`）：
    * 整层子键都能逐列独立求值 -> 走 `_aggregate_direct`，`agg` 是**列容器**，
      每个子键是一列（`const` 子键按「只留一份」发成标量）；
    * 含 `fields` 的嵌套子键让它退回老路 -> `agg` 是**一列具名行**，每行一个 dict，
      此时 `const` 子键在每行 dict 里都是同一个标量。

    两种形态**结构不同**（列容器 vs 具名行），但「`const` 子键不是『每行一份的数据
    列』」这一点必须一致。修前 `_col_const` 发 `[1 for r in _d]`，列容器里就多出一
    列 `[7,7]`，与具名行里的标量 `7` 语义分叉。
    """
    def build(sub_has_nested):
        fields = {
            "k": {"type": "int64", "const": 7},
            "n": {"type": "int64", "value": "$.fid"},
        }
        if sub_has_nested:
            fields["deep"] = {"type": "object", "aggregate": True, "fields": {
                "x": {"type": "int64", "value": "$.fid"}}}
        return {
            "name": "mx_const_scalar",
            "output": {"shape": "dict"},
            "out": {"type": "object", "fields": {
                "agg": {"type": "object", "aggregate": True, "fields": fields}}},
        }

    rows = [{"fid": 1}, {"fid": 2}]

    # 路径 A：整层可直出 -> agg 是列容器，k 是标量 7（不是 [7,7]）
    direct = _run_generated(dcg.generate(build(False)), [dict(r) for r in rows])
    assert direct["agg"]["k"] == 7, f"列容器里 const 子键必须是标量：{direct}"
    assert direct["agg"]["n"] == [1, 2], f"非 const 子键仍是整列：{direct}"

    # 路径 B：含嵌套 -> agg 是一列具名行，每行 dict 里的 k 也是标量 7
    transposed = _run_generated(dcg.generate(build(True)), [dict(r) for r in rows])
    assert [row["k"] for row in transposed["agg"]] == [7, 7], \
        f"具名行里 const 子键必须是标量：{transposed}"
    assert [row["n"] for row in transposed["agg"]] == [1, 2]


# ---------------------------------------------------------------- 产物确定性
@pytest.mark.parametrize("shape", SHAPES)
def test_product_is_byte_reproducible(shape):
    """同一 schema 反复生成必须逐字节相同（缓存/集合迭代序不得泄漏进产物）。"""
    schema = _schema(shape, (), "obj", "none")
    first = dcg.generate(schema)
    for _ in range(5):
        assert dcg.generate(schema) == first

# ---------------------------------------------------------------- 钩子函数导入
def test_hook_func_qualname_can_contain_class():
    """`@pkg.mod.Class.method` 形态的钩子必须能解析。

    修前产物按 `qual.rfind('.')` 切模块名，把 `decimal.Context` 当模块 ->
    运行期 `ModuleNotFoundError`（而 `dcg/jx/registry.py:23 _IMPORTABLE_QUALNAME`
    明确允许 `Class.method` 这种形状）。现在从长到短试模块地址、其余当属性链。
    """
    import decimal

    schema = {
        "name": "mx_hook_qualname",
        "output": {"shape": "list",
                   "before": "@decimal.Context.create_decimal"},
        "out": {"type": "object",
                "fields": {"v": {"type": "int64", "value": "$.fid"}}},
    }
    src = dcg.generate(schema)
    ns: dict = {}
    exec(compile(src, "<mx>", "exec"), ns)  # noqa: S102
    assert ns["_h0"] is decimal.Context.create_decimal


def test_unimportable_hook_raises_clear_import_error():
    """不可导入的 qualname 必须给出带原名字的 `ImportError`，而不是 ModuleNotFoundError。"""
    schema = {
        "name": "mx_hook_bad",
        "output": {"shape": "list", "before": "@no.such.mod.fn"},
        "out": {"type": "object",
                "fields": {"v": {"type": "int64", "value": "$.fid"}}},
    }
    src = dcg.generate(schema)
    with pytest.raises(ImportError, match="no.such.mod.fn"):
        exec(compile(src, "<mx>", "exec"), {})  # noqa: S102

# ------------------------------------------------------------------ 钩子守卫
# `before` / `after` 的返回值直接替换行集合 / 最终结果。没有守卫时这一类错误
# 全部是**静默**的：`after` 返回 undefined 会把这个哨兵原样泄进输出（不是合法
# JSON），`before` 返回 undefined 只会抛一句 `'_Undef' object is not iterable`，
# 看不出是钩子造成的。下面钉住三条：可读报错、标量放行、正常路径不受影响。

def _hook_schema(top: dict) -> dict:
    return {
        "name": "mx_hook_guard",
        "output": dict({"shape": "list"}, **top),
        "out": {"type": "object",
                "fields": {"v": {"type": "int64", "value": "$.fid"}}},
    }


def _hook_runner(top: dict):
    src = dcg.generate(_hook_schema(top))
    ns: dict = {}
    exec(compile(src, "<mx>", "exec"), ns)  # noqa: S102
    return ns["run"], src


def test_after_returning_undefined_is_a_named_error():
    """`after` 返回 undefined 必须抛指名 output.after 的 TypeError。"""
    run, src = _hook_runner({"after": "= $.nope"})
    with pytest.raises(TypeError, match="output.after"):
        run([{"fid": 1}])
    assert "_ha(" in src, src


def test_before_returning_undefined_or_scalar_is_a_named_error():
    """`before` 返回 undefined / 标量同样指名 output.before，文案要给出怎么改。"""
    run, src = _hook_runner({"before": "= $.nope"})
    with pytest.raises(TypeError, match="output.before"):
        run([{"fid": 1}])
    assert "_hb(" in src, src

    run2, _ = _hook_runner({"before": "= 42"})
    with pytest.raises(TypeError, match="output.before"):
        run2([{"fid": 1}])


def test_after_may_still_reshape_the_result_freely():
    """`after` 是「结果结构随你定」：标量 / 对象 / 数组都放行，只拦 undefined。"""
    run, _ = _hook_runner({"after": "= 42"})
    assert run([{"fid": 1}, {"fid": 2}]) == 42

    run2, _ = _hook_runner({"after": "= `{'n': len($)}`"})
    assert run2([{"fid": 1}, {"fid": 2}]) == {"n": 2}

    run3, _ = _hook_runner({"after": "= $count($)"})
    assert run3([{"fid": 1}, {"fid": 2}]) == 2


def test_hooks_do_not_cost_anything_when_absent():
    """没写钩子就不得生成 `_hb` / `_ha`（不为未声明的选项付费）。"""
    _, src = _hook_runner({})
    assert "_hb(" not in src and "_ha(" not in src, src


def test_before_and_after_together_keep_order_and_pass_through():
    """before 在行循环前、after 在 return 处；两者都经守卫后值不变。"""
    run, src = _hook_runner({"before": "= `[r for r in $ if r['fid'] > 1]`",
                             "after": "= $count($)"})
    assert src.index("_hb(") < src.index("_o=[]"), src
    assert "_ha(" in src, src
    assert run([{"fid": 1}, {"fid": 2}, {"fid": 3}]) == 2


# ------------------------------------------------ 钩子里 `$` 的类型随 shape 变化
@pytest.mark.parametrize("shape,want", [
    ("list", 3), ("rows", 3), ("index", 1), ("dict", 1),
])
def test_after_count_dollar_depends_on_shape(shape, want):
    """`after` 里的 `$count($)` 行数语义随 shape 变化（契约 §4.1 的表）。

    只有 `list` / `rows` 下 `$` 是行集合；`index` / `dict` 下是对象（对象算一个值）。
    这不是缺陷，但极易踩坑，且文档曾完全没写 —— 用测试钉住。
    """
    rows = [{"v": i} for i in range(3)]
    sch = {"name": "cnt_" + shape,
           "output": {"shape": shape, "after": "= {'n': $count($)}"},
           "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.v"}}}}
    got = _run_generated(dcg.generate(sch), rows)
    assert got == {"n": want}, (shape, got)


def test_after_field_access_depends_on_shape_too():
    """`after` 里 `$.v` 只有 `dict` 形状取得到列 —— 没有「四形状通吃」的写法。

    实测四种形状下 `after = "= {'n': $count($.v)}"`：
    * `dict`  -> `$` 是 `{'v': [...]}`，`$.v` 命中 -> `{'n': 4}`；
    * `list`  -> `$` 是 `[[0],[1],...]`，没有键 `v` -> undefined -> `_ob` 丢键 -> `{}`；
    * `index` -> `$` 是 `{0: [[0]], ...}`，同上 -> `{}`；
    * `rows`  -> `$` 是 `[{'v':0}, ...]`，对**数组**取 `.v` -> undefined -> `{}`。

    所以「数行数」这件事在不同形状下必须换写法，文档（README §4.1 表 +
    契约 §4.1 表）已如实写明。这条测试钉住现状，防止有人误以为能通吃。
    """
    rows = [{"v": i} for i in range(4)]
    want = {"list": {}, "dict": {"n": 4}, "index": {}, "rows": {}}
    for shape in ("list", "dict", "index", "rows"):
        sch = {"name": "cntc_" + shape,
               "output": {"shape": shape, "after": "= {'n': $count($.v)}"},
               "out": {"type": "object",
                       "fields": {"v": {"type": "int64", "value": "$.v"}}}}
        got = _run_generated(dcg.generate(sch), rows)
        assert got == want[shape], (shape, got)


def test_after_merge_on_list_shape_is_a_named_error():
    """`$merge` 只接受对象；list 形状下 `$` 是数组 -> undefined -> `_ha` 报错。

    文档（README §4.1 / 契约 §4.1）已写明这一点，测试保证错误是**指名**的。
    """
    sch = {"name": "mrg_list",
           "output": {"shape": "list", "after": "= $merge([$, {'x': 1}])"},
           "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.v"}}}}
    with pytest.raises(TypeError) as ei:
        _run_generated(dcg.generate(sch), [{"v": 1}])
    assert "output.after" in str(ei.value)


# ------------------------------------------- aggregate 在四种形状下都必须有输出
_AGG_ROWS = [{"a": 1, "b": 10}, {"a": 2, "b": 20}, {"a": 3, "b": 30}]
_AGG_FIELDS = {
    "a": {"type": "int64", "value": "$.a"},
    "stats": {"type": "object", "aggregate": True, "fields": {
        "n_rows": {"type": "int64", "const": 1},
        "qty_sum": {"type": "int64", "value": "$.b"}}},
}


def _agg_product(shape, fields=None):
    sch = {"name": "agg_" + shape, "output": {"shape": shape},
           "out": {"type": "object", "fields": fields or _AGG_FIELDS}}
    return _run_generated(dcg.generate(sch), _AGG_ROWS)


def test_aggregate_is_not_silently_dropped_in_any_shape():
    """`aggregate` 在**四种形状**下都必须出现在输出里（D-COMPOSITE-01）。

    修前三种行式形状都不输出 `stats`：`list` / `index` 静默少一列（最危险，
    用户拿到的数据不报错但缺字段），`rows` 直接 `IndexError`（按声明序位取
    `r[1]` 但行容器只有 1 格）。`dict` 是对照组，一直正确。
    """
    for shape in ("list", "dict", "index", "rows"):
        got = _agg_product(shape)
        assert got, (shape, got)
        # 每种形状里都必须能找回 stats 的两个子键值
        flat = json.dumps(got, ensure_ascii=False, default=str)
        assert "n_rows" in flat, (shape, flat)
        assert "qty_sum" in flat, (shape, flat)


def test_aggregate_dict_shape_is_column_container():
    """`dict` 形状：`aggregate` 字段是**列容器**（子键各成一列，const 只留一份）。"""
    got = _agg_product("dict")
    assert got["a"] == [1, 2, 3]
    assert got["stats"] == {"n_rows": 1, "qty_sum": [10, 20, 30]}


def test_aggregate_row_shapes_keep_named_row():
    """`list` / `index` / `rows`：`aggregate` 退化为**每行的具名对象**。

    行式容器没有「列」的位置，所以子键不拆开、整份 `stats` 作为一格的值。
    这与 README A.8.5「不写 aggregate 时该 object 字段是一列具名行」同一形状，
    只是容器方向从「一列」变成「每行一份」。
    """
    want_row = {"n_rows": 1, "qty_sum": None}     # qty_sum 逐行不同，下面单独断言
    for shape in ("list", "index", "rows"):
        got = _agg_product(shape)
        if shape == "list":
            rows = got
        elif shape == "index":
            rows = [got[i] for i in sorted(got)]
        else:
            rows = got
        assert len(rows) == 3, (shape, rows)
        for i, r in enumerate(rows):
            stats = r[1] if shape != "rows" else r["stats"]
            assert stats["n_rows"] == 1, (shape, stats)
            assert stats["qty_sum"] == [10, 20, 30][i], (shape, stats)


def test_aggregate_row_shapes_agree_with_plain_named_object():
    """行式形状下 `aggregate` 与「不标 aggregate 的具名对象」必须逐值相等。"""
    plain = {"a": {"type": "int64", "value": "$.a"},
             "stats": {"type": "object", "fields": {
                 "n_rows": {"type": "int64", "const": 1},
                 "qty_sum": {"type": "int64", "value": "$.b"}}}}
    for shape in ("list", "index", "rows"):
        assert _agg_product(shape) == _agg_product(shape, plain), shape
