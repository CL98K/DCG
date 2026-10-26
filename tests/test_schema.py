"""`dcg/schema.py` 的契约测试（DCG 接口实现细节文档.md §2 / §4.1 / §4.2 规则 ④）。

覆盖点（每条都对应契约里一句硬规则，不是"跑通即可"）：
1. 裸字符串**永远不是路径**（`$`/`#` 前缀闸门已废弃）；
2. 嵌套**必须**显式 `"fields"`（list-of-dict 特例已废弃）；
3. 所有非法形状报**带 JSON 路径的 `SchemaError`**，绝不裸 `KeyError`，路径绝不为空；
4. `validate()` 返回规范化深拷贝；**规范形不得回喂**（第九轮裁决 B：`validate()`
   入口会拒收 `input.source`/`value` 段 tuple、钩子二元组这类内部形态，报错并
   提示「直接传原始 schema」；`generate()`/`build()` 会自行校验，无需先 validate）；
5. `output` 只有 `shape`/`before`/`after`/`trace` 四键，其余一律是未知键；
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
        "shape": "list", "before": None,
        "after": None, "trace": False,
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


def test_filter_key_is_rejected():
    """`filter` 不存在（无论取值是 false 还是表达式），只有 `before`。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"]["filter"] = False
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output.filter"
    assert "不存在" in ei.value.msg


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
    """`parse_value_path` 内部接受已规范化 tuple（供生成器/树内部复用）。

    注意：这不等于「`validate()` 幂等」——`validate()` 入口会先拒收整个规范形
    schema（`test_normalized_schema_is_rejected`）。
    """
    assert parse_value_path((5, "a", 1)) == (5, "a", 1)
    assert parse_value_path([5, "a", 1]) == (5, "a", 1)
    assert parse_value_path((5, "a", 1), "p") == (5, "a", 1)


@pytest.mark.parametrize("bad", [(1.5,), (True,), ("a", object())])
def test_parse_value_path_rejects_bad_chain_segments(bad):
    with pytest.raises(SchemaError):
        parse_value_path(bad, "out.fields.x.value")


def test_normalized_schema_is_rejected():
    """第九轮裁决 B：把 `validate()` 的返回值回喂 `validate()`/`generate()` 一律拒绝。

    规范形里的段 tuple / 钩子二元组不是可信输入；提示必须指向「直接传原始 schema」。
    """
    raw = _schema(a={"type": "int64", "value": "$.5.a.1"},
                  b={"type": "str", "const": "x"})
    import dcg

    once = validate(raw)
    assert once["out"]["fields"]["a"]["value"] == (5, "a", 1)
    with pytest.raises(SchemaError) as ei:
        validate(once)
    assert ei.value.path == "out.fields.a.value"
    assert "规范化内部形态" in ei.value.msg and "原始 schema" in ei.value.msg
    # 生成器入口同样拒收（它们内部会自行 validate 一次，不该再吃规范形）
    with pytest.raises(SchemaError):
        dcg.generate(once)
    # 钩子元组也拒收（这是以前最容易撞到的「得到 tuple」来源）
    hook_raw = {
        "name": "h",
        "output": {"shape": "list", "before": "= $.rows"},
        "out": {"fields": {"x": {"type": "int64", "const": 1}}},
    }
    hook_norm = validate(hook_raw)
    with pytest.raises(SchemaError) as ei2:
        validate(hook_norm)
    assert ei2.value.path == "output.before"
    assert "规范化内部形态" in ei2.value.msg
    # 原始 schema 路径不受影响
    assert validate(hook_raw)["output"]["before"] == ("expr", "$.rows")
    assert "def run" in dcg.generate(hook_raw)


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
def test_items_is_rejected_as_unknown_key(spec):
    """`items` 只是一个不存在的键：数组用 value/expr，嵌套用 fields。

    path 停在字段本身（`out.fields.n`），不再有「已移除」专属提示——
    项目未发布，v2 不为任何旧写法留后门（用户 m01313）。
    """
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n=spec))
    assert ei.value.path == "out.fields.n"
    assert "未知键: items" in str(ei.value)
    assert "互斥" not in str(ei.value)


