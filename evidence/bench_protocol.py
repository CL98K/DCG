"""基准协议：10 万行 × 每项 5 遍 × 去掉最大最小取中间 3 遍均值（trimmed mean），
整组连跑 3 次、取 3 个比值的中位数作为判定值（单轮被调度打断最多污染 1/3 样本）。

与 perf_methodology.md 里 "min-of-k" 的做法不同：这里用 trimmed mean。
额外记录 A/A 复现性（同一函数跑两轮本协议，看两组结果差多少），
以便判断某个差异是否真实。本模块是供 bench.py / 任何探针复用的 Timing 工具。
"""
from __future__ import annotations

import gc
import time

#: 每项测量遍数
RUNS = 5
#: 数据行数
ROWS = 100_000
#: 整组重复次数；判定值取 REPEATS 个比值的中位数
REPEATS = 3

METHOD_NOTE = """\
协议：10 万行 × 每项 5 遍 × 去掉最大最小后取中间 3 遍的算术平均；
整组（这一项的 5 遍）连跑 3 次，取 3 个比值的中位数作为判定值。
单轮被一次 GC 或调度抖动打断时，中位数最多被污染 1/3 的样本；判读阈值仍然是
「比值 < 1.10 不宣称快慢」。
"""


def trimmed_mean(samples):
    """去掉 1 个最大、1 个最小，其余取平均（样本不足 3 个时退化成全平均）。"""
    xs = sorted(samples)
    if len(xs) < 3:
        return sum(xs) / len(xs) if xs else 0.0
    mid = xs[1:-1]
    return sum(mid) / len(mid)


def measure(fn, runs=RUNS, warmup=2):
    """跑 `runs` 遍（每遍一次 `fn`），返回 (trimmed_mean_seconds, all_samples)。

    `warmup` 遍预热不计数：第一遍要付 CPython 自适应专门化与 cache 冷启动的钱，
    那不是"生成码慢"，是解释器热身。
    """
    gc.collect()
    for _ in range(warmup):
        fn()
    xs = []
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - t0)
    if not xs:
        return 0.0, []
    return trimmed_mean(xs), xs


def per_row(seconds, n):
    """秒 -> 微秒/行。"""
    return seconds * 1e6 / n


def ratio(dcg_s, opt_s):
    """DCG / 理论最优。>1 表示 DCG 更慢。"""
    if not opt_s:
        return float("inf")
    return dcg_s / opt_s
