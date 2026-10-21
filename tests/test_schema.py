"""`dcg/schema.py` 的契约测试（DCG 接口实现细节文档.md §2 / §4.1 / §4.2 规则 ④）。

覆盖点（每条都对应契约里一句硬规则，不是"跑通即可"）：
1. 裸字符串**永远不是路径**（`$`/`#` 前缀闸门已废弃）；
2. 嵌套**必须**显式 `"fields"`（list-of-dict 特例已废弃）；
3. 所有非法形状报**带 JSON 路径的 `SchemaError`**，绝不裸 `KeyError`，路径绝不为空；
4. `validate()` 返回规范化深拷贝，且**幂等**（P0-2：`validate(validate(s))` 必过，
   已规范化的链 tuple 必须原样接受，否则 `build()` 会对自己的输出二次报错）；
5. `output.single=true` 在 `dict`/`index` 形状下**校验期**报错（§4.1）；
6. `type` 是**声明不是转换**（§4.2 规则 ④）—— 校验期不得注入任何转换。
"""

from __future__ import annotations

import copy

import pytest

from dcg.schema import (
    SHAPES,
    SchemaError,
    parse_value_path,
    validate,
    value_source,
)


def _schema(**field_spec):
    return {
        "name": "t",
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": field_spec},
    }


# --------------------------------------------------------------- 默认值与规范化
def test_validate_fills_all_defaults():
    norm = validate(_schema(a={"type": "int64", "value": "$.a"}))
    assert norm["input"] == {"source": ()}
    assert norm["output"] == {
        "shape": "list", "single": False, "filter": True,
        "post": None, "trace": False,
    }
    assert norm["out"]["type"] == "object"
    assert set(norm["out"]["fields"]) == {"a"}
    assert norm["out"]["fields"]["a"]["optional"] is False


def test_validate_returns_deep_copy():
    raw = _schema(a={"type": "int64", "value": "$.a"})
    norm = validate(raw)
    norm["out"]["fields"]["a"]["optional"] = True
    assert raw["out"]["fields"]["a"] == {"type": "int64", "value": "$.a"}
    assert copy.deepcopy(raw) is not norm


def test_filter_defaults_true_and_can_be_disabled():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"]["filter"] = False
    assert validate(s)["output"]["filter"] is False


# ------------------------------------------------------------------ 裸字符串
def test_bare_string_is_not_a_path():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(hourA={"type": "int64", "value": "hourA"}))
    assert ei.value.path == "out.fields.hourA.value"
    assert '"$"' in ei.value.msg


def test_raw_field_name_in_expr_is_not_a_path():
    """`expr` 里的裸标识符是**字段引用**，不是路径；此处只验证校验期不解释它。"""
    norm = validate(_schema(a={"type": "int64", "value": "$.a"},
                            b={"type": "any", "expr": "a + 1"}))
    assert norm["out"]["fields"]["b"]["expr"] == "a + 1"


# ------------------------------------------------------- parse_value_path 细节
@pytest.mark.parametrize("src,want", [
    ("$.a", ("a",)),
    ("$.5", (5,)),
    ("$.5.a.1", (5, "a", 1)),
    ("$.a[0]", ("a", 0)),
    ("$.a[0][1]", ("a", 0, 1)),
    ("$", ()),
    # `$` 之后点号与方括号**等价**：纯数字段一律解析成整数下标
    ("$.a.0.b", ("a", 0, "b")),
    ("$.a[0].b", ("a", 0, "b")),
    ("$.a.0.1", ("a", 0, 1)),
    ("$.0", (0,)),
    # `$` 后可直接跟方括号（不强制点号）
    ("$[0].a", (0, "a")),
    ("$[0][1]", (0, 1)),
    ("$[0]['k']", (0, "k")),
    # `$` 后不强制点号的标识符段
    ("$a.b", ("a", "b")),
    # 冗余点号与首尾空白容忍
    ("$.a..b", ("a", "b")),
    ("$.a.", ("a",)),
    ("  $.a.b  ", ("a", "b")),
])
def test_parse_value_path_forms(src, want):
    assert parse_value_path(src) == want


@pytest.mark.parametrize("src,want", [
    ('$.a["b c"]', ("a", "b c")),          # 带引号的段允许含空格
    ("$['x y']", ("x y",)),
    ("$['a'].b", ("a", "b")),
    ('$["a.b"]', ("a.b",)),                # 引号段里的点是键名的一部分
])
def test_parse_value_path_quoted_segments(src, want):
    assert parse_value_path(src) == want


@pytest.mark.parametrize("bad", ["a", "5.a", "", "$.a[-1]", "$.a[b]",
                                 "$.a[", "$.a]", "$.a[1x]", "$.a[1,2]"])
