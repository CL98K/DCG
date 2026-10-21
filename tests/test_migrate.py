# -*- coding: utf-8 -*-
"""dcg/migrate.py：v1 文档键的结构化还原 + 报告口径（不静默丢键）。"""
from __future__ import annotations

import json

import dcg
from dcg import migrate


def _describe(**over):
    d = {
        "datas": "$.index", "mode": "", "protocol": True, "single": False,
        "filter": "", "_field": {},
        "struct": {"doc": [
            {"name": "code", "alias": "", "type": "str", "value": "$.0"},
            {"name": "qty", "alias": "", "type": "int", "value": "$.1"},
        ]},
    }
    d.update(over)
    return d


def _items(rep):
    return {(it["kind"], it["path"]): it for it in rep.items}


def _row_fields(schema):
    """本层行字段：有文档键时在 out.fields.<组>.fields 下，否则就是 out.fields。"""
    out = schema["out"]["fields"]
    if len(out) == 1:
        only = next(iter(out.values()))
        if isinstance(only, dict) and only.get("type") in ("object", "list") \
                and "fields" in only:
            return only["fields"]
    return out


class TestDocumentStructure:
    """v1 的 struct 顶层键就是输出文档键：v2 用 out.fields.<组> 结构化还原，不靠 post。"""

    def test_single_group_becomes_document_field(self):
        schema, rep = migrate.migrate_describe(_describe(), name="demo")
        assert schema["output"]["shape"] == "dict"
        assert schema["output"]["post"] is None
        doc = schema["out"]["fields"]["doc"]
        assert doc["type"] == "list" and "row" not in doc
        assert list(doc["fields"]) == ["code", "qty"]
        assert ("derived", "out.fields.doc") in _items(rep)
        assert rep.needs_manual is False

    def test_protocol_false_uses_named_objects(self):
        schema, rep = migrate.migrate_describe(_describe(protocol=False), name="demo")
        assert schema["output"]["shape"] == "dict"
        doc = schema["out"]["fields"]["doc"]
        assert doc["type"] == "object"
        assert "row" not in doc
        assert rep.needs_manual is False

    def test_migrated_product_matches_v1_output(self):
        schema, rep = migrate.migrate_describe(_describe(), name="demo")
        src = dcg.generate(dcg.validate(schema))
        ns = {}
        exec(compile(src, "<migrated>", "exec"), ns)
        assert ns["run"]({"index": [("a0", 0), ("a1", 1)]}) == {
            "doc": [["a0", 0], ["a1", 1]]}

    def test_migrated_output_never_uses_removed_row_or_items_keys(self):
        """迁移产物必须是 v2.1 的规范形：不带 `row` 别名、不带 `items`。

        v2.1 把位置行判别键从 `row:"list"` 改成 `type:"list"`，并删掉 `items`。
        migrate 若还在发旧键，迁移结果会靠 `schema.py` 的兼容别名兜住 —— 表面上能跑，
        但用户拿到的是一份"过时写法"，下次大版本会再炸一次。这条把迁移产物钉死在
        规范形上（递归检查整份 schema，不只是顶层文档字段）。
        """
        schema, rep = migrate.migrate_describe(_describe(), name="demo")

        def walk(node, path="out"):
            assert isinstance(node, dict), f"{path} 不是 dict"
            assert "row" not in node, f"{path} 仍带已废弃的 row 键"
            assert "items" not in node, f"{path} 仍带已移除的 items 键"
            if "type" in node:
                assert node["type"] in (
                    "object", "list", "int64", "int32", "float64",
                    "str", "bool", "any"), f"{path}: 非法 type {node['type']!r}"
            if "fields" in node:
                for k, v in node["fields"].items():
                    walk(v, f"{path}.fields.{k}")

        for fname, fspec in schema["out"]["fields"].items():
            walk(fspec, f"out.fields.{fname}")

    def test_migrated_product_is_accepted_by_validate(self):
        """迁移产物必须能被 `dcg.validate()` 原样接受（规范形自洽的硬标准）。"""
        for kwargs in ({}, {"wrap_doc": False}, {"protocol": False}):
            schema, _rep = migrate.migrate_describe(
                _describe(**kwargs), name="demo")
            dcg.validate(schema)  # 不抛即通过

    def test_no_wrap_doc_flattens_and_reports_manual(self):
        schema, rep = migrate.migrate_describe(_describe(), name="demo", wrap_doc=False)
        assert schema["output"]["shape"] == "list"
        assert list(schema["out"]["fields"]) == ["code", "qty"]
        assert ("manual", "output.post") in _items(rep)
        assert rep.needs_manual is True

    def test_multi_group_is_manual(self):
        d = _describe()
        d["struct"]["docs"] = [{"name": "x", "alias": "", "type": "int", "value": "$.2"}]
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["output"]["shape"] == "list"
        assert ("manual", "struct") in _items(rep)
        assert rep.needs_manual is True

    def test_func_is_mapped_post(self):
        schema, rep = migrate.migrate_describe(_describe(func="pkg.mod.fn"), name="demo")
        assert schema["output"]["post"] == "pkg.mod.fn"
        assert ("derived", "output.post") in _items(rep)
        assert rep.needs_manual is False

    def test_custom_group_name_is_preserved(self):
        d = _describe()
        d["struct"] = {"rows_out": d["struct"]["doc"]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert "rows_out" in schema["out"]["fields"]
        assert schema["output"]["shape"] == "dict"


class TestCli:
    def test_cli_writes_structural_schema(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(), ensure_ascii=False), encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["output"]["post"] is None
        assert not (tmp_path / "demo_wrap.py").exists()
        ns = {}
        exec(compile(dcg.generate(dcg.validate(schema)), "<p>", "exec"), ns)
        assert ns["run"]({"index": [("a0", 0)]}) == {"doc": [["a0", 0]]}

    def test_cli_no_wrap_doc_exit_3(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(), ensure_ascii=False), encoding="utf-8")
        rc = migrate.main([str(src), "-o", str(tmp_path / "v2_schema.json"),
                           "--name", "demo", "--no-wrap-doc", "--quiet"])
        assert rc == 3
        assert not (tmp_path / "demo_wrap.py").exists()

    def test_cli_protocol_false_uses_named_objects(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(protocol=False), ensure_ascii=False),
                       encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["output"]["shape"] == "dict"
        doc = schema["out"]["fields"]["doc"]
        assert "row" not in doc


