# -*- coding: utf-8 -*-
"""format.py 例1（9 字段，含 `#.hour.3.2` 位置绑定）在 v1 与 v2 上的多维性能对比。

案例来源：`format.py:589-623`（原文整段被注释掉的 demo）。v1 侧 `describe` 与
`datas` **逐字照抄**；v2 侧用 `examples/flight.json`（例1 的等义迁移结果，
`#.hour.3.2 + #.hour.3.1` -> `` `hour[3][2] + hour[3][1]` ``）。

口径（CONTRACT.md §7.1）：
  * `time.process_time()`（只计本进程 CPU，免疫抢占）；
  * 每次测量内跑 k 遍摊销 Windows ~15.6 ms 计时粒度；
  * 变体交替测量（interleaved），每轮取 min，报各变体自身 spread；
  * 判定只引用**同进程同轮**的比值，绝对 µs/行 跨会话不可比；
  * A/A 噪声底 = 同一 callable 占两个 slot 时的比值离散度。

维度：
  D1 正确性      D2 编译期一次性成本   D3 稳态吞吐
  D4 输出容器    D5 规模扩展          D6 产物规模（字符/AST 节点/compile 次数）
  D7 字段数分解  D8 Cython 路径        D9 内存峰值

用法：
    python -X utf8 bench/bench_user_case.py             # 全量
    python -X utf8 bench/bench_user_case.py --quick     # 冒烟
    python -X utf8 bench/bench_user_case.py --no-cython
输出：
    控制台表格 + evidence/user_case_bench.json
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import gc
import io
import json
import os
import platform
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _workload as W          # noqa: E402  只在加载 v1/v2 时复用，不复制计时实现
import bench_row as BR         # noqa: E402  CONTRACT §7.1 唯一计时实现

RESULTS: dict = {}


# ===========================================================================
# 案例：v1 describe 逐字照抄 format.py:589-603
# ===========================================================================
V1_DESCRIBE = {
    "datas": "$.index",
    "mode": "",
    "protocol": True,
    "single": False,
    "filter": "",
    "_field": {},
    "struct": {"doc": [
        {"name": "hour",  "alias": "", "type": "list", "value": "$.5.a"},
        {"name": "hourA", "alias": "", "type": "int",  "value": "$.5.a.1"},
        {"name": "hourB", "alias": "", "type": "int",  "value": "$.5.a.2"},
        {"name": "hourC", "alias": "", "type": "int",  "value": "$.2"},
        {"name": "hourD", "alias": "", "type": "str",  "value": "",
         "ext": "str($.5.a.1) + str(hourB)"},
        {"name": "hourE", "alias": "", "type": "int",  "value": "",
         "ext": "hourA + hourB"},
        {"name": "hourF", "alias": "", "type": "int",  "value": "",
         "ext": "$.5.a.2 + $.5.a.1"},
        {"name": "hourG", "alias": "", "type": "int",  "value": "",
         "ext": "hour[2] + 123"},
        {"name": "hourH", "alias": "", "type": "int",  "value": "",
         "ext": "#.hour.3.2 + #.hour.3.1"},
    ]},
}

FIELD_ORDER = [f["name"] for f in V1_DESCRIBE["struct"]["doc"]]

# 用户给的 10 条原样（format.py:605-614）
ROWS10 = [
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
]

with open(os.path.join(ROOT, "examples", "flight.json"), encoding="utf-8") as _fh:
    V2_FLIGHT = json.load(_fh)


def make_rows(n: int):
    """把用户那 10 条扩成 n 条：保持同样 6 列布局与 `a` 的嵌套 list 形状，逐行变值。

    `a[3]` 必须是长度 >= 3 的 list（`hourH` 取 `a[3][2]` / `a[3][1]`），故保留嵌套。
    """
    rows = []
    for i in range(n):
        rows.append((
            "a%d" % (i % 10),
            i & 255,
            (i * 7) & 1023,
            i % 100,
            (i * 3) % 50,
            {"a": [i & 15, i & 31, i & 7, [(i * 5) & 63, i & 7, (i * 3) & 15]]},
        ))
    return rows


def ref9(datas):
    """手写参照（9 字段），返回 v1 的容器形态 {'doc': [...]}。"""
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        hA = a[1]
        hB = a[2]
        a3 = a[3]
        ap([a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA,
            a[2] + 123, a3[2] + a3[1]])
    return {"doc": out}


def ref9_list(datas):
    """手写参照，返回 v2 shape=list 的容器形态（裸 list）。"""
    out = []
    ap = out.append
    for r in datas["index"]:
        a = r[5]["a"]
        hA = a[1]
        hB = a[2]
        a3 = a[3]
        ap([a, hA, hB, r[2], str(hA) + str(hB), hA + hB, hB + hA,
            a[2] + 123, a3[2] + a3[1]])
    return out


# ===========================================================================
# 构造器
# ===========================================================================
def _quiet():
    return contextlib.redirect_stdout(io.StringIO())


def _v1_format():
    fmt = W.load_v1_engine()
    fmt.Format.OPT_MODE = True
    fmt.Format.CYTHON_MODE = False
    fmt.Format.TRYE_MODE = False
    fmt.Format.SAFE_MODE = False
    fmt.Format.DEBUG = False
    return fmt


def build_v1(desc=None, label="UC9", *,
             cython=False, trye=False, protocol=True, filt=""):
    """返回 (handler, info)。v1 是只读参照，不改 dcg.py / format.py。"""
    fmt = _v1_format()
    d = copy.deepcopy(V1_DESCRIBE if desc is None else desc)
    d["protocol"] = protocol
    d["filter"] = filt
    fmt.Format.CYTHON_MODE = bool(cython)
    fmt.Format.TRYE_MODE = bool(trye)
    try:
        with _quiet():
            info, handler = fmt.Format.parse(label, d)
    finally:
        fmt.Format.CYTHON_MODE = False
        fmt.Format.TRYE_MODE = False
    return handler, info


def v2_schema(*, shape="list", single=False, filt=False,
              k=None, base=None):
    s = copy.deepcopy(base if base is not None else V2_FLIGHT)
    if k is not None:
        s["out"]["fields"] = dict(list(s["out"]["fields"].items())[:k])
    s["output"]["shape"] = shape
    s["output"]["single"] = bool(single)
    s["output"]["filter"] = bool(filt)
    return s


def build_v2(schema):
    run, src = W.load_v2(schema)
    return run, src


def build_v2_cython(schema):
    """generate(cython=True) + pyximport 真编译。返回 (run, src, compile_s)。"""
    import importlib
    import tempfile
    import dcg
    src = dcg.generate(schema, cython=True)
    tmp = tempfile.mkdtemp(prefix="dcg_uc_cy_")
    modname = "dcg_uc_cy_%d" % (abs(hash(src)) % 10 ** 8)
    with open(os.path.join(tmp, modname + ".pyx"), "w", encoding="utf-8") as fh:
        fh.write(src)
    import pyximport
    with _quiet():
        pyximport.install(language_level=3, build_dir=tmp, inplace=False)
    sys.path.insert(0, tmp)
    with _quiet():
        t0 = time.perf_counter()
        mod = importlib.import_module(modname)
        dt = time.perf_counter() - t0
    return mod.run, src, dt


# ===========================================================================
# 计时包装
# ===========================================================================
def inter(fns, n, *, reps, k):
    """fns = [(label, callable)] -> {label: (min_us, arr, spread_pct)}"""
    return BR.measure_interleaved_cpu(list(fns), n, reps=reps, k=k)


def auto_k(n, target_s=0.20, us_per_row=0.5):
    """把每次测量撑到 >= target_s，确保 15.6 ms 的 process_time 粒度不是主导误差。

    Windows 上 `time.process_time()` 的粒度约 15.6 ms；若一次测量只有 10 ms，
    结果会量化成 0 或 2 倍。k 遍批量化把测量时长拉长到 ~0.2 s（量化 ~7%），
    配合 min-of-reps + 比值口径即可用。
    """
    import math
    need = target_s / (n * us_per_row * 1e-6)
    return max(1, min(4096, int(math.ceil(need))))


def safe_ratio(a, b):
    return (a / b) if b else float("inf")


def ratios(m):
    """把 inter() 结果转成 {label: us/row} 并给出以第一个为基的比值。"""
    base = m[list(m)[0]][0]
    return {lbl: (v[0], v[0] / base) for lbl, v in m.items()}


def fmt_row(label, us, base_us=None, spread=None):
    parts = [f"{label:<26}", f"{us:8.4f} µs/行", f"{1e6/us:11,.0f} 行/s",
             f"{us*1000:8.1f} ns/行"]
    if base_us:
        parts.append(f"{us/base_us:6.3f}x")
    if spread is not None:
        parts.append(f"spread {spread:4.1f}%")
    return "  ".join(parts)


def hdr(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ===========================================================================
# D1 正确性
# ===========================================================================
def d1_correctness(n):
    hdr(f"D1 正确性 —— N={n:,}（v1 / v2 / 手写参照 三方逐行比对）")
    rows = make_rows(n)
    datas = {"index": rows}
    ref = ref9_list(datas)

    v1_run, _ = build_v1()                       # protocol=True -> {'doc': [...]}
    v1_rows = W.normalize(v1_run(datas))
    print(f"  v1  行数={len(v1_rows):,}  与手写参照逐行相等: {v1_rows == ref}")
    print(f"       row0 = {v1_rows[0]}")

    print("  用户原代码的开关组合（TRYE_MODE=True 会改返回值形态）：")
    for cy, tr in [(False, False), (False, True)]:
        try:
            hand, _ = build_v1(label="UC9m%d%d" % (cy, tr), cython=cy, trye=tr)
            res = hand(datas)
            note = ""
            if isinstance(res, tuple):
                note = f"  <-- tuple(len={len(res)}) 不是结果 dict！用户原代码即此组合"
            print(f"    CYTHON_MODE={cy!s:<5} TRYE_MODE={tr!s:<5} -> "
                  f"{type(res).__name__}{note}")
        except Exception as exc:  # noqa: BLE001
            print(f"    CYTHON_MODE={cy!s:<5} TRYE_MODE={tr!s:<5} -> "
                  f"{type(exc).__name__}: {exc}")

    v2_run, _src = build_v2(v2_schema(shape="list"))
    v2_rows = W.normalize(v2_run(W.v2_input(rows, V2_FLIGHT)))
    print(f"  v2  行数={len(v2_rows):,}  与手写参照逐行相等: {v2_rows == ref}"
          f"  与 v1 逐行相等: {v2_rows == v1_rows}")
    print(f"       row0 = {v2_rows[0]}")

    RESULTS["correctness"] = {
        "n": n,
        "v1_rows": len(v1_rows), "v2_rows": len(v2_rows),
        "v1_eq_ref": v1_rows == ref, "v2_eq_ref": v2_rows == ref,
        "v1_eq_v2": v1_rows == v2_rows,
    }
    return rows, datas, ref


# ===========================================================================
# D2 编译期一次性成本
# ===========================================================================
def d2_compile(rows, datas):
    hdr("D2 编译期一次性成本（每次 schema 一份产物，只付一次）")
    # 一次性成本量级远小于 process_time 的 15.6 ms 粒度，故这里用 wall 计时
    # 取 min（不参与 §7 比值判定，只报量级）。
    print("  [口径] 编译期用 time.perf_counter() 取 5 次 min（一次性成本，非 §7 判定）")

    def timeit(fn, reps=5):
        arr = []
        for _ in range(reps):
            gc.collect()
            t0 = time.perf_counter()
            fn()
            arr.append(time.perf_counter() - t0)
        return min(arr) * 1e3                  # ms

    t_v1 = timeit(lambda: build_v1(label="UC9c"), reps=5)
    v1_sources = W.load_v1_src(V1_DESCRIBE, label="UC9c")
    print(f"  v1  Format.parse（含 {len(v1_sources)} 次 compile()）  {t_v1:8.2f} ms")

    v2_schema_ok = v2_schema(shape="list")

    def do_validate():
        import dcg
        dcg.validate(v2_schema_ok)

    def do_generate():
        import dcg
        dcg.generate(v2_schema_ok)

    def do_compile_exec():
        import dcg
        src = dcg.generate(v2_schema_ok)
        ns = {}
        exec(compile(src, "<dcg>", "exec"), ns)

    t_val = timeit(do_validate, reps=7)
    t_gen = timeit(do_generate, reps=5)
    t_ce = timeit(do_compile_exec, reps=5)
    print(f"  v2  validate()                               {t_val:8.3f} ms")
    print(f"  v2  generate()                               {t_gen:8.2f} ms")
    print(f"  v2  generate()+compile()+exec()（端到端）    {t_ce:8.2f} ms")
    print(f"  ---> 编译期 v2/v1 = {t_ce/t_v1:.3f}x")

    RESULTS["compile_ms"] = {
        "v1_parse": t_v1, "v2_validate": t_val, "v2_generate": t_gen,
        "v2_end_to_end": t_ce, "ratio": t_ce / t_v1,
        "v1_compile_calls": len(v1_sources),
    }
    return v1_sources


# ===========================================================================
# D3 稳态吞吐（主判定）
# ===========================================================================
def d3_steady(rows, datas, ref_list, *, n, reps, k, blocks):
    hdr(f"D3 稳态吞吐（CPU 口径，n={n:,}，k={k}，reps={reps}，{blocks} 轮交替）")
    v1_run, _ = build_v1()
    v2_run, _ = build_v2(v2_schema(shape="list"))
    v2_in = W.v2_input(rows, V2_FLIGHT)

    variants = [
        ("hand ref (list)", lambda: ref9_list(datas)),
        ("hand ref (dict)", lambda: ref9(datas)),
        ("v1 (protocol=T)", lambda: v1_run(datas)),
        ("v2 (shape=list)", lambda: v2_run(v2_in)),
        # A/A：同一个 callable 占两个 slot
        ("A/A slot a", lambda: v2_run(v2_in)),
        ("A/A slot b", lambda: v2_run(v2_in)),
    ]

    rounds = []
    for b in range(blocks):
        m = inter(variants, n, reps=reps, k=k)
        rounds.append(m)
        print(f"  轮 {b+1}: " + "  ".join(
            f"{i}:{m[lbl][0]:.4f}" for i, (lbl, _) in enumerate(variants)))

    best = {}
    for lbl, _ in variants:
        arr = [r[lbl][0] for r in rounds]
        best[lbl] = min(arr)
    aa = [safe_ratio(rounds[b]["A/A slot a"][0], rounds[b]["A/A slot b"][0])
          for b in range(blocks)]
    noise = max(abs(x - 1.0) for x in aa)

    print()
    hand_list = best["hand ref (list)"]
    print(f"  {'变体':<26} {'µs/行':>9} {'行/秒':>12} {'ns/行':>9}  "
          f"{'vs hand':>8}  {'spread':>8}")
    print("  " + "-" * 74)
    for lbl, _ in variants[:4]:
        sp = rounds[0][lbl][2]
        us = best[lbl]
        print(f"  {lbl:<26} {us:9.4f} {1e6/us:12,.0f} {us*1000:9.1f}  "
              f"{us/hand_list:8.3f}x  {sp:7.1f}%")
    print(f"  {'A/A 噪声底 (|ratio-1| 最差)':<26} {noise:.4f}"
          f"   (= {noise*100:.2f}%)")

    r_v2_hand = best["v2 (shape=list)"] / hand_list
    r_v1_hand = best["v1 (protocol=T)"] / hand_list
    r_v2_v1 = best["v2 (shape=list)"] / best["v1 (protocol=T)"]
    print()
    print(f"  v2 / 手写参照 = {r_v2_hand:.3f}x     "
          f"v1 / 手写参照 = {r_v1_hand:.3f}x     v2 / v1 = {r_v2_v1:.3f}x")
    nsf = best["v2 (shape=list)"] * 1000 / len(FIELD_ORDER)
    print(f"  v2 每产生一个输出单元格 = {nsf:.1f} ns（{len(FIELD_ORDER)} 字段/行）")

    RESULTS["steady"] = {
        "n": n, "k": k, "reps": reps, "blocks": blocks,
        "noise_floor": noise,
        "us_per_row": {
            lbl: {"min": best[lbl],
                  "rounds": [r[lbl][0] for r in rounds],
                  "spread_pct": [r[lbl][2] for r in rounds]}
            for lbl, _ in variants[:4]
        },
        "ratio_v2_hand": r_v2_hand, "ratio_v1_hand": r_v1_hand,
        "ratio_v2_v1": r_v2_v1, "ns_per_cell_v2": nsf,
    }


# ===========================================================================
# D4 输出容器
# ===========================================================================
def d4_container(rows, datas, *, n, reps, k):
    hdr("D4 输出容器形态（同一份计算，只换外层包装）")
    v1_t, _ = build_v1(label="UC9t", protocol=True)    # {'doc': [ [..] ]}
    v1_f, _ = build_v1(label="UC9f", protocol=False)   # {'doc': [ {..} ]}
    runs = {}
    for shape in ("list", "dict", "index"):
        runs[shape] = build_v2(v2_schema(shape=shape))[0]
    v2_in = W.v2_input(rows, V2_FLIGHT)

    variants = [
        ("v1 protocol=True",  lambda: v1_t(datas)),
        ("v1 protocol=False", lambda: v1_f(datas)),
        ("v2 shape=list",     lambda: runs["list"](v2_in)),
        ("v2 shape=dict",     lambda: runs["dict"](v2_in)),
        ("v2 shape=index",    lambda: runs["index"](v2_in)),
    ]
    # 正确性：list / dict / index 三种 v2 形态互相投影后必须一致
    base_rows = W.normalize(runs["list"](v2_in))
    col = runs["dict"](v2_in)
    rowidx = runs["index"](v2_in)
    ok_dict = [[col[k][i] for k in FIELD_ORDER] for i in range(len(base_rows))]
    ok_index = [rowidx[i] for i in range(len(base_rows))]
    print(f"  v2 shape=list/dict/index 三者投影一致: "
          f"{ok_dict == base_rows and ok_index == base_rows}")
    print(f"  v1 protocol=True  ->  {{'doc': [9 元 list, ...]}}  行0={base_rows[0][:3]}...")
    print(f"  v1 protocol=False ->  {{'doc': [字段名 dict, ...]}}  "
          f"（与 v2 shape=dict 的列式布局**不同构**）")

    m = inter(variants, n, reps=reps, k=k)
    print()
    for lbl, _ in variants:
        us = m[lbl][0]
        print(f"  {lbl:<20} {us:8.4f} µs/行  {1e6/us:11,.0f} 行/s  spread {m[lbl][2]:4.1f}%")
    RESULTS["container"] = {
        "shapes_consistent": ok_dict == base_rows and ok_index == base_rows,
        "us_per_row": {lbl: m[lbl][0] for lbl, _ in variants},
    }


# ===========================================================================
# D5 规模扩展
# ===========================================================================
def d5_scaling(*, sizes, reps, blocks):
    hdr("D5 规模扩展（µs/行 是否随 N 线性；每点 k 自动调到 ~80 万行/次测量）")
    v1_run, _ = build_v1()
    v2_run, _ = build_v2(v2_schema(shape="list"))

    table = []
    print(f"  {'N':>10} {'k':>5} {'v1 µs/行':>10} {'v2 µs/行':>10} "
          f"{'v2/v1':>7} {'v2 行/s':>12}")
    print("  " + "-" * 62)
    for n in sizes:
        rows = make_rows(n)
        datas = {"index": rows}
        v2_in = W.v2_input(rows, V2_FLIGHT)
        k = max(1, min(2048, round(800_000 / n)))
        m = inter([("v1", lambda: v1_run(datas)),
                   ("v2", lambda: v2_run(v2_in))], n, reps=reps, k=k)
        v1u, v2u = m["v1"][0], m["v2"][0]
        table.append({"n": n, "k": k, "v1_us": v1u, "v2_us": v2u,
                      "v2_over_v1": v2u / v1u})
        print(f"  {n:>10,} {k:>5} {v1u:>10.4f} {v2u:>10.4f} "
              f"{v2u/v1u:>7.3f} {1e6/v2u:>12,.0f}")
        del rows, datas, v2_in
        gc.collect()

    if len(table) >= 2:
        a, b = table[0], table[-1]
        drift_v1 = a["v1_us"] / b["v1_us"]
        drift_v2 = a["v2_us"] / b["v2_us"]
        print(f"  线性度：N {a['n']:,} -> {b['n']:,}（{b['n']/a['n']:.0f}x）"
              f"  v1 µs/行 变化 {drift_v1:.3f}x，v2 µs/行 变化 {drift_v2:.3f}x")
    RESULTS["scaling"] = table


# ===========================================================================
# D6 产物规模
# ===========================================================================
def d6_artifact(v1_sources):
    hdr("D6 产物规模（v1 / v2 各自真正 compile() 的源码）")
    real = [s for s in v1_sources if "for data in datas" in s]
    stub = [s for s in v1_sources if s not in real]
    v2_src = W.load_v2(v2_schema(shape="list"))[1]
    v2_src_dict = W.load_v2(v2_schema(shape="dict"))[1]

    def stat(src, name):
        nodes = len(list(ast.walk(ast.parse(src))))
        print(f"  {name:<34} {len(src):>5} 字符 {len(src.splitlines()):>3} 行 "
              f"{nodes:>3} AST 节点")
        return {"chars": len(src), "lines": len(src.splitlines()), "ast_nodes": nodes}

    v1_real = stat(real[-1], "v1 真实 handler（第 2 次 compile）")
    if stub:
        v1_stub = stat(stub[0], "v1 空桩（第 1 次 compile，死代码）")
    v2_list = stat(v2_src, "v2 shape=list")
    v2_dict = stat(v2_src_dict, "v2 shape=dict")
    print()
    print(f"  compile() 调用次数：v1 = {len(v1_sources)}   v2 = 1   "
          f"{'（v1 多出的空桩是死代码）' if len(v1_sources) > 1 else ''}")
    print(f"  v2/v1 字符 = {v2_list['chars']/v1_real['chars']:.3f}x   "
          f"AST 节点 = {v2_list['ast_nodes']/v1_real['ast_nodes']:.3f}x")
    print()
    print("  --- v1 真实 handler ---")
    for ln in real[-1].splitlines():
        print("   |" + ln)
    print("  --- v2 shape=list ---")
    for ln in v2_src.splitlines():
        print("   |" + ln)
    RESULTS["artifact"] = {
        "v1_real": v1_real, "v1_stub": stub and v1_stub or None,
        "v2_list": v2_list, "v2_dict": v2_dict,
        "v1_compile_calls": len(v1_sources),
        "char_ratio": v2_list["chars"] / v1_real["chars"],
        "ast_ratio": v2_list["ast_nodes"] / v1_real["ast_nodes"],
        "v1_src": real[-1], "v2_src": v2_src,
    }


# ===========================================================================
# D7 字段数分解
# ===========================================================================
def d7_fields(rows, datas, *, n, reps, k):
    hdr(f"D7 字段数分解（n={n:,}，逐个字段追加，看边际成本落在哪）")
    v2_in = W.v2_input(rows, V2_FLIGHT)
    table = []
    prev = {}
    print(f"  {'累计字段':>4} {'末增字段':<8} {'v1 µs/行':>10} {'v2 µs/行':>10} "
          f"{'v2/v1':>7} {'v2 边际':>9}")
    print("  " + "-" * 62)
    for kk in range(1, len(FIELD_ORDER) + 1):
        d = copy.deepcopy(V1_DESCRIBE)
        d["struct"]["doc"] = d["struct"]["doc"][:kk]
        v1_run, _ = build_v1(desc=d, label="UC9k%d" % kk)
        v2_run, _ = build_v2(v2_schema(shape="list", k=kk))
        a = W.normalize(v1_run(datas))
        b = W.normalize(v2_run(v2_in))
        same = a == b
        m = inter([("v1", lambda: v1_run(datas)),
                   ("v2", lambda: v2_run(v2_in))], n, reps=reps, k=k)
        v1u, v2u = m["v1"][0], m["v2"][0]
        marg = v2u - prev.get("v2", 0.0) if prev else v2u
        table.append({"fields": kk, "added": FIELD_ORDER[kk - 1],
                      "v1_us": v1u, "v2_us": v2u, "v2_over_v1": v2u / v1u,
                      "v2_marginal_us": marg, "equal": same})
        print(f"  {kk:>4} {FIELD_ORDER[kk-1]:<8} {v1u:>10.4f} {v2u:>10.4f} "
              f"{v2u/v1u:>7.3f} {marg:>+9.4f}"
              + ("" if same else "   <-- 行输出不一致!"))
        prev = {"v1": v1u, "v2": v2u}
    print(f"  全部 {len(FIELD_ORDER)} 字段：v2/v1 = "
          f"{table[-1]['v2_over_v1']:.3f}x，"
          f"v2 总边际 = {table[-1]['v2_us'] - table[0]['v2_us']:+.4f} µs/行")
    RESULTS["fields"] = table


# ===========================================================================
# D8 Cython
# ===========================================================================
def d8_cython(rows, datas, ref_list, *, n, reps, k):
    hdr("D8 Cython 路径（v1 CYTHON_MODE vs v2 generate(cython=True)）")
    v1_run, _ = build_v1()
    v2_run, _ = build_v2(v2_schema(shape="list"))
    v2_in = W.v2_input(rows, V2_FLIGHT)

    variants = [("v1 (pure py)", lambda: v1_run(datas)),
                ("v2 (pure py)", lambda: v2_run(v2_in)),
                ("hand ref", lambda: ref9_list(datas))]
    notes = {}

    try:
        t0 = time.process_time()
        v1c, _ = build_v1(label="UC9cy", cython=True)
        dt1 = time.process_time() - t0
        got = W.normalize(v1c(datas))
        ok1 = got == ref_list
        variants.append(("v1 (CYTHON_MODE=T)", lambda: v1c(datas)))
        notes["v1_cython"] = f"编译 {dt1:.2f}s，语义一致={ok1}"
        print(f"  v1 CYTHON_MODE=True: 编译 {dt1:.2f}s，语义一致={ok1}")
    except Exception as exc:  # noqa: BLE001
        notes["v1_cython"] = f"{type(exc).__name__}: {exc}"
        print(f"  v1 CYTHON_MODE=True 失败: {notes['v1_cython']}")

    try:
        v2c, src_c, dtc = build_v2_cython(v2_schema(shape="list"))
        got = W.normalize(v2c(v2_in))
        ok2 = got == ref_list
        variants.append(("v2 (cython=True)", lambda: v2c(v2_in)))
        notes["v2_cython"] = f"编译 {dtc:.2f}s，语义一致={ok2}"
        print(f"  v2 cython=True: 编译 {dtc:.2f}s，语义一致={ok2}")
        cdefs = [ln for ln in src_c.splitlines() if ln.strip().startswith("cdef ")]
        notes["v2_cdef_lines"] = len(cdefs)
        print(f"  v2 cython 产物: {len(cdefs)} 行 cdef，总 {len(src_c)} 字符")
    except Exception as exc:  # noqa: BLE001
        notes["v2_cython"] = f"{type(exc).__name__}: {exc}"
        print(f"  v2 cython=True 失败: {notes['v2_cython']}")

    m = inter(variants, n, reps=reps, k=k)
    print()
    base = m["v1 (pure py)"][0]
    for lbl, _ in variants:
        us = m[lbl][0]
        print(f"  {lbl:<22} {us:8.4f} µs/行  {1e6/us:11,.0f} 行/s  "
              f"{us/base:6.3f}x vs v1纯Python  spread {m[lbl][2]:4.1f}%")
    RESULTS["cython"] = {**notes,
                         "us_per_row": {lbl: m[lbl][0] for lbl, _ in variants}}


# ===========================================================================
# D9 内存
# ===========================================================================
def d9_memory(n):
    hdr(f"D9 内存峰值（tracemalloc，n={n:,}）")
    rows = make_rows(n)
    datas = {"index": rows}
    v1_run, _ = build_v1()
    v2_run, _ = build_v2(v2_schema(shape="list"))
    v2_in = W.v2_input(rows, V2_FLIGHT)

    def peak(fn, warm=False):
        gc.collect()
        if warm:
            fn()
        gc.collect()
        tracemalloc.start()
        res = fn()
        cur, pk = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del res
        gc.collect()
        return pk

    structure_bytes = sum(sys.getsizeof(r) + sys.getsizeof(r[0]) + 64 for r in rows)
    p_v1 = peak(lambda: v1_run(datas), warm=True)
    p_v2 = peak(lambda: v2_run(v2_in), warm=True)
    print(f"  输入 rows 结构粗估（tuple+str+dict 头）  {structure_bytes/2**20:8.1f} MiB")
    print(f"  v1 单次运行 peak 额外分配               {p_v1/2**20:8.1f} MiB")
    print(f"  v2 单次运行 peak 额外分配               {p_v2/2**20:8.1f} MiB")
    print(f"  输出行数 {n:,}；每行 9 个元素，其中 1 个是 list 引用（零拷贝）")
    RESULTS["memory"] = {
        "n": n, "input_structure_mib": structure_bytes / 2 ** 20,
        "v1_peak_mib": p_v1 / 2 ** 20, "v2_peak_mib": p_v2 / 2 ** 20,
    }
    del rows, datas, v2_in
    gc.collect()


# ===========================================================================
# 主
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--k", type=int, default=BR.CPU_K)
    ap.add_argument("--reps", type=int, default=BR.CPU_REPS)
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-cython", action="store_true")
    ap.add_argument("--skip", default="", help="逗号分隔跳过的维度，如 D8,D9")
    ap.add_argument("--out", default=os.path.join(ROOT, "evidence", "user_case_bench.json"))
    a = ap.parse_args(argv)

    n = a.n
    k = a.k
    reps = a.reps
    blocks = a.blocks
    if a.quick:
        n, reps, blocks = 20_000, 3, 2
    k = max(k, auto_k(n))

    skip = {s.strip().upper() for s in a.skip.split(",") if s.strip()}

    print("=" * 78)
    print("DCG  v1 vs v2 —— format.py 例1（9 字段）多维性能对比")
    print("=" * 78)
    print(f"  机器      : {platform.processor()} / {platform.machine()}")
    print(f"  Python    : {platform.python_version()}  ({platform.python_implementation()})")
    print(f"  CPU 逻辑核: {os.cpu_count()}")
    print(f"  口径      : process_time(), k={k}, reps={reps}, blocks={blocks}, n={n:,}")
    print(f"  案例      : v1 describe 逐字照抄 format.py:589-603（含 #.hour.3.2）")
    print(f"  v2 schema : examples/flight.json（等义迁移）")
    print(f"  判定      : 只引比值；绝对 µs/行 跨会话不可比")

    rows, datas, ref_list = d1_correctness(min(n, 200_000) if not a.quick else n)
    v1_sources = d2_compile(rows, datas)

    if "D3" not in skip:
        d3_steady(rows, datas, ref_list, n=n, reps=reps, k=k, blocks=blocks)
    if "D4" not in skip:
        d4_container(rows, datas, n=n, reps=min(reps, 5), k=k)
    if "D6" not in skip:
        d6_artifact(v1_sources)
    if "D7" not in skip:
        # n 与主判定同档 => 量化 ~9.8 ns/行；reps 降为 3（D3 才是主判定）
        d7_fields(rows, datas, n=n, reps=min(reps, 3), k=k)
    if "D8" not in skip and not a.no_cython:
        d8_cython(rows, datas, ref_list, n=n, reps=min(reps, 3), k=k)
    del rows, datas, ref_list
    gc.collect()

    if "D5" not in skip:
        sizes = [20_000] if a.quick else [1_000, 10_000, 100_000, 500_000, 1_000_000]
        d5_scaling(sizes=sizes, reps=3, blocks=blocks)
    if "D9" not in skip:
        d9_memory(n if a.quick else 200_000)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    print()
    print(f"结果已写入 {os.path.relpath(a.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