def test_parse_value_path_rejects(bad):
    with pytest.raises(SchemaError):
        parse_value_path(bad)


def test_parse_value_path_error_path_is_not_empty():
    with pytest.raises(SchemaError) as ei:
        parse_value_path("nope", "out.fields.x.value")
    assert ei.value.path == "out.fields.x.value"


def test_parse_value_path_accepts_normalized_chain():
    """P0-2 幂等的关键：已规范化的 tuple 必须原样接受。"""
    assert parse_value_path((5, "a", 1)) == (5, "a", 1)
    assert parse_value_path([5, "a", 1]) == (5, "a", 1)
    assert parse_value_path((5, "a", 1), "p") == (5, "a", 1)


@pytest.mark.parametrize("bad", [(1.5,), (True,), ("a", object())])
def test_parse_value_path_rejects_bad_chain_segments(bad):
    with pytest.raises(SchemaError):
        parse_value_path(bad, "out.fields.x.value")


def test_validate_is_idempotent():
    raw = _schema(a={"type": "int64", "value": "$.5.a.1"},
                  b={"type": "str", "const": "x"})
    once = validate(raw)
    twice = validate(once)
    assert twice == once
    assert once["out"]["fields"]["a"]["value"] == (5, "a", 1)


# ------------------------------------------------------------ value/const/expr
def test_exactly_one_of_value_const_expr_required():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "int64"}))
    assert ei.value.path == "out.fields.a"
    assert "一个都没给" in ei.value.msg

    with pytest.raises(SchemaError) as ei2:
        validate(_schema(a={"type": "int64", "value": "$.a", "const": 1}))
    assert ei2.value.path == "out.fields.a"
    assert "value" in ei2.value.msg and "const" in ei2.value.msg


def test_value_and_expr_paths_are_tagged():
    norm = validate(_schema(a={"type": "int64", "value": "$.a"},
                            b={"type": "int64", "expr": "$.b + 1"},
                            c={"type": "int64", "const": 7}))
    assert value_source(norm["out"]["fields"]["a"]) == ("value", ("a",))
    assert value_source(norm["out"]["fields"]["c"]) == ("const", 7)
    assert value_source(norm["out"]["fields"]["b"]) == ("expr", "$.b + 1")


# ------------------------------------------------------------------ type 声明
def test_type_is_declaration_not_coercion():
    """§4.2 规则 ④：校验期不得插入转换，`type` 原样保留。"""
    norm = validate(_schema(a={"type": "int64", "value": "$.a"}))
    spec = norm["out"]["fields"]["a"]
    assert spec == {"type": "int64", "optional": False, "value": ("a",)}
    assert "coerce" not in spec
    assert "cast" not in spec


def test_unknown_type_rejected_with_path():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "i64", "value": "$.a"}))
    assert ei.value.path == "out.fields.a.type"
    assert "i64" in ei.value.msg


def test_missing_type_rejected():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"value": "$.a"}))
    assert ei.value.path == "out.fields.a"
    assert "type" in ei.value.msg


# ---------------------------------------------------------------- 嵌套 fields
def test_nested_fields_require_explicit_fields_key():
    norm = validate(_schema(
        n={"type": "object", "fields": {"x": {"type": "int64", "value": "$.x"}}}))
    inner = norm["out"]["fields"]["n"]["fields"]
    assert inner["x"]["value"] == ("x",)
    assert value_source(norm["out"]["fields"]["n"])[0] == "fields"


def test_fields_only_allowed_on_object_or_list_type():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n={"type": "int64",
                            "fields": {"x": {"type": "int64", "value": "$.x"}}}))
    assert ei.value.path == "out.fields.n.fields"
    assert '只有 type 为 "object" 或 "list" 才能用 "fields"' in ei.value.msg


def test_fields_and_value_are_mutually_exclusive():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n={"type": "object", "value": "$.n",
                            "fields": {"x": {"type": "int64", "value": "$.x"}}}))
    assert ei.value.path == "out.fields.n"
    assert "互斥" in ei.value.msg


@pytest.mark.parametrize("spec", [
    pytest.param({"type": "list", "items": {"type": "int64", "value": "$.x"}},
                 id="list"),
    pytest.param({"type": "int64", "items": {"type": "int64", "value": "$.x"}},
                 id="int64"),
    pytest.param({"type": "object",
                  "fields": {"a": {"type": "int64", "value": "$.x"}},
                  "items": {"type": "int64", "value": "$.x"}}, id="object+fields"),
    pytest.param({"type": "list", "value": "$.x",
                  "items": {"type": "int64", "value": "$.x"}}, id="value"),
])
def test_items_is_removed(spec):
    """`items` 已从 schema 层移除：任何类型上都在最前置校验处报「已移除」。

    错误 path 精确指到 `items` 键本身（不是 `.items.type`），旧的
    「"fields" 与 "items" 互斥」提示已下线。
    """
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n=spec))
    assert ei.value.path == "out.fields.n.items"
    assert "已移除" in str(ei.value)
    assert "互斥" not in str(ei.value)


