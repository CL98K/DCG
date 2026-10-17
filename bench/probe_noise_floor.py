"""Lead-owned evidence probe backing CONTRACT.md §7.1.

Why this file exists
--------------------
On 2026-10-17 the v2 efficiency gate reported mutually contradictory verdicts on the
*frozen* revision A40457511277 (v2/hand = 1.065 / 1.117 / 1.169 "FAIL" vs
0.901 / 0.937 / 1.000 "PASS").  This probe shows the cause is the *measurement method*,
not the product:

  Part 1  A/A noise floor   -- the SAME callable measured twice, interleaved slots.
                               If two identical functions can produce a "ratio" far from
                               1.0, then no 5%-level threshold is decidable.
  Part 2  2x2 ablation      -- prebind (`_ap = _o.append`) x guard (`if R:`) vs base,
                               so the real effect size of each is visible above the floor.
  Part 3  product vs hand   -- the real generated product (frozen schema) against the
                               frozen hand-written reference, both CPU-time measured.

Method (CONTRACT.md §7.1): `time.process_time()` (this process only -> immune to the 5-9
concurrent python processes on this box), K workload passes batched inside one timing to
amortise the ~15.6 ms Windows clock tick, variants interleaved, min of `reps`.

Usage
-----
    python -X utf8 bench/probe_noise_floor.py [--n 200000] [--k 8] [--reps 7] [--blocks 3]
Writes evidence/noise_floor.json.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import _workload as W  # noqa: E402


def measure_cpu(fn, k):
    """One CPU-time reading: k workload passes batched, divided by k."""
    gc.collect()
    t = time.process_time()
    for _ in range(k):
        fn()
    return (time.process_time() - t) / k


def interleaved_cpu(fns, k, reps):
    """Interleave the callables so slot order cannot favour one variant.

    Returns {label: min_seconds}.  Reported min (not mean) because contention/GC only
    ever *adds* time, so the min is the best estimator of the uncontended cost.
    """
    for fn in fns.values():
        fn()  # warm
    best = {label: float("inf") for label in fns}
    for i in range(reps):
        order = list(fns.items())
        if i % 2:
            order.reverse()
        for label, fn in order:
            best[label] = min(best[label], measure_cpu(fn, k))
    return best


def _spread(xs):
    return (max(xs) / min(xs) - 1.0) if xs and min(xs) > 0 else 0.0


def paired_cpu(fns_per_block, k, reps, blocks):
    """Run `blocks` independent blocks; each returns {label: min}.  Captures drift."""
    return [interleaved_cpu(fns_per_block(), k, reps) for _ in range(blocks)]


# --------------------------------------------------------------------------
# Part 1 -- A/A noise floor
# --------------------------------------------------------------------------
def noise_floor(datas, k, reps, blocks):
    """The SAME workload in two slots.  Ideal: ratio == 1.0000 exactly."""
    def make():
        return {"slotA": lambda: W.ref_tight(datas), "slotB": lambda: W.ref_tight(datas)}

    per_block = paired_cpu(make, k, reps, blocks)
    ratios, spreads_a, spreads_b = [], [], []
    for blk in per_block:
        ratios.append(blk["slotB"] / blk["slotA"])
    # same-slot spread: repeat one slot's reps to see its own jitter
    xs = [measure_cpu(lambda: W.ref_tight(datas), k) for _ in range(reps)]
    spreads_a.append(_spread(xs))
    ys = [measure_cpu(lambda: W.ref_tight(datas), k) for _ in range(reps)]
    spreads_b.append(_spread(ys))
    worst = max(abs(r - 1.0) for r in ratios)
    return {
        "ratios_per_block": ratios,
        "worst_abs_dev_from_1": worst,
        "single_variant_spread": max(spreads_a + spreads_b),
        "verdict": "NOISY" if worst > 0.05 else "ok",
    }


# --------------------------------------------------------------------------
# Part 2 -- 2x2 ablation: prebind x guard
# --------------------------------------------------------------------------
BODY = "[_a,_a1,_a2,R[2],str(_a1)+str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123)]"
HDR = '_a = R[5]["a"]; _a1 = _a[1]; _a2 = _a[2]'


def _ablation_variant(prebind, guard):
    src = (
        "def f(d):\n _o=[]\n"
        + (" _ap = _o.append\n" if prebind else "")
        + " for R in d['index']:\n"
        + ("  if R:\n" if guard else "")
        + ("   " if guard else "  ")
        + HDR
        + "\n"
        + ("   " if guard else "  ")
        + ("_ap(" if prebind else "_o.append(")
        + BODY
        + ")\n return _o\n"
    )
    ns: dict = {}
    exec(src, ns)  # noqa: S102 - fixed source, never user input
    return ns["f"], src


def ablation(datas, k, reps, blocks):
    labels = {
        (False, False): "base (no guard, unbound)",
        (False, True): "guard only (if R:)",
        (True, False): "prebind only (_ap)",
        (True, True): "prebind + guard",
    }
    raw = {labels[key]: _ablation_variant(*key)[0] for key in labels}
    fns = {lbl: (lambda fn=fn: fn(datas)) for lbl, fn in raw.items()}

    def make():
        return dict(fns)

    per_block = paired_cpu(make, k, reps, blocks)
    base = "base (no guard, unbound)"
    out = {}
    for lbl in fns:
        us = [blk[lbl] / len(datas["index"]) * 1e6 for blk in per_block]
        rel = [blk[lbl] / blk[base] for blk in per_block]
        out[lbl] = {
            "us_per_row_min": min(us),
            "x_base_per_block": rel,
            "x_base_worst": max(rel),
        }
    return out


# --------------------------------------------------------------------------
# Part 3 -- real product vs frozen hand reference
# --------------------------------------------------------------------------
def product_vs_hand(rows, datas, k, reps, blocks):
    import copy

    import dcg

    m0_filter = copy.deepcopy(W.V2_M0)
    m0_nofilter = copy.deepcopy(W.V2_M0)
    m0_nofilter["output"]["filter"] = False
    src_f = dcg.generate(m0_filter)
    src_n = dcg.generate(m0_nofilter)

    def load(src):
        ns: dict = {}
        exec(compile(src, "<dcg-generated>", "exec"), ns)  # noqa: S102
        return ns["run"]

    run_f, run_n = load(src_f), load(src_n)
    hand_rows = W.ref_tight(datas)
    assert run_f(datas) == hand_rows, "filter=true product rows != hand reference"
    assert run_n(datas) == hand_rows, "filter=false product rows != hand reference"

    fns = {
        "hand ref (ref_tight, no guard)": lambda: W.ref_tight(datas),
        "v2 M0 filter=true": lambda: run_f(datas),
        "v2 M0 filter=false": lambda: run_n(datas),
    }

    def make():
        return dict(fns)

    per_block = paired_cpu(make, k, reps, blocks)
    base = "hand ref (ref_tight, no guard)"
    out = {}
    for lbl in fns:
        us = [blk[lbl] / len(rows) * 1e6 for blk in per_block]
        rel = [blk[lbl] / blk[base] for blk in per_block]
        out[lbl] = {"us_per_row_min": min(us), "x_hand_per_block": rel, "x_hand_worst": max(rel)}
    out["_correctness"] = "both products emit rows identical to the hand reference"
    out["_chars"] = {"filter_true": len(src_f), "filter_false": len(src_n)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--k", type=int, default=8, help="workload passes per timing")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--blocks", type=int, default=3)
    a = ap.parse_args(argv)

    rows = W.make_rows(a.n)
    datas = {"index": rows}
    print(f"probe_noise_floor: N={a.n} k={a.k} reps={a.reps} blocks={a.blocks}")
    print("metric = time.process_time(), batched k passes, interleaved, min of reps")

    print("\n[1] A/A noise floor (SAME callable in both slots; ideal ratio = 1.0000)")
    nf = noise_floor(datas, a.k, a.reps, a.blocks)
    print(f"    worst |ratio-1| = {nf['worst_abs_dev_from_1']:.4f}   "
          f"single-variant spread = {nf['single_variant_spread']:.1%}   -> {nf['verdict']}")

    print("\n[2] 2x2 ablation (base = no guard, unbound append)")
    ab = ablation(datas, a.k, a.reps, a.blocks)
    for lbl, d in ab.items():
        rel = " ".join(f"{x:.3f}" for x in d["x_base_per_block"])
        print(f"    {lbl:26s} {d['us_per_row_min']:.4f} us/row   x/base = {rel}")

    print("\n[3] real product vs frozen hand reference (rows asserted identical)")
    ph = product_vs_hand(rows, datas, a.k, a.reps, a.blocks)
    for lbl, d in ph.items():
        if lbl.startswith("_"):
            continue
        rel = " ".join(f"{x:.3f}" for x in d["x_hand_per_block"])
        print(f"    {lbl:32s} {d['us_per_row_min']:.4f} us/row   x/hand = {rel}")

    report = {
        "n": a.n, "k": a.k, "reps": a.reps, "blocks": a.blocks,
        "metric": "time.process_time() batched over k passes, interleaved, min of reps",
        "noise_floor": nf, "ablation": ab, "product_vs_hand": ph,
    }
    out = os.path.join(ROOT, "evidence", "noise_floor.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
