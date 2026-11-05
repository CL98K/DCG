# -*- coding: utf-8 -*-
"""tools/dcg_bridge.py 的单元测试（pytest，零第三方依赖）。

覆盖 API.md 冻结契约：infer 的 rows/wrapper/single、嵌套、跨行类型合并、
空数组、非法 JSON、特殊键告警；run 的表达式字段、wrapper 解包、截断、
各失败 stage、NaN 降级与 manifest。测试不联网、不开端口。
"""
import json
import re
import sys
from pathlib import Path

# 让 `import dcg_bridge` 与 `import dcg` 都可用：tools/ 与仓库根各进 sys.path。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _REPO_ROOT / "tools"
for _path in (str(_REPO_ROOT), str(_TOOLS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import dcg_bridge as bridge  # noqa: E402

VALID_TYPES = ("int64", "int32", "float64", "str", "bool", "list", "object", "any")


# --------------------------------------------------------------------------- 工具
def infer(text):
    return bridge.infer(text)


def run(schema, text):
    return bridge.run(schema, text)


def fields_by_path(result):
    return {field["path"]: field for field in result["fields"]}


def schema_one(field_type="int64", path="$.a", expr=None, name="t"):
    field = {"type": field_type, "expr": expr} if expr is not None else \
        {"type": field_type, "value": path}
    return {
        "name": name,
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"a": field}},
    }


# --------------------------------------------------------------------------- infer
def test_infer_rows_mode_and_fields():
    text = json.dumps([
        {"sku": "A-1", "qty": 2},
        {"sku": "B-2", "qty": 1},
        {"sku": "C-3", "qty": 7},
    ])
    result = infer(text)

    assert result["ok"] is True
    assert result["mode"] == "rows"
    assert result["source"] is None
    assert result["row_count"] == 3
    assert result["schema_name"] == "rows"
    assert result["warnings"] == []

    assert [f["path"] for f in result["fields"]] == ["$.sku", "$.qty"]
    assert [f["suggest"] for f in result["fields"]] == ["sku", "qty"]
    assert [f["type"] for f in result["fields"]] == ["str", "int64"]
    assert result["fields"][0]["samples"] == ["A-1", "B-2", "C-3"]
    assert result["fields"][0]["depth"] == 1

    tree = result["tree"]
    assert tree["type"] == "rows"
    assert tree["label"] == "rows[3]"
    assert tree["path"] == ""
    assert tree["count"] == 3
    assert tree["sample"] is None
    assert [node["label"] for node in tree["children"]] == ["sku", "qty"]
    assert tree["children"][1]["path"] == "$.qty"
    assert tree["children"][1]["type"] == "int64"


def test_infer_wrapper_mode():
    text = json.dumps({"orders": [{"sku": "A-1", "qty": 2}, {"sku": "B-2", "qty": 1}]})
    result = infer(text)

    assert result["ok"] is True
    assert result["mode"] == "wrapper"
    assert result["source"] == "$.orders"
    assert result["row_count"] == 2
    assert result["schema_name"] == "orders"
    assert fields_by_path(result)["$.sku"]["suggest"] == "sku"


def test_infer_wrapper_ignores_other_top_level_keys():
    text = json.dumps({"meta": {"version": 1}, "rows": [{"a": 1}]})
    result = infer(text)

    assert result["mode"] == "wrapper"
    assert result["source"] == "$.rows"
    warnings = " ".join(result["warnings"])
    assert "meta" in warnings


def test_infer_single_mode():
    text = json.dumps({"id": 1, "name": "x", "flag": True})
    result = infer(text)

    assert result["ok"] is True
    assert result["mode"] == "single"
    assert result["source"] is None
    assert result["row_count"] == 1
    assert [f["path"] for f in result["fields"]] == ["$.id", "$.name", "$.flag"]
    assert [f["type"] for f in result["fields"]] == ["int64", "str", "bool"]
    assert result["tree"]["type"] == "object"
    assert result["tree"]["path"] == ""