def test_nested_error_path_points_inside():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(n={"type": "object",
                            "fields": {"x": {"type": "bad", "value": "$.x"}}}))
    assert ei.value.path == "out.fields.n.fields.x.type"


# --------------------------------------------------------------- output 形状
def test_rows_shape_is_accepted():
    """`rows` = 逐行 dict；输出形状完全由 `shape` 决定。"""
    assert "rows" in SHAPES
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "rows"}
    norm = validate(s)["output"]
    assert norm["shape"] == "rows"
    assert set(norm) == {"shape", "before", "after", "trace"}


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


# ------------------------------------------------- before / after 数据级钩子
def test_before_defaults_to_none():
    """不写 `before` 就是不改写输入数据。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    assert validate(s)["output"]["before"] is None


def test_after_defaults_to_none():
    """不写 `after` 就是原样返回结果。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    assert validate(s)["output"]["after"] is None


@pytest.mark.parametrize("key", ["filter", "post"])
def test_row_hooks_are_rejected(key):
    """逐行钩子 `filter`/`post` 不存在，只有数据级 `before`/`after`（用户 m00292）。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", key: "$"}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output." + key
    assert "不存在" in ei.value.msg
    assert ("before" if key == "filter" else "after") in ei.value.msg


@pytest.mark.parametrize("key", ["before", "after"])
@pytest.mark.parametrize("raw", [True, False])
def test_hooks_no_longer_accept_bool(key, raw):
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", key: raw}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output." + key
    assert "不接受布尔值" in ei.value.msg


@pytest.mark.parametrize("key", ["before", "after"])
def test_hook_expr_with_and_without_prefix(key):
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", key: "$"}
    assert validate(s)["output"][key] == ("expr", "$")
    s["output"] = {"shape": "list", key: "= $"}
    assert validate(s)["output"][key] == ("expr", "$")


def test_hooks_accept_external_function():
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", "before": "@pkg.mod.prep",
                   "after": "@pkg.mod.keep"}
    norm = validate(s)["output"]
    assert norm["before"] == ("func", "pkg.mod.prep")
    assert norm["after"] == ("func", "pkg.mod.keep")


def test_hook_keeps_the_raw_expression_text():
    """表达式原样保存（去掉 `=` 标记），由 codegen 交给 jx 编译。"""
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", "after": "= $map($, function($r){{ $r }})"}
    assert validate(s)["output"]["after"] == \
        ("expr", "$map($, function($r){{ $r }})")


@pytest.mark.parametrize("key", ["before", "after"])
@pytest.mark.parametrize("raw", ["", "   ", "= ", 1, 1.5, ["a"], {"k": 1}])
def test_hook_rejects_empty_and_non_string(key, raw):
    s = _schema(a={"type": "int64", "value": "$.a"})
    s["output"] = {"shape": "list", key: raw}
    with pytest.raises(SchemaError) as ei:
        validate(s)
    assert ei.value.path == "output." + key


@pytest.mark.parametrize("key", ["before", "after"])
def test_hook_rejects_bad_qualname(key):
    """`@` 之后必须是 `pkg.mod.fn` 形式（至少一个点）。"""
    for bad in ("@nofunc", "@a.", "@.b", "@a..b"):
        s = _schema(a={"type": "int64", "value": "$.a"})
        s["output"] = {"shape": "list", key: bad}
        with pytest.raises(SchemaError) as ei:
            validate(s)
        assert ei.value.path == "output." + key


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


def test_unknown_top_level_keys_rejected():
    """未知顶层键一律拒绝；项目尚未发布，v2 不承担任何旧格式兼容。"""
    with pytest.raises(SchemaError) as ei:
        validate({"describe": {}, "datas": "$.index",
                  "output": {}, "out": {"fields": {}}})
    assert ei.value.path == ""
    assert "describe" in ei.value.msg and "datas" in ei.value.msg


def test_unknown_field_keys_rejected():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "int64", "value": "$.a", "alias": "x"}))
    assert ei.value.path == "out.fields.a"
    assert "alias" in ei.value.msg


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
    assert ei.value.path == "out.fields"
    assert "缺少必填键" in ei.value.msg


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
    # 规范形只验结构；回喂 validate 是契约错误（见 test_normalized_schema_is_rejected）
    assert isinstance(doc["fields"]["a"]["value"], tuple)


def test_object_fields_is_named_object_without_row():
    norm = validate(_schema(doc={"type": "object",
                                 "fields": {"a": {"type": "int64", "value": "$.0"}}}))
    doc = norm["out"]["fields"]["doc"]
    assert doc["type"] == "object"
    assert "row" not in doc


def test_row_key_is_rejected_as_unknown():
    """`row` 只是一个不存在的键：项目未发布，v2 不为任何旧写法留后门。

    用户指令 (2026-10-22, m01313)：旧格式相关内容全部删掉，既没正式发布就不需要
    兼容。所以这里不再区分「已移除」与「未知键」——一律是普通未知键报错。
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
        assert ei.value.path == "out.fields.doc"
        assert "未知键: row" in ei.value.msg


