"""Runtime benchmark: hand-written reference vs the generated product.

Frozen methodology (接口实现细节文档.md §7):
  same machine, same data, same process; gc.collect() before each rep;
  warm up then take the MINIMUM of 3 reps; report us/row and rows/s.

Ruling -- 主判定用比值:
  * 主判定 = 同进程、同顺序控制下的 **产物 <-> 手写参照 相对比值**; 门只看比值, 不看绝对 us/row.
  * 硬规则: 禁止把跨会话的绝对数字直接两两相减/相比; 报比值时必须附**当次实测**的参照值.
  * 效率承诺表述为比值: **产物 <= 1.05x 手写参照**.
  * 波动 >5% 时**必须**追加 settle 轮次, 并报告最小值与轮次数 (不允许只报一次).

Order artifact (measured): 不做 settle 时, 第一个被测变体的首轮会有 ~50% 波动, 看起来比手写
参照还快 -- 纯伪影. 故计时前对所有变体做对称 settle sweep.  §7.1 的 CPU 口径另有交替测量
(`measure_interleaved_cpu`)，用同一个 callable 占两个 slot 即得 A/A 噪声底。

Run:  python bench/bench_row.py [N] [--n N] [--reps R]
"""
from __future__ import annotations

import os
import sys

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)
import _workload as W  # noqa: E402

SETTLE_ROUNDS = 2          # 对称预热轮次 (所有变体)
SPREAD_LIMIT = 0.05        # 硬规则: >5% 追加 settle
MAX_EXTRA_SETTLE = 3       # 追加上限, 防止无限循环
RATIO_LIMIT_VS_REF = 1.05  # 效率承诺: 产物 <= 1.05x 手写参照

# 静默窗口的最终轮要在「明文轮次间轮转各变体的测量顺序」以消除固定首测位偏差。
# 这是一个**测量技术**钩子，不改 §7 的门径（比值定义 / min-of-N / 自适应 settle 全部照旧）：
# 调用方（bench/row_v2.py）把它设成 `lambda vs: vs[1:] + vs[:1]` 之类即可让某一轮换首测位。
VARIANT_ORDER = None


def parse_args(argv):
    """Positional N kept for backwards compatibility with earlier runs."""
    n, reps, positional = W.N, W.REPS, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--n":
            n = int(argv[i + 1]); i += 2
        elif a == "--reps":
            reps = int(argv[i + 1]); i += 2
        elif a.startswith("--n="):
            n = int(a.split("=", 1)[1]); i += 1
        elif a.startswith("--reps="):
            reps = int(a.split("=", 1)[1]); i += 1
        else:
            positional.append(a); i += 1
    if positional:
        n = int(positional[0])
    return n, reps


def measure(fn, n, reps):
    """min-of-reps with adaptive settle.

    Returns (min_us_per_row, us_per_row_list, settle_rounds_used, spread_pct, capped).
    Spread is recomputed after every extra settle round; the reported minimum can
    only get better, and the number of settle rounds actually used is printed so
    the reader knows how much warm-up the number needed.  `capped` is True when the
    spread was still > SPREAD_LIMIT after MAX_EXTRA_SETTLE extra rounds -- that fact
    is reported instead of being hidden.
    """
    settle_rounds = SETTLE_ROUNDS
    while True:
        arr = [W.us_per_row(x, n) for x in W.bench_reps(fn, reps=reps, warmup=False)]
        lo, hi = min(arr), max(arr)
        spread = (hi / lo - 1.0) if lo else 0.0
        capped = settle_rounds >= SETTLE_ROUNDS + MAX_EXTRA_SETTLE
        if spread <= SPREAD_LIMIT or capped:
            return lo, arr, settle_rounds, spread * 100.0, (capped and spread > SPREAD_LIMIT)
        # spread too wide -> one extra whole-variant settle round, then re-time
        settle_rounds += 1
        fn()


CPU_K = 8        # Windows ~15.6 ms 计时粒度；200k 行 ≈0.14 s/遍 ->
                 # K=8 时单测量量化 ≈1.4%（K=4 只有 2.8%，不足以支撑 1.05 的门）
CPU_REPS = 7     # min of 7


def _cpu_us_per_row(fn, n, k):
    """CPU 时间口径的单次测量：一次测量内跑 k 遍以摊销计时粒度。

    用 `time.process_time()`（只计本进程 CPU 消耗）→ 天然不受其它进程抢占影响
    （CONTRACT §7.1，「静默窗口」因此不再必要）。**不复用 wall 口径的自适应 settle**
    （自适应是为 wall 的 90% 波动设计的，套到 CPU 口径会凭空追加无用轮次）；
    固定 `reps x k`，spread 如实报出。
    """
    import gc
    import time as _time

    gc.collect()
    t0 = _time.process_time()
    for _ in range(k):
        fn()
    dt = _time.process_time() - t0
    return dt / (k * n) * 1e6