def test_infer_scalar_rows_use_whole_row_field():
    result = infer(json.dumps([1, 2, 3]))

    assert result["mode"] == "rows"
    assert result["row_count"] == 3
    assert [f["path"] for f in result["fields"]] == ["$"]
    assert result["fields"][0]["type"] == "int64"
    assert result["fields"][0]["suggest"] == "value"
    assert result["fields"][0]["samples"] == [1, 2, 3]


def test_infer_nested_object_and_scalar_array():
    text = json.dumps([{
        "a": {"b": 1},
        "tags": ["x", "y", "z", "w"],
        "items": [{"id": 1, "v": 2}, {"id": 3}],
    }])
    result = infer(text)

    paths = [f["path"] for f in result["fields"]]
    assert paths == ["$.a.b", "$.tags", "$.items.0.id", "$.items.0.v", "$.items.1.id"]
    assert fields_by_path(result)["$.tags"]["type"] == "list"
    assert fields_by_path(result)["$.tags"]["samples"] == ["x", "y", "z"]
    assert fields_by_path(result)["$.items.0.id"]["suggest"] == "id"
    assert fields_by_path(result)["$.items.1.id"]["suggest"] == "id_2"

    warnings = " ".join(result["warnings"])
    assert "对象字段 a" in warnings

    tree = result["tree"]
    node_a = tree["children"][0]
    assert node_a["label"] == "a"
    assert node_a["type"] == "object"
    assert node_a["children"][0]["path"] == "$.a.b"


def test_infer_array_of_scalars_is_a_list_field():
    result = infer(json.dumps([{"tags": ["a", "b"]}, {"tags": []}]))

    field = fields_by_path(result)["$.tags"]
    assert field["type"] == "list"
    assert field["samples"] == ["a", "b"]
    tree_tag = next(n for n in result["tree"]["children"] if n["label"] == "tags")
    assert tree_tag["type"] == "list"
    assert tree_tag["count"] == 2
    assert tree_tag["children"] is None


def test_infer_merges_mixed_types():
    float_result = infer(json.dumps([{"v": 1}, {"v": 2.5}]))
    assert fields_by_path(float_result)["$.v"]["type"] == "float64"

    any_result = infer(json.dumps([{"v": 1}, {"v": "x"}]))
    assert fields_by_path(any_result)["$.v"]["type"] == "any"

    none_result = infer(json.dumps([{"v": None}, {"v": 3}]))
    assert fields_by_path(none_result)["$.v"]["type"] == "any"

    bool_result = infer(json.dumps([{"v": True}, {"v": False}]))
    assert fields_by_path(bool_result)["$.v"]["type"] == "bool"

    bool_int_result = infer(json.dumps([{"v": True}, {"v": 1}]))
    assert fields_by_path(bool_int_result)["$.v"]["type"] == "any"


def test_infer_missing_key_does_not_break_merge():
    result = infer(json.dumps([{"v": 1}, {"w": "s"}, {"v": 3}]))
    field = fields_by_path(result)["$.v"]
    assert field["type"] == "int64"
    assert field["samples"] == [1, 3]


def test_infer_empty_array():
    result = infer("[]")

    assert result["ok"] is True
    assert result["mode"] == "rows"
    assert result["row_count"] == 0
    assert result["fields"] == []
    assert result["warnings"] == ["样本为空数组，无法推断字段"]
    assert result["tree"]["type"] == "rows"
    assert result["tree"]["count"] == 0
    assert result["tree"]["children"] == []


def test_infer_empty_object_single_mode():
    result = infer("{}")

    assert result["mode"] == "single"
    assert result["row_count"] == 1
    assert result["fields"] == []
    assert "空对象" in " ".join(result["warnings"])


def test_infer_invalid_json_reports_line_and_column():
    result = infer('{\n  "a": 1,\n}')

    assert result["ok"] is False
    assert result["stage"] == "json"
    error = result["error"]
    # 不同 Python 版本对 trailing comma 的行列号不同，只断言契约格式
    assert re.match(r"^第 \d+ 行第 \d+ 列: \S", error)
    assert "message" not in result


