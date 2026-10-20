# -*- coding: utf-8 -*-
"""dcg/migrate.py：v1 文档外层键的忠实还原 + 报告口径（不静默丢键）。"""
from __future__ import annotations

import json
import sys

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


class TestDocWrapper:
    def test_single_group_gets_post_wrapper(self):
        schema, rep = migrate.migrate_describe(_describe(), name="demo")
        assert schema["output"]["post"] == "demo_wrap.wrap"
        assert rep.wrapper_group == "doc"
        it = _items(rep)[("derived", "output.post")]
        assert "doc" in it["v1"] and "demo_wrap" in it["v2"]
        assert rep.needs_manual is False

    def test_no_wrap_doc_is_manual_not_silent(self):
        schema, rep = migrate.migrate_describe(_describe(), name="demo", wrap_doc=False)
        assert schema["output"]["post"] is None
        assert rep.wrapper_group is None
        it = _items(rep)[("manual", "output.post")]
        assert "run(rows)" in it["suggestion"]
        assert rep.needs_manual is True

    def test_protocol_false_still_gets_wrapper(self):
        # v1 的外层键与 protocol 无关：protocol=False 只是把行换成 {字段: 值} dict，
        # 输出仍然是 {"doc": [ {...}, ... ]}，所以同样要生成包装模块。
        schema, rep = migrate.migrate_describe(_describe(protocol=False), name="demo")
        assert schema["output"]["post"] == "demo_wrap.wrap"
        assert rep.wrapper_group == "doc"
        assert rep.needs_manual is False

    def test_multi_group_is_manual(self):
        d = _describe()
        d["struct"]["docs"] = [{"name": "x", "alias": "", "type": "int", "value": "$.2"}]
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["output"]["post"] is None
        assert ("manual", "struct") in _items(rep)
        assert rep.needs_manual is True

    def test_func_plus_wrapper_is_manual(self):
        schema, rep = migrate.migrate_describe(_describe(func="pkg.mod.fn"), name="demo")
        assert schema["output"]["post"] == "pkg.mod.fn"
        assert rep.wrapper_group is None
        assert ("manual", "output.post") in _items(rep)
        assert rep.needs_manual is True

    def test_custom_group_name_is_preserved(self):
        d = _describe()
        d["struct"] = {"rows_out": d["struct"]["doc"]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert rep.wrapper_group == "rows_out"
        assert schema["output"]["post"] == "demo_wrap.wrap"


class TestCli:
    def test_cli_writes_wrapper_and_artifact(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(), ensure_ascii=False), encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["output"]["post"] == "demo_wrap.wrap"
        wrapper = tmp_path / "demo_wrap.py"
        assert wrapper.exists()
        ns = {}
        exec(compile(wrapper.read_text(encoding="utf-8"), str(wrapper), "exec"), ns)
        assert ns["wrap"]([[1, 2]]) == {"doc": [[1, 2]]}
        src_py = dcg.generate(dcg.validate(schema))
        assert "_xp(" in src_py and "import importlib" in src_py

    def test_cli_no_wrap_doc_exit_3(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(), ensure_ascii=False), encoding="utf-8")
        rc = migrate.main([str(src), "-o", str(tmp_path / "v2_schema.json"),
                           "--name", "demo", "--no-wrap-doc", "--quiet"])
        assert rc == 3
        assert not (tmp_path / "demo_wrap.py").exists()

    def test_cli_protocol_false_writes_wrapper(self, tmp_path):
        # v1 的外层键与 protocol 无关：protocol=False 也必须还原 {"doc": ...}。
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(protocol=False), ensure_ascii=False),
                       encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["output"]["shape"] == "rows"
        assert schema["output"]["post"] == "demo_wrap.wrap"


