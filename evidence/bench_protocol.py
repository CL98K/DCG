"""用户指定的基准协议（m02815）。

规则（原文）：
  1. 基准测试使用 **10 万条数据**；
  2. 每项测试 **5 遍**；
  3. **去除最大最小，取平均值**（中间 3 遍的算术平均）。

这个模块把它实现成可复用的 Timing 工具，供 bench.py / 任何探针调用。
与 perf_methodology.md 里"min-of-k"的历史做法不同：用户要的是 trimmed mean。

**为什么 5 遍而不是更多**：用户明确指定 5 遍。我们额外记录 A/A 复现性
（同一函数跑两轮本协议，看两组结果差多少），以便判断某个差异是否真实。
"""
from __future__ import annotations

import gc
import time

#: 用户指定的每项测量遍数
RUNS = 5
#: 用户指定的数据行数
ROWS = 100_000

METHOD_NOTE = """\
协议：10 万行 × 每项 5 遍 × 去掉最大最小后取中间 3 遍的算术平均。
与历史 min-of-k 的差别：trimmed mean 对系统性噪声更敏感（一次 GC 或调度抖动
会落在中间 3 遍里），所以判读阈值仍然是「比值 < 1.10 不宣称快慢」。
"""


def trimmed_mean(samples):
    """去掉 1 个最大、1 个最小，其余取平均（样本不足 3 个时退化成全平均）。"""
    xs = sorted(samples)
    if len(xs) < 3:
        return sum(xs) / len(xs) if xs else 0.0
    mid = xs[1:-1]
    return sum(mid) / len(mid)


def measure(fn, runs=RUNS, warmup=2):
    """跑 `runs` 遍（每遍一次 `fn()`），返回 (trimmed_mean_seconds, all_samples)。

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
