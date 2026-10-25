"""单列场景适用边界：列数 x 特性组合 的实测对比。

回答用户 m04090 的问题「单列场景 DCG 到底值不值得用」—— 用同一份 10 万行数据、
同一套 5 遍去最大最小协议，把「N 列 = 1 个特性列 + (N-1) 个同类型简单列」
在 N=1/2/4/8/16 下逐档测出来。

用法::
    python -X utf8 evidence/bench_cols.py            # 约 3~5 分钟
    python -X utf8 evidence/bench_cols.py --quick    # 10000 行冒烟
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench_protocol as P            # noqa: E402
import dcg                            # noqa: E402

# 每格重复的轮数：每轮内部跑满协议规定的 RUNS 遍，再选轮内极差最小的一轮。
# 5 轮把单形状耗时推到约 140 秒、双形状约 5 分钟；对「收尾复跑」这种场景 1 轮
# （仍满足 10 万行 × 5 遍 × 去最大最小）就够看清方向，可用 --batch 临时调高。
BATCH = 1


def data(n):
    """9 元组行：0=id 1=str 2=int 3=int 4=float 5=bool 6=[int,int] 7={lv:int} 8=str"""
    return [(i, "s%03d" % (i % 200), i % 97, (i * 7) % 1000,
             float(i % 50) / 4.0, i % 7 == 0, [i, i + 1], {"lv": i % 3},
             "n%02d" % (i % 20)) for i in range(n)]


# 每个特性 = (名字, 单列 spec 工厂, 手写单列取值器)
# 手写侧一律写成「与产物同形的最朴素等价式」：零 helper、零哨兵、零 lambda。
FEATURES = {}


def feat(name):
    def deco(fn):
        FEATURES[name] = fn
        return fn
    return deco


@feat("bare")
def _(k):
    """直接取值（最简）"""
    return {"type": "int64", "value": "$.3"}, (lambda r: r[3])


@feat("expr")
def _(k):
    """表达式：乘 2"""
    return {"type": "int64", "expr": "`$.3 * 2`"}, (lambda r: r[3] * 2)


@feat("opt")
def _(k):
    """optional，数据里存在"""
    return {"type": "int64", "value": "$.3", "optional": True}, (lambda r: r[3])


@feat("default")
def _(k):
    """带 default（键恒存在，取不到才用默认值）"""
    return {"type": "int64", "value": "$.3", "default": 0}, (lambda r: r[3])


@feat("nested")
def _(k):
    """嵌套取子键"""
    return {"type": "int64", "value": "$.7.lv"}, (lambda r: r[7]["lv"])


@feat("sum")
def _(k):
    """聚合直出（dict 形状才有列语义）"""
    return {"type": "int64", "expr": "$sum($.6)"}, (lambda r: sum(r[6]))


@feat("map")
def _(k):
    """$map 内联"""
    return {"type": "list", "expr": "$map($.6, function($v){$v * 2})"}, \
        (lambda r: [v * 2 for v in r[6]])


@feat("filter")
def _(k):
    """$filter 内联"""
    return {"type": "list", "expr": "$filter($.6, function($v){$v > 1})"}, \
        (lambda r: [v for v in r[6] if v > 1])


# 填充列：同类型简单列（value 直取），保证「多列」时行头/循环结构不变
FILLERS = [("f2", {"type": "int64", "value": "$.2"}, lambda r: r[2]),
           ("f4", {"type": "float64", "value": "$.4"}, lambda r: r[4]),
           ("f8", {"type": "str", "value": "$.8"}, lambda r: r[8])]


def build(fname, ncols, shape):
    make, hand1 = FEATURES[fname](0)
    fields = {"v": make}
    hands = {"v": hand1}
    for i in range(ncols - 1):
        kn, ks, kh = FILLERS[i % len(FILLERS)]
        fields[kn] = dict(ks)
        hands[kn] = kh
    sch = {"name": "cols_%s_%d_%s" % (fname, ncols, shape),
           "output": {"shape": shape},
           "out": {"type": "object", "fields": fields}}

    def hand(d):
        ks = list(hands)
        cols = {k: [hands[k](r) for r in d] for k in ks}
        if shape == "dict":
            return cols
        return [[cols[k][i] for k in ks] for i in range(len(d))]
    return sch, hand


def compile_run(sch):
    src = dcg.generate(sch)
    ns = {}
    exec(compile(src, "<dcg>", "exec"), ns)
    return src, ns["run"]


def spread(xs):
    lo, hi = min(xs), max(xs)
    return (hi - lo) / lo if lo > 0 else float("inf")


def measure(sch, hand, dat):
    src, run = compile_run(sch)
    for _ in range(2):
        run(dat)
        hand(dat)
    best = None
    for _ in range(BATCH):
        ds, hs = [], []
        for _ in range(P.RUNS):
            import gc
            gc.collect(); t0 = time.perf_counter(); run(dat); t1 = time.perf_counter()
            gc.collect(); t2 = time.perf_counter(); hand(dat); t3 = time.perf_counter()
            ds.append(t1 - t0); hs.append(t3 - t2)
        sc = max(spread(ds), spread(hs))
        if best is None or sc < best[0]:
            best = (sc, ds, hs)
    _, ds, hs = best
    dm, hm = P.trimmed_mean(ds), P.trimmed_mean(hs)
    n = len(dat)
    return {"dcg_us": P.per_row(dm, n), "hand_us": P.per_row(hm, n),
            "ratio": P.ratio(dm, hm), "src": src,
            "dcg_samples_ms": [round(x * 1e3, 2) for x in ds],
            "hand_samples_ms": [round(x * 1e3, 2) for x in hs]}


def main(argv=None):
    global BATCH
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=P.ROWS)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--shapes", default="dict,list")
    ap.add_argument("--batch", type=int, default=BATCH,
                    help="每格重复轮数（轮内跑满 RUNS 遍，取极差最小一轮）")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "cols_boundary.json"))
    a = ap.parse_args(argv)
    if a.quick:
        a.rows = 10000

    BATCH = max(1, a.batch)
    dat = data(a.rows)
    shapes = [s.strip() for s in a.shapes.split(",") if s.strip()]
    out = []
    for shape in shapes:
        for fname in FEATURES:
            for ncols in (1, 2, 4, 8, 16):
                sch, hand = build(fname, ncols, shape)
                try:
                    r = measure(sch, hand, dat)
                except Exception as e:      # 某一格不适用就记下来，不中断
                    print("%-10s %-8s n=%-3d SKIP %s" % (shape, fname, ncols, e))
                    continue
                r.update({"shape": shape, "feature": fname, "ncols": ncols})
                out.append(r)
                flag = "慢" if r["ratio"] > 1.10 else ("快" if r["ratio"] < 0.90 else "持平")
                print("%-6s %-8s n=%-3d dcg=%7.4f hand=%7.4f ratio=%5.2fx %s"
                      % (shape, fname, ncols, r["dcg_us"], r["hand_us"], r["ratio"], flag))
    payload = {"protocol": {"rows": a.rows, "runs": P.RUNS,
                            "stat": "trimmed_mean(去掉最大最小)",
                            "batch": BATCH, "note": P.METHOD_NOTE},
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "python": sys.version.split()[0],
               "rows": out}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