class TestValueExtComposition:
    """v1 的 value+ext：先按 value 赋值（字段名绑定），再执行 ext，ext 可自引用字段名。"""

    def test_int_ext_self_reference_is_replaced(self):
        d = _describe()
        d["struct"] = {"doc": [{"name": "key", "alias": "", "type": "int",
                                "value": "$.1", "ext": "-(key)"}]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert _row_fields(schema)["key"]["expr"] == "`-($.1)`"
        it = _items(rep)[("derived", "struct.doc[0](value+ext)")]
        assert "-(key)" in it["v1"] and it["v2"] == "-($.1)"
        assert rep.needs_manual is False

    def test_hash_value_self_reference_is_replaced(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "day", "alias": "", "value": "$.3"},
            {"name": "hourZ", "alias": "", "value": "#.day", "ext": "hourZ + 100"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert _row_fields(schema)["hourZ"]["expr"] == "`day + 100`"

    def test_ext_without_self_reference_keeps_ext(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "hourX", "alias": "", "value": "#.hour.0"},
            {"name": "hourZ", "alias": "", "value": "#.day", "ext": "hourX + 100"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert _row_fields(schema)["hourZ"]["expr"] == "`hourX + 100`"
        assert ("info", "struct.doc[1](value+ext)") in _items(rep)


class TestDuplicateFieldNames:
    """v1 允许同名字段：后写覆盖先写，append 前的最后一次赋值决定两个位置的取值。"""

    def test_last_declaration_wins_for_both_positions(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "hour", "alias": "", "value": "$.4"},
            {"name": "hour", "alias": "", "value": "$.5.a"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        fields = _row_fields(schema)
        assert list(fields) == ["hour", "hour_2"]
        assert fields["hour"]["value"] == "$.5.a"
        assert fields["hour_2"]["value"] == "$.5.a"
        it = _items(rep)[("manual", "struct.doc[1]")]
        assert "hour_2" in it["v2"] and "最后一次" in it["reason"]
        assert rep.needs_manual is True


class TestDynamicPathValue:
    """v1 syntaxParse 的拼路径（f-string/方括号段）-> v2 expr 反引号内联 Python。"""

    def test_fstring_segment_with_alias_is_expanded(self):
        d = _describe(_field={"IATA": "$.data.AIRPORTIATA"})
        d["struct"] = {"doc": [
            {"name": "planned", "alias": "", "type": "str",
             "value": "$.data.f'AIRPORTIATA_{len(IATA)}'"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        field = _row_fields(schema)["planned"]
        assert field["expr"] == "`$.data[f'AIRPORTIATA_{len($.data.AIRPORTIATA)}']`"
        assert "value" not in field
        assert any(it["kind"] == "derived" and "f-string" in (it["reason"] or "")
                   for it in rep.items)
        assert rep.counts()["unsupported"] == 0

    def test_dynamic_path_product_runs(self):
        d = _describe(datas="")
        d["struct"] = {"doc": [
            {"name": "planned", "alias": "", "type": "str",
             "value": "$.data.f'AIRPORTIATA_{len($.data.AIRPORTIATA)}'"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo", wrap_doc=False)
        src = dcg.generate(dcg.validate(schema))
        ns = {}
        exec(compile(src, "<migrated>", "exec"), ns)
        out = ns["run"]([{"data": {"AIRPORTIATA": "URC", "AIRPORTIATA_3": "X"}}])
        assert out == [["X"]]

    def test_plain_bracket_index_stays_value(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "v", "alias": "", "value": "$.data.TASK_TYPE[0]"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        # 常量方括号下标是普通 value 路径（v2 的 value 语法两者等价，迁移期统一成点形式）
        assert _row_fields(schema)["v"]["value"] == "$.data.TASK_TYPE.0"
        assert "expr" not in _row_fields(schema)["v"]

    def test_dynamic_first_segment_is_unsupported(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "ok", "alias": "", "value": "$.0"},
            {"name": "bad", "alias": "", "value": "$.f'x{$.0}'"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema is not None
        assert "bad" not in _row_fields(schema)
        assert any(it["kind"] == "unsupported" and "开头" in (it["reason"] or "")
                   for it in rep.items)

    def test_slice_segment_is_unsupported(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "ok", "alias": "", "value": "$.0"},
            {"name": "bad", "alias": "", "value": "$.data.a[1:3]"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert "bad" not in _row_fields(schema)
        assert any(it["kind"] == "unsupported" and "切片" in (it["reason"] or "")
                   for it in rep.items)


class TestForwardReference:
    """v2 按依赖拓扑序求值：字段可以引用后面声明的字段，迁移期不得报「未声明」。"""

    def test_forward_reference_runs(self):
        d = _describe(datas="")
        d["struct"] = {"doc": [
            {"name": "a", "alias": "", "type": "int", "ext": "b + 1"},
            {"name": "b", "alias": "", "type": "int", "value": "1"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo", wrap_doc=False)
        assert _row_fields(schema)["a"]["expr"] == "`b + 1`"
        assert not any("裸名" in (it["reason"] or "") for it in rep.items)
        src = dcg.generate(dcg.validate(schema))
        ns = {}
        exec(compile(src, "<migrated>", "exec"), ns)
        assert ns["run"]([{}]) == [[2, 1]]


class TestSingleMode:
    """v1 的 single=True 是「每行一份文档」，不是 v2 output.single 的「只返回第一行」。"""

    def test_single_true_uses_rows(self):
        schema, rep = migrate.migrate_describe(_describe(single=True), name="demo")
        assert schema["output"]["shape"] == "rows"
        assert schema["output"]["single"] is False
        assert schema["output"]["post"] is None
        assert rep.needs_manual is False

    def test_single_true_protocol_false_uses_rows(self):
        schema, rep = migrate.migrate_describe(
            _describe(single=True, protocol=False), name="demo")
        assert schema["output"]["shape"] == "rows"
        assert schema["output"]["single"] is False
        assert "row" not in schema["out"]["fields"]["doc"]

    def test_cli_single_true_runs(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(single=True)), encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        ns = {}
        exec(compile(dcg.generate(dcg.validate(schema)), "<p>", "exec"), ns)
        got = ns["run"]({"index": [("a0", 0), ("a1", 1)]})
        assert got == [{"doc": ["a0", 0]}, {"doc": ["a1", 1]}]

    def test_single_true_with_ext_is_info_only(self):
        d = _describe(single=True)
        d["struct"]["doc"][1]["ext"] = "qty + 1"
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["output"]["shape"] == "rows"
        assert ("info", "output.single") in _items(rep)
        assert rep.needs_manual is False

    def test_single_true_with_multi_group_is_manual(self):
        d = _describe(single=True)
        d["struct"]["docs"] = [{"name": "x", "alias": "", "type": "int", "value": "$.2"}]
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["output"]["shape"] == "list"
        assert ("manual", "output.single") in _items(rep)
        assert rep.needs_manual is True
