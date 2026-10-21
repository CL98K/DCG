# -*- coding: utf-8 -*-
"""v1 `_inline_call_DSS` 的 v2 等价实现。

v1 产物 = `buffer` 一个 dict，11 个顶层键：
    docs / docsx / docids / xdocs / xdocsx / format / lasttime / count / ext / extx / ext1

v2 拆成两个生成产物 + 一个组装函数（v2 一篇 schema 只产出一段容器、只遍历一次）：

    docs.json       output.shape="list"   -> docs 组（14 个位置字段）
    docsx_docs.json output.shape="list"   -> docsx.docs（6 个位置字段）
    assemble(v1 输入, docs, docsx_docs) -> 与 v1 逐字段相同的 buffer

其余 9 个顶层键是常量列或由输入直接推导的列，不构成"每行一段文档"，在组装函数里
按 v1 表达式原样计算（不做额外防御：v1 里会崩的地方这里也照原样崩）。

用法::

    python -X utf8 examples/legacy_docs/build.py    # 生成 + 对拍
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg  # noqa: E402

# ---------------------------------------------------------------------------
# v2 schema：docs 组（v1 struct.docs，14 个位置字段）
# ---------------------------------------------------------------------------
# v1 代码里 `hour` 被赋值两次（先 data[4]、后 data[5]['a']），dict 字面量里
# `'hour'` 也出现两次 —— 后者覆盖前者，最终只有 1 个 hour 键、值取 data[5]['a']。
# 因此 v2 只声明一次 hour，位置固定在 hourYY 之后、hourA 之前（与 v1 dict 顺序一致）。
DOCS_SCHEMA = {
    "name": "legacy_docs",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": False},
    "out": {"type": "object", "fields": {
        "keyx": {"type": "int64", "const": 100},
        "keyy": {"type": "int64", "expr": "`1 if keyx else 0`"},
        "key": {"type": "any", "expr": "`-($.1)`"},
        "val": {"type": "any", "value": "$.2"},
        "day": {"type": "any", "value": "$.3"},
        "hour": {"type": "list", "value": "$.5.a"},
        "hourX": {"type": "list", "expr": "`[1, 2, 3]`"},
        "hourY": {"type": "any", "expr": "`hourX[0]`"},
        "hourZ": {"type": "any", "expr": "`day + 100`"},
        "hourXX": {"type": "any", "expr": "`hourY + hourZ`"},
        "hourYY": {"type": "any", "expr": "`key`"},
        "hourA": {"type": "any", "expr": "`hour[0]`"},
        "hourB": {"type": "any", "expr": "`hour[1]`"},
        "hourC": {"type": "any", "expr": "`hour[2]`"},
    }},
}

#: docs 组的位置顺序（组装时按它把 list 还原成 dict）
DOCS_ORDER = tuple(DOCS_SCHEMA["out"]["fields"])

# ---------------------------------------------------------------------------
# v2 schema：docsx.docs（v1 struct.docsx.docs，6 个键）
# ---------------------------------------------------------------------------
# v1 里 `keyx` 声明两次（100 与 `1 if 100 else 0`），后者覆盖 → 值恒为 1。
# `key` 在这里是 abs(data[1])，与 docs 组的 -data[1] 不同源，故两个产物独立。
DOCSX_DOCS_SCHEMA = {
    "name": "legacy_docsx_docs",
    "input": {"source": "index"},
    "output": {"shape": "list", "single": False, "filter": False},
    "out": {"type": "object", "fields": {
        "keyx": {"type": "int64", "expr": "`1 if 100 else 0`"},
        "key": {"type": "any", "expr": "`abs($.1)`"},
        "val": {"type": "any", "value": "$.2"},
        "day": {"type": "any", "value": "$.3"},
        "hour": {"type": "any", "value": "$.4"},
        "hourX": {"type": "any", "expr": "`abs($.1) + 100`"},
    }},
}

DOCSX_DOCS_ORDER = tuple(DOCSX_DOCS_SCHEMA["out"]["fields"])


def _to_dicts(rows, order):
    """把 list 形状的位置行还原成 v1 那样的具名 dict（保持声明顺序）。"""
    return [dict(zip(order, r)) for r in rows]


def assemble(run_docs, run_docsx_docs, datas):
    """按 v1 `_inline_call_DSS` 的表达式，把两段产物 + 常量列拼成 v1 的 buffer。

    ``run_docs`` / ``run_docsx_docs`` 是两个生成产物的 ``run``；
    ``datas`` 是 v1 的输入（``{"index": [...]}``）。
    """
    idx = datas["index"]
    docs = _to_dicts(run_docs(datas), DOCS_ORDER)
    docsx_docs = _to_dicts(run_docsx_docs(datas), DOCSX_DOCS_ORDER)

    # 以下逐条对应 v1 第 3 行 buffer 字面量里的表达式，顺序与 v1 保持一致。
    return {
        "docs": docs,
        "docsx": {
            "docs": docsx_docs,
            "docids": [(0 if (data[1] == 4) else data[0]) for data in idx],
            "A": 100,
            "B": {
                "B1": 100,
                "B2": [{"val": data[2], "day": data[3], "hour": data[4]}
                       for data in idx],
                "B3": {"C": {"D": {"E": [{"keyx": 100} for data in idx]}}},
            },
        },
        "docids": [(0 if (data[1] == 2) else data[0]) for data in idx],
        "xdocs": [{"cnt": (1 if data[0] else 0)} for data in idx],
        "xdocsx": ["" for data in idx],
        "format": [1, 2, 3],
        "lasttime": [0 for data in idx],
        "count": [1 for data in idx],
        "ext": {
            "index": 1,
            "list": [3],
            "docids": [data[0] for data in idx],
            "keys": [{"cnt": (0 if (data[0][1] == "1") else data[0])}
                     for data in idx],
        },
        "extx": [1],
        "ext1": [{"ext": [1]} for data in idx],
    }


def build(datas=None):
    """生成两个产物并返回 (docs_run, docsx_run, assemble 结果)。"""
    if datas is None:
        datas = DATAS
    mods = {}
    for schema in (DOCS_SCHEMA, DOCSX_DOCS_SCHEMA):
        src = dcg.generate(schema)
        ns = {}
        exec(compile(src, "<%s>" % schema["name"], "exec"), ns)
        mods[schema["name"]] = ns["run"]
    return mods, assemble(mods[DOCS_SCHEMA["name"]], mods[DOCSX_DOCS_SCHEMA["name"]], datas)


DATAS = {"index": [
    ("a0", 0, 0, 0, 0, {"a": [0, 1, 2]}),
    ("a1", 2, -1, 1, 1, {"a": [0, 1, 2]}),
    ("a2", 2, 0, 2, 2, {"a": [0, 1, 2]}),
    ("a3", 3, 3, 3, 3, {"a": [0, 1, 2]}),
    ("a4", 4, -4, 4, 4, {"a": [0, 1, 2]}),
    ("a5", 5, 5, 5, 5, {"a": [0, 1, 2]}),
    ("a6", 6, -6, 6, 6, {"a": [0, 1, 2]}),
    ("a7", 7, 7, 7, 7, {"a": [0, 1, 2]}),
    ("a8", 8, 8, 8, 8, {"a": [0, 1, 2]}),
    ("a9", 9, 9, 9, 9, {"a": [0, 1, 2]}),
]}


if __name__ == "__main__":
    mods, got = build()
    print(json.dumps(got, ensure_ascii=False, indent=1)[:1200])