def test_nested_error_path_points_inside():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n={"type": "object",
                            "fields": {"x": {"type": "bad", "value": "$.x"}}}))
    assert ei.value.path == "out.fields.n.fields.x.type"


# --------------------------------------------------------------- output 形状
@pytest.mark.parametrize("shape", ["dict", "index"])
def test_single_meaningless_for_dict_and_index(shape):
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": shape, "single": True}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output.single"
    assert "无意义" in ei.value.msg


def test_single_allowed_for_list_shape():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", "single": True}
    assert validate(s)["output"]["single"] is True


def test_rows_shape_and_single_are_accepted():
    """`rows` = 逐行 dict；single 取首个行 dict（不是整列）。"""
    assert "rows" in SHAPES
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "rows", "single": False}
    assert validate(s)["output"]["shape"] == "rows"
    s["output"] = {"shape": "rows", "single": True}
    assert validate(s)["output"]["single"] is True


def test_rows_shape_is_not_a_top_level_key():
    """`rows` 只是 shape 的一个取值；它不是 schema 顶层的键。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["rows"] = 1
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert "rows" in ei.value.msg


def test_unknown_shape_rejected():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "table"}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output.shape"


def test_single_and_filter_must_be_bool():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", "single": 1}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output.single"


def test_post_must_be_dotted_path():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", "post": "nofunc"}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output.post"
    s["output"]["post"] = "pkg.mod.fn"
    assert validate(s)["output"]["post"] == "pkg.mod.fn"


# ------------------------------------------------------------------ 输入来源
def test_input_source_defaults_to_the_whole_input():
    assert validate(_schema(a={"type": "int64", "value": "$.a"}))["input"] == \
        {"source": ()}


def test_input_source_must_be_a_path():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["input"] = {"source": "table"}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "input.source"


def test_input_source_is_a_path_into_the_input():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["input"] = {"source": "$.a.b"}
    assert validate(s)["input"] == {"source": ("a", "b")}


def test_input_source_dollar_means_the_whole_input():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["input"] = {"source": "$"}
    assert validate(s)["input"] == {"source": ()}


def test_input_source_rejects_bare_words():
    """`"index"` 这种裸字符串不再是合法 source（v2 里裸字符串不是路径）。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["input"] = {"source": "index"}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "input.source"


# --------------------------------------------------------------- 顶层与字段名
def test_top_level_unknown_key_rejected():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["extra"] = 1
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == ""
    assert "extra" in ei.value.msg


def test_legacy_describe_shape_gets_migration_hint():
    with pytest.raises(SchemaError) as ei:
        validate({"describe": {}, "datas": "$.index",
                  "output": {}, "out": {"fields": {}}})
    assert "migrate" in ei.value.msg


def test_legacy_field_shape_gets_migration_hint():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "int64", "value": "$.a", "alias": "x"}))
    assert ei.value.path == "out.fields.a"
    assert "migrate" in ei.value.msg


def test_missing_name_and_output_and_out():
    s = _schema(a={"type": "int64", "value": "$.a"})
    for key in ("name", "output", "out"):
        broken = dict(s)
        del broken[key]
        with pytest.raises(SchemaError) as ei:
            validate(broken)
        assert ei.value.path  # 绝不为空
        assert key in str(ei.value)


def test_out_must_be_object_with_fields():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["out"] = {"list": []}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert "migrate" in ei.value.msg


def test_field_name_must_be_identifier():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(**{"a-b": {"type": "int64", "value": "$.a"}}))
    assert ei.value.path == "out.fields"
    assert "标识符" in ei.value.msg


@pytest.mark.parametrize("reserved", ["rows", "row", "r", "out", "ap", "_"])
def test_reserved_field_names_rejected(reserved):
    with pytest.raises(SchemaError):
        validate(_schema(**{reserved: {"type": "int64", "value": "$.a"}}))


def test_field_name_may_not_shadow_helper_prefix():
    """字段名进产物是**局部名**；与 helper 前缀冲突会在运行期静默覆盖，必须挡在门外。

    `validate()` 只管形状（它不读 runtime），真正挡住的是 `generate()`（`_RESERVED`）。
    """
    import dcg

    for reserved in ("_ad", "_S", "_U", "_o", "_in", "R", "run"):
        with pytest.raises(dcg.SchemaError):
            dcg.generate(_schema(**{reserved: {"type": "int64", "value": "$.a"}}))