class TestValueExtComposition:
    """v1 的 value+ext：先按 value 赋值（字段名绑定），再执行 ext，ext 可自引用字段名。"""

    def test_int_ext_self_reference_is_replaced(self):
        d = _describe()
        d["struct"] = {"doc": [{"name": "key", "alias": "", "type": "int",
                                "value": "$.1", "ext": "-(key)"}]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["out"]["fields"]["key"]["expr"] == "`-($.1)`"
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
        assert schema["out"]["fields"]["hourZ"]["expr"] == "`day + 100`"

    def test_ext_without_self_reference_keeps_ext(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "hourX", "alias": "", "value": "#.hour.0"},
            {"name": "hourZ", "alias": "", "value": "#.day", "ext": "hourX + 100"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["out"]["fields"]["hourZ"]["expr"] == "`hourX + 100`"
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
        fields = schema["out"]["fields"]
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
        field = schema["out"]["fields"]["planned"]
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
        assert schema["out"]["fields"]["v"]["value"] == "$.data.TASK_TYPE.0"
        assert "expr" not in schema["out"]["fields"]["v"]

    def test_dynamic_first_segment_is_unsupported(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "ok", "alias": "", "value": "$.0"},
            {"name": "bad", "alias": "", "value": "$.f'x{$.0}'"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema is not None
        assert "bad" not in schema["out"]["fields"]
        assert any(it["kind"] == "unsupported" and "开头" in (it["reason"] or "")
                   for it in rep.items)

    def test_slice_segment_is_unsupported(self):
        d = _describe()
        d["struct"] = {"doc": [
            {"name": "ok", "alias": "", "value": "$.0"},
            {"name": "bad", "alias": "", "value": "$.data.a[1:3]"},
        ]}
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert "bad" not in schema["out"]["fields"]
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
        assert schema["out"]["fields"]["a"]["expr"] == "`b + 1`"
        assert not any("裸名" in (it["reason"] or "") for it in rep.items)
        src = dcg.generate(dcg.validate(schema))
        ns = {}
        exec(compile(src, "<migrated>", "exec"), ns)
        assert ns["run"]([{}]) == [[2, 1]]

class TestSingleMode:
    """v1 的 single=True 是「每行一份文档」，不是 v2 output.single 的「只返回第一行」。"""

    def test_single_true_is_per_row_document_post(self):
        schema, rep = migrate.migrate_describe(_describe(single=True), name="demo")
        assert schema["output"]["single"] is False
        assert schema["output"]["post"] == "demo_single.wrap"
        assert rep.single_group == "doc"
        assert rep.wrapper_group is None
        assert rep.needs_manual is False
        it = _items(rep)[("derived", "output.post")]
        assert "[{" in it["v1"] or "每行" in it["v1"]

    def test_single_true_protocol_false_uses_rows(self):
        schema, rep = migrate.migrate_describe(
            _describe(single=True, protocol=False), name="demo")
        assert schema["output"]["shape"] == "rows"
        assert schema["output"]["single"] is False
        assert schema["output"]["post"] == "demo_single.wrap"

    def test_cli_writes_single_module_and_runs(self, tmp_path):
        src = tmp_path / "old_describe.json"
        src.write_text(json.dumps(_describe(single=True)), encoding="utf-8")
        out = tmp_path / "v2_schema.json"
        rc = migrate.main([str(src), "-o", str(out), "--name", "demo", "--quiet"])
        assert rc == 0
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["output"]["single"] is False
        assert schema["output"]["post"] == "demo_single.wrap"
        single = tmp_path / "demo_single.py"
        assert single.exists()

        ns = {}
        exec(compile(single.read_text(encoding="utf-8"), str(single), "exec"), ns)
        assert ns["wrap"]([["a0", 0], ["a1", 1]]) == [
            {"doc": ["a0", 0]}, {"doc": ["a1", 1]}]

        sys.path.insert(0, str(tmp_path))
        try:
            ns2 = {}
            exec(compile(dcg.generate(dcg.validate(schema)), "<p>", "exec"), ns2)
            got = ns2["run"]({"index": [("a0", 0), ("a1", 1)]})
        finally:
            sys.path.remove(str(tmp_path))
        assert got == [{"doc": ["a0", 0]}, {"doc": ["a1", 1]}]

    def test_single_true_with_func_is_manual(self):
        schema, rep = migrate.migrate_describe(
            _describe(single=True, func="pkg.mod.fn"), name="demo")
        assert schema["output"]["post"] is None
        assert rep.single_group is None
        assert ("manual", "output.post") in _items(rep)
        assert rep.needs_manual is True

    def test_single_true_with_ext_is_info_only(self):
        d = _describe(single=True)
        d["struct"]["doc"][1]["ext"] = "qty + 1"
        schema, rep = migrate.migrate_describe(d, name="demo")
        assert schema["output"]["post"] == "demo_single.wrap"
        assert ("info", "output.single") in _items(rep)
        assert rep.needs_manual is False
