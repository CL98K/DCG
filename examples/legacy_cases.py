# -*- coding: utf-8 -*-
"""五个真实业务用例的 v2 实现（判据：运行结果逐值等于业务真跑的期望值）。

只关心结果，不关心生成代码的形态。v2 的一篇 schema 只产出一个容器、只遍历一次
输入，因此每个案例按业务的实际结构拆成「产物 + 组装」。

业务侧 ``handler(datas)`` 的返回形状有三种，本模块逐案复现：

* 单组：``(buffer, try_errors)`` 二元组
* 多组：**裸 dict**，不套 try 二元组
* 布局是 list 而非 dict：直接是 list（案例 5）

同目录 ``baseline.json`` 是这五个用例的实测基准（冻结的期望值）。

用法::

    python -X utf8 examples/legacy_cases.py          # 打印案例名
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg  # noqa: E402

# ---------------------------------------------------------------------------
# 公共数据
# ---------------------------------------------------------------------------
DATAS_INDEX = {"index": [
    ("a0", 0, 0, 0, 0, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a1", 2, -1, 1, 1, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a2", 2, 0, 2, 2, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a3", 3, 3, 3, 3, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a4", 4, -4, 4, 4, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a5", 5, 5, 5, 5, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a6", 6, -6, 6, 6, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a7", 7, 7, 7, 7, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a8", 8, 8, 8, 8, {"a": [0, 1, 2, [1, 2, 3]]}),
    ("a9", 9, 9, 9, 9, {"a": [0, 1, 2, [1, 2, 3]]}),
]}

DATAS_FLIGHT = [{"schema": "acdm", "table": "flight_dynamics", "action": "insert",
                 "data": {"SEQN": 1690519759394,
                          "SENDTIME": "2023-07-28T12:49:19",
                          "FLIGHT_DATE": "2023-07-28",
                          "AIRLINE_NAME": "HU", "AIRLINE_ID": 69,
                          "ID": 14528086, "IS_ARR_FLIGHT": "1",
                          "Flight_NO": "HU7892",
                          "TASK_TYPE": "W/Z", "CRAFT_NO": "B5905",
                          "AIRPORTIATA": "URC", "STAY_TYPE": "航后",
                          "ROUTEORDER_1": 1, "ROUTEORDER_2": 2,
                          "AIRPORTIATA_1": "KHG", "AIRPORTIATA_2": "URC",
                          "AIRPORTIATA_3": None, "REGION": "REGI",
                          "PLAN_TAKEOFF_1": "2023-07-28T10:55:00",
                          "REAL_TAKEOFF_1": "2023-07-28T11:03:00",
                          "PLAN_LANDING_2": "2023-07-28T12:45:00",
                          "FLIGHT_STATE_CODE": "PUBLISH", "DTL_STATUS": "ARR",
                          "DELAY_TIME": 0, "HASVIP": 0}}]



def _compile(schema):
    ns = {}
    exec(compile(dcg.generate(schema), "<%s>" % schema["name"], "exec"), ns)
    return ns["run"]


# ===========================================================================
# 案例 1：类型声明 + 反引号表达式 + 字段引用
# ---------------------------------------------------------------------------
# 期望值: ({"doc": [[位置 list] × 10]}, [])
#   hour=[0,1,2,[1,2,3]]  hourA=1  hourB=2  hourC=data[2]
#   hourD="12"  hourE=3  hourF=3  hourG=125  hourH=5
# ===========================================================================
CASE1_SCHEMA = {
    "name": "case1_doc",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        # hour 是 `$.5.a` 整段（叶子里有嵌套 list，原样透传）
        "hour": {"type": "list", "value": "$.5.a"},
        "hourA": {"type": "int64", "value": "$.5.a.1"},
        "hourB": {"type": "int64", "value": "$.5.a.2"},
        "hourC": {"type": "any", "value": "$.2"},
        "hourD": {"type": "str", "expr": "`str($.5.a.1) + str(hourB)`"},
        "hourE": {"type": "int64", "expr": "`hourA + hourB`"},
        "hourF": {"type": "int64", "expr": "`$.5.a.2 + $.5.a.1`"},
        "hourG": {"type": "int64", "expr": "`hour[2] + 123`"},
        # hour[3][2] + hour[3][1]
        "hourH": {"type": "int64", "expr": "`hour[3][2] + hour[3][1]`"},
    }},
}
CASE1_ORDER = tuple(CASE1_SCHEMA["out"]["fields"])


def case1(datas):
    run = _compile(CASE1_SCHEMA)
    rows = run(datas)
    return {"doc": [list(r) for r in rows]}, []


# ===========================================================================
# 案例 2：11 组，多段文档 + 嵌套
# ---------------------------------------------------------------------------
# 期望值: ({"docs":…, "docsx":…, "docids":…, …}, [])，11 个顶层键。
# 关键实测（来自业务真跑结果，不是推断）：
#   docs 组的 hour：声明两次，后者覆盖 → 单个 hour 键，值 = data[5]['a']
#   docs 组的 keyy：1 if keyx else 0，keyx=100 → 恒 1
#   docsx.docs 的 keyx：声明两次，后者覆盖 → 恒 1
#   docsx.docs 的 hour：data[4]（标量），与 docs 组的 hour 不同源
#   xdocsx：'' 空串（常量列退化）
#   format：[1,2,3] 是常量值，不随行数变长
# ===========================================================================
CASE2_DOCS_SCHEMA = {
    "name": "case2_docs",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
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
CASE2_DOCS_ORDER = tuple(CASE2_DOCS_SCHEMA["out"]["fields"])

CASE2_DOCSX_DOCS_SCHEMA = {
    "name": "case2_docsx_docs",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        # 业务侧同名 keyx 声明两次：键只留一个、值是后者（常量 100）。前一条
        # `1 if keyx else 0` 自引用读到的是该名字当时的值——实测 1，所以产出的
        # keyx 恒 1。v2 不允许字段裸名自引用，这里直接写常量。
        "keyx": {"type": "int64", "const": 1},
        "key": {"type": "any", "expr": "`abs($.1)`"},
        "val": {"type": "any", "value": "$.2"},
        "day": {"type": "any", "value": "$.3"},
        "hour": {"type": "any", "value": "$.4"},
        "hourX": {"type": "any", "expr": "`abs($.1) + 100`"},
    }},
}
CASE2_DOCSX_DOCS_ORDER = ("keyx", "key", "val", "day", "hour", "hourX")

def _positional_to_dicts(rows, order):
    """位置行还原具名 dict，顺序由 ``order`` 给出（可与字段声明顺序不同）。"""
    return [dict(zip(order, r)) for r in rows]


def case2(datas):
    """业务实测：多组时返回**裸 dict**（不套 try 二元组）。"""
    idx = datas["index"]
    docs = _positional_to_dicts(_compile(CASE2_DOCS_SCHEMA)(datas), CASE2_DOCS_ORDER)
    dxs = _positional_to_dicts(_compile(CASE2_DOCSX_DOCS_SCHEMA)(datas),
                               CASE2_DOCSX_DOCS_ORDER)
    buffer = {
        "docs": docs,
        "docsx": {
            "docs": dxs,
            # 0 if (data[1] == 4) else data[0]
            "docids": [(0 if data[1] == 4 else data[0]) for data in idx],
            "A": 100,
            "B": {
                "B1": 100,
                "B2": [{"val": d[2], "day": d[3], "hour": d[4]} for d in idx],
                "B3": {"C": {"D": {"E": [{"keyx": 100} for _ in idx]}}},
            },
        },
        # 0 if (data[1] == 2) else data[0]
        "docids": [(0 if data[1] == 2 else data[0]) for data in idx],
        "xdocs": [{"cnt": (1 if d[0] else 0)} for d in idx],
        "xdocsx": ["" for _ in idx],
        "format": [1, 2, 3],
        "lasttime": [0 for _ in idx],
        "count": [1 for _ in idx],
        "ext": {
            "index": 1,
            "list": [3],
            "docids": [d[0] for d in idx],
            # 0 if (cnt[1] == "1") else cnt，cnt = data[0]
            "keys": [{"cnt": (0 if d[0][1] == "1" else d[0])} for d in idx],
        },
        "extx": [1],
        "ext1": [{"ext": [1]} for _ in idx],
    }
    return buffer


# ===========================================================================
# 案例 3：字段别名
# ---------------------------------------------------------------------------
# 期望值: ({"docs": [[d[1], d[2], d[3]] × 10]}, [])
# ===========================================================================
CASE3_SCHEMA = {
    "name": "case3_docs",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "hourA": {"type": "any", "value": "$.1"},
        "hourB": {"type": "any", "value": "$.2"},
        "hourC": {"type": "any", "value": "$.3"},
    }},
}


def case3(datas):
    return {"docs": _compile(CASE3_SCHEMA)(datas)}, []


# ===========================================================================
# 案例 4：单组 4 字段
# ---------------------------------------------------------------------------
# 期望值: ({"docs": [{"hour":…, "hourA": d[0], "hourB": d[1], "hourC": d[2]}]}, [])
# ===========================================================================
CASE4_SCHEMA = {
    "name": "case4_docs",
    "input": {"source": "$.index"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "hour": {"type": "list", "value": "$.5.a"},
        "hourA": {"type": "any", "value": "$.0"},
        "hourB": {"type": "any", "value": "$.1"},
        "hourC": {"type": "any", "value": "$.2"},
    }},
}
CASE4_ORDER = tuple(CASE4_SCHEMA["out"]["fields"])


def case4(datas):
    rows = _compile(CASE4_SCHEMA)(datas)
    return {"docs": _positional_to_dicts(rows, CASE4_ORDER)}, []


# ===========================================================================
# 案例 5：真实航班数据，动态路径
# ---------------------------------------------------------------------------
# 期望值: ([[14528086, "W", null]], []), 即顶层是 list。
# 第三列 Planned_Arrival_Time 实测是 null —— 复现该行为。
# ===========================================================================
CASE5_SCHEMA = {
    "name": "case5_flight",
    "input": {},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "Flight_ID": {"type": "any", "value": "$.data.ID"},
        "Task_Type": {"type": "any", "value": "$.data.TASK_TYPE[0]"},
        "Planned_Arrival_Time": {"type": "any", "value": "$.data.AIRPORTIATA_3"},
    }},
}


def case5(datas):
    rows = _compile(CASE5_SCHEMA)(datas)
    out = []
    for r in rows:
        row = list(r)
        # f-string 动态路径实测未取到值 → null
        if row[2] is None:
            row[2] = None
        out.append(row)
    return out, []


CASES = [
    ("case1", CASE1_SCHEMA),
    ("case2", None),
    ("case3", CASE3_SCHEMA),
    ("case4", CASE4_SCHEMA),
    ("case5", CASE5_SCHEMA),
]

# 案例名 → 输入数据（与真跑时的输入逐字节相同）
DATAS = {
    "case1": DATAS_INDEX,
    "case2": DATAS_INDEX,
    "case3": DATAS_INDEX,
    "case4": DATAS_INDEX,
    "case5": DATAS_FLIGHT,
}

RUNNERS = {"case1": case1,
           "case2": case2,
           "case3": case3,
           "case4": case4,
           "case5": case5}


if __name__ == "__main__":
    print(json.dumps(list(RUNNERS), ensure_ascii=False, indent=1))
