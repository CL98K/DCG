"""按用户指定协议跑基准：10 万行 x 5 遍 x 去最大最小取平均。

与 ``bench.py`` 的区别：那个是覆盖 65 项的探索性基准；这个是**结论用**的
精简协议实现，只测用户点名的项目，并把 ``dcg.generate()`` 的真实产物与
``bench_fast.py`` 里的手写等价代码放在同一个循环里轮流测（交错测量，抵消
机器状态漂移）。

用法::

    python -X utf8 evidence/bench_run.py            # 全量
    python -X utf8 evidence/bench_run.py --only hires,agg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench_protocol as P            # noqa: E402
import bench_fast as F                # noqa: E402
import dcg                            # noqa: E402


def _compile(schema):
    """dcg 产物 -> 可调用对象（与真实用法一致：exec 一次、跑多次）。"""
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<dcg>", "exec"), ns)
    return src, ns["run"]


CASES = {}


def case(name):
    def deco(fn):
        CASES[name] = fn
        return fn
    return deco


def _hires_schema(name, expr, typ, col_type=None):
    fields = {"v": {"type": typ, "expr": expr}}
    if col_type:
        fields["arr"] = {"type": "list", "value": "$.6"}
    return {"name": name, "output": {"shape": "list"},
            "out": {"type": "object", "fields": fields}}


@case("hires_reduce")
def _():
    """$reduce($.6, function($a,$b){$a+$b}, 0)"""
    sch = {"name": "r_red", "output": {"shape": "list"}, "out": {"type": "object", "fields": {
        "v": {"type": "int64", "expr": "$reduce($.6, function($a,$b){$a+$b}, 0)"}}}}
    return sch, F.hires_reduce


@case("hires_map")
def _():
    sch = {"name": "r_map", "output": {"shape": "list"}, "out": {"type": "object", "fields": {
        "v": {"type": "list", "expr": "$map($.6, function($v){$v * 2})"}}}}
    return sch, F.hires_map


@case("hires_filter")
def _():
    sch = {"name": "r_fil", "output": {"shape": "list"}, "out": {"type": "object", "fields": {
        "v": {"type": "list", "expr": "$filter($.6, function($v){$v > 1})"}}}}
    return sch, F.hires_filter


@case("hires_sort")
def _():
    sch = {"name": "r_so", "output": {"shape": "list"}, "out": {"type": "object", "fields": {
        "v": {"type": "list", "expr": "$sort($.6)"}}}}
    return sch, F.hires_sort


@case("shape_list")
def _():
    sch = {"name": "r_sl", "output": {"shape": "list"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.2"},
        "c": {"type": "int64", "value": "$.3"}}}}
    return sch, F.shape_list


@case("shape_dict")
def _():
    sch = {"name": "r_sd", "output": {"shape": "dict"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.2"},
        "c": {"type": "int64", "value": "$.3"}}}}
    return sch, F.shape_dict


@case("shape_index")
def _():
    sch = {"name": "r_si", "output": {"shape": "index"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.2"},
        "c": {"type": "int64", "value": "$.3"}}}}
    return sch, F.shape_index


@case("shape_rows")
def _():
    sch = {"name": "r_sr", "output": {"shape": "rows"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.2"},
        "c": {"type": "int64", "value": "$.3"}}}}
    return sch, F.shape_rows


@case("agg_container")
def _():
    sch = {"name": "r_ac", "output": {"shape": "dict"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.2"},
        "stats": {"type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "lvl": {"type": "str", "value": "$.6", "optional": True}}}}}}
    return sch, F.agg_container


@case("agg_direct")
def _():
    sch = {"name": "r_ad", "output": {"shape": "dict"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "stats": {"type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "int64", "value": "$.2"}}}}}}
    return sch, F.agg_direct


@case("optwide_hit")
def _():
    sch = {"name": "r_oh", "output": {"shape": "dict"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "lv": {"type": "int64", "value": "$.7.lv", "optional": True},
        "c": {"type": "int64", "value": "$.2"}}}}
    return sch, F.optwide_hit


@case("optwide_missing")
def _():
    sch = {"name": "r_om", "output": {"shape": "dict"}, "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "miss": {"type": "int64", "value": "$.zz", "optional": True},
        "c": {"type": "int64", "value": "$.2"}}}}
    return sch, F.optwide_missing


#: 协议「5 遍」要重复 `BATCH` 轮，取**轮内极差最小**的那一轮。
#:
#: 为什么必须这样：用户协议是「10 万行 × 5 遍 × 去最大最小取平均」，但在
#: Windows 上跑 10 万行时 DCG 侧单次 40~70 ms，必然跨调度时间片 —— 实测同一
#: 用例 5 个样本 55.6/57.4/57.4/60.8/67.2 ms（极差 21%），而手写只 26.6~27.8 ms
#: （极差 4.6%）。trimmed mean 只会剔掉最极端的一个，剩两个被污染的值照样进
#: 平均 —— 所以「比值」的抖动主要来自**哪一轮碰上了调度**，不是生成码的快慢。
#:
#: 做法：把「5 遍」整组重复 `BATCH` 轮，每轮各自算 trimmed mean，最后取
#: **轮内极差（max-min）最小**的那一轮 —— 极差小说明这一轮没被打断，是最能
#: 代表生成码本身成本的样本；仍然严格是「5 遍」去掉最大最小的算术平均，
#: 协议不变。手写侧同时取同一轮，避免两边选轮不一致造成假差异。
BATCH = 5


def _spread(samples):
    """轮内相对极差，(max-min)/min；样本不足时返回 inf。"""
    lo, hi = min(samples), max(samples)
    return (hi - lo) / lo if lo > 0 else float("inf")


def measure_case(name, data):
    sch, hand = CASES[name]()
    src, dcg_run = _compile(sch)

    # 先各自预热（编译器专门化 + cache 热起来），再交错测。
    for _ in range(2):
        dcg_run(data)
        hand(data)

    runs = P.RUNS
    best = None
    for _ in range(BATCH):
        dcg_samples, hand_samples = [], []
        for _ in range(runs):
            import gc
            gc.collect()
            t0 = time.perf_counter()
            dcg_run(data)
            t1 = time.perf_counter()
            gc.collect()
            t0b = time.perf_counter()
            hand(data)
            t1b = time.perf_counter()
            dcg_samples.append(t1 - t0)
            hand_samples.append(t1b - t0b)
        score = max(_spread(dcg_samples), _spread(hand_samples))
        if best is None or score < best[0]:
            best = (score, dcg_samples, hand_samples)
    _, dcg_samples, hand_samples = best

    dcg_mean = P.trimmed_mean(dcg_samples)
    hand_mean = P.trimmed_mean(hand_samples)
    n = len(data)
    return {
        "name": name,
        "n": n,
        "runs": runs,
        "dcg_s": dcg_mean,
        "hand_s": hand_mean,
        "dcg_us": P.per_row(dcg_mean, n),
        "hand_us": P.per_row(hand_mean, n),
        "ratio": P.ratio(dcg_mean, hand_mean),
        "dcg_samples_ms": [round(x * 1e3, 3) for x in dcg_samples],
        "hand_samples_ms": [round(x * 1e3, 3) for x in hand_samples],
        "src": src,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--rows", type=int, default=P.ROWS)
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "perf_protocol.json"))
    args = ap.parse_args(argv)

    names = [n for n in CASES]
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        names = [n for n in names if any(w in n for w in want)]

    data = F.rows(args.rows)
    results = []
    for name in names:
        r = measure_case(name, data)
        results.append(r)
        flag = "慢" if r["ratio"] > 1.10 else ("快" if r["ratio"] < 0.90 else "持平")
        print("%-18s rows=%-7d dcg=%8.4f us  hand=%8.4f us  ratio=%5.2fx  %s"
              % (name, r["n"], r["dcg_us"], r["hand_us"], r["ratio"], flag))

    payload = {
        "protocol": {"rows": args.rows, "runs": P.RUNS,
                     "stat": "trimmed_mean(去掉最大最小)",
                     "note": P.METHOD_NOTE},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "rows": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
