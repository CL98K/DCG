# -*- coding: utf-8 -*-
"""DCG 性能回归 runner（可复跑，一条命令）。

    python evidence/perf_runner.py                 # 全量 12 项 + order_pipeline + A/A
    python evidence/perf_runner.py --rows 2000 --only hires_  # 冒烟

协议（与 evidence/perf_protocol.json 一致，见 evidence/bench_protocol.py）：
    10 万行 × 每项 5 遍 × 去掉最大最小取中间 3 遍平均；DCG 与手写**交错**计时；
    每遍前 gc.collect()；外层重复 `bench_run.BATCH`(=5) 轮，取轮内相对极差最小的一轮
    （手写侧取同一轮）。判读硬规则：比值 < 1.10 不宣称快慢；与旧值的 Δ 在 ±10% 内
    一律记「持平（噪声内）」。

复用点（不复制实现，直接 import 仓库里的现有驱动/协议/基线）：
    evidence/bench_protocol.py  trimmed_mean / per_row / ratio / RUNS / ROWS / METHOD_NOTE
    evidence/bench_run.py       CASES(12 个 schema) / measure_case / _compile / BATCH / _spread
    evidence/bench_fast.py      手写对照 12 函数 + rows(n)
    evidence/bench.py           ORDER_PIPELINE / order_pipeline_hand / make_orders
                                （该模块 **不可 import**：顶层 `import bench_optimallib` 已缺失，
                                 故按 AST 只抽取需要的 4 个定义后 exec，见 _load_pipeline_parts）
    evidence/perf_protocol.json 12 项权威旧值

不修改仓库任何文件；只写 --out-dir（默认 _tmp/perf/）。本文件入库，是性能复跑的唯一入口。
"""
from __future__ import annotations

import argparse
import ast
import gc
import hashlib
import json
import math
import os
import platform
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EVID = os.path.join(ROOT, "evidence")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for p in (ROOT, EVID):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench_protocol as P      # noqa: E402
import bench_fast as F          # noqa: E402
import bench_run as R           # noqa: E402

#: order_pipeline 的旧值**不在** perf_protocol.json（README 标注「不在协议集内」），
#: 唯一出处是 README.md:2381 与 验收报告.md:347 的同一对数（µs/行）。
PIPELINE_OLD = {
    "dcg_us": 4.7674,
    "hand_us": 1.7421,
    "ratio": 2.7366,
    "source": "README.md:2381 / 验收报告.md:347（不在 perf_protocol.json 内）",
}

#: 判读阈值（硬规则）
NOISE_PCT = 10.0          # |Δ| < 10% → 持平（噪声内）
RATIO_CLAIM = 1.10        # 比值 < 1.10 不宣称快慢


# --------------------------------------------------------------------------- utils
def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def tree_fingerprint() -> dict:
    """给本次读数打上"哪个版本的 dcg/ 代码"的指纹，便于事后对账。"""
    out = {}
    d = os.path.join(ROOT, "dcg")
    for dirpath, _dirnames, filenames in os.walk(d):
        if "__pycache__" in dirpath:
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, ROOT).replace("\\", "/")] = hashlib.sha1(
                    f.read()).hexdigest()[:12]
    todo = [n for n in ("README.md", "验收报告.md", "DCG 接口实现细节文档.md")
            if os.path.exists(os.path.join(ROOT, n))]
    for n in todo:
        with open(os.path.join(ROOT, n), "rb") as f:
            out[n] = hashlib.sha1(f.read()).hexdigest()[:12]
    return out


def make_payload_readme(n):
    """README 附录 B.5 的 `make_payload(n)` 原样（**不 shuffle**）。

    为什么要自己写一份：README B.6/B.7 的 `order_pipeline` 旧值是在这份数据上测的，
    而 `evidence/bench.py:make_orders()` 末尾会 `random.shuffle`，两者 per-row 成本
    差 40% 量级（见台账 12_perf_harness.md §数据同源性）。与旧值对比必须同源。
    """
    orders = []
    for i in range(n):
        o = {
            "id": "ORD-%05d" % i,
            "qty": (i % 13),
            "price": float((i % 97) + 1) / 4,
            "item": {"sku": "SKU%04d" % (i % 501), "from": "URC", "to": "PEK"},
            "buyer": {"name": "user%d" % (i % 37)},
        }
        if i % 3 == 0:
            o["buyer"]["level"] = "VIP"
        if i % 7 == 0:
            o["buyer"]["note"] = "gift"
        orders.append(o)
    return {"payload": {"orders": orders}}