def test_row_key_rejected_without_fields():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(a={"type": "int64", "value": "$.0", "row": "list"}))
    assert ei.value.path == "out.fields.a"
    assert "未知键: row" in ei.value.msg


def test_items_key_is_rejected_as_unknown():
    """`items` 同样只是一个不存在的键（2026-10-22 起不再给专属提示）。"""
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "list",
                              "fields": {"a": {"type": "int64", "value": "$.0"}},
                              "items": {"type": "int64", "value": "$.0"}}))
    assert ei.value.path == "out.fields.doc"
    assert "未知键: items" in ei.value.msg


def test_fields_requires_object_or_list_type():
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "int64",
                              "fields": {"a": {"type": "int64", "value": "$.0"}}}))
    assert '只有 type 为 "object" 或 "list" 才能用 "fields"' in str(ei.value)


def test_generate_rejects_unknown_key_before_any_codegen():
    """`generate()` 也走同一套校验：不合规的键在 validate 阶段就抛错，产物不产生。"""
    with pytest.raises(SchemaError) as ei:
        validate(_schema(doc={"type": "object", "row": "list", "fields": {
            "hour": {"type": "str", "value": "$.0"},
            "hourA": {"type": "int64", "value": "$.1"},
        }}))
    assert ei.value.path == "out.fields.doc"
    assert "未知键: row" in ei.value.msg

    # 对照：规范写法照常生成，且行内字段全可证非 undefined → 发列表字面量
    import dcg

    good = {
        "name": "t",
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"doc": {
            "type": "list",
            "fields": {
                "hour": {"type": "str", "value": "$.0"},
                "hourA": {"type": "int64", "value": "$.1"},
            },
        }}},
    }
    src_good = dcg.generate(good)
    assert "_rl([" not in src_good and "[R[0],R[1]]" in src_good


def test_positional_row_falls_back_to_rl_when_field_may_be_undefined():
    """回退路径覆盖：行内有可 undefined 的字段 → 位置行仍发 `_rl([...])`。"""
    import dcg

    norm = validate(_schema(doc={"type": "list", "fields": {
        "hour": {"type": "any", "value": "$.0"},
        "hourA": {"type": "int64", "value": "$.1"},
    }}))
    assert norm["out"]["fields"]["doc"]["type"] == "list"
    # 生成器只吃原始 schema（规范形回喂是契约错误，见 test_normalized_schema_is_rejected）
    raw = _schema(doc={"type": "list", "fields": {
        "hour": {"type": "any", "value": "$.0"},
        "hourA": {"type": "int64", "value": "$.1"},
    }})
    src = dcg.generate(raw)
    assert "_rl([" in src
