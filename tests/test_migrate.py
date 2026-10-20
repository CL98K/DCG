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

    def test_protocol_false_has_no_wrapper(self):
        schema, rep = migrate.migrate_describe(_describe(protocol=False), name="demo")
        assert schema["output"]["post"] is None
        assert "output.post" not in [it["path"] for it in rep.items]

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