def load_old() -> dict:
    p = os.path.join(EVID, "perf_protocol.json")
    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    old = {r["name"]: r for r in doc["rows"]}
    return {"protocol": doc.get("protocol"), "generated_at": doc.get("generated_at"),
            "python": doc.get("python"), "rows": old}


# ----------------------------------------------------------------- order_pipeline
def _load_pipeline_parts(path: str | None = None) -> dict:
    """从 evidence/bench.py 抽 ORDER_PIPELINE / make_orders / order_pipeline_hand[_bare]。

    为什么不用 import：bench.py 顶层 `import bench_optimallib as olib`（第 62 行），
    该模块在仓库里已不存在 → `import bench` 直接 ModuleNotFoundError。
    抽 AST 节点再 exec，等价于只取那几段源码，不触发其余顶层副作用。
    """
    path = path or os.path.join(EVID, "bench.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, path)
    want_funcs = {"make_orders", "order_pipeline_hand", "order_pipeline_hand_bare"}
    want_names = {"ORDER_PIPELINE", "SEED"}
    body = []
    for node in tree.body:                       # 保持源码顺序（SEED 在 make_orders 之前）
        if isinstance(node, ast.FunctionDef) and node.name in want_funcs:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want_names for t in node.targets):
            body.append(node)
    mod = ast.Module(body=body, type_ignores=[])
    ns = {"random": random, "math": math}
    exec(compile(mod, path, "exec"), ns)
    missing = (want_funcs | want_names) - set(ns)
    if missing:
        raise RuntimeError("evidence/bench.py 里找不到: %s" % sorted(missing))
    return ns


# ---------------------------------------------------------------------- 计时内核
def measure(name, dcg_run, hand, data, src="", n=None, aa=False):
    """bench_run.measure_case 的同构实现，但接受任意 (dcg_run, hand) 组合。"""
    n = len(data) if n is None else n
    for _ in range(2):                     # 预热：编译器专门化 + cache 热起来
        dcg_run(data)
        hand(data)

    runs = P.RUNS
    best = None
    for _ in range(R.BATCH):
        dcg_samples, hand_samples = [], []
        for _ in range(runs):
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
        score = max(R._spread(dcg_samples), R._spread(hand_samples))
        if best is None or score < best[0]:
            best = (score, dcg_samples, hand_samples)
    spread, dcg_samples, hand_samples = best

    dcg_mean = P.trimmed_mean(dcg_samples)
    hand_mean = P.trimmed_mean(hand_samples)
    return {
        "name": name,
        "n": n,
        "runs": runs,
        "batch": R.BATCH,
        "aa": aa,
        "dcg_s": dcg_mean,
        "hand_s": hand_mean,
        "dcg_us": P.per_row(dcg_mean, n),
        "hand_us": P.per_row(hand_mean, n),
        "ratio": P.ratio(dcg_mean, hand_mean),
        "spread_dcg": spread,
        "dcg_samples_ms": [round(x * 1e3, 3) for x in dcg_samples],
        "hand_samples_ms": [round(x * 1e3, 3) for x in hand_samples],
        "src_len": len(src),
        "src_sha1": _sha(src),
    }


def pct(new, old):
    return None if not old else (new - old) / old * 100.0


