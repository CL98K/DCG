"""按用户指定协议跑基准：10 万行 x 5 遍 x 去最大最小取平均，整组连跑 3 次取比值中位数。

与 ``bench.py`` 的区别：那个是覆盖 65 项的探索性基准；这个是**结论用**的
精简协议实现，测 13 项**用户点名/验收引用**的负载（含完整样例
``order_pipeline``），并把 ``dcg.generate()`` 的真实产物与 ``bench_fast.py``
里的手写等价代码放在同一个循环里轮流测（交错测量，抵消机器状态漂移）。

判定值：每个 case 按 5 遍协议算一次 dcg/hand 比值，整组连跑 ``REPEATS=3``
次，取 3 个比值的中位数（用户 m03272 的降噪裁决）。

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


#: 完整样例 ``order_pipeline``（README 附录 B 同款）：13 项里唯一的
#: 「行容器 + 裸名互引 + before/after 钩子」端到端负载。
ORDER_PIPELINE = {
    "name": "order_pipeline",
    "input": {"source": "$.payload.orders"},
    "output": {
        "shape": "dict",
        "before": "= $filter($, function($o){ $o['qty'] > 0 })",
        "after": "= $merge([$merge([$, {'orders': $.order_id, "
                 "'revenue': $.amount}]), "
                 "{'count': $count($.qty), 'total_revenue': $sum($.amount)}])",
    },
    "out": {"type": "object", "fields": {
        "order_id": {"type": "str", "value": "$.id"},
        "sku": {"type": "str", "value": "$.item.sku"},
        "qty": {"type": "int64", "value": "$.qty"},
        "unit_price": {"type": "float64", "value": "$.price"},
        "customer": {"type": "str", "value": "$.buyer.name"},
        "level": {"type": "str", "optional": True, "value": "$.buyer.level"},
        "note": {"type": "str", "optional": True, "value": "$.buyer.note"},
        "amount": {"type": "float64", "expr": "qty * unit_price"},
        "bulk": {"type": "bool", "expr": "qty >= 10"},
        "discount": {"type": "float64", "expr": "bulk ? amount * 0.9 : amount"},
        "fee": {"type": "float64", "expr": "discount + $sqrt(amount)"},
        "tag": {"type": "str", "expr": "`sku[:3].upper() + '-' + str(qty)`"},
        "label": {"type": "str", "expr": "bulk ? 'BULK' : 'SINGLE'"},
        "leg": {"type": "object", "fields": {
            "from": {"type": "str", "value": "$.item.from"},
            "to": {"type": "str", "value": "$.item.to"},
            "span": {"type": "int64", "expr": "qty * 2"}}},
        "stats": {"type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "list", "expr": "$.qty"},
            "pair": {"type": "list", "expr": "`[$.qty, $.qty * $.price]`"}}},
    }},
}


@case("order_pipeline")
def _():
    """完整样例（README 附录 B 同款）：行容器 + 裸名互引 + 钩子。"""
    return ORDER_PIPELINE, F.order_pipeline_hand


#: 协议「5 遍」整组重复 ``REPEATS`` 次，取这些比值的中位数作为判定值（m03272）。
#:
#: 之前的做法是「重复 5 轮、取轮内极差最小的一轮」——那仍是单轮样本，跨轮
#: 漂移会让相邻两次跑出 1.0x 与 2.6x（见第十三轮的复跑记录）。中位数只由
#: 中间那次决定：某一轮被调度/GC 打断时，最多污染 3 个样本里的 1 个。
REPEATS = P.REPEATS


def _run_pass(dcg_run, hand, data):
    """跑一遍协议：预热 2 次 + 5 遍交错测量，返回 trimmed mean 与原始样本。"""
    import gc
    for _ in range(2):
        dcg_run(data)
        hand(data)
    dcg_samples, hand_samples = [], []
    for _ in range(P.RUNS):
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
    return (P.trimmed_mean(dcg_samples), P.trimmed_mean(hand_samples),
            dcg_samples, hand_samples)


def measure_case(name, data, n):
    sch, hand = CASES[name]()
    src, dcg_run = _compile(sch)
    passes = []
    for _ in range(REPEATS):
        dcg_s, hand_s, dcg_samples, hand_samples = _run_pass(dcg_run, hand, data)
        passes.append({
            "dcg_s": dcg_s,
            "hand_s": hand_s,
            "ratio": P.ratio(dcg_s, hand_s),
            "dcg_samples_ms": [round(x * 1e3, 3) for x in dcg_samples],
            "hand_samples_ms": [round(x * 1e3, 3) for x in hand_samples],
        })
    ratios = sorted(p["ratio"] for p in passes)
    median = ratios[len(ratios) // 2]
    picked = min(passes, key=lambda p: abs(p["ratio"] - median))
    return {
        "name": name,
        "n": n,
        "runs": P.RUNS,
        "repeats": REPEATS,
        "ratios": [round(p["ratio"], 4) for p in passes],
        "ratio": picked["ratio"],
        "dcg_s": picked["dcg_s"],
        "hand_s": picked["hand_s"],
        "dcg_us": P.per_row(picked["dcg_s"], n),
        "hand_us": P.per_row(picked["hand_s"], n),
        "dcg_samples_ms": picked["dcg_samples_ms"],
        "hand_samples_ms": picked["hand_samples_ms"],
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

    data_rows = F.rows(args.rows)
    data_order = (F.make_orders(args.rows)
                  if any(n == "order_pipeline" for n in names) else None)
    results = []
    for name in names:
        if name == "order_pipeline":
            data = data_order
            n = sum(1 for o in data["payload"]["orders"] if o["qty"] > 0)
        else:
            data = data_rows
            n = len(data_rows)
        r = measure_case(name, data, n)
        results.append(r)
        flag = "慢" if r["ratio"] > 1.10 else ("快" if r["ratio"] < 0.90 else "持平")
        print("%-18s rows=%-7d dcg=%8.4f us  hand=%8.4f us  ratio=%5.2fx  (各轮=%s)  %s"
              % (name, r["n"], r["dcg_us"], r["hand_us"], r["ratio"],
                 " ".join("%.2f" % x for x in r["ratios"]), flag))

    payload = {
        "protocol": {"rows": args.rows, "runs": P.RUNS,
                     "repeats": P.REPEATS,
                     "stat": "每项 5 遍 trimmed_mean(去最大最小)；整组连跑 3 次，取 3 个比值的中位数",
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
