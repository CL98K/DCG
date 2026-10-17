"""Per-example v1 (`format.py` examples) vs v2 (`dcg.generate`) benchmark (task-5).

Methodology (bench/bench_compare.md §7 + the Lead's ruling, reused unchanged)
----------------------------------------------------------------------------
* same machine, same data, same process; `gc.collect()` before every timed rep;
  warm up, then report the MINIMUM of the reps -- never a single rep.
* symmetrical settle sweep on BOTH sides (2 untimed passes), then adaptive
  top-up: if the spread of the timed reps is above 5%, add another reps-sized
  round and report the rep count, per the Lead's rule 5.
* the primary verdict is the **relative v1/v2 ratio measured in this process**,
  never a subtraction of absolute numbers taken in another session.

What is being compared
----------------------
`v1 side`: the `describe` frozen from `format.py:577-816`, executed through
`bench/_v1bridge.py`, with `CYTHON_MODE=False` and `TRYE_MODE=False` forced.
Those two flags are neutralised on purpose:
  * `CYTHON_MODE=True` is v1's default and is *broken* for examples 2/3/4
    (`TypeError: Expected list, got int|tuple`; with `TRYE_MODE=True` the rows are
    silently swallowed into the error bucket) -- timing it would time a data-loss
    path, not an engine;
  * `TRYE_MODE=False` removes v1's per-row `try/except` wrapper, which is what the
    frozen 0.480 us/row baseline was measured with.
`FREE_MODE` is left at the value the example itself chose (it changes v1's code
shape, so overriding it would benchmark a configuration no example used).

`v2 side`: the schema from `tests/examples/v1_parity/schemas.py` that re-implements
the same example, run through the public `dcg.generate` -> exec -> `run`.

Product shapes are NOT identical and this script says so everywhere: v1 returns
`{'doc': [rows]}` while v2's `shape='list'` returns `[rows]`.  The per-row field
values are compared by the parity tests; this script compares engine cost.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import gc
import io
import json
import os
import platform
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _workload import bench_reps, v2_ready  # noqa: E402
from _v1bridge import load_v1_engine  # noqa: E402

EVIDENCE = os.path.join(ROOT, "evidence", "examples")
FROZEN = os.path.join(EVIDENCE, "v1_examples_frozen.json")

# 产物命名（Lead m01011 ① / m01149，对齐 `dcg.build(..., overwrite=False)`）：
#   * `--final` **必须**带 `--tag` -> `bench_examples_final_<tag>_{raw.txt,json}`
#   * 只带 tag                   -> `bench_examples_<tag>_{raw.txt,json}`
#   * 都不带                     -> `bench_examples_{raw.txt,json}`（默认名，tag 视作空串）
#   * 目标文件已存在 -> **拒绝覆盖**，除非显式 `--overwrite`
# 背景（两个真实事故）：同一次窗口内 Run B 的 JSON 被 Run C 覆盖；provisional 的
# 00:48:12 那次被 00:51:03 覆盖。测出来的数丢了比没测出来更糟 —— 固定文件名不许再用。
LEGACY_FINAL_TXT = os.path.join(EVIDENCE, "bench_examples_final_raw.txt")
LEGACY_FINAL_JSON = os.path.join(EVIDENCE, "bench_examples_final.json")


def output_paths(tag=None, final=False):
    """返回 (txt, json) 目标路径。tag=None 等价于空 tag（默认名）。"""
    stem = "bench_examples"
    if final:
        stem += "_final"
    if tag:
        stem += "_%s" % tag
    return (os.path.join(EVIDENCE, stem + "_raw.txt"),
            os.path.join(EVIDENCE, stem + ".json"))


def refuse_overwrite(paths, overwrite):
    """同 tag 拒绝覆盖；返回已存在的文件列表（空列表 = 可以写）。"""
    if overwrite:
        return []
    return [p for p in paths if os.path.exists(p)]


# 最终数字必须钉死到生成器源码的 sha1（Lead 静默窗口流程第 3 条；m01149 追加
# 「测量前后各取一次并断言 revision_stable_during_run」）。
SHA_FILES = ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py", "dcg/jx/compiler.py")


def source_sha1():
    """本次测量所对应的 v2 生成器源码 sha1（缺文件如实记为 MISSING，不静默跳过）。"""
    import hashlib
    out = {}
    for rel in SHA_FILES:
        path = os.path.join(ROOT, *rel.split("/"))
        try:
            with io.open(path, "rb") as fh:
                out[rel] = hashlib.sha1(fh.read()).hexdigest()
        except OSError as exc:
            out[rel] = "MISSING (%s)" % exc
    return out

N_DEFAULT = 200_000
REPS = 3
MAX_REPS = 12
SETTLE = 2
SPREAD_LIMIT = 0.05
# CONTRACT §5 的效率承诺口径（与 `bench/bench_row.py:33::RATIO_LIMIT_VS_REF` 同一个数）
RATIO_LIMIT_VS_REF = 1.05

_SCHEMAS_SRC = os.path.join(ROOT, "tests", "examples")
if _SCHEMAS_SRC not in sys.path:
    sys.path.insert(0, _SCHEMAS_SRC)


# ---------------------------------------------------------------------------
# frozen v1 record
# ---------------------------------------------------------------------------
def frozen_chunks():
    with io.open(FROZEN, encoding="utf-8") as fh:
        return {c["index"]: c for c in json.load(fh)["chunks"]}


def _parse(text):
    import ast
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        import datetime as _dt
        return eval(text, {"datetime": _dt, "__builtins__": {}}, {})  # noqa: S307


# ---------------------------------------------------------------------------
# v1 side
# ---------------------------------------------------------------------------
def v1_flags(rec):
    """The v1 MODE for this example, with the two defect-prone flags neutralised."""
    ex = dict(rec.get("modes_set_by_example") or {})
    return {
        "OPT_MODE": True,
        "DEBUG": False,
        "CYTHON_MODE": False,
        "SAFE_MODE": False,
        "TRYE_MODE": False,
        "FREE_MODE": bool(ex.get("FREE_MODE", False)),
    }


def v1_parse(label, describe, flags):
    """Build (handler, src) for one describe, with `flags` applied to MODE."""
    fmt = load_v1_engine()
    F = fmt.Format
    for k in ("OPT_MODE", "DEBUG", "CYTHON_MODE", "SAFE_MODE", "TRYE_MODE", "FREE_MODE"):
        setattr(F, k, bool(flags.get(k, False)))
    import builtins
    grabbed = []
    orig = builtins.compile

    def spy(src, *a, **k):
        if isinstance(src, str) and "_inline_call_" in src:
            grabbed.append(src)
        return orig(src, *a, **k)

    builtins.compile = spy
    try:
        info, handler = F.parse(label, copy.deepcopy(describe))
    finally:
        builtins.compile = orig
    return handler, (grabbed[-1] if grabbed else "")


def v1_describe_for(rec, index):
    """The describe this script benchmarks for one example.

    examples 1/3/5 -> the frozen describe verbatim (its own field set)
    example  2    -> the frozen describe restricted to the `docs` struct group,
                     i.e. exactly the 15 fields the v2 schema re-implements
    example  4    -> the frozen describe with `protocol` forced True so both sides
                     emit positional rows (v2 has no dict-row shape); the
                     as-written dict-row output is reported separately
    """
    d = copy.deepcopy(_parse(rec["describe"]))
    d.setdefault("datas", "")
    d.setdefault("filter", "")
    d.setdefault("_field", {})
    if index == 2:
        struct = d["struct"]
        d["struct"] = {"docs": struct["docs"]}
    if index == 4:
        d["protocol"] = True
    return d


V1_LABEL = {1: "EX1", 2: "EX2", 3: "EX3", 4: "EX4", 5: "EX5", 6: "EX6"}


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------
def payload_for(rec, n):
    """Scale the example's own 10-row payload up to `n` rows.

    The rows themselves are the frozen ones, cycled -- identical bytes on the v1
    and v2 side, so the comparison is engine cost only.  Returns (payload, n_rows).
    """
    datas = _parse(rec["datas"])
    if isinstance(datas, dict) and "index" in datas:
        tmpl = datas["index"]
        rows = (list(tmpl) * (n // max(1, len(tmpl)) + 1))[:n]
        return {"index": rows}, len(rows)
    if isinstance(datas, list):
        tmpl = datas
        rows = (list(tmpl) * (n // max(1, len(tmpl)) + 1))[:n]
        return rows, len(rows)
    raise AssertionError("example %s has no usable payload" % rec["index"])


# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------
def measure(fn, settle=SETTLE, reps=REPS, max_reps=MAX_REPS):
    """Symmetric settle sweep + adaptive top-up (see module docstring)."""
    for _ in range(settle):
        fn()
    rounds = []
    lo = hi = 0.0
    while True:
        rounds.extend(bench_reps(fn, reps=reps, warmup=False))
        lo, hi = min(rounds), max(rounds)
        spread = (hi - lo) / lo if lo else float("inf")
        if spread <= SPREAD_LIMIT or len(rounds) >= max_reps:
            break
    return {"min": lo, "max": hi, "spread": spread, "reps": len(rounds),
            "settle": settle, "reps_s": [round(r, 6) for r in rounds]}


def process_table():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=60, check=False)
    except Exception as e:  # pragma: no cover - environment dependent
        return ["<tasklist unavailable: %s: %s>" % (type(e).__name__, e)]
    lines = [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]
    return lines or ["<no python.exe rows returned>"]


# ---------------------------------------------------------------------------
# v2 side
# ---------------------------------------------------------------------------
def v2_handler(schema):
    import dcg
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<generated %s>" % schema.get("name", "?"), "exec"), ns)  # noqa: S102
    return ns["run"], src


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_plan(n):
    """One plan entry per example; unavailable sides are recorded, not hidden."""
    from v1_parity import schemas
    chunks = frozen_chunks()
    plan = []
    for index in (1, 2, 3, 4, 5, 6):
        rec = chunks[index]
        entry = {"index": index, "label": V1_LABEL[index],
                 "format_py_lines": rec["format_py_lines"], "n": n,
                 "v1": None, "v2": None, "notes": []}
        if index == 6:
            entry["notes"].append(
                "v1 never produced a product: the index template calls `chain`, which "
                "format.py never imports (NameError), and the example has no `datas`")
            entry["v2"] = None
            entry["v2_note"] = ("not transliterated: v1's index shape does not exist in "
                               "v2's `shape` set (list/dict/index have different meanings)")
            plan.append(entry)
            continue
        schema = schemas.by_name().get({1: "ex1", 2: "ex2_docs", 3: "ex3", 4: "ex4"}.get(index))
        if schema is None:
            # example 5: v2 cannot express `$.data.ID` (two symbol segments, §6)
            entry["v2_note"] = "v2 Unsupported: `$.data.ID` has 2 symbol segments"
            entry["v1"] = {"describe": v1_describe_for(rec, index), "flags": v1_flags(rec)}
            entry["notes"].append(
                "kept in the table as `v2 N/A` so the migration gap is visible; excluded "
                "from every ratio")
            plan.append(entry)
            continue
        if index == 4:
            entry["notes"].append(
                "v1 `protocol` forced True for the ratio (v2 has no dict-row shape); "
                "the as-written dict-row form is a recorded product-shape difference")
        if index == 2:
            entry["notes"].append(
                "v1 describe restricted to the `docs` group = the same 15 fields the v2 "
                "schema implements")
        entry["v1"] = {"describe": v1_describe_for(rec, index), "flags": v1_flags(rec)}
        entry["v2"] = schema
        plan.append(entry)
    return plan


def run_once(plan, label):
    report = {"when": datetime.datetime.now().astimezone().isoformat(),
              "python": sys.version, "platform": platform.platform(),
              "cpu_count": os.cpu_count(), "n": plan[0]["n"], "label": label,
              "processes": process_table(), "examples": [], "errors": []}
    chunks = frozen_chunks()
    for entry in plan:
        index = entry["index"]
        rec = chunks[index]
        row = {"index": index, "label": entry["label"],
               "format_py_lines": entry["format_py_lines"], "notes": entry["notes"],
               "v1_note": None, "v2_note": entry.get("v2_note")}
        if index == 6:
            report["examples"].append(row)
            continue
        payload, n_rows = payload_for(rec, entry["n"])
        row["rows"] = n_rows
        if entry["v1"] is not None:
            try:
                describe, flags = entry["v1"]["describe"], entry["v1"]["flags"]
                handler, src = v1_parse(entry["label"], describe, flags)
                row["v1"] = measure(lambda h=handler, p=payload: h(p))
                row["v1"]["flags"] = flags
                row["v1"]["src_chars"] = len(src)
                row["v1_product"] = "dict of frame -> rows (TRYE_MODE=False)"
            except Exception as e:
                row["v1"] = None
                row["v1_note"] = "v1 N/A: %s: %s" % (type(e).__name__, e)
                report["errors"].append("ex%s v1: %s: %s" % (index, type(e).__name__, e))
        else:
            row["v1"] = None
            row["v1_note"] = entry.get("v1_note") or "v1 N/A"
        if entry["v2"] is not None:
            try:
                run, src = v2_handler(entry["v2"])
                row["v2"] = measure(lambda r=run, p=payload: r(p))
                row["v2"]["src_chars"] = len(src)
                row["v2_product"] = "list of rows (shape='list')"
            except Exception as e:
                row["v2"] = None
                row["v2_note"] = "v2 N/A: %s: %s" % (type(e).__name__, e)
                report["errors"].append("ex%s v2: %s: %s" % (index, type(e).__name__, e))
        else:
            row["v2"] = None
        report["examples"].append(row)
    # weighted total over examples where BOTH sides ran (same N each)
    both = [r for r in report["examples"] if r.get("v1") and r.get("v2")]
    if both:
        tr = sum(r["rows"] for r in both)
        t1 = sum(r["v1"]["min"] for r in both)
        t2 = sum(r["v2"]["min"] for r in both)
        report["total"] = {
            "examples": [r["label"] for r in both],
            "rows": tr,
            "v1_min_s": t1, "v2_min_s": t2,
            "v1_us_per_row": t1 / tr * 1e6, "v2_us_per_row": t2 / tr * 1e6,
            "v1_rows_per_s": tr / t1, "v2_rows_per_s": tr / t2,
            "ratio_v1_over_v2": t1 / t2,
        }
    return report


# ---------------------------------------------------------------------------
# CONTRACT §5 workload: hand-written reference vs v1 vs v2 (same order control)
#
# The per-example table above answers "porting the six v1 examples, how do the
# two engines compare".  The acceptance criterion is a different question:
# "is v2 within 1.05x of a hand-written plain-Python loop" (CONTRACT §5), on the
# frozen 8-field M0 workload (`bench/_workload.py`: `ref_tight` / `V1_DESCRIBE` /
# `V2_SCHEMA`).  Both questions share one methodology, so the reference is
# measured here, in the same process, under the same settle/rep control.
# ---------------------------------------------------------------------------
CPU_BLOCKS = 3              # Lead m01149: ≥3 block，用于 blocks_disagree_in_sign
CPU_NOISE_FLOOR = 0.0506    # Lead m01149 本机 A/A 标定（worst |ratio-1|），仅在 A/A 跑失败时兜底


def _sec5_verdict(ratios, target=RATIO_LIMIT_VS_REF, floor=None):
    """CONTRACT §7.1 判决规则（Lead m01149，逐字实现）：

    `margin = |ratio - target|`；`blocks_disagree_in_sign -> INCONCLUSIVE`；
    `margin < noise_floor -> WITHIN_NOISE`；否则 `RESOLVED`。
    `ratio` 取**最保守**的那个 block（max，即 v2 相对最慢的一轮）。
    """
    worst = max(ratios)
    passed = [r <= target for r in ratios]
    margin = abs(worst - target)
    out = {"ratios_per_block": ratios, "worst_ratio": worst, "target": target,
           "noise_floor": floor, "margin": margin, "all_blocks_pass": all(passed)}
    if len(set(passed)) > 1:
        out["verdict"] = "INCONCLUSIVE"
        out["reason"] = "blocks_disagree_in_sign"
    elif margin < floor:
        out["verdict"] = "WITHIN_NOISE"
        out["reason"] = "margin %.4f < noise_floor %.4f" % (margin, floor)
    else:
        out["verdict"] = "RESOLVED"
        out["reason"] = "margin %.4f >= noise_floor %.4f" % (margin, floor)
    out["pass"] = bool(out["verdict"] == "RESOLVED" and out["all_blocks_pass"])
    return out


def hand_block(n):
    """CONTRACT §5：真产物 vs 手写参照，**CPU 时间口径**（CONTRACT §7.1 / Lead m01149）。

    计时器**只能**来自 `bench/bench_row.py::measure_cpu` / `measure_interleaved_cpu`
    —— 这里只 import，绝不另写一份（本项目吃过双实现漂移的亏：`dcg/runtime.py` 的
    `HELPERS` vs `SOURCE` 两份实现对拍出 1942 处不一致；测出来的数不可信比测不出来更糟）。
    """
    from _workload import (make_rows, ref_tight, load_v1, load_v2, v1_datas,
                           v2_input, V1_DESCRIBE, V2_SCHEMA)
    from bench_row import CPU_K, CPU_REPS, measure_interleaved_cpu  # 唯一实现处

    rows = make_rows(n)
    datas = v1_datas(rows)
    v1_handler, _info = load_v1(V1_DESCRIBE, "M0")
    v2_handler, v2_src = load_v2(V2_SCHEMA)

    def ref():
        return ref_tight(datas)

    def v1():
        return v1_handler(datas)

    def v2():
        return v2_handler(v2_input(rows, V2_SCHEMA))

    def _rows(res):
        # v1 和参照把行包在容器 dict 里；v2 的 `shape: list` 直接返回行。
        return list(res.values())[0] if isinstance(res, dict) else res

    # 先验产物再报比值：拿不同的工作负载比时间，是最经典的"发一个没意义的比值"。
    r_ref, r_v1, r_v2 = _rows(ref()), _rows(v1()), _rows(v2())
    out = {"n": n, "workload": "CONTRACT §5 M0 (8 fields)",
           "metric": "time.process_time(), k=%d batched passes per timing, interleaved, "
                     "min of %d reps" % (CPU_K, CPU_REPS),
           "k": CPU_K, "reps": CPU_REPS, "blocks": CPU_BLOCKS,
           "rows_ref": len(r_ref), "rows_v1": len(r_v1), "rows_v2": len(r_v2),
           "equal_ref_v1": r_ref == r_v1, "equal_ref_v2": r_ref == r_v2,
           "v2_source_chars": len(v2_src)}

    variants = [("hand", ref), ("v1", v1), ("v2", v2)]
    per_block = [measure_interleaved_cpu(variants, n, reps=CPU_REPS, k=CPU_K)
                 for _ in range(CPU_BLOCKS)]

    # A/A 噪声底：**同一个 callable** 放进两个 slot（Lead m01149 要求必跑，≥3 block）。
    # 若两个逐字相同的函数都能给出远离 1.0 的"比值"，5% 阈值就不可判定 —— 这正是
    # wall-clock 口径被判 INCONCLUSIVE 的实验依据。
    try:
        aa = [measure_interleaved_cpu([("slotA", ref), ("slotB", ref)], n,
                                      reps=CPU_REPS, k=CPU_K)
              for _ in range(CPU_BLOCKS)]
        aa_ratios = [b["slotB"][0] / b["slotA"][0] for b in aa]
        floor = max(abs(r - 1.0) for r in aa_ratios)
        out["aa_noise_floor"] = {"ratios_per_block": aa_ratios,
                                 "worst_abs_dev_from_1": floor,
                                 "fallback_used": False}
    except Exception as e:  # noqa: BLE001 - 兜底值必须显式标记，不静默
        floor = CPU_NOISE_FLOOR
        out["aa_noise_floor"] = {"ratios_per_block": [],
                                 "worst_abs_dev_from_1": floor,
                                 "fallback_used": True,
                                 "error": "%s: %s" % (type(e).__name__, e)}

    for name in ("hand", "v1", "v2"):
        mins = [blk[name][0] for blk in per_block]
        if min(mins) <= 0.0:
            # Windows `process_time()` 粒度 ~15.6 ms：n 太小的时候 k 遍跑完还不到一个 tick，
            # dt 直接是 0.0。这不是"很快"，是**没有分辨率** —— 必须显式报错，不许拿 0 去算比值。
            raise RuntimeError(
                "CPU 口径在 n=%d 上没有分辨率：%s 的某 block dt=0.0 "
                "(k=%d 遍 < Windows ~15.6ms 计时粒度)。把 --n 提到 >=50000（终值用 200000）。"
                % (n, name, CPU_K))
        # 单位注意：`bench_row.measure_interleaved_cpu` 返回的**已经是 µs/行**
        # （内部 `dt/(k*n)*1e6`）。曾经在这里又除了一次 n，把绝对值放大 5 倍 —— 比值不受影响
        # （分子分母同源），但 us/row 与 rows/s 两列会错，属于"数字看着合理但没意义"。
        us = min(mins)
        out[name] = {"us_per_row": us, "us_per_row_per_block": mins,
                     "min_s": us * n / 1e6, "rows_per_s": 1e6 / us,
                     "spread_min_pct": min(blk[name][2] for blk in per_block),
                     "spread_max_pct": max(blk[name][2] for blk in per_block),
                     "reps": len(per_block[0][name][1])}

    r1 = [blk["v1"][0] / blk["hand"][0] for blk in per_block]
    r2 = [blk["v2"][0] / blk["hand"][0] for blk in per_block]
    out["ratio_v1_over_ref_per_block"] = r1
    out["ratio_v2_over_ref_per_block"] = r2
    out["ratio_v1_over_ref"] = max(r1)
    out["ratio_v2_over_ref"] = max(r2)
    out["verdict_v2"] = _sec5_verdict(r2, RATIO_LIMIT_VS_REF, floor)
    out["verdict_v1"] = _sec5_verdict(r1, RATIO_LIMIT_VS_REF, floor)
    out["sec5_target_met"] = bool(out["equal_ref_v2"] and out["verdict_v2"]["pass"])
    # 三态标签（**不是** 布尔）：Lead m01149 的判决规则里 `WITHIN_NOISE` 是一个合法出口，
    # 而它可能与"三个 block 全部 ≤1.05"同时成立（margin 落在噪声底以内）。把这种情况标成
    # "NOT MET" 会误导读者 —— 故绝对不把它和"真的没达标"合并。
    v2 = out["verdict_v2"]
    if not out["equal_ref_v2"]:
        out["sec5_label"] = "INVALID (v2 产物与手写参照不逐行相等)"
    elif v2["verdict"] == "RESOLVED" and v2["all_blocks_pass"]:
        out["sec5_label"] = "MET (RESOLVED; all %d blocks <= 1.05)" % len(r2)
    elif v2["verdict"] == "RESOLVED":
        out["sec5_label"] = "NOT MET (RESOLVED; blocks = %s)" % " ".join("%.3f" % x for x in r2)
    else:
        out["sec5_label"] = "%s (blocks = %s; all_blocks_pass=%s)" % (
            v2["verdict"], " ".join("%.3f" % x for x in r2), v2["all_blocks_pass"])
    return out


def fmt_us(d, n):
    return d / n * 1e6


def table(report, provisional):
    tag = " (provisional)" if provisional else ""
    L = []
    L.append("=" * 100)
    L.append("DCG example benchmark: v1 format.py examples vs v2 dcg.generate%s" % tag)
    L.append("when  : %s" % report["when"])
    L.append("python: %s" % report["python"].splitlines()[0])
    for rel, digest in (report.get("sha1") or {}).items():
        L.append("sha1  : %s  %s" % (digest, rel))
    L.append("N     : %d rows per example (cycled from each example's own frozen rows)" % report["n"])
    L.append("label : %s   [requested=%r  final=%s  tag=%s]"
             % (report.get("label_actual", report.get("label")), report.get("label_requested"),
                report.get("final"), report.get("tag")))
    L.append("method: WALL-CLOCK for the per-example table -- settle %d untimed passes, "
             "gc.collect() per rep, MIN of reps, adaptive top-up while spread > %.0f%% (max %d reps). "
             "The CONTRACT §5 block at the bottom uses the CPU metric instead."
             % (SETTLE, SPREAD_LIMIT * 100, MAX_REPS))
    L.append("stable: revision_stable_during_run = %s" % report.get("revision_stable_during_run"))
    L.append("=" * 100)
    head = ("%-5s %8s %10s %9s %12s %8s %10s %9s %12s %8s %9s"
            % ("ex", "rows", "v1 us/row", "v1 rows/s", "v1 min(s)", "v1 spr",
               "v2 us/row", "v2 rows/s", "v2 min(s)", "v2 spr", "v1/v2"))
    L.append(head)
    L.append("-" * 100)
    for r in report["examples"]:
        n = r.get("rows") or report["n"]
        v1, v2 = r.get("v1"), r.get("v2")
        if v1 and v2:
            L.append("%-5s %8d %10.3f %9.0f %12.4f %7.1f%% %10.3f %9.0f %12.4f %7.1f%% %8.3f"
                     % (r["label"], n, fmt_us(v1["min"], n), n / v1["min"],
                        v1["min"], v1["spread"] * 100, fmt_us(v2["min"], n),
                        n / v2["min"], v2["min"], v2["spread"] * 100,
                        v1["min"] / v2["min"]))
        else:
            L.append("%-5s %8d %10s %9s %12s %8s %10s %9s %12s %8s %9s"
                     % (r["label"], n,
                        "%.3f" % fmt_us(v1["min"], n) if v1 else "n/a",
                        "%.0f" % (n / v1["min"]) if v1 else "n/a",
                        "%.4f" % v1["min"] if v1 else "n/a",
                        "%.1f%%" % (v1["spread"] * 100) if v1 else "n/a",
                        "%.3f" % fmt_us(v2["min"], n) if v2 else "n/a",
                        "%.0f" % (n / v2["min"]) if v2 else "n/a",
                        "%.4f" % v2["min"] if v2 else "n/a",
                        "%.1f%%" % (v2["spread"] * 100) if v2 else "n/a",
                        "%.3f" % (v1["min"] / v2["min"]) if (v1 and v2) else "n/a"))
    L.append("-" * 100)
    if "total" in report:
        t = report["total"]
        L.append("weighted total over %s (same N per example, %d rows):"
                 % (",".join(t["examples"]), t["rows"]))
        L.append("  v1 %.3f us/row  %.0f rows/s  (min total %.4f s)"
                 % (t["v1_us_per_row"], t["v1_rows_per_s"], t["v1_min_s"]))
        L.append("  v2 %.3f us/row  %.0f rows/s  (min total %.4f s)"
                 % (t["v2_us_per_row"], t["v2_rows_per_s"], t["v2_min_s"]))
        L.append("  ratio v1/v2 = %.3f  (>1 means v2 is faster)" % t["ratio_v1_over_v2"])
    h = report.get("hand")
    if h:
        n = h["n"]
        L.append("-" * 100)
        L.append("CONTRACT §5 workload (%s), **CPU-clock** metric (CONTRACT §7.1):" % h["workload"])
        L.append("  %s -- 计时器 = bench/bench_row.py::measure_interleaved_cpu（唯一实现）" % h["metric"])
        L.append("  %-8s %10s %12s %10s %16s" % ("variant", "us/row", "rows/s", "min(s)", "spread/blk"))
        for name, label in (("hand", "hand"), ("v1", "v1"), ("v2", "v2")):
            d = h[name]
            L.append("  %-8s %10.3f %12.0f %10.4f %6.1f%%-%.1f%%"
                     % (label, d["us_per_row"], d["rows_per_s"], d["min_s"],
                        d["spread_min_pct"], d["spread_max_pct"]))
        L.append("  products equal to the hand reference: v1=%s  v2=%s  (rows %d/%d/%d)"
                 % (h["equal_ref_v1"], h["equal_ref_v2"], h["rows_ref"], h["rows_v1"], h["rows_v2"]))
        L.append("  x/hand per block: v1 = %s"
                 % " ".join("%.3f" % x for x in h["ratio_v1_over_ref_per_block"]))
        L.append("  x/hand per block: v2 = %s   (worst = %.3f)"
                 % (" ".join("%.3f" % x for x in h["ratio_v2_over_ref_per_block"]),
                    h["ratio_v2_over_ref"]))
        aa = h["aa_noise_floor"]
        L.append("  A/A noise floor (SAME callable, 2 slots, %d blocks): %s -> worst |ratio-1| = %.4f%s"
                 % (len(aa["ratios_per_block"]),
                    " ".join("%.3f" % x for x in aa["ratios_per_block"]) if aa["ratios_per_block"] else "n/a",
                    aa["worst_abs_dev_from_1"],
                    "  [FALLBACK -- A/A 没跑成: %s]" % aa.get("error") if aa.get("fallback_used") else ""))
        v = h["verdict_v2"]
        L.append("  §5 verdict  (target v2 <= 1.05x hand): %s  (%s; margin=%.4f, noise_floor=%.4f)"
                 % (v["verdict"], v["reason"], v["margin"], v["noise_floor"]))
        L.append("  §5 verdict  (same rule applied to v1): %s  (%s)" % (h["verdict_v1"]["verdict"],
                                                                        h["verdict_v1"]["reason"]))
        L.append("  §5 target v2 <= 1.05x: %s   [只报比值；绝对 µs/行 跨会话可漂 56%%，不可引用]"
                 % h["sec5_label"])
        L.append("     (sec5_target_met=%s 是布尔：只有 RESOLVED 且全 block 达标才 True；"
                 "WITHIN_NOISE 不是『没达标』，是『这条门在当前噪声底下判不出』)" % h["sec5_target_met"])
        L.append("  v2 source for this workload: %d chars" % h["v2_source_chars"])
    L.append("=" * 100)
    for r in report["examples"]:
        for note in r["notes"]:
            L.append("note %s: %s" % (r["label"], note))
        if r.get("v1_note"):
            L.append("note %s: %s" % (r["label"], r["v1_note"]))
        if r.get("v2_note"):
            L.append("note %s: %s" % (r["label"], r["v2_note"]))
    if report["errors"]:
        L.append("-" * 100)
        for e in report["errors"]:
            L.append("ERROR %s" % e)
    L.append("process table at measurement time (%d rows):" % len(report["processes"]))
    for p in report["processes"][:40]:
        L.append("  %s" % p)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=N_DEFAULT)
    ap.add_argument("--label", default=None,
                    help="报告里的状态标签；缺省由 --final 决定（FINAL / PROVISIONAL）")
    ap.add_argument("--final", action="store_true",
                    help="终值运行：必须与 --tag 连用，写 bench_examples_final_<tag>_*")
    ap.add_argument("--only", type=int, default=None, help="benchmark a single example")
    ap.add_argument("--tag", default=None,
                    help="产物名后缀 -> bench_examples[_final]_<tag>_{raw.txt,json}；同 tag 拒绝覆盖")
    ap.add_argument("--overwrite", action="store_true",
                    help="允许覆盖同名产物（默认拒绝，对齐 dcg.build(overwrite=False)）")
    args = ap.parse_args(argv)

    if args.final and not args.tag:
        print("--final 必须带 --tag（例：--final --tag W3-2026-10-17T01-30）。")
        print("固定文件名会让同一次窗口的多次运行互相覆盖 —— 见 bench/bench_examples.md §6.1b。")
        return 2
    out_txt, out_json = output_paths(args.tag, args.final)
    existing = refuse_overwrite((out_txt, out_json), args.overwrite)
    if existing:
        print("拒绝覆盖既有产物（同一个 tag 不重跑）：")
        for p in existing:
            print("  %s" % p)
        print("换一个 --tag，或显式加 --overwrite。")
        return 2

    label = args.label or ("FINAL (CPU clock)" if args.final else "PROVISIONAL (concurrent load)")

    ready, reason = v2_ready()
    if not ready:
        print("v2 NOT READY: %s" % reason)
        return 3
    plan = build_plan(args.n)
    if args.only is not None:
        plan = [p for p in plan if p["index"] == args.only]
        if not plan:
            print("no such example: %s" % args.only)
            return 2
    sha_before = source_sha1()
    t0 = time.perf_counter()
    report = run_once(plan, label)
    if args.only is None:
        try:
            report["hand"] = hand_block(args.n)
        except Exception as e:  # noqa: BLE001 - recorded, never silently dropped
            import traceback
            report["errors"].append("hand_block: %s: %s\n%s"
                                    % (type(e).__name__, e, traceback.format_exc()))
    report["provisional"] = not args.final
    report["final"] = bool(args.final)
    report["tag"] = args.tag
    report["label_requested"] = args.label
    report["label_actual"] = label
    report["output_txt"] = out_txt
    report["output_json"] = out_json
    sha_after = source_sha1()
    report["sha1"] = sha_before
    report["sha1_after"] = sha_after
    report["revision_stable_during_run"] = (sha_before == sha_after)
    if not report["revision_stable_during_run"]:
        report["errors"].append("revision changed during the run: %s -> %s" % (sha_before, sha_after))
    report["wall_s"] = time.perf_counter() - t0
    text = table(report, not args.final)
    print(text)
    os.makedirs(EVIDENCE, exist_ok=True)
    with io.open(out_txt, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with io.open(out_json, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("\nwrote %s" % out_txt)
    print("wrote %s" % out_json)
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
