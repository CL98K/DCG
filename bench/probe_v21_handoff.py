"""v2.1 变更语义边界探针（edge-prober / task-2 交付物）。

目标：**独立证明**（不复述实现注释）v2.1 三组改动的语义正确性：
  A. 位置行语法 `{"type":"list","fields":{...}}`（取代 `"row":"list"`）→ `_rl` / 列表字面量
  B. 具名对象 `{"type":"object","fields":{...}}` → `_ob` / 字典字面量
  C. schema 移除 `items`
  D. `_never_undef` / `_bare_name_never_undef` 的"可证非 undefined"判定（裸字面量 vs helper）
  E. `_flat_row`（shape=dict/rows 单键时裸存行）

所有用例都走真实链路：dcg.generate(schema) -> compile -> exec -> run(rows)，
断言**运行期输出值**（辅助断言生成源码里该用 helper 的地方没有退化成裸字面量）。

不使用 tempfile / 不删任何目录（沙箱 WinError 5）。

用法：python -X utf8 bench/probe_v21_handoff.py
退出码：0 = 全部用例符合预期；1 = 有 FAIL（明细见正文）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402
from dcg.schema import SchemaError, validate  # noqa: E402

RESULTS = []   # (case_id, ok, detail)


def run_schema(schema, rows):
    """生成 -> 编译 -> 执行；返回 (src, output)。生成/执行异常向上抛。"""
    src = dcg.generate(schema)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
    return src, ns["run"](rows)


def check(case_id, cond, detail):
    RESULTS.append((case_id, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {case_id}: {detail}")


def row_schema(fields, shape="list", single=False, source="rows"):
    return {"name": "probe", "input": {"source": source},
            "output": {"shape": shape, "single": single, "filter": False},
            "out": {"type": "object", "fields": fields}}


def has_undef(value) -> bool:
    """递归判断结果里是否残留 UNDEF 哨兵（哨兵绝不能出现在产物里）。"""
    if rt.is_u(value):
        return True
    if isinstance(value, dict):
        return any(has_undef(v) for v in value.values())
    if isinstance(value, list):
        return any(has_undef(v) for v in value)
    return False


# ---------------------------------------------------------------- D 组：py 裸名
PY_EXPR = "`str(a)`"


def case_d_py_optional_sibling():
    """嵌套位置行内 py 段引用同层 optional(无 default) 兄弟字段。

    `a` 缺键时 a 是 UNDEF；py 段 `str(a)` 拿到哨兵会产出字符串 "undefined"。
    期望：`b` 能"看到" a 的 undefined（要么 a 先落 None 再 str，要么整行丢弃 b），
    绝不能把哨兵当值算进输出。
    """
    s = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "any", "optional": True, "value": "$.x"},
        "b": {"type": "str", "expr": PY_EXPR}}}})
    try:
        src, out = run_schema(s, [{"x": 1}, {"y": 2}])
    except BaseException as exc:  # noqa: BLE001
        check("D1 py 裸名引用同层 optional 兄弟", False,
              f"执行异常 {type(exc).__name__}: {exc}")
        return
    leak = has_undef(out)
    b_val = out[0][0][1] if out else None
    fallback_used = "_rl([" in src          # 说明判定为"可能 undefined"
    tmp_leak = "str(_t" in src               # 裸名解析到未守卫的临时名
    ok = not leak and not (fallback_used and b_val == "undefined")
    check("D1 py 裸名引用同层 optional 兄弟", ok,
          f"输入缺键行 x=缺 -> b={b_val!r}；产物用 _rl={fallback_used}；"
          f"裸名落到未守卫临时名={tmp_leak}；残留哨兵={leak}")


def case_d_py_required_sibling():
    """required + 具体类型 兄弟被 py 裸名引用：判定"可证非 undefined"是正确的
    （缺键是真实 KeyError，不是 undefined），且值必须正确。"""
    s = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "int64", "value": "$.x"},
        "b": {"type": "str", "expr": PY_EXPR}}}})
    try:
        src, out = run_schema(s, [{"x": 5}])
        ok = out == [[[5, "5"]]] and "_rl([" not in src
        detail = f"out={out!r} 裸字面量(无 _rl)={'no _rl' if '_rl([' not in src else '_rl'}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("D2 py 裸名引用 required int64 兄弟", ok, detail)


def case_d_py_nested_default():
    """嵌套 default 字段被 py 裸名引用：`_df` 兜底后一定非 undefined，值正确。"""
    s = row_schema({"q": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.x", "default": 7},
        "b": {"type": "str", "expr": PY_EXPR}}}})
    try:
        src, out = run_schema(s, [{"x": 1}, {"y": 2}])
        ok = out == [[{"a": 1, "b": "1"}], [{"a": 7, "b": "7"}]]
        detail = f"out={out!r}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("D3 py 裸名引用嵌套 default 兄弟", ok, detail)


def case_d_py_outer_optional():
    """嵌套 py 段引用**外层** optional 字段：外层 body() 有 None 守卫，值正确。"""
    s = row_schema({
        "a": {"type": "any", "optional": True, "value": "$.x"},
        "q": {"type": "list", "fields": {"b": {"type": "str", "expr": PY_EXPR}}}})
    try:
        src, out = run_schema(s, [{"x": 1}, {"y": 2}])
        ok = out == [[1, ["1"]], [None, ["None"]]] and not has_undef(out)
        detail = f"out={out!r}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("D4 py 裸名引用外层 optional（有 None 守卫）", ok, detail)


# ------------------------------------------------- A/B 组：位置行 / 具名对象
def case_a_positional_missing():
    """位置行：optional 无 default 字段缺键 -> 保位落 None（_rl 语义）。"""
    s = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "any", "optional": True, "value": "$.x"},
        "b": {"type": "str", "value": "$.y"}}}})
    try:
        src, out = run_schema(s, [{"y": "s"}])
        ok = out == [[[None, "s"]]] and "_rl([" in src and not has_undef(out)
        detail = f"out={out!r} 用 _rl={'_rl([' in src}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("A1 位置行缺键保位（_rl -> None）", ok, detail)


def case_a_positional_order():
    """位置行：计算结果按**声明顺序**组装（前向引用合法、计算顺序 != 声明顺序）。"""
    s = row_schema({"q": {"type": "list", "fields": {
        "first": {"type": "int64", "value": "$.a"},
        "second": {"type": "int64", "expr": "`first` + 100"},
        "third": {"type": "int64", "value": "$.b"}}},
        "z": {"type": "int64", "value": "$.a"}})
    try:
        src, out = run_schema(s, [{"a": 1, "b": 2}])
        ok = out == [[[1, 101, 2], 1]]
        detail = f"out={out!r}（second 引用前向声明的 first）"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("A2 位置行按声明顺序组 list（允许前向引用）", ok, detail)


def case_a_positional_flat():
    """全字段可证非 undefined 时发列表字面量（无 _rl），值与 _rl 版一致。"""
    s = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "int64", "value": "$.a"},
        "b": {"type": "int64", "value": "$.b"}}}})
    try:
        src, out = run_schema(s, [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        ok = out == [[[1, 2]], [[3, 4]]] and "_rl([" not in src
        detail = f"out={out!r} 裸字面量={'_rl([' not in src}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("A3 位置行全可证 -> 列表字面量", ok, detail)


def case_a_positional_nested():
    """位置行里再套位置行：递归保位，结构 [[a,[b,c]], ...]。"""
    s = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "int64", "value": "$.a"},
        "pair": {"type": "list", "fields": {
            "b": {"type": "any", "optional": True, "value": "$.b"},
            "c": {"type": "int64", "value": "$.c"}}}}}})
    try:
        src, out = run_schema(s, [{"a": 1, "c": 3}])
        ok = out == [[[1, [None, 3]]]]
        detail = f"out={out!r}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("A4 嵌套位置行递归保位", ok, detail)


def case_b_object_missing():
    """具名对象：缺键字段整键丢弃（_ob 语义），不是 None。"""
    s = row_schema({"q": {"type": "object", "fields": {
        "a": {"type": "any", "optional": True, "value": "$.x"},
        "b": {"type": "str", "value": "$.y"}}}})
    try:
        src, out = run_schema(s, [{"y": "s"}])
        ok = out == [[{"b": "s"}]] and "_ob([" in src
        detail = f"out={out!r} 用 _ob={'_ob([' in src}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("B1 具名对象缺键丢键（_ob）", ok, detail)


# ------------------------------------------------------------ C 组：items 移除
def case_c_items_removed():
    """`items` 出现在任何层级都必须 build 期 SchemaError，且错误路径指到 items。"""
    probes = {
        "C1a 顶层 items": ({"a": {"type": "list", "items": {"type": "int64"}}},
                          "out.fields.a.items"),
        "C1b items+value 并存": ({"a": {"type": "list", "value": "$.q",
                                       "items": {"type": "int64"}}},
                                "out.fields.a.items"),
        "C1c 嵌套 fields 内 items": (
            {"a": {"type": "list", "fields": {
                "x": {"type": "list", "items": {"type": "int64"}}}}},
            "out.fields.a.fields.x.items"),
    }
    for cid, (fields, want_path) in probes.items():
        try:
            dcg.generate(row_schema(fields))
            check(cid, False, "未抛 SchemaError（items 被静默接受）")
        except SchemaError as exc:
            msg = str(exc)
            ok = want_path in msg and '"items"' in msg
            check(cid, ok, f"SchemaError: {msg}")
        except BaseException as exc:  # noqa: BLE001
            check(cid, False, f"抛了非 SchemaError：{type(exc).__name__}: {exc}")


def case_c_row_legacy():
    """`row` 兼容别名：row:"list"/"object" + fields 仍合法（归一成 type），
    只给 row 不给 fields 报明确错误（引导新写法）。"""
    diag = []
    alias_ok = True
    for legacy, want_type in (("list", "list"), ("object", "object")):
        try:
            norm = validate(row_schema(
                {"a": {"type": "object", "row": legacy, "fields":
                       {"x": {"type": "int64", "value": "$.0"}}}}))
            got = norm["out"]["fields"]["a"]
            this_ok = got.get("type") == want_type and "row" not in got
            alias_ok &= this_ok
            diag.append(f"row={legacy} -> type={got.get('type')}")
        except BaseException as exc:  # noqa: BLE001
            alias_ok = False
            diag.append(f"row={legacy} EXC {type(exc).__name__}: {exc}")
    try:
        dcg.generate(row_schema({"a": {"type": "list", "row": "list"}}))
        legacy_err = None
    except SchemaError as exc:
        legacy_err = str(exc)
    except BaseException as exc:  # noqa: BLE001
        legacy_err = f"非 SchemaError {type(exc).__name__}: {exc}"
    ok = alias_ok and legacy_err is not None and '"row" 已移除' in legacy_err
    check("C2 row 兼容别名 + 旧写法报错", ok,
          "；".join(diag) + f"；row 无 fields -> {legacy_err}")


# --------------------------------------------------------- E 组：_flat_row
def _old_semantics_dict(rows, key="a", idx=0):
    """v1 语义：单键 dict 形状 = {k: [r[idx] for r in rows]}（转置写法）。"""
    return {key: [r[idx] for r in rows]}


def _old_semantics_rows(rows, key="a", idx=0, single=False):
    """v1 语义：单键 rows 形状 = [{k: r[idx]} ...]（转置写法）。"""
    if single:
        return {key: rows[0][idx]}
    return [{key: r[idx]} for r in rows]


def case_e_flat_dict():
    """dict 形状单键：_flat_row 裸存行，与旧转置语法逐值相等。"""
    s = row_schema({"a": {"type": "int64", "value": "$.0"}}, shape="dict")
    ok_all, notes = True, []
    src = dcg.generate(s)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
    for data in ([], [[0]], [[1], [2], [3]], [[0], [5], [None]]):
        try:
            new = ns["run"](data)
            old = _old_semantics_dict(data)
            same = repr(new) == repr(old)
            ok_all &= same and not has_undef(new)
            notes.append(f"{data} new={new!r} old={old!r} same={same}")
        except BaseException as exc:  # noqa: BLE001
            ok_all = False
            notes.append(f"{data} EXC {type(exc).__name__}: {exc}")
    check("E1 shape=dict 单键 _flat_row 等价旧语义", ok_all, " | ".join(notes))


def case_e_flat_rows():
    """rows 形状单键（含 single）：与旧转置语法逐值相等。"""
    s = row_schema({"a": {"type": "int64", "value": "$.0"}}, shape="rows")
    src = dcg.generate(s)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
    ok_all, notes = True, []
    for data in ([], [[0]], [[1], [2], [3]]):
        new = ns["run"](data)
        old = _old_semantics_rows(data)
        same = repr(new) == repr(old)
        ok_all &= same
        notes.append(f"{data} new={new!r} old={old!r} same={same}")
    s1 = row_schema({"a": {"type": "int64", "value": "$.0"}}, shape="rows",
                    single=True)
    ns1: dict = {}
    exec(compile(dcg.generate(s1), "<gen>", "exec"), ns1)  # noqa: S102
    for data in ([[0]], [[1]]):
        new = ns1["run"](data)
        old = _old_semantics_rows(data, single=True)
        same = repr(new) == repr(old)
        ok_all &= same
        notes.append(f"single {data} new={new!r} old={old!r} same={same}")
    check("E2 shape=rows 单键 _flat_row 等价旧语义", ok_all, " | ".join(notes))


def case_e_flat_optional():
    """单键 dict 形状 + optional 字段：缺键行落 None（不丢行、不丢键）。"""
    s = row_schema({"a": {"type": "int64", "optional": True, "value": "$.0"}},
                   shape="dict")
    src = dcg.generate(s)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
    try:
        out = ns["run"]([[1], [], [3]])
        ok = repr(out) == repr({"a": [1, None, 3]}) and not has_undef(out)
        detail = f"out={out!r}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("E3 _flat_row + optional 缺键落 None", ok, detail)


def case_e_no_flat_for_list():
    """list 形状的单键**不能**裸存（裸存会把 [[v],...] 变成行本身 v）。

    shape=list 外层就是「每行一个 list」，单字段行 = `[v]`；若启用 `_flat_row`
    （`_o.append(v)`）结构就退化成 `[v1, v2, ...]`，与旧实现不同。
    """
    ok_all, notes = True, []
    probes = (("list", [[1], [2]], [[1], [2]], "_o.append([R[0]])"),)
    for shape, data, want, want_frag in probes:
        s = row_schema({"a": {"type": "int64", "value": "$.0"}}, shape=shape)
        src = dcg.generate(s)
        ns: dict = {}
        exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
        out = ns["run"](data)
        same = repr(out) == repr(want)
        row_is_list = want_frag in src
        ok_all &= same and row_is_list
        notes.append(f"{shape} out={out!r} want={want!r} same={same} "
                     f"行仍包 list={row_is_list}")
    check("E4 list 形状单键不走 _flat_row（行仍包 list）", ok_all, " | ".join(notes))


# ------------------------------------------------------------ 附加：守卫不丢行
def case_filter_rows():
    """`filter` 语义：顶层 optional 缺键行保留（值 None），falsy 行被丢。"""
    s = row_schema({"a": {"type": "any", "optional": True, "value": "$.x"}})
    src = dcg.generate(s)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)  # noqa: S102
    data = [{"x": 1}, {"y": 2}, {"x": 0}]
    out = ns["run"](data)
    ok = out == [[1], [None], [0]]
    check("F1 行过滤与 optional 守卫并存", ok, f"out={out!r}")


# ------------------------------------------------- 已知缺陷的回归探针（v2.1 遗留）
def case_g_nested_optional_guard():
    """**已知缺陷探针**：嵌套 fields 里的 optional(无 default) 字段没有 None 守卫。

    顶层 optional 会发 `X=None if _t is _U else _t`（codegen.py:1397-1413），
    但 `_emit_object` 里的 optional 字段直接落 `_ix_get(...)` / 裸读：
      * `type:"any"` 时读值走 `_ix_get` -> 缺键得 UNDEF，靠外层 `_rl`/`_ob` 兜住，
        所以"位置行仍保位 None / 具名对象丢键"看起来是对的；
      * **声明了具体类型**（int64/str/...）时 `_never_undef` 的 value 分支判 True
        -> 走裸读 `R['x']`，缺键直接 KeyError —— 与 README §2.3
        「optional 缺值给 None，不抛异常」的公开语义冲突。

    判定方式不用固定异常类型（避免把修复方式写死）：缺键行必须**产出结果**，
    且该位置的值是 None；抛任何异常即 FAIL。
    """
    for cid, spec_a, label in (
            ("G1a 位置行 optional int64 缺键应落 None",
             {"type": "int64", "optional": True, "value": "$.x"}, "list"),
            ("G1b 具名对象 optional str 缺键应落 None",
             {"type": "str", "optional": True, "value": "$.x"}, "object")):
        s = row_schema({"q": {"type": label, "fields": {
            "a": spec_a,
            "b": {"type": "str", "value": "$.y"}}}})
        try:
            src, out = run_schema(s, [{"y": "s"}])
            ok = out == [[[None, "s"]]] if label == "list" else \
                out == [[{"a": None, "b": "s"}]]
            detail = f"缺 x 的行 out={out!r}"
        except BaseException as exc:  # noqa: BLE001
            ok = False
            detail = f"缺 x 的行 -> {type(exc).__name__}: {exc}（可选字段应给 None）"
        check(cid, ok, detail)

    # 对照 —— 同 schema 把 type 换成 any 就正常（证明差异来自"具体类型"分支）
    s2 = row_schema({"q": {"type": "list", "fields": {
        "a": {"type": "any", "optional": True, "value": "$.x"},
        "b": {"type": "str", "value": "$.y"}}}})
    try:
        src2, out2 = run_schema(s2, [{"y": "s"}])
        ok2 = out2 == [[[None, "s"]]]
        d2 = f"out={out2!r}（type:any 正常，说明缺陷只在具体类型）"
    except BaseException as exc:  # noqa: BLE001
        ok2, d2 = False, f"type:any -> {type(exc).__name__}: {exc}"
    check("G2 对照：同为嵌套 optional 但 type:any", ok2, d2)


def case_h_optional_row_guard():
    """**已知缺陷探针**：顶层 `optional:true` + 嵌套 fields 的守卫是死代码。

    body() 给 optional 字段发 `a=None if _t1 is _U else _t1`，但 `_t1` 是
    `{'u':...}` / `[...]` 字面量（永远不是 _U），所以守卫恒假；
    内层字段缺键时产出的 dict 里会残留 `undefined` 哨兵。
    """
    s = row_schema({"a": {"type": "object", "optional": True, "fields": {
        "u": {"type": "any", "optional": True, "value": "$.u"}}},
        "b": {"type": "int64", "value": "$.y"}})
    try:
        src, out = run_schema(s, [{"y": 9}])
        leak = has_undef(out)
        ok = not leak
        detail = f"缺 u 的行 out={out!r} 残留哨兵={leak}"
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"执行异常 {type(exc).__name__}: {exc}"
    check("H1 optional+fields 守卫恒假导致哨兵外泄", ok, detail)


def main() -> int:
    for fn in (case_d_py_optional_sibling, case_d_py_required_sibling,
               case_d_py_nested_default, case_d_py_outer_optional,
               case_a_positional_missing, case_a_positional_order,
               case_a_positional_flat, case_a_positional_nested,
               case_b_object_missing, case_c_items_removed,
               case_c_row_legacy, case_e_flat_dict, case_e_flat_rows,
               case_e_flat_optional, case_e_no_flat_for_list,
               case_filter_rows, case_g_nested_optional_guard,
               case_h_optional_row_guard):
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            check(fn.__name__, False, f"探针自身异常 {type(exc).__name__}: {exc}")

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print()
    print("=" * 78)
    print(f"合计 {total} 条，PASS {passed}，FAIL {total - passed}")
    fails = [cid for cid, ok, _ in RESULTS if not ok]
    if fails:
        print("FAIL 用例：" + "、".join(fails))
        print("（G*/H* 是 v2.1 范围外的既有缺陷探针，FAIL 即记录在案；"
              "A/B/C/D/E/F 是 v2.1 三组改动的验收用例，必须全 PASS）")
    print("=" * 78)
    # 退出码只看 v2.1 验收用例（A/B/C/D/E/F），G/H 仅作证据记录
    gate = [cid for cid, ok, _ in RESULTS
            if not ok and not cid.startswith(("G", "H"))]
    return 0 if not gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