def _spread_pct(arr):
    lo, hi = min(arr), max(arr)
    return ((hi / lo - 1.0) if lo else 0.0) * 100.0


def measure_cpu(fn, n, reps=CPU_REPS, k=CPU_K):
    """CPU 口径：固定 reps 次 `_cpu_us_per_row`，返回 (min_us, arr, spread_pct)。"""
    arr = [_cpu_us_per_row(fn, n, k) for _ in range(reps)]
    return min(arr), arr, _spread_pct(arr)


def measure_interleaved_cpu(variants, n, reps=CPU_REPS, k=CPU_K):
    """交替（interleaved）测量：每个 rep 内把**所有变体**各测一遍。

    要求交替而非分块顺序测：分块顺序把「谁先谁后」变成系统偏差，交替后每个变体
    在时间上均匀分布；同一 callable 占两个 slot 即得 A/A 噪声底。
    返回 {label: (min_us, arr, spread_pct)}，保持传入顺序。
    """
    acc = {label: [] for label, _fn in variants}
    for _ in range(reps):
        for label, fn in variants:
            acc[label].append(_cpu_us_per_row(fn, n, k))
    out = {}
    for label, _fn in variants:
        arr = acc[label]
        out[label] = (min(arr), arr, _spread_pct(arr))
    return out


