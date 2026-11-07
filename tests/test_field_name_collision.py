"""字段名与产物内部名撞车 —— 静默错值 / 崩溃的回归守护。

## 为什么这个文件存在

产物是**文本生成**的，字段名会被 `_Gen.plan()` 通过 `bind_name` 变成局部名：

```python
bind_name(('c',))  -> '_c'      # 撞上 `_c=_o.append`
bind_name(('o',))  -> '_o'      # 撞上输出列表
bind_name(('U',))  -> '_U'      # 撞上 undefined 哨兵
bind_name(('sm',)) -> '_sm'     # 撞上 runtime helper `$sum`
```

撞上之后的后果分三档，**都不可接受**：

| 撞到的名 | 后果 | 为什么危险 |
| --- | --- | --- |
| `_c`（`_o.append`） | `TypeError: 'int' object is not callable` | 至少会炸 |
| `_o`（输出列表） | **静默返回标量**，或 `index` 下 `not iterable` | 错值 |
| `_U`（undefined 哨兵） | optional 字段本该是 `5`，**静默变成 `None`** | 错值，且只在有 optional 字段时暴露 |
| `_sm`/`_mt`（helper） | `TypeError: 'list' object is not callable` | 至少会炸 |
| `_SP`/`_g`/`_i0` | `UnboundLocalError` | 至少会炸 |

最阴的是 `_U` 与 `_o`：**同一个 schema 换 `shape` 就换个结果**（`rows` 正确、
`list` 错），这种「换个形态就错」最难被发现。

## 断言口径

每个用例都**独立算出期望值**（不拿某个 shape 当基准），然后要求四个 shape
（`rows`/`list`/`index`/`dict`）全部等于它。这样断言本身不会跟着缺陷漂。

字段名 `_xxx` 那种已经被 `_check_reserved` 挡住的（`fname.startswith("_")`）不在
本文件范围内 —— 那是**构建期拒绝**，本文件管的是「构建期放行但产物是错的」。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402

SHAPES = ("rows", "list", "index", "dict")


def _run(src, data, tables=None):
    ns: dict = {}
    exec(compile(src, "<artifact>", "exec"), ns)
    run = ns["run"]
    try:
        return run(data, tables) if tables is not None else run(data)
    except TypeError:
        return run(data)


def _schema(fields, shape, after=None):
    out = {"shape": shape}
    if after is not None:
        out["after"] = after
    return {"name": "collide", "input": {"source": "$"}, "output": out,
            "out": {"type": "object", "fields": fields}}


def _expected(fname, shape, val):
    """四个 shape 的期望值，**独立于被测实现**算出。"""
    pair = [val, val + 1]
    if shape == "rows":
        return [{fname: pair[0], "zz": pair[1]}]
    if shape == "list":
        return [pair]
    if shape == "index":
        return {0: pair}
    return {fname: [pair[0]], "zz": [pair[1]]}


#: 这些名字都曾被 `bind_name` 变成产物内部名，实测产物是错的。
#: 每一档的「错法」写在注释里，便于失败时判断回归到哪一档。
COLLIDING_NAMES = [
    # ---- 撞 `_c=_o.append` / `_o`（输出列表）：list 下崩或静默返回标量
    "c", "o",
    # ---- 撞 undefined 哨兵 `_U`：只有 optional 字段在场时才暴露（静默错值）
    "U",
    # ---- 撞 `_d`（数据形参）：for 已消费完，当前不崩 —— 仍然不该遮蔽
    "d",
    # ---- 撞 `tmp()` 的 `_t{n}` 家族：CSE / optional 中转会用到 `_t1`
    "t1",
    # ---- 撞 `$lookup` 槽位 `_i{n}` 与规格表 `_SP`
    "i0", "SP",
    # ---- 撞 `_dot(tables,_SP)` 的结果 `_T`
    "T",
    # ---- 撞 `_local_binds` 注入的 `_g=globals()`
    "g",
]


@pytest.mark.parametrize("fname", COLLIDING_NAMES)
@pytest.mark.parametrize("shape", SHAPES)
def test_field_name_does_not_shadow_internal_name(fname, shape):
    """字段名经 `bind_name` 生成的局部名，绝不能撞产物自己的内部名。

    两条字段（`fname` 与引用它的 `zz`）保证 `plan()` 真的会提升出局部名 ——
    只写一个字段时它被内联，撞不上，测试就成了假绿。
    """
    val = 3
    fields = {
        fname: {"type": "int64", "value": "$.%s" % fname},
        "zz": {"type": "int64", "expr": "$.%s + 1" % fname},
    }
    src = dcg.generate(_schema(fields, shape))
    got = _run(src, [{fname: val}])
    assert got == _expected(fname, shape, val), (
        "字段名 %r 在 shape=%r 下产物错值：\n%s" % (fname, shape, src))


@pytest.mark.parametrize("fname", ["U", "d", "c", "o"])
def test_sentinel_collision_with_optional_field(fname):
    """`_U` 被字段遮蔽后，optional 的守卫会把**有值**判成 undefined。

    `_t1 is _U` 里的 `_U` 已经是字段值（如 5），于是 `opt` 本应是 5 却变成
    `None`。`rows` 形态不走行容器、不提升局部名，所以它是对的 —— 断言里
    四个 shape 必须一致，正是为了抓住这种「换形态就错」。
    """
    fields = {
        fname: {"type": "int64", "value": "$.%s" % fname},
        "zz": {"type": "int64", "expr": "$.%s + 1" % fname},
        "opt": {"type": "int64", "value": "$.opt", "optional": True},
    }
    row = {fname: 5, "opt": 5}
    exp = {"rows": [{fname: 5, "zz": 6, "opt": 5}],
           "list": [[5, 6, 5]],
           "index": {0: [5, 6, 5]},
           "dict": {fname: [5], "zz": [6], "opt": [5]}}
    for shape in SHAPES:
        src = dcg.generate(_schema(fields, shape))
        got = _run(src, [row])
        assert got == exp[shape], (
            "字段名 %r 在 shape=%r 下 optional 守卫误判：\n%s" % (fname, shape, src))


#: runtime helper 的名字（`$sum` -> `_sm`、`$match` -> `_mt`…）。
#: 字段名叫这些时，`_local_binds` 注入的 `_sm=_g['_sm']` 会被字段值覆盖。
HELPER_NAMES = ["sm", "mt", "up", "lo", "av", "mx", "mn", "cc", "ln"]


@pytest.mark.parametrize("helper", HELPER_NAMES)
@pytest.mark.parametrize("shape", SHAPES)
def test_field_name_does_not_shadow_runtime_helper(helper, shape):
    """字段名撞 runtime helper 时，helper 调用会打到字段值上。

    实测：字段名 `sm` + `$sum($.sm)` → `_sm=R['sm']` 之后 `_sm(_sm)` →
    `TypeError: 'list' object is not callable`。
    """
    fields = {
        helper: {"type": "list", "itype": "int64", "value": "$.%s" % helper},
        "zz": {"type": "int64", "expr": "$sum($.%s)" % helper},
    }
    row = {helper: [1, 2, 3]}
    src = dcg.generate(_schema(fields, shape))
    got = _run(src, [row])
    if shape == "rows":
        assert got == [{helper: [1, 2, 3], "zz": 6}]
    elif shape == "list":
        assert got == [[[1, 2, 3], 6]]
    elif shape == "index":
        assert got == {0: [[1, 2, 3], 6]}
    else:
        assert got == {helper: [[1, 2, 3]], "zz": [6]}


def test_uppercase_helper_field_name():
    """`$uppercase` 走 `_STR_METHODS` 内联成 `.upper()`，撞名时不炸但也要对。

    这条是**对照**：`up` 在 `rows`/`list` 下都正常（内联），说明并非所有
    helper 名都会坏 —— 只有真的走 helper 调用的才坏。
    """
    fields = {"up": {"type": "str", "value": "$.up"},
              "zz": {"type": "str", "expr": "$uppercase($.up)"}}
    for shape in SHAPES:
        src = dcg.generate(_schema(fields, shape))
        got = _run(src, [{"up": "ab"}])
        if shape == "rows":
            assert got == [{"up": "ab", "zz": "AB"}]
        elif shape == "list":
            assert got == [["ab", "AB"]]
        elif shape == "index":
            assert got == {0: ["ab", "AB"]}
        else:
            assert got == {"up": ["ab"], "zz": ["AB"]}


@pytest.mark.parametrize("fname", ["g", "SP", "i0"])
def test_field_name_does_not_shadow_lookup_plumbing(fname):
    """`$lookup` 的管线名（`_SP` 规格表 / `_T` 表 / `_i0` 槽位 / `_g`）也会被遮蔽。"""
    fields = {
        fname: {"type": "int64", "value": "$.%s" % fname},
        "lk": {"type": "str", "expr": "$lookup($.k,'T','k','v')"},
    }
    tables = {"T": [{"k": 7, "v": "seven"}]}
    src = dcg.generate(_schema(fields, "list"))
    got = _run(src, [{fname: 1, "k": 7}], tables)
    assert got == [[1, "seven"]], src
