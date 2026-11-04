"""扁平列特化的等价性守报（`_Gen._stream_fold_plan`）。

`list` 形状 + `output.after` 恰好折叠某一列时，产物不再逐行造 `[v]` 行容器，
而是把那一格直接攒成**扁平列**（`_o` 里是值本身），`after` 也从
`<fold>($map($, λ))` 换成 `<helper>(_o)` 直接调用。实测 10 万行
（Windows/3.13，A/B 同源交错）：`$sum` 231.5 → 44.6 ns/行（5.18×）、
`$average` 215.4 → 69.3（3.11×）、`$max` 224.1 → 57.4（3.91×）。

守报办法：**同一个 schema 强制走老路径与命中路径，逐值对拍**。把
`_Gen._stream_fold_plan` 临时替换成恒 `None`，得到的就是特化之前的产物。

最容易漏的一处是 `$average`：它的 helper 是 `_sm/len`，**分母**等于 `_mp`
丢弃 undefined 之后的长度，所以它必须逐行 `if _v is not _u: _c(_v)`；
`$sum`/`$min`/`$max` 的 helper 自己会跳过 undefined，循环里只要一条 `_c(...)`。
"""

import pytest

from dcg import codegen as C
from dcg import generate
from dcg.runtime import UNDEF


def _render(src):
    ns = {}
    exec(compile(src, "<gen>", "exec"), ns)
    return ns["run"]


def _gen(sch, trace):
    """公开的 `generate` 不暴露 `trace`（trace 只走 `_generate_norm`）。"""
    if not trace:
        return generate(sch)
    return C._generate_norm(C.validate(sch), trace=True)


def _build(after, fields, shape="list", trace=False, specialize=True):
    out = {"shape": shape, "after": after}
    sch = {"name": "agg_stream", "output": out,
           "out": {"type": "object", "fields": fields}}
    if specialize:
        return _gen(sch, trace)
    orig = C._Gen._stream_fold_plan
    C._Gen._stream_fold_plan = lambda self: None
    try:
        return _gen(sch, trace)
    finally:
        C._Gen._stream_fold_plan = orig


def _call(src, data):
    fn = _render(src)
    try:
        return ("value", fn(data))
    except Exception as e:                       # noqa: BLE001 —— 对拍要连异常一起比
        return (type(e).__name__, str(e))


def _both(after, fields, data, shape="list", trace=False):
    new_src = _build(after, fields, shape=shape, trace=trace, specialize=True)
    old_src = _build(after, fields, shape=shape, trace=trace, specialize=False)
    return _call(new_src, data), _call(old_src, data), new_src, old_src


ONE_COL = {"v": {"type": "int64", "value": "$.1"}}
THREE_COL = {"a": {"type": "int64", "value": "$.1"},
             "b": {"type": "int64", "value": "$.0"},
             "c": {"type": "int64", "value": "$.1"}}
# `type:"any"` + `value` 是直接取值：源行里放 `UNDEF` 就能造出「列里有 undefined」，
# 这正是 `_mp` 丢弃、而 `$average` 的分母要跟着变的那种行。
UNDEF_COL = {"v": {"type": "any", "value": "$.a"}}
UNDEF_ROWS = [{"a": 3}, {"a": UNDEF}, {"a": 5}, {"a": UNDEF}, {"a": 7}]

FOLDS = ["$sum", "$average", "$max", "$min"]


@pytest.mark.parametrize("fold", FOLDS)
@pytest.mark.parametrize("fields", [ONE_COL, THREE_COL], ids=["1col", "3col"])
def test_equivalent_on_int_column(fold, fields):
    after = "=" + fold + "($map($, function($r){$r[0]}))"
    data = [(i, i * 3 % 1000) for i in range(200)]
    got, want, _, _ = _both(after, fields, data)
    assert got == want


@pytest.mark.parametrize("fold", FOLDS)
def test_equivalent_on_empty_input(fold):
    """空输入：`$sum([])` 是 0，其余折叠落 undefined 再被 `_ha` 拒——两条路径必须一致。"""
    after = "=" + fold + "($map($, function($r){$r[0]}))"
    got, want, _, _ = _both(after, ONE_COL, [])
    assert got == want


