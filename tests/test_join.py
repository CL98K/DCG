# -*- coding: utf-8 -*-
"""2.2.3 表连接（`$lookup` + `dcg.Table`）的验收测试。

分五层：

* **运行期对象**：`Table` / `Index` / `RangeIndex` / `prepare` / `bind` 的契约；
* **编译期校验**：参数个数、表名/列名必须是字面量、模式与策略枚举、右键形态；
* **端到端**：取列 / 取整行 / 1:N 数组 / 1:N 聚合 / 区间谓词 / 复合键 / strict-loose；
* **输出后处理**：`output.where`（semi/anti）与 `output.expand`（fanout）；
* **复核回归**：复合键 loose、`specs_of`+`bind` 区间规格、释放后 `range`、区间 + `all`、位置行展开、键分桶。

语义权威是 `DCG Join 能力设计.md`。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg                                              # noqa: E402
from dcg import SchemaError                             # noqa: E402
from dcg.runtime import UNDEF                           # noqa: E402
from dcg.table import Table, bind, prepare              # noqa: E402

CUSTOMERS = [
    {"id": 1, "name": "Ada", "region": "N"},
    {"id": 2, "name": "Bob", "region": "S"},
]
ORDERS = [
    {"id": 10, "cust": 1, "amount": 120},
    {"id": 11, "cust": 2, "amount": 80},
    {"id": 12, "cust": 9, "amount": 30},
]
ITEMS = [
    {"order": 10, "sku": "A", "qty": 2},
    {"order": 10, "sku": "B", "qty": 3},
    {"order": 11, "sku": "C", "qty": 1},
]
RATES = [
    {"region": "N", "lo": 0, "hi": 100, "rate": 0.05},
    {"region": "N", "lo": 100, "hi": None, "rate": 0.08},
    {"region": "S", "lo": 0, "hi": None, "rate": 0.03},
]
#: 单一口径的阶梯表：纯区间索引（不带等值前缀）只能对**一张表一种口径**有意义
LADDER = [
    {"lo": 0, "hi": 100, "rate": 0.05},
    {"lo": 100, "hi": None, "rate": 0.08},
]


def _schema(fields, shape="rows", extra=None):
    out_cfg = {"shape": shape}
    if extra:
        out_cfg.update(extra)
    return {"name": "join_case", "input": {"source": "$"},
            "output": out_cfg, "out": {"fields": fields}}


def _run(fields, rows, tables, shape="rows", extra=None):
    """编译一份单 out 的 schema，返回 `(src, run(rows, tables))`。

    默认 `shape="rows"`（每行一个 dict）；`"list"` 是**位置行**，断言不便。
    """
    schema = _schema(fields, shape, extra)
    dcg.validate(schema)
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<join>", "exec"), ns)
    # 没有 `$lookup` 的 schema 仍生成单参 `run(_d)`，多传 tables 会 TypeError
    if "tables=None" in src:
        return src, ns["run"](rows, tables=tables)
    return src, ns["run"](rows)


def _field(expr, typ="any", optional=True):
    return {"type": typ, "optional": optional, "expr": expr}


# --------------------------------------------------------------------------- #
# 1. 运行期对象
# --------------------------------------------------------------------------- #

def test_table_index_returns_one_row_and_dedupes():
    t = Table(CUSTOMERS)
    ix = t.index(key="id")
    assert ix.g(1) == {"id": 1, "name": "Ada", "region": "N"}
    assert ix.g(9) is None
    # 同 spec 幂等：再挂一次拿到同一个对象，不重复建桶
    assert t.index(key="id") is ix


def test_table_index_duplicate_key_is_an_error_for_one():
    with pytest.raises(ValueError, match="右表键重复"):
        Table([{"id": 1}, {"id": 1}]).index(key="id")


def test_table_index_strategy_first_and_all():
    rows = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}]
    first = Table(rows).index(key="id", strategy="first")
    assert first.g(1) == {"id": 1, "v": "a"}
    every = Table(rows).index(key="id", strategy="all")
    assert [r["v"] for r in every.a(1)] == ["a", "b"]
    assert every.a(9) == ()


def test_table_index_strict_vs_loose():
    t = Table([{"id": 1}])
    assert t.index(key="id", mode="strict").g("1") is None
    assert t.index(key="id", mode="loose").g("1") == {"id": 1}


def test_table_index_composite_key():
    rows = [{"a": 1, "b": "x"}, {"a": 1, "b": "y"}]
    ix = Table(rows).index(key=["a", "b"])
    assert ix.g([1, "x"]) == {"a": 1, "b": "x"}
    assert ix.g([1, "z"]) is None
    # 任一分量为 null 不参与匹配
    assert ix.g([1, None]) is None


def test_table_range_index_picks_overlapping_band():
    ix = Table(LADDER).range(key="lo", hi="hi")
    assert ix.pick(30)["rate"] == 0.05
    assert ix.pick(120)["rate"] == 0.08
    # `hi: null` 是上界开放（+inf），不是「这一档作废」
    assert ix.pick(2000)["rate"] == 0.08
    assert ix.pick(-1) is None
    # 整行 / 取列两种形态
    assert ix.pr(120)["rate"] == 0.08
    assert ix.pv(120, "rate") == 0.08
    assert ix.pv(-1, "rate") is None


def test_table_release_drops_rows_but_keeps_index():
    t = Table(CUSTOMERS, keep_rows=False)
    t.index(key="id")
    t.release()
    assert t.rows is None
    assert t.index(key="id").g(1)["name"] == "Ada"
    with pytest.raises(RuntimeError, match="不允许释放"):
        Table(CUSTOMERS).release()


def _spec(table="customers", key="id", mode="strict", strategy="one",
          kind="hash", hi=None):
    return {"table": table, "key": key, "mode": mode, "strategy": strategy,
            "kind": kind, "hi": hi}


def test_prepare_builds_specs_and_bind_only_matches():
    t = Table(CUSTOMERS)
    got = prepare([_spec()], {"customers": t})
    assert got["customers"].s0.g(1)["name"] == "Ada"

    with pytest.raises(ValueError, match="缺少索引"):
        bind([_spec()], {"customers": Table(CUSTOMERS)})

    # 已手建索引时 bind 只做绑定，不重建
    hand = Table(CUSTOMERS)
    hand.index(key="id")
    bind([_spec()], {"customers": hand})
    assert hand.s0.g(2)["name"] == "Bob"


def test_bind_rejects_bare_lists_and_missing_tables():
    with pytest.raises(TypeError, match="必须是 dcg.Table"):
        bind([_spec()], {"customers": CUSTOMERS})
    with pytest.raises(TypeError, match="必须是 dcg.Table"):
        bind([_spec()], {})


# --------------------------------------------------------------------------- #
# 2. 编译期校验
# --------------------------------------------------------------------------- #

def _gen(expr):
    schema = _schema({"v": _field(expr)})
    return dcg.generate(schema)


def test_two_arg_lookup_is_removed_with_a_pointer():
    with pytest.raises(SchemaError, match="两参形态"):
        _gen("$lookup($.o, 'k')")


def test_table_name_must_be_a_literal():
    with pytest.raises(SchemaError, match="表名"):
        _gen("$lookup($.id, $.t, 'id', 'name')")


def test_mode_and_strategy_enums_are_validated():
    with pytest.raises(SchemaError, match="strict"):
        _gen("$lookup($.id, 'c', 'id', 'name', 'fuzzy')")
    with pytest.raises(SchemaError, match="'all'"):
        _gen("$lookup($.id, 'c', 'id', 'name', 'strict', 'many')")


def test_too_few_and_too_many_args():
    with pytest.raises(SchemaError, match="至少 3 个参数"):
        _gen("$lookup($.id)")
    with pytest.raises(SchemaError, match="最多 7 个参数"):
        _gen("$lookup($.id,'c','id','n','strict','one','true','x')")


def test_null_right_key_requires_a_range_predicate():
    with pytest.raises(SchemaError, match="只支持区间谓词"):
        _gen("$lookup($.id, 'c', null, 'name')")


def test_literal_only_arguments_are_rejected_with_their_own_message():
    """字面量校验的 6 条文案：每条都要能定位到是哪个参数。"""
    cases = [
        ("$lookup($.id, 'c', [], 'name')", "不能是空数组"),
        ("$lookup($.id, 'c', $.k, 'name')", "第 3 个参数（右键）"),
        ("$lookup($.id, 'c', [$.k], 'name')", "右键数组的元素"),
        ("$lookup($.id, 'c', 'id', $.col)", "第 4 个参数（列名）"),
        ("$lookup($.id, 'c', 'id', 'name', $.m)", "第 5 个参数（模式）"),
        ("$lookup($.id, 'c', 'id', 'name', 'strict', 'one', $.p)", "第 7 个参数（谓词）"),
    ]
    for expr, msg in cases:
        with pytest.raises(SchemaError, match=msg):
            _gen(expr)


def test_range_predicate_reverse_shapes_are_recognised():
    """谓词的反向写法（`$l.x >= $r.lo`、`null = $r.hi`、lo/hi 换序）也要走区间快路径。"""
    shapes = [
        "$r.lo <= $l.amount and ($r.hi = null or $l.amount < $r.hi)",
        "$l.amount >= $r.lo and ($r.hi = null or $r.hi > $l.amount)",
        "$r.lo <= $l.amount and (null = $r.hi or $l.amount < $r.hi)",
    ]
    for pred in shapes:
        expr = ("$lookup($.amount, 'ladder', null, 'rate', 'strict', 'first',"
                " %r)" % pred)
        fields = {"rate": _field(expr, "float64")}
        src, out = _run(fields, [{"amount": 30}, {"amount": 500}], {"ladder": LADDER})
        assert [r["rate"] for r in out] == [0.05, 0.08], pred
        assert ".pv(" in src, pred


def test_range_predicate_with_all_and_whole_row():
    """区间 + `all` + 整行（`pra`）：返回全部命中行的副本。"""
    expr = ("$lookup($.amount, 'ladder', null, null, 'strict', 'all',"
            " '$r.lo <= $l.amount and ($r.hi = null or $l.amount < $r.hi)')")
    fields = {"band": _field(expr, "list")}
    src, out = _run(fields, [{"amount": 30}, {"amount": -1}], {"ladder": LADDER})
    assert out[0]["band"] == [{"lo": 0, "hi": 100, "rate": 0.05}]
    assert out[1]["band"] == []
    assert ".pra(" in src


def test_range_whole_row_miss_is_undefined():
    """区间 + 整行未命中返回 `undefined`（配合 `$exists` 用），不是 `None`。"""
    ix = Table(LADDER).range(key="lo", hi="hi")
    assert ix.pr(-1) is UNDEF
    assert ix.pv(-1, "rate") is None
    assert ix.pra(-1) == []
    assert [r["rate"] for r in ix.pra(30)] == [0.05]


def test_index_buckets_keep_types_apart():
    """分桶按 `type()`：`True`、`1`、`1.0` 是三个不同的键；不可哈希的键不进索引。"""
    t = Table([{"k": True, "v": "b"}, {"k": 1, "v": "i"}, {"k": 1.0, "v": "f"},
               {"k": [1, 2], "v": "list"}])
    ix = t.index(key="k", strategy="first")
    assert ix.gv(True, "v") == "b"
    assert ix.gv(1, "v") == "i"
    assert ix.gv(1.0, "v") == "f"
    assert ix.g([1, 2]) is None          # 不可哈希：不进索引，标量键永不命中



# --------------------------------------------------------------------------- #
# 3. 端到端
# --------------------------------------------------------------------------- #

def test_lookup_takes_one_column_and_misses_to_null():
    fields = {"id": _field("$.id", "int64", False),
              "name": _field("$lookup($.cust, 'customers', 'id', 'name')", "str")}
    _src, out = _run(fields, ORDERS, {"customers": CUSTOMERS})
    assert out == [{"id": 10, "name": "Ada"},
                   {"id": 11, "name": "Bob"},
                   {"id": 12, "name": None}]


def test_lookup_whole_row_is_a_dict_and_misses_to_undefined():
    fields = {"id": _field("$.id", "int64", False),
              "cust": _field("$lookup($.cust, 'customers', 'id')", "object")}
    _src, out = _run(fields, ORDERS[:1], {"customers": CUSTOMERS})
    assert out[0]["cust"] == {"id": 1, "name": "Ada", "region": "N"}
    # 整行返回值是浅拷贝：改它不影响表
    out[0]["cust"]["name"] = "X"
    assert CUSTOMERS[0]["name"] == "Ada"

    # 未命中在**表达式里**是 `undefined`（`$exists` 为假），落到 `optional` 字段
    # 上按字段语义归一成 `null`；这一点由 semi/anti 用例从外面钉住。
    _src, miss = _run(fields, ORDERS[2:], {"customers": CUSTOMERS})
    assert miss[0]["cust"] is None
    ix = Table(CUSTOMERS).index(key="id")
    assert ix.gr(9) is UNDEF


def test_lookup_all_returns_array_and_aggregates():
    fields = {"id": _field("$.id", "int64", False),
              "qty": _field("$sum($lookup($.id, 'items', 'order', 'qty', 'strict', 'all'))", "int64"),
              "n": _field("$count($lookup($.id, 'items', 'order', 'qty', 'strict', 'all'))", "int64")}
    _src, out = _run(fields, ORDERS, {"items": ITEMS})
    assert [r["qty"] for r in out] == [5, 1, 0]
    assert [r["n"] for r in out] == [2, 1, 0]


def test_lookup_first_takes_first_duplicate():
    rows = [{"k": 1, "v": "a"}, {"k": 1, "v": "b"}]
    fields = {"v": _field("$lookup($.k, 'r', 'k', 'v', 'strict', 'first')", "str")}
    _src, out = _run(fields, [{"k": 1}], {"r": rows})
    assert out == [{"v": "a"}]


def test_lookup_range_predicate_with_prefix():
    expr = ("$single($lookup($.region, 'rates', 'region', 'rate', 'strict', 'all',"
            " '$r.lo <= $l.amount and ($r.hi = null or $l.amount < $r.hi)'))")
    fields = {"id": _field("$.id", "int64", False),
              "region": _field("$lookup($.cust, 'customers', 'id', 'region')", "str"),
              "rate": _field(expr.replace("$.region", "$lookup($.cust, 'customers', 'id', 'region')"),
                             "float64")}
    _src, out = _run(fields, ORDERS, {"customers": CUSTOMERS, "rates": RATES})
    assert [r["rate"] for r in out] == [0.08, 0.03, None]


def test_lookup_range_predicate_with_dollar_left_row():
    """回归：谓词里写 `$.amount`（与 `$l.amount` 等价）曾被当成未知变量。

    症状是 `SchemaError: 行模式没有变量作用域：$l1 不是任何函数字面量的形参`，
    而 `$l.amount` 走的是同一条路——`$.` 必须先折成 `$l.`。
    """
    doc = ("$lookup($.amount, 'ladder', null, 'rate', 'strict', 'first',"
           " '$r.lo <= $.amount and ($r.hi = null or $.amount < $r.hi)')")
    left = ("$lookup($.amount, 'ladder', null, 'rate', 'strict', 'first',"
            " '$r.lo <= $l.amount and ($r.hi = null or $l.amount < $r.hi)')")
    for expr in (doc, left):
        fields = {"id": _field("$.id", "int64", False),
                  "rate": _field(expr, "float64")}
        _src, out = _run(fields, [{"id": 1, "amount": 30}, {"id": 2, "amount": 500},
                                  {"id": 3, "amount": 99}], {"ladder": LADDER})
        assert [r["rate"] for r in out] == [0.05, 0.08, 0.05]


def test_predicate_dollar_inside_string_literal_is_left_alone():
    """`$.` 折算要认引号：谓词里的字符串字面量原样保留。"""
    right = [{"k": 1, "tag": "$.x"}, {"k": 1, "tag": "y"}]
    expr = ("$lookup($.k, 'r', 'k', 'tag', 'strict', 'first',"
            " '$r.tag = \"$.x\"')")
    fields = {"tag": _field(expr, "str")}
    _src, out = _run(fields, [{"k": 1}], {"r": right})
    assert out == [{"tag": "$.x"}]


def test_lookup_predicate_whole_row_is_a_dict_copy():
    """第 4 参写 `null` + 谓词 = 「谓词 + 整行」。回归：曾一律返回 `undefined`。"""
    expr = ("$lookup($.cust, 'customers', 'id', null, 'strict', 'one',"
            " '$r.id > 0')")
    fields = {"id": _field("$.id", "int64", False),
              "cust": _field(expr, "object")}
    _src, out = _run(fields, ORDERS, {"customers": CUSTOMERS})
    assert out[0]["cust"] == {"id": 1, "name": "Ada", "region": "N"}
    assert out[2]["cust"] is None          # cust=9 未命中：不抛异常，也不返回 {}
    # 谓词 + 整行与 `gr()` 一样给副本：改它不影响表
    out[0]["cust"]["name"] = "X"
    assert CUSTOMERS[0]["name"] == "Ada"


def test_lookup_predicate_miss_has_no_candidate():
    """回归：等值键未命中时 `_cand` 曾返回 `(_EMPTY,)`，谓词里访问字段即崩。"""
    ix = Table(CUSTOMERS).index(key="id")
    pred = lambda left, right: right["region"] == "N"   # noqa: E731
    assert ix.q(9, {}, pred, "name") is None
    assert ix.q(9, {}, pred, None) is UNDEF
    assert ix.qa(9, {}, pred, "name") == []
    assert ix.q(1, {}, pred, None) == {"id": 1, "name": "Ada", "region": "N"}


def test_lookup_composite_key_end_to_end():
    right = [{"a": 1, "b": "x", "v": "hit"}]
    expr = "$lookup([$.a, $.b], 'r', ['a', 'b'], 'v')"
    fields = {"v": _field(expr, "str")}
    _src, out = _run(fields, [{"a": 1, "b": "x"}, {"a": 1, "b": "z"}], {"r": right})
    assert [r["v"] for r in out] == ["hit", None]


def test_lookup_missing_table_names_what_is_injected():
    fields = {"v": _field("$lookup($.id, 'customers', 'id', 'name')", "str")}
    with pytest.raises(TypeError, match="未注入"):
        _run(fields, ORDERS, {"items": ITEMS})


# --------------------------------------------------------------------------- #
# 4. output.where / output.expand
# --------------------------------------------------------------------------- #

def test_where_filters_input_rows():
    fields = {"id": _field("$.id", "int64", False)}
    _src, out = _run(fields, ORDERS, {}, extra={"where": "$.amount < 100"})
    assert [r["id"] for r in out] == [11, 12]


def test_where_implements_semi_and_anti():
    semi = {"id": _field("$.id", "int64", False)}
    _src, out = _run(semi, ORDERS, {"customers": CUSTOMERS},
                     extra={"where": "$exists($lookup($.cust, 'customers', 'id'))"})
    assert [r["id"] for r in out] == [10, 11]

    _src, out = _run(semi, ORDERS, {"customers": CUSTOMERS},
                     extra={"where": "$not($exists($lookup($.cust, 'customers', 'id')))"})
    assert [r["id"] for r in out] == [12]


def test_expand_turns_an_array_column_into_rows():
    fields = {"id": _field("$.id", "int64", False),
              "sku": _field("$lookup($.id, 'items', 'order', 'sku', 'strict', 'all')", "list")}
    _src, out = _run(fields, ORDERS[:2], {"items": ITEMS}, extra={"expand": "sku"})
    assert out == [{"id": 10, "sku": "A"}, {"id": 10, "sku": "B"}, {"id": 11, "sku": "C"}]


def test_expand_drops_rows_with_empty_array():
    fields = {"id": _field("$.id", "int64", False),
              "tag": _field("$.tags", "list")}
    _src, out = _run(fields, [{"id": 1, "tags": ["a", "b"]}, {"id": 2, "tags": []}],
                     {}, extra={"expand": "tag"})
    assert [r["id"] for r in out] == [1, 1]


def test_expand_rejected_on_dict_shape():
    schema = _schema({"id": _field("$.id")}, shape="dict", extra={"expand": "id"})
    with pytest.raises(SchemaError, match="只在 output.shape"):
        dcg.validate(schema)


def test_where_rejects_right_row_reference():
    schema = _schema({"id": _field("$.id")}, extra={"where": "$r.x > 1"})
    with pytest.raises(SchemaError, match="\\$r"):
        dcg.generate(schema)


def test_row_count_follows_where():
    schema = _schema({"id": _field("$.id", "int64", False)},
                     extra={"where": "$.amount < 100"})
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<cnt>", "exec"), ns)
    got = ns["run"](ORDERS)
    assert len(got) == 2


# --------------------------------------------------------------------------- #
# 5. 复核回归（R2 审计的必须补 / 建议补）
# --------------------------------------------------------------------------- #

def test_loose_composite_key_matches_like_a_tuple():
    """回归：loose 复合键两侧的 `str()` 形状曾不一致（list vs tuple），永不命中。

    左键是数组 `[1,'x']`、索引里存的是元组 `(1,'x')`——必须先折元组再 `str()`。
    """
    rows = [{"a": 1, "b": "x", "v": "hit"}]
    ix = Table(rows).index(key=["a", "b"], mode="loose")
    assert ix.g([1, "x"]) == rows[0]
    assert ix.g((1, "x")) == rows[0]
    assert ix.gv([1, "x"], "v") == "hit"
    assert ix.g([1, "z"]) is None


def test_loose_join_end_to_end():
    """回归：端到端 loose 形态此前零覆盖（文档却声称已覆盖）。"""
    expr = "$lookup($.k, 'r', 'k', 'v', 'loose')"
    fields = {"v": _field(expr, "str")}
    _src, out = _run(fields, [{"k": "1"}, {"k": 1}, {"k": "9"}],
                     {"r": [{"k": 1, "v": "hit"}]})
    assert [r["v"] for r in out] == ["hit", "hit", None]


def test_specs_of_then_bind_matches_range_indexes():
    """回归：`specs_of` 保留用户策略，而 `RangeIndex` 恒记 strict/one → bind 失配。"""
    from dcg.codegen import specs_of
    schema = _schema({
        "id": _field("$.id", "int64", False),
        "rate": _field("$lookup($.amount, 'ladder', null, 'rate', 'strict',"
                       " 'first', '$r.lo <= $l.amount and"
                       " ($r.hi = null or $l.amount < $r.hi)')", "float64")})
    specs = specs_of(schema)
    assert specs[0]["kind"] == "range"

    # prepare 与 bind 两条路都要能对上同一份规格
    t1 = Table(LADDER)
    prepare(specs, {"ladder": t1}, release=False)
    assert t1.s0.pick(30)["rate"] == 0.05

    t2 = Table(LADDER)
    prepare(specs, {"ladder": t2}, release=False)
    bind(specs, {"ladder": t2})
    assert t2.s0.pick(120)["rate"] == 0.08


def test_bind_accepts_a_hand_built_range_index():
    """`find` 对区间索引按 `(key, hi)` 兜底：手工建的区间索引也能被 bind 认出。"""
    t = Table(LADDER)
    hand = t.range(key="lo", hi="hi")          # 恒记 strict/one
    bind([_spec(table="ladder", key="lo", kind="range", hi="hi",
                mode="strict", strategy="first")], {"ladder": t})
    assert t.s0 is hand
    assert t.s0.pv(30, "rate") == 0.05


def test_range_index_on_released_rows_raises_instead_of_empty():
    """回归：行数据释放后 `range()` 曾静默建成空索引，之后查询永远未命中。"""
    t = Table(LADDER, keep_rows=False)
    prepare([_spec(table="ladder", key="lo", kind="range", hi="hi")],
            {"ladder": t})
    assert t.rows is None
    with pytest.raises(RuntimeError, match="行数据已释放"):
        t.range(key="lo", hi="hi", mode="strict", strategy="all")


def test_null_right_key_with_all_strategy_uses_the_range_index():
    """回归：null 右键 + `all` + 合法区间谓词曾编译失败（被当成「等值连接缺右键」）。"""
    expr = ("$lookup($.amount, 'ladder', null, 'rate', 'strict', 'all',"
            " '$r.lo <= $l.amount and ($r.hi = null or $l.amount < $r.hi)')")
    fields = {"rate": _field(expr, "list")}
    src, out = _run(fields, [{"amount": 30}, {"amount": 500}, {"amount": -1}],
                    {"ladder": LADDER})
    assert [r["rate"] for r in out] == [[0.05], [0.08], []]
    assert ".pva(" in src


def test_expand_on_list_shape_keeps_positional_rows_aligned():
    """回归：`shape="list"` 多字段展开曾把整行当数组展开，元素错位。"""
    fields = {"id": _field("$.id", "int64", False),
              "tg": _field("$.tags", "list")}
    src, out = _run(fields, [{"id": 1, "tags": ["a", "b"]}, {"id": 2, "tags": ["c"]}],
                    {}, shape="list", extra={"expand": "tg"})
    assert out == [[1, "a"], [1, "b"], [2, "c"]]
    # 单字段扁平形态：行本身就是该列的值
    _src, flat = _run({"tg": _field("$.tags", "list")},
                      [{"tags": ["a", "b"]}], {}, shape="list", extra={"expand": "tg"})
    assert flat == [["a"], ["b"]]


def test_agg_rejects_unknown_kind():
    """回归：`agg` 的 `else` 兜底曾把任何未知种类当成 avg。"""
    ix = Table([{"k": 1, "v": 2}]).index(key="k", strategy="all")
    assert ix.agg("v", "sum")[1] == 2
    with pytest.raises(ValueError, match="聚合种类"):
        ix.agg("v", "zzz")


def test_prepare_names_missing_tables_instead_of_keyerror():
    """回归：`prepare` 缺表曾抛裸 `KeyError`（`_tm` 早已是友好 `TypeError`）。"""
    with pytest.raises(TypeError, match="未注入"):
        prepare([_spec()], {"items": Table(ITEMS)})


# --------------------------------------------------------------------------- #
# 6. 聚合改写（`$sum($lookup(...,'all'))` → `Index.aggs`）
# --------------------------------------------------------------------------- #

_AGG_PAIRS = (("sum", "_sm"), ("count", "_ct"), ("max", "_mx"),
              ("min", "_mn"), ("avg", "_av"))


def test_aggregate_over_lookup_uses_the_index_bucket():
    """1:N 聚合曾只发扫描形态；现在直接吃索引的按列预聚合。"""
    expr = "$sum($lookup($.id, 'items', 'order', 'qty', 'strict', 'all'))"
    src, out = _run({"oid": _field("$.id", "int64", False),
                     "total": _field(expr, "int64")},
                    [{"id": 10}, {"id": 11}, {"id": 12}], {"items": ITEMS})
    assert ".aggs(" in src
    assert [r["total"] for r in out] == [5, 1, 0]


def test_aggregate_matches_the_scan_form_value_for_value():
    """改写必须与扫描逐值等价，包括空桶、null、布尔与非数值元素。"""
    import dcg.runtime as rt
    rows = [{"k": 1, "v": 5}, {"k": 1, "v": 7}, {"k": 1, "v": None},
            {"k": 2, "v": True}, {"k": 3, "v": 2.5}, {"k": 3, "v": 4.5},
            {"k": 4, "v": "x"}]
    ix = Table(rows).index(key="k", strategy="all")
    def _call(fn, *a):
        try:
            return ("ok", fn(*a))
        except TypeError as exc:            # `$sum` 遇非数值元素两侧都得抛
            return ("err", str(exc))

    for k in (1, 2, 3, 4, 99):
        for kind, func in _AGG_PAIRS:
            scan = _call(getattr(rt, func), ix.ga(k, "v"))
            got = _call(ix.aggs, k, "v", kind)
            assert got == scan, (k, kind, got, scan)
    # 空桶口径固定：sum/count 得 0，max/min/avg 得 UNDEF
    assert ix.aggs(99, "v", "sum") == 0
    assert ix.aggs(99, "v", "count") == 0
    for kind in ("max", "min", "avg"):
        assert ix.aggs(99, "v", kind) is UNDEF


def test_aggregate_requires_the_all_strategy():
    """`strategy='one'` 的索引没有桶集合，预聚合必须拒绝。"""
    ix = Table(ITEMS).index(key="order", strategy="first")
    with pytest.raises(ValueError, match="strategy='all'"):
        ix.aggs(10, "qty", "sum")


def test_aggregate_is_skipped_when_not_value_equivalent():
    """非 all 策略、带谓词、整行形态都必须回落扫描，结果不变。"""
    # 非 all 策略（'one' 要求右键唯一，所以换成单行表）
    uniq = [{"order": 10, "qty": 2}, {"order": 11, "qty": 1}]
    src, out = _run({"total": _field(
        "$sum($lookup($.id, 'items', 'order', 'qty', 'strict', 'one'))", "any")},
        [{"id": 10}, {"id": 11}], {"items": uniq})
    assert ".aggs(" not in src
    assert [r["total"] for r in out] == [2, 1]
    # 带谓词
    src, out = _run({"total": _field(
        "$sum($lookup($.id, 'items', 'order', 'qty', 'strict', 'all',"
        " '$r.qty > 1'))", "any")},
        [{"id": 10}, {"id": 11}], {"items": ITEMS})
    assert ".aggs(" not in src
    assert [r["total"] for r in out] == [5, 0]
    # 整行形态（没给列名）
    src, out = _run({"c": _field(
        "$count($lookup($.id, 'items', 'order', null, 'strict', 'all'))", "any")},
        [{"id": 10}], {"items": ITEMS})
    assert ".aggs(" not in src
    assert [r["c"] for r in out] == [2]