def test_infer_special_keys_warned_and_skipped():
    text = json.dumps([{"a.b": 1, "ok": 2, "1bad": 3, "中文": 4}])
    result = infer(text)

    assert [f["path"] for f in result["fields"]] == ["$.ok"]
    warnings = " ".join(result["warnings"])
    assert "a.b" in warnings
    assert "不是合法标识符" in warnings


def test_infer_samples_capped_at_three():
    result = infer(json.dumps([{"v": i} for i in range(10)]))
    assert fields_by_path(result)["$.v"]["samples"] == [0, 1, 2]


def test_infer_tree_node_shape_and_types():
    result = infer(json.dumps([{"a": 1, "b": 2.5, "c": "s", "d": True, "e": None,
                                "f": [1, 2], "g": {"h": 3}}]))
    for node in result["tree"]["children"]:
        assert set(node) == {"label", "path", "type", "count", "sample", "children"}
        assert node["type"] in VALID_TYPES or node["type"] in ("rows",)
    assert fields_by_path(result)["$.e"]["type"] == "any"


def test_infer_rejects_python_nan_constant():
    # Python 的 json 扩展常量不是合法 JSON：按 json stage 报错（带行列号）
    result = infer('[{"v": 1}, {"v": NaN}]')
    assert result["ok"] is False
    assert result["stage"] == "json"
    assert result["error"].startswith("第 1 行第") and "NaN" in result["error"]


def test_infer_response_is_json_encodable():
    result = infer(json.dumps([{"v": 1}, {"v": 2.5, "s": "中文"}]))
    json.dumps(result, ensure_ascii=False, allow_nan=False)  # 不抛即通过
    assert result["ok"] is True


# --------------------------------------------------------------------------- run
def test_run_basic_and_manifest():
    schema = {
        "name": "calc",
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            "qty": {"type": "int64", "value": "$.qty"},
            "double": {"type": "int64", "expr": "`qty * 2`"},
        }},
    }
    result = run(schema, json.dumps([{"qty": 2}, {"qty": 3}]))

    assert result["ok"] is True
    assert result["out"] == [[2, 4], [3, 6]]
    assert result["out_count"] == 2
    assert result["truncated"] is False
    assert result["warnings"] == []
    assert result["code"].startswith("def run(")
    assert isinstance(result["elapsed_ms"], float)
    assert result["elapsed_ms"] >= 0

    manifest = result["manifest"]
    assert manifest["name"] == "calc"
    assert manifest["lines"] == len(result["code"].splitlines())
    assert manifest["chars"] == len(result["code"])
    assert len(manifest["schema_sha1"]) == 40
    assert manifest["generator_version"].endswith(".g1")


def test_run_wrapper_schema_unpacks_source():
    schema = {
        "name": "orders",
        "input": {"source": "$.orders"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            "sku": {"type": "str", "value": "$.sku"},
            "qty": {"type": "int64", "value": "$.qty"},
        }},
    }
    text = json.dumps({"orders": [{"sku": "A-1", "qty": 2}, {"sku": "B-2", "qty": 1}]})
    result = run(schema, text)

    assert result["ok"] is True
    assert result["out"] == [["A-1", 2], ["B-2", 1]]
    assert result["out_count"] == 2


def test_run_single_mode_wraps_row():
    schema = schema_one("int64", "$.id", name="single")
    result = run(schema, json.dumps({"id": 7}))

    assert result["ok"] is True
    assert result["out"] == [[7]]
    assert result["out_count"] == 1


def test_run_truncates_after_200_rows():
    schema = schema_one("int64", "$.i", name="big")
    text = json.dumps([{"i": i} for i in range(250)])
    result = run(schema, text)

    assert result["ok"] is True
    assert result["out_count"] == 250
    assert result["truncated"] is True
    assert len(result["out"]) == 200
    assert result["out"][0] == [0]
    assert result["out"][-1] == [199]