@pytest.mark.parametrize("fold", FOLDS)
def test_equivalent_with_undefined_rows(fold):
    """列里混 undefined —— `$average` 的分母是这里的关键。"""
    after = "=" + fold + "($map($, function($r){$r[0]}))"
    got, want, _, _ = _both(after, UNDEF_COL, UNDEF_ROWS)
    assert got == want


def test_average_denominator_ignores_undefined():
    """`$average` 的分母必须是「非 undefined 的个数」（`_mp` 会丢掉 undefined）。"""
    after = "=$average($map($, function($r){$r[0]}))"
    got, want, src, _ = _both(after, UNDEF_COL, UNDEF_ROWS)
    assert got == want == ("value", 5.0)        # (3+5+7)/3，不是 …/5
    assert "if _v is not _u: _c(_v)" in src
    assert "_u=_U" in src


def test_sum_skips_undefined_without_filter():
    """`$sum` 不需要逐行过滤：`_sm` 自己的慢路径会跳过 undefined。"""
    after = "=$sum($map($, function($r){$r[0]}))"
    got, want, src, _ = _both(after, UNDEF_COL, UNDEF_ROWS)
    assert got == want == ("value", 15)
    assert "if _v is not _u" not in src


def test_flat_column_no_row_container():
    """命中时行容器是扁平列：`_c(R[1])`，不是 `_c([R[1]])`。"""
    src = _build("=$sum($map($, function($r){$r[0]}))", ONE_COL)
    assert "_c(R[1])\n" in src
    assert "_c([R[1]])" not in src
    assert "_ha(_sm(_o))" in src
    assert "_mp" not in src


def test_second_column_index():
    """`$r[1]` 取第二格，不是固定取第 0 格。"""
    after = "=$sum($map($, function($r){$r[1]}))"
    data = [(i, i * 3 % 1000) for i in range(50)]
    got, want, src, _ = _both(after, THREE_COL, data)
    assert got == want == ("value", sum(r[0] for r in data))
    assert "_c(R[0])\n" in src


@pytest.mark.parametrize("after,why", [
    ("=$sum($map($, function($r){$r[0]+1}))", "λ 体不是裸下标"),
    ("=$count($map($, function($r){$r[0]}))", "折叠函数不在白名单"),
    ("=$sum($.v)", "实参不是 $map($, λ)"),
    ("=$sum($map($, function($a,$b){$a[0]}))", "λ 有两个形参"),
    ("=$sum($map($, function($r){$r[9]}))", "下标越界"),
    ("=$sum($map($.x, function($r){$r[0]}))", "map 的实参不是裸 $"),
])
def test_non_matching_keeps_old_source(after, why):
    """不命中时必须**逐字节**与老产物相同（不能只是结果相同）。"""
    new_src = _build(after, ONE_COL, specialize=True)
    old_src = _build(after, ONE_COL, specialize=False)
    assert new_src == old_src, why


def test_index_shape_not_specialized():
    """`index` 形状外面还套 `{i:...}`，不在特化范围。"""
    after = "=$sum($map($, function($r){$r[0]}))"
    new_src = _build(after, ONE_COL, shape="index", specialize=True)
    old_src = _build(after, ONE_COL, shape="index", specialize=False)
    assert new_src == old_src


def test_trace_not_specialized():
    """trace 要记 `_o[-1]`，扁平列之后那一格不再是行。"""
    after = "=$sum($map($, function($r){$r[0]}))"
    new_src = _build(after, ONE_COL, trace=True, specialize=True)
    old_src = _build(after, ONE_COL, trace=True, specialize=False)
    assert new_src == old_src


def test_float_column_equivalent():
    """浮点列：`sum` 左到右累加，两条路径必须逐位相同。"""
    after = "=$sum($map($, function($r){$r[0]}))"
    fields = {"v": {"type": "float64", "value": "$.0"}}
    data = [(i * 0.1, 0) for i in range(100)]
    got, want, _, _ = _both(after, fields, data)
    assert got == want


def test_optional_field_equivalent():
    """optional 字段缺键落成 `None`：两条路径对 `sum([3, None, 5])` 必须同样报错。"""
    after = "=$sum($map($, function($r){$r[0]}))"
    fields = {"v": {"type": "any", "value": "$.a", "optional": True}}
    data = [{"a": 3}, {"b": 4}, {"a": 5}]
    got, want, _, _ = _both(after, fields, data)
    assert got == want