def load_cython_product(rows, ref_out):
    """Compile the `generate(cython=True)` product with Cython (pyximport).

    `cdef` is not valid Python, so CPython's compile() *must* fail on this
    product -- that is expected, not a defect.  Returns (run_or_None, note).

    the cython product is "same source + one cdef per local",
    its measured benefit is small, and CONTRACT §7's "Cython row-object"
    target needs a typed-column-input C path that is NOT implemented in this
    round.  So this variant is reported as informational and is never charged
    to the CAP/M0 char budget.
    """
    import importlib
    import tempfile
    import time as _time

    src_cy = None
    try:
        src_cy = __import__("dcg").generate(W.V2_M0, cython=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"generate(cython=True) 失败: {type(exc).__name__}: {exc}"
    # structural assertion (same source + cdef lines only), independent of timing
    pure = [ln for ln in src_cy.splitlines() if not ln.strip().startswith("cdef ")]
    src_py = __import__("dcg").generate(W.V2_M0)
    if "\n".join(pure) != src_py.rstrip("\n"):
        return None, "结构断言失败: 剥掉 cdef 行后不等于纯 Python 产物"
    try:
        tmp = tempfile.mkdtemp(prefix="dcgbench_cy_")
        modname = "dcgbench_cy_%d" % (abs(hash(src_cy)) % 10 ** 8)
        with open(os.path.join(tmp, modname + ".pyx"), "w", encoding="utf-8") as fh:
            fh.write(src_cy)
        import pyximport
        pyximport.install(language_level=3, build_dir=tmp, inplace=False)
        sys.path.insert(0, tmp)
        t0 = _time.perf_counter()
        mod = importlib.import_module(modname)
        dt = _time.perf_counter() - t0
        run = getattr(mod, "run", None)
        if run is None:
            return None, "编译产物里没有 run()"
        # V2_M0 uses input.source="index" -> the call convention is run({"index": rows})
        data = W.v2_input(rows, W.V2_M0)
        got = W.normalize(run(data))
        if got != ref_out:
            return None, "语义不一致: cython 产物 != 手写参照"
        return (lambda _run=run, _d=data: _run(_d),
                f"pyximport 真编译 {dt:.2f}s, 语义与纯 Python/手写参照一致; "
                f"不计入 CAP/M0 预算, 非 §7 typed-column 档")
    except Exception as exc:  # noqa: BLE001
        return None, (f"需 Cython 编译（CPython compile() 对 cdef 预期语法错误）: "
                      f"{type(exc).__name__}: {exc}")


def build_variants(N, *, with_cython=True, verbose=True):
    """构造变体列表 + 正确性前置校验；**不计时**。

    `main()`（wall 口径）与 `bench/row_v2.py`（CPU + wall 双口径）共用的唯一实现
    —— 共享实现放 bench_row.py，避免两套 §7 漂移。
    返回 dict：rows / datas / ref_out / v2 / v2c / cython_note / variants。
    """
    rows = W.make_rows(N)
    datas = {"index": rows}
    ref_out = W.ref_tight(datas)
    if verbose:
        print(f"[ref ] hand-written rows={len(ref_out)}  row0={ref_out[0]}")

    v2 = v2c = None
    cython_note = ""
    ready, reason = W.v2_ready()
    if ready:
        try:
            v2, _v2_src = W.load_v2()
            v2_out = W.normalize(v2(rows))
            if verbose:
                print(f"[v2  ] rows={len(v2_out)}  matches hand ref: "
                      f"{v2_out == ref_out}  row0={v2_out[0]}")
        except Exception as e:
            if verbose:
                print(f"[v2  ] FAILED: {type(e).__name__}: {e}")
            v2 = None
        if with_cython:
            try:
                v2c, cython_note = load_cython_product(rows, ref_out)
                if verbose:
                    if v2c is not None:
                        print(f"[v2c ] cython=True rows={len(rows)}  "
                              f"matches hand ref: True  ({cython_note})")
                    else:
                        print(f"[v2c ] cython=True 不计入: {cython_note}")
            except Exception as e:
                cython_note = f"{type(e).__name__}: {e}"
                v2c = None
                if verbose:
                    print(f"[v2c ] cython=True 加载失败: {cython_note}")
        else:
            cython_note = "skipped (--no-cython)"
    else:
        cython_note = reason
        if verbose:
            print(f"[v2  ] SKIPPED: {reason}")
            print(f"[v2c ] SKIPPED: {reason}")

    variants = [("hand ref (unpack once)", lambda: W.ref_tight(datas))]
    if v2 is not None:
        variants.append(("v2 (dcg.generate + run)", lambda: v2(rows)))
    if v2c is not None:
        variants.append(("v2 cython=True", lambda: v2c()))

    if VARIANT_ORDER is not None:
        variants = list(VARIANT_ORDER(list(variants)))

    return {"rows": rows, "datas": datas, "ref_out": ref_out, "v2": v2,
            "v2c": v2c, "cython_note": cython_note, "variants": variants}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    N, reps = parse_args(argv)

    print("=" * 78)
    print("DCG v2 -- bench_row.py   (CONTRACT §7: warmup + min of %d reps, "
          "gc.collect() each rep)" % reps)
    print(f"N = {N} rows x 8 fields | row = (code:str, a:int, b:int, c:int, d:int, "
          "payload:{'a':[4]})")
    print("主判定 = 同进程相对比值")
    print("=" * 78)

    # ---------------- correctness first: never time wrong code ----------------
    prep = build_variants(N, verbose=True)
    cython_note = prep["cython_note"]
    variants = list(prep["variants"])

    # ---------------- timing ----------------
    print("-" * 78)
    print(f"{'variant':30s} {'ms':>8s} {'us/row':>8s} {'rows/s':>12s} {'vs hand':>8s}")
    print("-" * 78)

    if VARIANT_ORDER is not None:
        print(f"[order] 轮转测量顺序: {[lbl for lbl, _ in variants]}")
    else:
        print(f"[order] 固定测量顺序: {[lbl for lbl, _ in variants]}")

    # Symmetric settle for every variant before timing (see module docstring).
    for _ in range(SETTLE_ROUNDS):
        for _lbl, fn in variants:
            fn()

    results, spreads, settles = {}, {}, {}
    for label, fn in variants:
        us, arr, settle_used, spread, capped = measure(fn, N, reps)
        results[label] = us
        spreads[label] = arr
        settles[label] = (settle_used, spread, capped)

    hand = results["hand ref (unpack once)"]
    for label, _fn in variants:
        us = results[label]
        ratio_hand = us / hand if hand else float("nan")
        print(f"{label:30s} {us * N / 1e6:8.1f} {us:8.3f} "
              f"{1e6 / us if us else 0:12.0f} {ratio_hand:7.2f}x")
    print("-" * 78)
    print("重复明细 (us/row) + 波动 (硬规则: >5% 必须追加 settle 并报轮次数):")
    for label, arr in spreads.items():
        settle_used, spread, capped = settles[label]
        if settle_used > SETTLE_ROUNDS:
            flag = "  <-- 追加 settle 至上限仍 >5% (已如实报告)" if capped else \
                   "  <-- 追加 settle"
        else:
            flag = ""
        print(f"  {label:30s} min={min(arr):7.3f}  reps={[round(x, 3) for x in arr]}"
              f"  波动={spread:5.1f}%  settle_rounds={settle_used}{flag}")
    print("-" * 78)

    # ---------------- verdicts (ratio-first) ----------------
    print("VERDICT (主判定 = 比值; 参考值仅供定位, 禁止跨会话绝对值相减):")
    print(f"  [ref ] 当次实测 hand ref = {hand:.3f} us/row  (本会话唯一可比基准)")
    for label, what in (("v2 (dcg.generate + run)", "v2 纯 Python"),
                        ("v2 cython=True", "v2 Cython row-object")):
        if label not in results:
            why = (cython_note if (label == "v2 cython=True" and cython_note)
                   else "v2 未就绪, 不构成证据")
            print(f"  [SKIP] {what:22s} <= {RATIO_LIMIT_VS_REF:.2f}x hand ref "
                  f"({why})")
            continue
        us = results[label]
        r_hand = us / hand
        verdict = "PASS" if r_hand <= RATIO_LIMIT_VS_REF else "FAIL"
        print(f"  [{verdict}] {what:22s} {r_hand:.2f}x hand ref "
              f"(上限 {RATIO_LIMIT_VS_REF:.2f}x)")
    print("=" * 78)
    return results


if __name__ == "__main__":
    main()