def test_run_schema_validation_failure():
    schema = {
        "name": "bad",
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {"a": {"type": "number", "value": "$.a"}}},
    }
    result = run(schema, '[{"a": 1}]')

    assert result["ok"] is False
    assert result["stage"] == "schema"
    assert "必须是" in result["error"] and "number" in result["error"]
    assert result["code"] is None


def test_run_json_failure_precedes_schema_failure():
    result = run({"name": "bad"}, '{"a":')

    assert result["ok"] is False
    assert result["stage"] == "json"
    assert result["error"].startswith("第 1 行第")
    assert result["code"] is None


def test_run_expression_failure_keeps_code():
    schema = schema_one("int64", expr="`1 / 0`", name="boom")
    result = run(schema, "[{}]")

    assert result["ok"] is False
    assert result["stage"] == "run"
    assert "ZeroDivisionError" in result["error"]
    assert isinstance(result["code"], str) and result["code"].startswith("def run(")


def test_run_missing_path_is_run_stage():
    schema = schema_one("int64", "$.nope", name="missing")
    result = run(schema, '[{"x": 1}]')

    assert result["ok"] is False
    assert result["stage"] == "run"
    assert result["error"].startswith("KeyError")


def test_run_nan_is_downgraded_to_string():
    schema = schema_one("float64", expr="`float('nan')`", name="nan")
    result = run(schema, "[1]")

    assert result["ok"] is True
    assert result["out"] == [["NaN"]]
    assert "NaN" in " ".join(result["warnings"])
    json.dumps(result, ensure_ascii=False, allow_nan=False)  # 整套响应可编码


def test_run_response_contract_keys():
    schema = schema_one("int64", "$.a", name="contract")
    result = run(schema, '[{"a": 1}]')

    assert set(result) == {"ok", "out", "out_count", "truncated", "code",
                           "elapsed_ms", "manifest", "warnings"}


def schema_shape(shape, field_type="int64", path="$.a", name="shape"):
    return {
        "name": name,
        "output": {"shape": shape},
        "out": {"type": "object", "fields": {"a": {"type": field_type, "value": path}}},
    }


def test_run_rows_shape_returns_row_dicts():
    result = run(schema_shape("rows"), json.dumps([{"a": i} for i in range(250)]))

    assert result["ok"] is True
    assert result["out_count"] == 250
    assert result["truncated"] is True
    assert len(result["out"]) == 200
    assert result["out"][0] == {"a": 0}
    assert result["out"][-1] == {"a": 199}


def test_run_dict_shape_returns_columns():
    result = run(schema_shape("dict"), json.dumps([{"a": i} for i in range(250)]))

    assert result["ok"] is True
    assert result["out"] == {"a": list(range(200))}
    assert result["out_count"] == 250
    assert result["truncated"] is True


def test_run_dict_shape_empty_column():
    result = run(schema_shape("dict"), "[]")

    assert result["ok"] is True
    assert result["out"] == {"a": []}
    assert result["out_count"] == 0
    assert result["truncated"] is False


def test_run_index_shape_returns_indexed_rows():
    result = run(schema_shape("index"), json.dumps([{"a": i} for i in range(250)]))

    assert result["ok"] is True
    assert result["out_count"] == 250
    assert result["truncated"] is True
    assert len(result["out"]) == 200
    keys = list(result["out"])
    assert keys[0] == "0" and keys[-1] == "199"
    assert result["out"]["0"] == [0]
    assert result["out"]["199"] == [199]
    # 行号键本来就是数字，转字符串是 JSON 要求，不应产生告警
    assert result["warnings"] == []


def test_run_unknown_shape_object_is_schema_stage():
    result = run(schema_shape("object"), '[{"a": 1}]')

    assert result["ok"] is False
    assert result["stage"] == "schema"
    assert "shape" in result["error"]
    assert result["code"] is None


def test_dcg_version():
    assert bridge.dcg_version() == "2.2.2"
