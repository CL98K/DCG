# -*- coding: utf-8 -*-
"""可选原生加速后端（`dcg._accel`）的双臂基准：开 / 关两条路径。

两套用例，口径都取自 `evidence/bench_protocol.py`（RUNS=5 去极值、REPEATS=3
奇偶换序取比值中位、计时前整批 `repr` 逐值校验）：

* `main`：`evidence/bench_run.py` 的 12 项主基准 + `order_pipeline`。这些用例
  每行的内层数组只有 2 个元素，低于门控阈值，因此两条臂应当**逐值相同、
  耗时不构成差异** —— 这条本身就是结论。
* `ops`：把行内数组加宽到 n（8 / 32 / 128），让算子真正落进门控区间，
  测出「开加速后能快多少」。

产物：`evidence/accel_protocol.json`。

用法：
    python -X utf8 evidence/bench_accel.py                # 两套都跑
    python -X utf8 evidence/bench_accel.py --only main
    python -X utf8 evidence/bench_accel.py --only ops --widths 8,32,128
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402
from evidence import bench_fast as F  # noqa: E402
from evidence import bench_protocol as P  # noqa: E402
from evidence.bench_run import CASES  # noqa: E402

ACC = getattr(rt, "_ACC", None)
OUT = os.path.join(ROOT, "evidence", "accel_protocol.json")


def compile_src(src, tag):
    ns = {}
    exec(compile(src, "<%s>" % tag, "exec"), ns)
    return ns["run"]


def compile_schema(schema, tag):
    return compile_src(dcg.generate(schema), tag)


def arms(fn, data):
    """返回 (开加速输出, 关加速输出)，并把 `_ACC` 复位。"""
    rt._ACC = ACC
    try:
        on = fn(data)
        rt._ACC = None
        try:
            off = fn(data)
        finally:
            rt._ACC = ACC
    finally:
        rt._ACC = ACC
    return on, off


def measure(fn, data, repeats):
    """双臂交错计时，返回 (比值中位, 开ms中位, 关ms中位, 各轮比值)。"""
    on_ms, off_ms, ratios = [], [], []
    for i in range(repeats):
        if i % 2 == 0:
            rt._ACC = ACC
            a, _ = P.measure(lambda: fn(data))
            rt._ACC = None
            b, _ = P.measure(lambda: fn(data))
        else:
            rt._ACC = None
            b, _ = P.measure(lambda: fn(data))
            rt._ACC = ACC
            a, _ = P.measure(lambda: fn(data))
        on_ms.append(a * 1e3)
        off_ms.append(b * 1e3)
        ratios.append(b / a if a else float("inf"))
    rt._ACC = ACC
    on_ms.sort()
    off_ms.sort()
    ratios.sort()
    mid = len(ratios) // 2
    return round(ratios[mid], 3), round(on_ms[mid], 3), round(off_ms[mid], 3), [
        round(r, 3) for r in ratios]


def run_main(rows, repeats):
    data = F.rows(rows)
    #: `order_pipeline` 的数据形态与其余 12 项不同（单个 dict，行在
    #: ``payload.orders`` 下），沿用 `evidence/bench_run.py` 的同一工厂与同一个
    #: 行数口径，否则会取错形态抛 `TypeError: list indices must be integers`。
    data_order = F.make_orders(rows)
    out = []
    for name in CASES:
        schema, _hand = CASES[name]()
        if name == "order_pipeline":
            rows_here = sum(1 for o in data_order["payload"]["orders"]
                            if o["qty"] > 0)
            data_here = data_order
        else:
            rows_here = rows
            data_here = data
        try:
            fn = compile_schema(schema, name)
            on, off = arms(fn, data_here)
            same = repr(on) == repr(off)
            ratio, on_ms, off_ms, samples = measure(fn, data_here, repeats)
            rec = {
                "name": name, "rows": rows_here, "same_output": same,
                "ratio_off_on": ratio, "ratio_samples": samples,
                "on_ms": on_ms, "off_ms": off_ms,
                "on_us_per_row": round(on_ms * 1e3 / rows_here, 4),
                "off_us_per_row": round(off_ms * 1e3 / rows_here, 4),
            }
        except Exception as exc:  # noqa: BLE001 - 单例失败不拖垮整轮
            rec = {"name": name, "rows": rows_here,
                   "error": "%s: %s" % (type(exc).__name__, exc)}
        out.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
    return out


OPS = [
    ("$min", "$min($.6)"),
    ("$max", "$max($.6)"),
    ("$average(int)", "$average($.6)"),
    ("$distinct(int)", "$distinct($.6)"),
    ("$encodeUrl", "$encodeUrl($.9)"),
    ("$decodeUrl", "$decodeUrl($.9)"),
    ("$toMillis", "$toMillis($.10)"),
    ("$fromMillis", "$fromMillis($.11)"),
    ("$formatNumber", "$formatNumber($.4, '#,##0.00')"),
    ("$parseInteger", "$parseInteger($.8, '00')"),
]


def op_rows(count, width):
    out = []
    for i in range(count):
        ints = [(i * 31 + k * 7) % 1000 for k in range(width)]
        out.append((
            i,
            "s%03d" % (i % 200),
            i % 97,
            (i * 7) % 1000,
            float(i % 50) / 4.0,
            i % 7 == 0,
            ints,
            {"lv": i % 3},
            "n%02d" % (i % 20),
            "Path/To Item?q=%d&x=1#frag" % (i % 50),
            "2024-01-%02dT12:34:56Z" % (i % 28 + 1),
            i * 60000,
        ))
    return out


def run_ops(rows, widths, repeats):
    out = []
    for width in widths:
        data = op_rows(rows, width)
        for name, expr in OPS:
            schema = {
                "name": "px_%d_%d" % (width, abs(hash(expr)) % 1000000),
                "output": {"shape": "list"},
                "out": {"type": "object", "fields": {"v": {"type": "object", "expr": expr}}},
            }
            try:
                fn = compile_schema(schema, "%s@%d" % (name, width))
                on, off = arms(fn, data)
                same = repr(on) == repr(off)
                ratio, on_ms, off_ms, samples = measure(fn, data, repeats)
                rec = {
                    "name": name, "expr": expr, "n": width, "rows": rows,
                    "same_output": same, "ratio_off_on": ratio,
                    "ratio_samples": samples, "on_ms": on_ms, "off_ms": off_ms,
                    "on_us_per_row": round(on_ms * 1e3 / rows, 4),
                    "off_us_per_row": round(off_ms * 1e3 / rows, 4),
                }
            except Exception as exc:  # noqa: BLE001
                rec = {"name": name, "expr": expr, "n": width, "rows": rows,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
            out.append(rec)
            print(json.dumps(rec, ensure_ascii=False), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("main", "ops"), default=None)
    ap.add_argument("--rows", type=int, default=P.ROWS)
    ap.add_argument("--repeats", type=int, default=P.REPEATS)
    ap.add_argument("--widths", default="8,32,128")
    args = ap.parse_args()
    widths = [int(w) for w in args.widths.split(",") if w.strip()]

    print("accel backend:", "on" if ACC is not None else "off",
          sorted(n for n in dir(ACC) if not n.startswith("_")) if ACC is not None else [],
          flush=True)

    payload = {
        "protocol": {"rows": args.rows, "runs": P.RUNS, "repeats": args.repeats,
                     "widths": widths},
        "accel": ACC is not None,
        "ratio_note": "比值 = 关加速 / 开加速，>1 表示开加速更快",
    }
    if args.only in (None, "main"):
        payload["main"] = run_main(args.rows, args.repeats)
    if args.only in (None, "ops"):
        payload["ops"] = run_ops(args.rows, widths, args.repeats)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("written", OUT, flush=True)


if __name__ == "__main__":
    main()
