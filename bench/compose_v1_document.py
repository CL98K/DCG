"""例2（`format.py:626-697`）：v1 的**异构文档输出**在 v2 里怎么实现？

背景
----
v1 的 `struct` 可以不是「一张表」，而是一篇**多段文档**：一个 dict 里混着
行式列表（`docs` / `docsx.docs` / ...）、逐行标量列表（`docids` / `count` / ...）、
顶层常量（`A=100` / `format=[1,2,3]`）和嵌套子文档（`docsx.B.B3.C.D.E`）。

v2 的一个 schema 只产出一个**行列表**（`list`/`dict`/`index`/`rows`），没有
`shape="document"`。所以这一例的迁移方式不是「一个 schema 对一篇文档」，而是
**拆成多个 v2 产物 + 一层组装函数**（这也是生产迁移里 `manual` 项的处理方式）。

本脚本就是那层组装的**可执行样板**：每个可编译的段落各自 generate + run，
常量段落直接写字面量，最后拼回 v1 的文档形状，并与冻结的 v1 产物逐值比较。

    python -X utf8 bench/compose_v1_document.py          # 表格 + 退出码
    python -X utf8 bench/compose_v1_document.py --json f  # 机器证据

v1 侧数据来源：`evidence/examples/v1_examples_frozen.json`（chunk index=2）的
`cython_off_trye_off` 变体 —— 同时关掉 CYTHON 与 TRYE，得到裸 dict 而不是
`(dict, 失败行)` 二元组；这一变体也是 v1 自己的「无 CYTHON 缺陷」输出
（CYTHON_MODE=True 时 v1 把 `docs` 的 10 行全丢进错误桶，`docs` 变成空列表）。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "examples"))

import dcg  # noqa: E402
from dcg import runtime  # noqa: E402
from v1_parity import schemas  # noqa: E402

FROZEN = os.path.join(ROOT, "evidence", "examples", "v1_examples_frozen.json")


# ---------------------------------------------------------------- v2 plumbing
def _schema(name, fields):
    return {
        "name": name,
        "input": {"source": "index"},
        "output": {"shape": "list", "single": False, "filter": False,
                   "post": None, "trace": False},
        "out": {"type": "object", "fields": fields},
    }


def _f(expr=None, value=None, type_="any"):
    d = {"type": type_}
    if expr is not None:
        d["expr"] = "`" + expr + "`"
    else:
        d["value"] = value
    return d


def build(schema):
    """dcg.generate -> compile -> exec -> run（与 bench/_workload.py 同策略）。"""
    src = dcg.generate(schema)
    ns = dict(runtime.SOURCE)
    g: dict = {}
    exec(compile(src, "<gen:%s>" % schema["name"], "exec"), g)  # noqa: S102
    for k, v in ns.items():
        g.setdefault(k, v)
    return g["run"], src


def run_schema(schema, payload):
    fn, _src = build(schema)
    return fn(payload)


# ------------------------------------------- 文档里每个「可编译段落」的 v2 schema
# 键 = v1 文档里的段落路径；值 = (kind, schema-or-literal)
# kind: rows -> 直接用；flat -> 逐行取第 0 列（v1 单字段段落产出的是标量列表）
SECTION_SCHEMAS = {
    # docs：15 字段 × 10 行，v1 输出可直接对（EX2_DOCS 已验证逐值相等）
    ("docs",): ("rows", schemas.EX2_DOCS),
    # docsx.docs：7 字段。v1 这里声明了两次 keyx（重名），v2 拒绝重名 =>
    # 第二个改名 keyx2，位置不变，产物逐值不受影响。
    ("docsx", "docs"): ("rows", _schema("ex2_docsx_docs", {
        "keyx": _f("100"),
        "keyx2": _f("1 if keyx else 0"),
        "key": _f("abs($.1)"),
        "val": _f(None, "$.2"),
        "day": _f(None, "$.3"),
        "hour": _f(None, "$.4"),
        "hourX": _f("abs($.1) + 100"),
    })),
    # docsx.docids：v1 _field.code = $.1，ext = 0 if code == 4 else cid
    ("docsx", "docids"): ("flat", _schema("ex2_docsx_docids", {
        "cid": _f("0 if $.1 == 4 else $.0"),
    })),
    # docsx.B.B2：3 字段 × 10 行
    ("docsx", "B", "B2"): ("rows", _schema("ex2_b2", {
        "val": _f(None, "$.2"),
        "day": _f(None, "$.3"),
        "hour": _f(None, "$.4"),
    })),
    # docsx.B.B3.C.D.E：单字段 keyx=100，逐行 => [[100], [100], ...]
    ("docsx", "B", "B3", "C", "D", "E"): ("rows", _schema("ex2_e", {
        "keyx": _f("100"),
    })),
    # 顶层 docids：ext = 0 if code == 2 else cid
    ("docids",): ("flat", _schema("ex2_docids", {
        "cid": _f("0 if $.1 == 2 else $.0"),
    })),
    # xdocs：单字段 const 1 + ext => 逐行 [1]
    ("xdocs",): ("rows", _schema("ex2_xdocs", {
        "cnt": _f("1"),
    })),
    # xdocsx：v1 struct 是 {name, value:'1', const:'0', atom:'0'} => 逐行空串
    ("xdocsx",): ("flat", _schema("ex2_xdocsx", {
        "xdocsx": _f("''"),
    })),
    # lasttime / count：v1 单字段 const => 逐行标量
    ("lasttime",): ("flat", _schema("ex2_lasttime", {
        "timestamp": _f("0"),
    })),
    ("count",): ("flat", _schema("ex2_count", {
        "count": _f("1"),
    })),
    # ext.docids：逐行 $.0
    ("ext", "docids"): ("flat", _schema("ex2_ext_docids", {
        "cid": _f(None, "$.0"),
    })),
    # ext.keys：单字段 + ext 条件（cnt[1] == '1' 时置 0）
    ("ext", "keys"): ("rows", _schema("ex2_ext_keys", {
        "cnt": _f("0 if $.0[1] == '1' else $.0"),
    })),
    # ext1：列表 + ext '[1]' => 逐行 [[1]]
    ("ext1",): ("rows", _schema("ex2_ext1", {
        "ext": _f("[1]"),
    })),
}

# 文档里**不需要代码生成**的段落：顶层常量 / 单标量。
SECTION_CONSTANTS = {
    ("docsx", "A"): 100,
    ("docsx", "B", "B1"): 100,
    ("format",): [1, 2, 3],
    ("ext", "index"): 1,
    ("ext", "list"): [3],
    ("extx",): [1],
}


# ------------------------------------------------------------------ 文档组装
def assemble(payload):
    """按 v1 文档的字段顺序组装出整篇文档。"""
    doc = {}

    def put(path, value):
        cur = doc
        for seg in path[:-1]:
            cur = cur.setdefault(seg, {})
        cur[path[-1]] = value

    for path, (kind, schema) in SECTION_SCHEMAS.items():
        out = run_schema(schema, payload)
        put(path, out if kind == "rows" else [r[0] for r in out])
    for path, value in SECTION_CONSTANTS.items():
        put(path, value)
    return doc


# ---------------------------------------------------------------------- 取证
def load_v1():
    with io.open(FROZEN, encoding="utf-8") as fh:
        data = json.load(fh)
    chunk = [c for c in data["chunks"] if c["index"] == 2][0]
    variant = chunk["variants"]["cython_off_trye_off"]
    result = variant["result"]
    if isinstance(result, str):
        result = ast.literal_eval(result)
    datas = chunk["datas"]
    if isinstance(datas, str):
        datas = ast.literal_eval(datas)
    return result, datas


def diff(expected, actual, path=()):
    """逐值比较，返回第一处差异（None = 全等）。"""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return {"path": "/".join(path) or "<root>",
                    "why": "keys differ",
                    "expected": sorted(expected), "actual": sorted(actual)}
        for k in expected:
            d = diff(expected[k], actual[k], path + (k,))
            if d:
                return d
        return None
    if isinstance(expected, (list, tuple)) and isinstance(actual, list):
        if len(expected) != len(actual):
            return {"path": "/".join(path), "why": "length differs",
                    "expected": len(expected), "actual": len(actual)}
        for i, (a, b) in enumerate(zip(expected, actual)):
            d = diff(a, b, path + (str(i),))
            if d:
                return d
        return None
    if expected != actual:
        return {"path": "/".join(path), "why": "value differs",
                "expected": expected, "actual": actual}
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default="evidence/examples/v2_ex2_document.json")
    args = ap.parse_args(argv)

    expected, datas = load_v1()
    payload = datas                      # 已经是 {'index': [行...]}，正是 v2 的 run 入参

    lines = []

    def say(t=""):
        lines.append(t)
        print(t)

    # 每段的独立核对：期望值 = v1 文档里那一段
    say("例2（format.py:626-697）异构文档：v2 组装 vs v1 冻结产物")
    say("=" * 78)
    actual = assemble(payload)
    per_section = []
    for path in sorted(set(SECTION_SCHEMAS) | set(SECTION_CONSTANTS)):
        exp = expected
        for seg in path:
            exp = exp[seg]
        act = actual
        for seg in path:
            act = act[seg]
        d = diff(exp, act)
        per_section.append({"section": "/".join(path), "equal": d is None,
                            "v2_kind": (SECTION_SCHEMAS[path][0]
                                        if path in SECTION_SCHEMAS else "const")})
        say("%-28s %-8s %s" % ("/".join(path),
                               "identical" if d is None else "MISMATCH",
                               "" if d is None else str(d)[:120]))

    whole = diff(expected, actual)
    say("=" * 78)
    say("整篇文档：%s（%d 个顶层段落，%d 个段落逐值相等）"
        % ("逐值相等" if whole is None else "存在差异",
           len(expected), sum(1 for s in per_section if s["equal"])))
    if whole is not None:
        say("第一处差异：%s" % whole)

    payload_out = {
        "question": "例2 的异构文档输出在 v2 里能否做到结果一致？（m03834）",
        "v1_evidence": "evidence/examples/v1_examples_frozen.json#chunk2/cython_off_trye_off",
        "approach": "多个 v2 产物 + 一层组装函数（v2 无 shape=document）",
        "sections": per_section,
        "whole_document_equal": whole is None,
    }
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload_out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with io.open(os.path.splitext(args.json)[0] + ".txt", "w",
                 encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("JSON -> %s" % os.path.relpath(args.json, ROOT))
    return 0 if whole is None else 1


if __name__ == "__main__":
    sys.exit(main())