def test_schema_must_be_object():
    with pytest.raises(SchemaError) as ei:
        validate(["not", "a", "dict"])
    assert ei.value.path == ""


# ---------------------------------------------- 位置行（type:"list" + fields）
def _positional_schema(**field_spec):
    return _schema(doc={"type": "list", "fields": field_spec})


def test_list_fields_is_normalized_positional_row():
    norm = validate(_positional_schema(a={"type": "int64", "value": "$.0"}))
    doc = norm["out"]["fields"]["doc"]
    assert doc["type"] == "list"
    assert "row" not in doc
    assert validate(norm) == norm


def test_object_fields_is_named_object_without_row():
    norm = validate(_schema(doc={"type": "object",
                                 "fields": {"a": {"type": "int64", "value": "$.0"}}}))
    doc = norm["out"]["fields"]["doc"]
    assert doc["type"] == "object"
    assert "row" not in doc


def test_legacy_row_is_rejected_not_normalized():
    """`row` 已彻底删除：**不再做别名归一**，任何取值/组合都必须在 build 期报错。

    用户指令 (2026-10-21): 「row 到底是否全部删除？不需要管历史兼容」。
    上一版把 `row:"list"` 归一成 `type:"list"`、`row:"object"` 归一成
    `type:"object"`；保留别名等于让两份语义相同的 schema 在不同版本间静默产出
    不同字节，且规范形里永远留着一个不出现的键。现在与 `items` 同待遇：显式拒绝
    + 规范写法指引。
    """
    for spec in ({"type": "object", "row": "list", "fields": {
                      "a": {"type": "int64", "value": "$.0"}}},
                 {"type": "object", "row": "object", "fields": {
                      "a": {"type": "int64", "value": "$.0"}}},
                 {"type": "list", "row": "object", "fields": {
                      "a": {"type": "int64", "value": "$.0"}}},
                 {"type": "object", "row": "tuple", "fields": {
                      "a": {"type": "int64", "value": "$.0"}}}):
        with pytest.raises(SchemaError) as ei:
            validate(_schema(doc=spec))
        assert ei.value.path == "out.fields.doc.row"
        assert "已移除" in ei.value.msg
        assert "type" in ei.value.msg and "fields" in ei.value.msg


def test_legacy_row_alias_rejected_without_fields():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "int64", "value": "$.0", "row": "list"}))
    msg = str(ei.value)
    assert "已移除" in msg and "位置行" in msg


def test_items_no_longer_conflicts_with_fields():
    """`items` 在 schema 层被前置拒绝，原「"fields" 与 "items" 互斥」提示已下线。"""
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "list",
                              "fields": {"a": {"type": "int64", "value": "$.0"}},
                              "items": {"type": "int64", "value": "$.0"}}))
    assert ei.value.path == "out.fields.doc.items"
    assert "已移除" in str(ei.value)
    assert "互斥" not in str(ei.value)


def test_fields_requires_object_or_list_type():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "int64",
                              "fields": {"a": {"type": "int64", "value": "$.0"}}}))
    assert '只有 type 为 "object" 或 "list" 才能用 "fields"' in str(ei.value)


def test_generate_legacy_row_is_rejected_before_any_codegen():
    """删除别名后 `generate()` 也必须拒收旧写法——**不能有半条兼容路径**。

    上一版这条断言「旧 `row:"list"` 与新 `type:"list"` 的 generate() 输出逐字节
    相同」；现在语义变为：旧写法在 validate 阶段就抛 SchemaError，连产物都不产生。
    """
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "object", "row": "list", "fields": {
            "hour": {"type": "str", "value": "$.0"},
            "hourA": {"type": "int64", "value": "$.1"},
        }}))
    assert ei.value.path == "out.fields.doc.row"

    # 对照：规范写法照常生成，且行内字段全可证非 undefined → 发列表字面量
    import dcg

    modern = validate(_schema(doc={"type": "list", "fields": {
        "hour": {"type": "str", "value": "$.0"},
        "hourA": {"type": "int64", "value": "$.1"},
    }}))
    src_modern = dcg.generate(modern)
    assert "_rl([" not in src_modern and "[R[0],R[1]]" in src_modern


def test_generate_legacy_row_falls_back_to_rl_when_field_may_be_undefined():
    """回退路径覆盖：行内有可 undefined 的字段 → 规范写法仍发 `_rl([...])`。

    旧写法（`row`）已删除，故这条不再做「新旧逐字节相同」对照，只钉住回退路径本身。
    """
    import dcg

    modern = validate(_schema(doc={"type": "list", "fields": {
        "hour": {"type": "any", "value": "$.0"},
        "hourA": {"type": "int64", "value": "$.1"},
    }}))
    src_modern = dcg.generate(modern)
    assert "_rl([" in src_modern
