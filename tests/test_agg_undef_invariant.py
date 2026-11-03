"""扁平列特化所依赖的 **helper 语义契约**（D 项，2026-11-03）。

`dcg/codegen.py` 的 `_Gen._stream_fold_plan()` 会把

    <fold>($map($, function($r){$r[K]}))

改写成 `<helper>(_o)`，其中 `_o` 是**扁平列**（每行那一格的值本身，不再是 `[v]`）。
这个改写只有在下面三条契约同时成立时才逐值等价 —— 这三条以前是隐式的、散落在
`runtime.py` 的注释里，任一处被人「顺手优化」就会静默改变聚合结果，所以在这里
钉死成显式测试：

* `_mp(a, f)` 丢弃逐元素结果里的 `UNDEF`；
* `_sm` / `_mn` / `_mx` 自己跳过 `UNDEF`（`$min`/`$max` 还要跳过 `bool`）；
* `_av` 的**分母**是「丢弃 `UNDEF` 之后」的长度。

一旦某一轮有人改了这三条中的任意一条，`tests/test_agg_stream.py` 的对拍仍可能
侥幸通过（它比较的是「特化前后」两条都在变），而本文件比较的是「helper 的语义
本身」，是那条对拍照不到的地板。
"""

import pytest

from dcg import runtime as rt
from dcg.runtime import UNDEF


# ---------------------------------------------------------------- $map 丢弃 undefined

def test_map_drops_undefined_results():
    assert rt._mp([1, UNDEF, 3], lambda v, i: v) == [1, 3]


def test_map_keeps_falsy_but_defined_results():
    """只丢 `UNDEF`，不丢 `0`/`""`/`False`/`None` —— 这些是合法值。"""
    assert rt._mp([0, "", False, None], lambda v, i: v) == [0, "", False, None]


def test_map_scalar_is_promoted():
    assert rt._mp(7, lambda v, i: v) == [7]


def test_map_undefined_argument_is_undefined():
    assert rt._mp(UNDEF, lambda v, i: v) is UNDEF


def test_map_passes_index():
    assert rt._mp(["a", "b"], lambda v, i: i) == [0, 1]


# ---------------------------------------------------------------- 折叠 helper 跳过 undefined

@pytest.mark.parametrize("helper", [rt._sm, rt._mn, rt._mx])
def test_folds_skip_undefined(helper):
    assert helper([3, UNDEF, 5]) == helper([3, 5])


@pytest.mark.parametrize("helper", [rt._mn, rt._mx])
def test_min_max_skip_bool(helper):
    """`bool` 是 `int` 子类但不是数值：`$min([True, 5])` 必须是 5。"""
    assert helper([True, 5]) == 5
    assert helper([False, 5]) == 5


def test_all_undefined_folds_to_undefined():
    assert rt._mn([UNDEF, UNDEF]) is UNDEF
    assert rt._mx([UNDEF, UNDEF]) is UNDEF
    assert rt._av([UNDEF, UNDEF]) is UNDEF


# ---------------------------------------------------------------- $average 的分母

def test_average_denominator_excludes_undefined():
    """(3+5+7)/3 = 5.0，不是 15/5 = 3.0 —— 分母跟着 `_mp` 的过滤走。"""
    assert rt._av([3, UNDEF, 5, UNDEF, 7]) == 5.0


def test_average_denominator_matches_map_then_fold():
    rows = [{"v": 3}, {"v": UNDEF}, {"v": 5}]
    col = rt._mp(rows, lambda r, i: r["v"])
    assert rt._av(col) == 4.0


def test_average_rejects_non_numeric_after_filtering():
    """过滤掉 undefined 之后仍有非数值：整条 `$average` 落 undefined。"""
    assert rt._av([3, "x", 5]) is UNDEF
    assert rt._av([3, True, 5]) is UNDEF


def test_average_empty_column_is_undefined():
    assert rt._av([]) is UNDEF


# ---------------------------------------------------------------- 与特化产物的一致性

def test_specialized_average_matches_plain_map_fold():
    """端到端：特化产物与「手工 `_av(_mp(...))`」对同一份数据必须同值。"""
    from dcg import codegen as C
    from dcg import generate

    sch = {"name": "agg_undef", "output": {"shape": "list",
                                           "after": "=$average($map($, function($r){$r[0]}))"},
           "out": {"type": "object", "fields": {"v": {"type": "any", "value": "$.a"}}}}
    src = generate(sch)
    ns = {}
    exec(compile(src, "<gen>", "exec"), ns)
    rows = [{"a": 3}, {"a": UNDEF}, {"a": 5}, {"a": UNDEF}, {"a": 7}]
    assert ns["run"](rows) == 5.0
    assert "_mp" not in src, "命中扁平列特化后不应再有 `_mp` 调用"


def test_specialized_sum_matches_plain_map_fold():
    from dcg import generate

    sch = {"name": "agg_undef_sum",
           "output": {"shape": "list",
                      "after": "=$sum($map($, function($r){$r[0]}))"},
           "out": {"type": "object", "fields": {"v": {"type": "any", "value": "$.a"}}}}
    src = generate(sch)
    ns = {}
    exec(compile(src, "<gen>", "exec"), ns)
    rows = [{"a": 3}, {"a": UNDEF}, {"a": 5}]
    assert ns["run"](rows) == 8