def verdict(delta):
    if delta is None:
        return "无旧值"
    if abs(delta) < NOISE_PCT:
        return "持平（噪声内）"
    return "变慢" if delta > 0 else "变快"


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="DCG 性能回归 runner（可复跑）")
    ap.add_argument("--rows", type=int, default=P.ROWS, help="默认 100000（协议值）")
    ap.add_argument("--only", default="", help="逗号分隔的子串过滤（只影响 12 项）")
    ap.add_argument("--no-pipeline", action="store_true", help="跳过 order_pipeline")
    ap.add_argument("--pipeline-data", choices=("readme", "bench", "both"),
                    default="readme",
                    help="order_pipeline 的数据生成器：readme=B.5 不 shuffle（与旧值同源，默认）；"
                         "bench=bench.make_orders（shuffle）；both=两种都测")
    ap.add_argument("--no-aa", action="store_true", help="跳过 A/A 噪声底对照")
    ap.add_argument("--keep-src", action="store_true", help="JSON 里保留完整产物源码")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "_tmp", "perf"))
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    t_start = time.time()
    fp_start = tree_fingerprint()          # 必须在测量**之前**取：测量期间可能有人在改代码
    old = load_old()
    rows = args.rows
    data = F.rows(rows)                       # 12 项共用同一份 9 元组行数据
    names = [n for n in R.CASES]
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        names = [n for n in names if any(w in n for w in want)]

    results = []
    print("=" * 108)
    print("rows=%d  runs=%d  BATCH=%d  python=%s" % (
        rows, P.RUNS, R.BATCH, sys.version.split()[0]))
    print("旧值: evidence/perf_protocol.json (generated_at=%s, python=%s)" % (
        old["generated_at"], old["python"]))
    print("=" * 108)

    # ---- 12 项协议集：直接复用 bench_run.measure_case（它就是产出旧值的那个函数）
    for name in names:
        try:
            r = R.measure_case(name, data)
        except Exception as e:                                  # 代码在改，单项失败不中止
            results.append({"name": name, "error": "%s: %s" % (type(e).__name__, e)})
            print("%-18s  ERROR  %s" % (name, type(e).__name__))
            continue
        o = old["rows"].get(name, {})
        r["src_len"] = len(r.get("src") or "")
        r["src_sha1"] = _sha(r.get("src") or "")
        r["old"] = {k: o.get(k) for k in ("dcg_us", "hand_us", "ratio")}
        r["delta_dcg_pct"] = pct(r["dcg_us"], o.get("dcg_us"))
        r["delta_hand_pct"] = pct(r["hand_us"], o.get("hand_us"))
        r["delta_ratio_pct"] = pct(r["ratio"], o.get("ratio"))
        r["old_src_sha1"] = _sha(o["src"]) if o.get("src") else None
        r["src_changed"] = (r["old_src_sha1"] != r["src_sha1"])
        r["verdict"] = verdict(r["delta_dcg_pct"])
        if not args.keep_src:
            r.pop("src", None)
        results.append(r)

    # ---- order_pipeline（协议集外；旧值来自 README/验收报告）
    # 关键：README B.6/B.7 的旧值 4.7674 / 1.7421 是在 **README B.5 的 make_payload**
    # 上测的，那份生成器**不 shuffle**；而 evidence/bench.py 的 make_orders 末尾
    # `random.Random(seed).shuffle(orders)`。数据顺序对 per-row 成本影响很大
    # （实测：手写侧 1.67 → 2.40 µs/行，+43%），所以默认用与旧值同源的 readme 数据。
    pipeline_cmp = None
    if not args.no_pipeline:
        try:
            parts = _load_pipeline_parts()
            sch = parts["ORDER_PIPELINE"]
            src, dcg_run = R._compile(sch)
            hand = parts["order_pipeline_hand"]
        except Exception as e:
            results.append({"name": "order_pipeline",
                            "error": "%s: %s" % (type(e).__name__, e)})
            print("%-18s  ERROR  %s" % ("order_pipeline", type(e).__name__))
            parts = None
        if parts is not None:
            variants = []
            if args.pipeline_data in ("readme", "both"):
                variants.append(("order_pipeline",
                                 "README-B.5 make_payload（不 shuffle，与旧值同源）",
                                 False))
            if args.pipeline_data in ("bench", "both"):
                nm = "order_pipeline" if args.pipeline_data == "bench" else \
                    "order_pipeline@shuffled"
                variants.append((nm, "evidence/bench.py make_orders（shuffle）", True))
            for nm, label, shuffled in variants:
                try:
                    pay = (parts["make_orders"](rows) if shuffled
                           else make_payload_readme(rows))
                    eq = (dcg_run(pay) == hand(pay))     # 先验正确性，再比性能
                    r = measure(nm, dcg_run, hand, pay, src=src, n=rows)
                    r["equal_to_hand"] = eq
                    r["data"] = label
                    r["data_src"] = "shuffled" if shuffled else "sequential"
                    r["old"] = dict(PIPELINE_OLD)
                    r["delta_dcg_pct"] = pct(r["dcg_us"], PIPELINE_OLD["dcg_us"])
                    r["delta_hand_pct"] = pct(r["hand_us"], PIPELINE_OLD["hand_us"])
                    r["delta_ratio_pct"] = pct(r["ratio"], PIPELINE_OLD["ratio"])
                    r["src_changed"] = None
                    r["verdict"] = verdict(r["delta_dcg_pct"])
                    r["counts_for_regression"] = not shuffled
                    r["note"] = ("旧值口径：README.md:2381 / 验收报告.md:347（不在协议集内）"
                                 if not shuffled else
                                 "不同源（shuffle），Δ 仅供参考、不计入回归判定")
                    results.append(r)
                    if not shuffled:
                        pipeline_cmp = {"equal_to_hand": eq}
                    del pay
                    gc.collect()
                except Exception as e:
                    results.append({"name": nm,
                                    "error": "%s: %s" % (type(e).__name__, e)})
                    print("%-18s  ERROR  %s" % (nm, type(e).__name__))
    # ---- A/A 噪声底（同一产物 vs 自身；比值偏离 1 的幅度即本机噪声）
    aa_results = []
    if not args.no_aa:
        for name in [n for n in ("hires_filter", "agg_direct") if n in R.CASES]:
            if args.only and not any(w in name for w in
                                     [x.strip() for x in args.only.split(",") if x.strip()]):
                continue
            try:
                sch, _hand = R.CASES[name]()
                src, dcg_run = R._compile(sch)
                r = measure(name + "__AA", dcg_run, dcg_run, data, src=src, aa=True)
                r["aa_gap_pct"] = (r["ratio"] - 1.0) * 100.0
                aa_results.append(r)
            except Exception as e:
                aa_results.append({"name": name + "__AA",
                                   "error": "%s: %s" % (type(e).__name__, e)})

    # ---- 汇总
    ok = [r for r in results if "error" not in r]
    counted = [r for r in ok if r.get("counts_for_regression") is not False]
    regressions = [r["name"] for r in counted
                   if r.get("delta_dcg_pct") is not None and r["delta_dcg_pct"] >= NOISE_PCT]
    improvements = [r["name"] for r in counted
                    if r.get("delta_dcg_pct") is not None and r["delta_dcg_pct"] <= -NOISE_PCT]
    worst = max((r for r in counted if r.get("delta_dcg_pct") is not None),
                key=lambda r: r["delta_dcg_pct"], default=None)
    aa_gaps = [abs(a["aa_gap_pct"]) for a in aa_results if "aa_gap_pct" in a]
    fp_end = tree_fingerprint()
    summary = {
        "items": len(results),
        "errors": [r["name"] for r in results if "error" in r],
        "regressions_ge_10pct": regressions,
        "improvements_ge_10pct": improvements,
        "worst_delta_dcg_pct": None if worst is None else round(worst["delta_dcg_pct"], 2),
        "worst_item": None if worst is None else worst["name"],
        "aa_max_gap_pct": round(max(aa_gaps), 2) if aa_gaps else None,
        "pipeline_equal_to_hand": None if pipeline_cmp is None else pipeline_cmp["equal_to_hand"],
        "elapsed_s": round(time.time() - t_start, 1),
    }
    summary["verdict"] = ("无回归（全部 |Δ| < 10% 或变快）" if not summary["errors"]
                          and not regressions else
                          ("有 ERROR 项" if summary["errors"] else "存在变慢项"))

    payload = {
        "protocol": {"rows": rows, "runs": P.RUNS, "batch": R.BATCH,
                     "stat": "trimmed_mean(去掉最大最小)",
                     "interleaved": True, "gc_before_each": True,
                     "note": P.METHOD_NOTE},
        "reference": {"perf_protocol_json": {
            "generated_at": old["generated_at"], "python": old["python"],
            "protocol": old["protocol"]},
            "order_pipeline_old": PIPELINE_OLD},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "fingerprint": fp_start,
        "code_changed_during_run": sorted(
            k for k in set(fp_start) | set(fp_end)
            if fp_start.get(k) != fp_end.get(k)),
        "summary": summary,
        "rows": results,
        "aa": aa_results,
    }

    js = os.path.join(args.out_dir, "report_current.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    md = _markdown(payload)
    mp = os.path.join(args.out_dir, "report_current.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(md)

    # ---- 控制台
    print("\n%-16s %8s %8s %8s %7s | %7s %7s %7s | %s" % (
        "case", "old_us", "dcg_us", "hand_us", "ratio",
        "Δdcg%", "Δhand%", "Δratio%", "判读"))
    print("-" * 108)
    for r in results:
        if "error" in r:
            print("%-16s  ERROR: %s" % (r["name"], r["error"]))
            continue
        print("%-16s %8.4f %8.4f %8.4f %7.2f | %+7.1f %+7.1f %+7.1f | %s%s" % (
            r["name"], r["old"]["dcg_us"] or 0, r["dcg_us"], r["hand_us"], r["ratio"],
            r["delta_dcg_pct"] or 0.0, r["delta_hand_pct"] or 0.0,
            r["delta_ratio_pct"] or 0.0, r["verdict"],
            "" if r.get("src_changed") is not True else "  [产物已变]"))
    for a in aa_results:
        if "error" in a:
            print("A/A %-12s ERROR: %s" % (a["name"], a["error"]))
        else:
            print("A/A %-12s dcg=%8.4f hand=%8.4f ratio=%5.3f  偏离=%+.2f%%"
                  % (a["name"], a["dcg_us"], a["hand_us"], a["ratio"], a["aa_gap_pct"]))
    print("-" * 108)
    print("结论: %s | 变慢项: %s | A/A 最大偏离: %s%% | 用时 %ss" % (
        summary["verdict"], summary["regressions_ge_10pct"] or "无",
        summary["aa_max_gap_pct"], summary["elapsed_s"]))
    print("wrote", js)
    print("wrote", mp)
    return 0


def _markdown(payload) -> str:
    s = payload["summary"]
    L = []
    L.append("# DCG 性能回归读数（当前树）\n")
    L.append("- 生成时间：`%s`" % payload["generated_at"])
    L.append("- Python：`%s`  CPU：`%s`" % (payload["python"], payload["cpu"]))
    L.append("- 协议：rows=%d × 每项 %d 遍去最大最小 × 外层 BATCH=%d 轮取最干净一轮；"
             "DCG/手写交错，每遍前 `gc.collect()`" % (
                 payload["protocol"]["rows"], payload["protocol"]["runs"],
                 payload["protocol"]["batch"]))
    L.append("- 旧值：`evidence/perf_protocol.json`（generated_at `%s`，python `%s`）"
             % (payload["reference"]["perf_protocol_json"]["generated_at"],
                payload["reference"]["perf_protocol_json"]["python"]))
    L.append("- `order_pipeline` 旧值来自 README.md:2381 / 验收报告.md:347（不在协议集内）")
    L.append("- 判读硬规则：比值 < 1.10 不宣称快慢；Δ 在 ±10% 内记「持平（噪声内）」\n")
    L.append("| 负载 | 旧 DCG µs/行 | 新 DCG µs/行 | 手写 µs/行 | 比值 | ΔDCG | 判读 |")
    L.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in payload["rows"]:
        if "error" in r:
            L.append("| `%s` | — | — | — | — | — | **ERROR**：`%s` |"
                     % (r["name"], r["error"]))
            continue
        od = r["old"]["dcg_us"]
        L.append("| `%s` | %s | %.4f | %.4f | %.2f× | %+.1f%% | %s%s |" % (
            r["name"], ("%.4f" % od) if od else "—", r["dcg_us"], r["hand_us"],
            r["ratio"], r["delta_dcg_pct"] or 0.0, r["verdict"],
            "" if r.get("src_changed") is not True else "（产物已变）"))
    if payload["aa"]:
        L.append("\n**A/A 噪声底**（同一产物跑两遍，比值偏离 1 的幅度）：\n")
        L.append("| 探针 | 比值 | 偏离 |")
        L.append("| --- | ---: | ---: |")
        for a in payload["aa"]:
            if "error" in a:
                L.append("| `%s` | — | ERROR `%s` |" % (a["name"], a["error"]))
            else:
                L.append("| `%s` | %.3f | %+.2f%% |" % (a["name"], a["ratio"], a["aa_gap_pct"]))
    L.append("\n**结论**：%s" % s["verdict"])
    L.append("- 变慢 ≥10%%：%s" % (s["regressions_ge_10pct"] or "无"))
    L.append("- 变快 ≥10%%：%s" % (s["improvements_ge_10pct"] or "无"))
    L.append("- 最差项：`%s` %+.2f%%" % (s["worst_item"], s["worst_delta_dcg_pct"] or 0.0))
    L.append("- ERROR 项：%s" % (s["errors"] or "无"))
    L.append("- A/A 最大偏离：%s%%" % s["aa_max_gap_pct"])
    L.append("- order_pipeline 与手写逐行相等：%s" % s["pipeline_equal_to_hand"])
    L.append("- 用时：%ss" % s["elapsed_s"])
    if payload.get("code_changed_during_run"):
        L.append("- ⚠️ 测量期间代码被改动（读数跨版本，需重跑）：%s"
                 % ", ".join("`%s`" % x for x in payload["code_changed_during_run"]))
    L.append("\n指纹（`dcg/*.py` 与三份文档的 sha1[:12]）：\n")
    L.append("```json")
    L.append(json.dumps(payload["fingerprint"], ensure_ascii=False, indent=1))
    L.append("```")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
