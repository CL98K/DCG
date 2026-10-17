"""`bench/row_v2.py` —— v2 效率门（ratio vs hand reference）：**双口径 + A/A 噪声底**。

这是 `evidence/accept.py` 的「§5 efficiency (ratio vs hand reference)」gate 显式等待的
harness（`accept.py:698` 检查它是否存在）。设计约束：

  * **主判定 = 同进程、同顺序控制下的比值**（`v2/hand`、`v2/v1`），不看绝对 µs/行；
  * 门：`v2/hand <= 1.05`（`bench_row.RATIO_LIMIT_VS_REF`）；
  * **CPU 口径（主）**：`time.process_time()`（只计本进程 CPU 消耗）→ 不受其它 agent
    抢占影响，因此**不需要静默窗口**（Lead m01939 ① / m01965 ⑦）。方法：interleaved
    slot（每个 rep 内所有变体各测一遍，避免"谁先谁后"变成系统偏差）+ `min of reps`
    + 一次测量内跑 `k` 遍摊销 Windows ~15.6 ms 计时粒度（K=8 → 量化 ≈1.4%）；
  * **wall 口径（辅，标 contaminated）**：保留 CONTRACT §7 的 warmup + gc.collect() +
    min of reps，但**不用于判门**（并发 agent 下波动可达 40–90%，见 §3.2f）；
  * **A/A 噪声底**：同一 callable 占两个 slot（A 占 slot1、A′ 占 slot2），与正式测量
    相同的 interleaved 顺序，≥3 个 block 取最差 `|ratio-1|`；
  * **判决规则（Lead m01965 ②，经 m02010 §1 修正）**：
        margin = |ratio - limit|
        blocks_disagree_in_sign   -> verdict = INCONCLUSIVE   # ratio 在 limit 两侧来回跨
        margin < noise_floor      -> verdict = PASS/FAIL, confidence = WITHIN_NOISE
        else                      -> verdict = PASS/FAIL, confidence = RESOLVED
    `noise_floor` 是**分辨率**，不是全局否决门：只有待判定的 margin 小于它才降级
    （本机 A/A 分辨率实测 ≈0.05，若设成「>5% 一律 INCONCLUSIVE」会让 harness 永远
    无法判定一个 5% 的目标 —— Lead 已自我否决初版规则）。
    `accept.py` 的 §5 门按 `verdict` 判，`WITHIN_NOISE` / `INCONCLUSIVE` 时判 FAIL 并在
    reason 里引用 `confidence` 与 `margin`（承诺是 ≤1.05，分辨不出就不算达成）；
  * **共享实现**：计时函数只在 `bench/bench_row.py`（`measure` / `measure_cpu` /
    `measure_interleaved_cpu` / `build_variants`），本文件只做编排/A/A/JSON —— 避免两套
    §7 漂移（Lead m01965 ⑤；本项目已因 `runtime.py` 的 HELPERS vs SOURCE 双实现吃过亏）；
  * **CPU 口径绝不复用 wall 的自适应 settle**（自适应是为 wall 的 90% 波动设计的）；
  * **3 轮 CPU 口径并报跨轮一致性**（Lead m01965 ⑥ 第 4 步）；
  * v2 不可调用时 **exit 4 且一个数字都不写**；`revision_stable_during_run` 照旧硬断言。

用法：
    python -X utf8 bench/row_v2.py                       # 双口径 + A/A，出终值行
    python -X utf8 bench/row_v2.py --frozen              # 标为可引用终值（不再需要窗口标签）
    python -X utf8 bench/row_v2.py --no-cython --no-wall  # 快速口径（调试用）
    python -X utf8 bench/row_v2.py --aa-blocks 3 --cpu-rounds 3 --k 8 --cpu-reps 7

退出码：0 = PASS 且 confidence=RESOLVED；1 = FAIL / WITHIN_NOISE / INCONCLUSIVE /
revision 不稳；3 = 用法拒绝；4 = v2 未就绪（无数字）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH_DIR)
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

import _workload as W          # noqa: E402
import bench_row as BR         # noqa: E402  (单一方法学实现处)

HAND_LABEL = "hand ref (unpack once)"
V1_LABEL = "v1 (format.Format.parse)"
V2_LABEL = "v2 (dcg.generate + run)"
V2C_LABEL = "v2 cython=True"

# W3 锚点（Lead m01965 §1）涉及的冻结文件：全部记录 sha1，供读者核对数字锚在哪一版
ANCHOR_FILES = ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py", "dcg/ir.py",
                "dcg/build.py", "dcg/__init__.py", "tests/test_codegen_snapshot.py")


def sha1_of(rel):
    p = os.path.join(ROOT, *rel.split("/"))
    try:
        with open(p, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest().upper()
    except OSError:
        return None


def process_table():
    # errors="replace"（保持 locale 编码，中文 Windows 上 tasklist 是 GBK）：
    # 只给 text=True 时遇到无法解码的字节会抛 UnicodeDecodeError（见 Lead m01706）。
    try:
        if os.name == "nt":
            return subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                                  capture_output=True, text=True,
                                  errors="replace").stdout.strip()
        return subprocess.run(["ps", "-eo", "pid,comm,etime"],
                              capture_output=True, text=True,
                              errors="replace").stdout.strip()
    except OSError as exc:
        return f"<unavailable: {exc}>"


def _ratios(res):
    """从 {label: (min_us, arr, spread)} 算比值。"""
    hand = res.get(HAND_LABEL, (None,))[0]
    v1 = res.get(V1_LABEL, (None,))[0]
    v2 = res.get(V2_LABEL, (None,))[0]
    out = {"hand": hand, "v1": v1, "v2": v2,
           "v1_over_hand": (v1 / hand) if (v1 and hand) else None,
           "v2_over_hand": (v2 / hand) if (v2 and hand) else None,
           "v2_over_v1": (v2 / v1) if (v2 and v1) else None}
    if V2C_LABEL in res:
        v2c = res[V2C_LABEL][0]
        out["v2c"] = v2c
        out["v2c_over_hand"] = (v2c / hand) if (v2c and hand) else None
    return out


def _ratio_stats(res, num_label, den_label):
    """**按 rep 配对**求比值再取中位 —— 对频率/热漂移稳健的聚合。

    本机实测：同一个 callable 的绝对 CPU 时间跨轮可漂 37–56%（Lead m02010 §2 也报
    跨会话漂 56%），而 min-of-reps 的比值在两个变体落在不同漂移区间时会失真。
    配对后每个比值都来自**相邻 slot**（同一 rep 的同一时间窗），漂移在分子分母上
    大致同阶、相除后抵消；再取中位以抗离群 slot。
    """
    num = res.get(num_label, (None, None, None))[1]
    den = res.get(den_label, (None, None, None))[1]
    if not num or not den or len(num) != len(den):
        return None
    rs = [n / d for n, d in zip(num, den) if d]
    if not rs:
        return None
    s = sorted(rs)
    return {"median": s[len(s) // 2], "min": min(rs), "max": max(rs),
            "spread_pct": ((max(rs) / min(rs) - 1.0) * 100.0 if min(rs) else 0.0),
            "per_rep": rs}


def _resolution_failures(res):
    """返回计时值 <=0 的标签 —— 说明 `k*n` 太小、落进 Windows 计时粒度里了。

    `time.process_time()` 粒度 ≈15.6 ms：`n=5000, k=2` 时单次测量只有几毫秒，
    会量化成 0 或一个 tick，比值随即可变成 0/0 或 1:2 的假象。此时**必须拒绝出数**，
    而不是把一个量化伪影当结论（Lead m00414 ③：禁止引用不可复现的数字）。
    """
    bad = []
    for label, tup in res.items():
        lo = tup[0]
        if lo is None or lo <= 0:
            bad.append(label)
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--reps", type=int, default=3, help="wall 口径 reps（辅口径）")
    ap.add_argument("--cpu-reps", type=int, default=BR.CPU_REPS,
                    help="CPU 口径 reps（每 rep 内所有变体各测一遍，min of reps）")
    ap.add_argument("--k", type=int, default=BR.CPU_K,
                    help="一次 CPU 测量内跑 k 遍以摊销计时粒度（K=8 → ~1.4%%）")
    ap.add_argument("--cpu-rounds", type=int, default=3,
                    help="CPU 口径重复整轮次数（报跨轮一致性，Lead m01965 ⑥-4）")
    ap.add_argument("--aa-blocks", type=int, default=3,
                    help="A/A 噪声底 block 数（同一 callable 占两个 slot，取最差）")
    ap.add_argument("--no-cython", action="store_true",
                    help="跳过 cython=True 变体（它的计时不计入 CAP/M0 预算）")
    ap.add_argument("--no-wall", action="store_true", help="跳过 wall 口径（省时间，调试用）")
    ap.add_argument("--json", default=os.path.join(ROOT, "evidence", "row_v2.json"))
    ap.add_argument("--frozen", action="store_true",
                    help="标为可引用终值（JSON provisional=false）。CPU 口径不受并发抢占"
                         "影响，Lead m01965 ⑦ 已作废静默窗口，故不再要求 --window 标签")
    ap.add_argument("--window", default=None,
                    help="可选标签（旧流程遗留；缺省时 frozen 自动生成 CPU-<时间戳>）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)

    # readiness gate: never emit numbers for an uncallable engine
    ready, reason = W.v2_ready()
    if not ready:
        print(f"v2 NOT READY ({reason}) -- STOPPING, no v2 number written")
        return 4
    if not a.frozen and a.window:
        print("REFUSED: 给了 --window 但不是 --frozen（标签只对终值有意义）")
        return 3

    before = {f: sha1_of(f) for f in ANCHOR_FILES}
    if a.no_cython:
        BR.load_cython_product = lambda *p, **k: (None, "skipped by --no-cython")

    print("=" * 78)
    print(f"row_v2.py  n={a.n}  cpu_reps={a.cpu_reps}  k={a.k}  "
          f"cpu_rounds={a.cpu_rounds}  aa_blocks={a.aa_blocks}  wall_reps={a.reps}")
    print("CPU 口径（主）= time.process_time(), interleaved slot, min of cpu_reps, "
          "每测量 k 遍摊销粒度")
    print("wall 口径（辅，contaminated_by_concurrent_agents）= CONTRACT §7 "
          "warmup+gc+min of reps，**不用于判门**")
    print(f"锚点 sha1[:12]: " + "  ".join(
        f"{os.path.basename(f)}={(before[f] or 'MISSING')[:12]}" for f in ANCHOR_FILES))
    print("=" * 78)

    # ---------------- correctness first (shared with bench_row.main) ----------------
    prep = BR.build_variants(a.n, with_cython=not a.no_cython, verbose=True)
    variants = list(prep["variants"])
    labels = [lbl for lbl, _ in variants]
    if HAND_LABEL not in labels or V2_LABEL not in labels:
        print(f"EFFICIENCY_RATIO unavailable (variants={labels})")
        return 4
    hand_fn = dict(variants)[HAND_LABEL]

    # ---------------- wall caliber (secondary, never decides the gate) ----------------
    wall = None
    if not a.no_wall:
        for _ in range(BR.SETTLE_ROUNDS):           # 对称 settle（仅 wall 口径）
            for _lbl, fn in variants:
                fn()
        wall_res, wall_spread = {}, {}
        for lbl, fn in variants:
            us, arr, _settle, spread, _capped = BR.measure(fn, a.n, a.reps)
            wall_res[lbl] = (us, arr, spread)
            wall_spread[lbl] = spread
        wall = {"us_per_row_all": {k: v[0] for k, v in wall_res.items()},
                "reps_detail": {k: v[1] for k, v in wall_res.items()},
                "spread_pct": wall_spread}
        wall.update(_ratios(wall_res))
        print(f"[wall] contaminated_by_concurrent_agents  "
              f"v2/hand={wall.get('v2_over_hand'):.3f} "
              f"v2/v1={'%.3f' % wall['v2_over_v1'] if wall.get('v2_over_v1') else 'n/a'}")

    # ---------------- A/A noise floor (same slot order as the real measurement) ------
    aa_blocks = []
    for i in range(a.aa_blocks):
        res = BR.measure_interleaved_cpu([("A@slot1", hand_fn), ("A'@slot2", hand_fn)],
                                         a.n, reps=a.cpu_reps, k=a.k)
        st = _ratio_stats(res, "A'@slot2", "A@slot1")
        if st is None or _resolution_failures(res):
            print(f"TIMER RESOLUTION TOO COARSE at n={a.n}, k={a.k} "
                  f"(measurement hit the ~15.6 ms process_time granularity) -- "
                  f"STOPPING, no number written; 提高 --k 或 --n（n=200000,k=8 起）")
            return 3
        r_min = (res["A'@slot2"][0] / res["A@slot1"][0]
                 if res["A@slot1"][0] else None)
        aa_blocks.append({"block": i + 1,
                          "ratio_Aprime_over_A": st["median"],
                          "abs_dev_from_1": abs(st["median"] - 1.0),
                          "ratio_min_of_reps": r_min,
                          "abs_dev_min_of_reps": (abs(r_min - 1.0)
                                                  if r_min is not None else None),
                          "per_rep_ratios": st["per_rep"],
                          "ratio_spread_pct": st["spread_pct"],
                          "slot1_min_us": res["A@slot1"][0],
                          "slot2_min_us": res["A'@slot2"][0],
                          "slot1_arr": res["A@slot1"][1],
                          "slot2_arr": res["A'@slot2"][1]})
        print(f"[A/A ] block {i + 1}/{a.aa_blocks}: paired-median ratio={st['median']:.4f}"
              f"  |ratio-1|={abs(st['median'] - 1.0):.4f}  "
              f"(min-of-reps ratio={'%.4f' % r_min if r_min is not None else 'n/a'}, "
              f"per-rep spread={st['spread_pct']:.1f}%)")
    noise_floor = max(b["abs_dev_from_1"] for b in aa_blocks)
    _mf = [b["abs_dev_min_of_reps"] for b in aa_blocks if b["abs_dev_min_of_reps"] is not None]
    noise_floor_min_reps = max(_mf) if _mf else float("nan")
    print(f"[A/A ] noise_floor (worst paired-median |ratio-1| of {len(aa_blocks)} blocks)"
          f" = {noise_floor:.4f}   [min-of-reps 口径: {noise_floor_min_reps:.4f}]")

    # ---------------- CPU caliber: N rounds, worst-of (conservative) ----------------
    cpu_rounds = []
    for i in range(a.cpu_rounds):
        res = BR.measure_interleaved_cpu(variants, a.n, reps=a.cpu_reps, k=a.k)
        rr = _ratios(res)
        ph = _ratio_stats(res, V2_LABEL, HAND_LABEL)
        pv = _ratio_stats(res, V2_LABEL, V1_LABEL)
        bad = _resolution_failures(res)
        if ph is None or bad:
            print(f"TIMER RESOLUTION TOO COARSE at n={a.n}, k={a.k} (zero/None timings: "
                  f"{bad}) -- STOPPING, no number written; 提高 --k 或 --n"
                  f"（正式口径 n=200000, k=8, cpu_reps=7 起）")
            return 3
        spread = {lbl: res[lbl][2] for lbl in res}
        cpu_rounds.append({"round": i + 1, "us_per_row_all": {k: v[0] for k, v in res.items()},
                           "reps_detail": {k: v[1] for k, v in res.items()},
                           "spread_pct": spread, **rr,
                           "paired_v2_over_hand": ph, "paired_v2_over_v1": pv})
        print(f"[cpu ] round {i + 1}/{a.cpu_rounds}: "
              f"hand={rr['hand']:.3f} v1={rr['v1']:.3f} v2={rr['v2']:.3f} us/row -> "
              f"paired v2/hand={ph['median']:.3f} "
              f"v2/v1={('%.3f' % pv['median']) if pv else 'n/a'} "
              f"| min-of-reps v2/hand={rr['v2_over_hand']:.3f} "
              f"(paired per-rep spread {ph['spread_pct']:.1f}%)")

    # 主聚合 = 每轮内**按 rep 配对**的比值中位（抗频率/热漂移）；min-of-reps 保留为次级
    round_ratios = [r["paired_v2_over_hand"]["median"] for r in cpu_rounds]
    round_ratios_min = [r["v2_over_hand"] for r in cpu_rounds]
    worst = max(cpu_rounds, key=lambda r: r["paired_v2_over_hand"]["median"])
    r_hand = worst["paired_v2_over_hand"]["median"]
    r_v1 = (worst["paired_v2_over_v1"] or {}).get("median")
    worst_min = max(cpu_rounds, key=lambda r: r["v2_over_hand"])
    r_hand_min = worst_min["v2_over_hand"]
    sorted_ratios = sorted(round_ratios)
    median_ratio = sorted_ratios[len(sorted_ratios) // 2]
    round_verdicts = ["PASS" if r <= BR.RATIO_LIMIT_VS_REF else "FAIL"
                      for r in round_ratios]
    rounds_agree = len(set(round_verdicts)) == 1

    # ---------------- verdict rule (Lead m01965 ②, corrected by m02010 §1) ---------
    limit = BR.RATIO_LIMIT_VS_REF
    margin = abs(r_hand - limit)
    # 「ratio 在 limit 两侧来回跨」：有些轮 > limit、有些轮 <= limit -> 分不清方向
    blocks_disagree_in_sign = any(r > limit for r in round_ratios) and \
        any(r <= limit for r in round_ratios)
    if blocks_disagree_in_sign:
        verdict, confidence = "INCONCLUSIVE", "INCONCLUSIVE"
    else:
        verdict = "PASS" if r_hand <= limit else "FAIL"
        confidence = "WITHIN_NOISE" if margin < noise_floor else "RESOLVED"
    # 稳健聚合（仅供参考）：中位轮的比值给出的 margin —— 报告里必须两个都给，
    # 因为 worst-of-N 会按噪声量级系统性抬高 ratio（选择偏差），而 noise_floor 是
    # 单个比值的分辨率（Lead m02010 §1）。
    margin_median = abs(median_ratio - limit)
    confidence_median = ("INCONCLUSIVE" if blocks_disagree_in_sign else
                         ("WITHIN_NOISE" if margin_median < noise_floor else "RESOLVED"))

    after = {f: sha1_of(f) for f in ANCHOR_FILES}
    stable = before == after
    changed = sorted(f for f in ANCHOR_FILES if before[f] != after[f])
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    window = a.window or (f"CPU-{ts}" if a.frozen else None)

    print("=" * 78)
    print(f"EFFICIENCY_RATIO v2_over_hand={r_hand:.3f} "
          f"v2_over_v1={'%.3f' % r_v1 if r_v1 is not None else 'n/a'} "
          f"limit={limit:.2f} verdict={verdict} confidence={confidence} "
          f"noise_floor={noise_floor:.4f} margin_over_limit={margin:.4f} "
          f"aggregate=paired-median median_ratio={median_ratio:.3f} "
          f"margin_median={margin_median:.4f} "
          f"min_reps_v2_over_hand={r_hand_min:.3f} "
          f"noise_floor_min_reps={noise_floor_min_reps:.4f} "
          f"blocks_disagree_in_sign={str(blocks_disagree_in_sign).lower()} "
          f"k={a.k} cpu_reps={a.cpu_reps} cpu_rounds={a.cpu_rounds} "
          f"n={a.n} rep_reps={a.reps} revision_stable={stable} "
          f"provisional={str(not a.frozen).lower()}")
    print(f"  worst-of {a.cpu_rounds} CPU rounds（配对中位口径）: round "
          f"{worst['round']}  hand={worst['hand']:.3f} v1={worst['v1']:.3f} "
          f"v2={worst['v2']:.3f} us/row；min-of-reps 口径 worst "
          f"v2/hand={r_hand_min:.3f}（选择偏差更大，仅作次级证据）")
    print(f"  per-round paired v2/hand = {['%.3f' % r for r in round_ratios]}  "
          f"verdicts = {round_verdicts}  rounds_agree={rounds_agree}")
    if not rounds_agree:
        print("  !! CPU 轮间结论不一致 -> worst-of 为保守值；若跨 limit 两侧则 INCONCLUSIVE")
    if wall:
        print(f"  [wall/info] v2/hand={wall['v2_over_hand']:.3f} "
              f"v2/v1={'%.3f' % wall['v2_over_v1'] if wall.get('v2_over_v1') else 'n/a'} "
              f"(contaminated_by_concurrent_agents，仅供参考)")
    if verdict == "INCONCLUSIVE":
        print(f"  !! 轮间 ratio 在 limit 两侧来回跨（{['%.3f' % r for r in round_ratios]}）"
              f"-> 判决 INCONCLUSIVE —— 同样不算通过")
    elif confidence == "WITHIN_NOISE":
        print(f"  !! margin {margin:.4f} < noise_floor {noise_floor:.4f}: 测了但分不清 —— "
              f"按 Lead m02010 §1，accept.py 必须判 FAIL（承诺是 ≤{limit:.2f}）")
    print(f"  [info] 稳健聚合（中位轮 paired ratio={median_ratio:.3f}）: margin_over_limit="
          f"{margin_median:.4f} -> confidence={confidence_median}；worst-of-N 会按噪声"
          f"量级抬高 ratio（选择偏差），两个都报，说明见报告 §3.2f")
    if a.frozen:
        print(f"FROZEN (可引用终值)  window={window}")
    else:
        print("PROVISIONAL -- 未加 --frozen；加 --frozen 即为可引用终值"
              "（CPU 口径不受并发抢占影响，Lead m01965 ⑦）")
    print(f"timestamp={ts}")
    if not stable:
        print(f"WARN: revision 在测量期间变化 -> 本轮数字不可引用: {changed}")

    pt = process_table()
    others = max(0, pt.lower().count("python.exe") - 1) if os.name == "nt" else 0
    if others:
        print(f"[info] 同时有其他 python 进程 {others} 个 —— 对 CPU 口径无影响"
              f"（这正是 CPU 口径的价值，Lead m01965 ⑦）；wall 数字标 contaminated")

    payload = {
        "harness": "bench/row_v2.py",
        "methodology_cpu": (f"time.process_time(); interleaved slot; {a.cpu_reps} reps with "
                            f"k={a.k} passes each; aggregate = per-rep PAIRED ratio median "
                            f"(drift-robust), worst of {a.cpu_rounds} rounds"),
        "methodology_wall": "CONTRACT §7 warmup + gc.collect() + min of reps (secondary, "
                            "contaminated_by_concurrent_agents -- never decides the gate)",
        "methodology_aa": (f"A/A same-slot order (A@slot1 vs A'@slot2), {a.aa_blocks} blocks, "
                           f"paired-median ratio, worst |ratio-1| = noise_floor"),
        "timestamp": ts,
        "provisional": not a.frozen,
        "window": window,
        "n_rows": a.n,
        "k": a.k,
        "reps": a.cpu_reps,
        "cpu_reps": a.cpu_reps,
        "cpu_rounds": a.cpu_rounds,
        "us_per_row_cpu": {k: v for k, v in worst["us_per_row_all"].items()},
        "us_per_row": {k: v for k, v in worst["us_per_row_all"].items()},
        "us_per_row_by_cpu_round": [r["us_per_row_all"] for r in cpu_rounds],
        "spread_pct_cpu": worst["spread_pct"],
        "aggregation_primary": "per-rep paired ratio median, worst-of-rounds",
        "ratios": {"v2_over_hand": r_hand, "v2_over_v1": r_v1},
        "ratios_by_cpu_round": [{"v2_over_hand": r["paired_v2_over_hand"]["median"],
                                 "v2_over_v1": (r["paired_v2_over_v1"] or {}).get("median"),
                                 "min_reps_v2_over_hand": r["v2_over_hand"]}
                                for r in cpu_rounds],
        "ratios_min_of_reps": {"v2_over_hand_worst": r_hand_min,
                               "per_round": round_ratios_min},
        "cpu_rounds_detail": cpu_rounds,
        "round_verdicts": round_verdicts,
        "rounds_agree": rounds_agree,
        "worst_of": f"cpu rounds 1..{a.cpu_rounds} (max paired-median v2/hand)",
        "limit": limit,
        "margin_over_limit": margin,
        "median_ratio": median_ratio,
        "margin_over_limit_median": margin_median,
        "confidence_median_aggregate": confidence_median,
        "aggregation_note": ("verdict/margin 用 worst-of-rounds（保守，防挑轮次达标）；"
                             "median_ratio/*_median 为稳健参考值 —— worst-of-N 会按噪声"
                             "量级抬高 ratio（选择偏差），见 bench_compare.md §3.2f"),
        "blocks_disagree_in_sign": blocks_disagree_in_sign,
        "noise_floor": noise_floor,
        "noise_floor_min_of_reps": noise_floor_min_reps,
        "noise_floor_blocks": aa_blocks,
        "confidence": confidence,
        "verdict": verdict,
        "sha1": after,
        "sha1_before": before,
        "revision_stable_during_run": stable,
        "revision_changed_files": changed,
        "wall_caliber": wall,
        "others_python_processes": others,
        "process_table": pt,
    }
    try:
        os.makedirs(os.path.dirname(a.json), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"wrote {a.json}")
    except OSError as exc:
        print(f"WARN: could not write {a.json}: {exc}")

    if not stable:
        return 1
    return 0 if (verdict == "PASS" and confidence == "RESOLVED") else 1


if __name__ == "__main__":
    sys.exit(main())
