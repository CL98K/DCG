# -*- coding: utf-8 -*-
"""复核 `dcg._accel` 的双活门控阈值：一条命令在任何机器上重测「多少个元素才值得走 Rust」。

为什么需要这个脚本
------------------
用户 m03837 第⑦条定的规则是「Rust 实现没有倍级提升就不做」。`dcg/runtime.py` 里
每个被门控的 helper 都写死了一个元素数阈值（`_mn`/`_mx`/`_av` 用 24、`_ds` 用 8），
那些数字来自本机一次测量。换机器、换 CPU、换 Python 版本后是否还成立，不能靠
口头保证 —— 这个脚本把那套测量口径固化下来：

  * 两条臂来自**同一份源码**，唯一差别是 `_ACC`（真扩展 / `None`），
    因此测出来的比值只反映「Rust 相对 Python」，不含调用点差异；
  * 两臂**交错**测量（每轮奇偶换序），取 `REPEATS` 个比值的中位数；
    本机单臂计时漂移可以到 ±30%，不交错就会得出相反结论；
  * 每个样本内跑 `CALLS` 次调用，按 `RUNS` 遍去极值后取均值。

读法
----
比值 = 纯 Python 耗时 / 走加速耗时，**>1 表示加速更快**。
脚本对每个用例给出：
  * `ratio`：各档实测比值；
  * `current`：`dcg/runtime.py` 里现在写死的阈值（从源码正则读出，不抄常量，避免漂移）；
  * `suggest`：按 `--target`（默认 2.0，即倍级）算出的最小档位；
  * `verdict`：`ok` / `too-late`（当前阈值的实测比值没到门槛）/ `too-early`
    （阈值的下一个更低档位就已经达标，可以更早放行）/ `n/a`。

退出码：装了扩展且有结论 → 0；没装扩展（没有可比对象）→ 0 并打印 `SKIP`；
用例报错 → 1。``SKIP`` 不是失败，只是本机没有可复现条件。

用法（Windows 控制台是 GBK，务必带 `-X utf8`，否则表头中文会显示成乱码）：
    python -X utf8 evidence/accel_thresholds.py
    python -X utf8 evidence/accel_thresholds.py --target 1.5 -o _tmp/t.json
"""
import argparse
import gc
import inspect
import io
import json
import os
import re
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dcg import runtime as rt  # noqa: E402  需要项目根在 sys.path 上

#: 每个样本内跑多少遍（去极值取均值）
RUNS = 7
#: 整组重复次数；判定取这些比值的中位数
REPEATS = 5
#: 数组类用例每次调用喂多少个元素之后，重复多少次调用
CALLS = 2000
#: 判读「不宣称快慢」的噪声阈值（与 evidence/bench_protocol.py 同口径）
NOISE = 1.10


# --------------------------------------------------------------------------- #
# 造参数
# --------------------------------------------------------------------------- #

def _ints(n):
    return list(range(n))


def _floats(n):
    return [i * 0.5 for i in range(n)]


def _mixed(n):
    """交替 int / float：`_av` 的窄域只吃「全 int」或「全 float」，这个必然落回。"""
    return [i if i % 2 else i * 0.5 for i in range(n)]


#: 数组类用例：(用例名, helper 名, 扩展属性名, 造参函数)
ARRAY_CASES = [
    ("$min", "_mn", "mn", _ints),
    ("$max", "_mx", "mx", _ints),
    ("$average(int)", "_av", "av", _ints),
    ("$average(float)", "_av", "av", _floats),
    ("$distinct", "_ds", "ds", _ints),
]

#: 数组类用例测试哪些元素数。跨度覆盖「现在阈值以下 / 附近 / 以上」。
ARRAY_SIZES = (2, 4, 8, 12, 16, 24, 32, 48, 64)

#: 标量类用例：(用例名, helper 名, 扩展属性名, 调用参数)。
#: 这些算子的成本与「元素数」无关，只有一次调用的固定成本，所以只测比值。
SCALAR_CASES = [
    ("$encodeUrl(short)", "_eu", "eu", ("a b?c=1&d=2#f",)),
    ("$encodeUrl(long)", "_eu", "eu", ("a b?c=1&d=2#f" * 8,)),
    ("$decodeUrl(short)", "_du", "du", ("a%20b%2Fc%3Dd%26e",)),
    ("$decodeUrl(long)", "_du", "du", ("a%20b%2Fc%3Dd%26e" * 8,)),
    ("$toMillis", "_tms", "tms", ("2024-01-01T00:00:00.123Z",)),
    ("$fromMillis(default)", "_fms", "fms", (1704067200123,)),
    ("$fromMillis(picture)", "_fms", "fms",
     (1704067200123, "[Y0001]-[M01]-[D01]T[H01]:[m01]:[s01].[f001]")),
    ("$formatNumber", "_fnm", "fnm", (1234.5678, "#,##0.00")),
    ("$formatNumber(exp)", "_fnm", "fnm", (1234.5678, "0.00e0")),
    ("$parseInteger", "_pgi", "pgi", ("1,234", "#,##0")),
]

#: 标量类用例每个样本内重复多少次调用（单次约 1–20 µs，比数组类小一个量级）
SCALAR_CALLS = 20000


# --------------------------------------------------------------------------- #
# 计时
# --------------------------------------------------------------------------- #

def _timeit(fn, args, calls, runs=RUNS):
    """`calls` 次调用的总耗时（µs），`runs` 遍去极值取均值。"""
    xs = []
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        for _ in range(calls):
            fn(*args)
        xs.append((time.perf_counter() - t0) * 1e6)
    return statistics.fmean(sorted(xs)[1:-1])


def _pure(name):
    """同一段源码、但 `_ACC = None`：这就是「没装扩展」时走的那条路。"""
    src = inspect.getsource(getattr(rt, name))
    ns = dict(vars(rt))
    ns["_ACC"] = None
    exec(compile(src, "<pure>", "exec"), ns)
    return ns[name]


def _bench(live, off, args, calls):
    """两条臂交错测量，返回比值中位与两侧单次成本（ns）。"""
    ratios = []
    t_off = t_live = 0.0
    for r in range(REPEATS):
        if r % 2 == 0:
            t_off = _timeit(off, args, calls)
            t_live = _timeit(live, args, calls)
        else:
            t_live = _timeit(live, args, calls)
            t_off = _timeit(off, args, calls)
        ratios.append(t_off / t_live)
    return {
        "ratio": round(statistics.median(ratios), 3),
        "samples": [round(x, 3) for x in ratios],
        "ns_python": round(t_off * 1000.0 / calls, 1),
        "ns_accel": round(t_live * 1000.0 / calls, 1),
    }


# --------------------------------------------------------------------------- #
# 从源码读出现役阈值
# --------------------------------------------------------------------------- #

_GATE_RE = re.compile(r"if _ACC is not None and len\(a\) >= (\d+):")


def current_thresholds():
    """`{helper 名: 元素数阈值}`；没有 `len(a) >= N` 形态的门控不出现。"""
    out = {}
    for name in dir(rt):
        if not name.startswith("_"):
            continue
        fn = getattr(rt, name, None)
        if not callable(fn) or not hasattr(fn, "__code__"):
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):  # pragma: no cover - 内置对象没有源码
            continue
        m = _GATE_RE.search(src)
        if m:
            out[name] = int(m.group(1))
    return out


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #

#: 判定「稳定达标」时要一并看的后续档数。单点噪声会让「所有更高档都达标」
#: 变成不可能满足的条件（实测 `$distinct` 在 n=16 掉到 1.84、n=24 又回到 2.74），
#: 所以只看「本档 + 后两档」，而不是「本档 + 之后全部」。
WINDOW = 3

#: 比值落在 target 的 ±10% 内视为边界值：这一档的结论不可用于改阈值。
BORDERLINE = 0.10


def _suggest(sizes, ratios, target):
    """最小达标档位：该档与紧随其后的 `WINDOW-1` 档都 ≥ target 才算稳定。"""
    for i, n in enumerate(sizes):
        window = ratios[i:i + WINDOW]
        if len(window) < WINDOW:
            # 尾部档位样本不足：要求它自己达标即可（更靠后就不再有数据了）。
            if ratios[i] >= target:
                return n
            continue
        if all(r >= target for r in window):
            return n
    return None


def _verdict(sizes, ratios, current, target, suggest):
    """对现役阈值给出结论，取值 `ok` / `too-late` / `too-early` / `borderline` / `n/a`。"""
    if current is None or not sizes or len(ratios) != len(sizes):
        return "n/a"
    if current not in sizes:
        # 现役阈值不在测过的档位里（有人改了阈值却没同步 `ARRAY_SIZES`），
        # 不做推断，只报 n/a —— 猜出来的结论比没有结论更糟。
        return "n/a"
    at = ratios[sizes.index(current)]
    if abs(at - target) <= target * BORDERLINE:
        # 边界值：同一档两次运行可能落在门槛两侧（实测 `$distinct` n=8 两次
        # 分别 1.97 与 2.13），据此改阈值等于拿噪声当真。
        return "borderline"
    if at < target:
        return "too-late"
    lower = [n for n in sizes if n < current]
    if lower:
        prev = ratios[sizes.index(max(lower))]
        if prev >= target and suggest is not None and suggest < current:
            return "too-early"
    return "ok"


def main(argv=None):
    ap = argparse.ArgumentParser(description="复核 dcg._accel 的门控阈值")
    ap.add_argument("-o", "--out", default=os.path.join("_tmp", "accel_thresholds.json"),
                    help="JSON 输出路径（默认 _tmp/accel_thresholds.json，已被 gitignore）")
    ap.add_argument("--target", type=float, default=2.0,
                    help="倍级门槛（默认 2.0，用户 m03837 第⑦条）")
    args = ap.parse_args(argv)

    acc = getattr(rt, "_ACC", None)
    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "target": args.target,
        "noise": NOISE,
        "runs": RUNS,
        "repeats": REPEATS,
        "accel": acc is not None,
        "exports": sorted(n for n in dir(acc) if not n.startswith("_")) if acc else [],
        "current": current_thresholds(),
        "array_cases": [],
        "scalar_cases": [],
    }

    if acc is None:
        print("SKIP: 本机没有安装 dcg._accel（先跑 python build_accel.py），无从对比。")
        _write(args.out, report)
        return 0

    failed = False

    for label, helper, attr, make in ARRAY_CASES:
        live = getattr(rt, helper)
        off = _pure(helper)
        accel_fn = getattr(acc, attr)
        sizes, ratios, cells = [], [], []
        for n in ARRAY_SIZES:
            arg = make(n)
            if accel_fn(arg) is None:
                # 该档会被 Rust 拒绝（落回 Python），两条臂实际是同一份代码，
                # 比值必然约 1.0 —— 记下来，不要误当「Rust 没提速」。
                cells.append({"n": n, "skipped": "accel returned None"})
                continue
            try:
                got = _bench(live, off, (arg,), CALLS)
            except Exception as exc:  # noqa: BLE001 - 单档报错不该拖垮整轮
                failed = True
                cells.append({"n": n, "error": "%s: %s" % (type(exc).__name__, exc)})
                continue
            got["n"] = n
            sizes.append(n)
            ratios.append(got["ratio"])
            cells.append(got)
        current = report["current"].get(helper)
        suggest = _suggest(sizes, ratios, args.target) if sizes else None
        report["array_cases"].append({
            "case": label,
            "helper": helper,
            "current": current,
            "suggest": suggest,
            "verdict": _verdict(sizes, ratios, current, args.target, suggest),
            "cells": cells,
        })

    for label, helper, attr, params in SCALAR_CASES:
        live = getattr(rt, helper)
        off = _pure(helper)
        accel_fn = getattr(acc, attr)
        if accel_fn(*params) is None:
            report["scalar_cases"].append({"case": label, "helper": helper,
                                           "skipped": "accel returned None"})
            continue
        try:
            got = _bench(live, off, params, SCALAR_CALLS)
        except Exception as exc:  # noqa: BLE001
            failed = True
            report["scalar_cases"].append({"case": label, "helper": helper,
                                           "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        got.update({"case": label, "helper": helper,
                    "verdict": "ok" if got["ratio"] >= NOISE else "no-gain"})
        report["scalar_cases"].append(got)

    _write(args.out, report)
    _print(report, args.out)
    return 1 if failed else 0


def _write(path, report):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, indent=2))


def _print(report, path):
    print("Python %s / %s / accel=%s / 门槛 %.2fx"
          % (report["python"], report["platform"],
             "on" if report["accel"] else "off", report["target"]))
    print("")
    print("%-18s %-5s %-9s %-7s %s" % ("用例", "现阈值", "建议阈值", "判定", "各档比值"))
    for row in report["array_cases"]:
        ratios = " ".join("%d:%.2f" % (c["n"], c["ratio"])
                          for c in row["cells"] if "ratio" in c)
        print("%-18s %-5s %-9s %-7s %s"
              % (row["case"], row["current"] if row["current"] else "-",
                 row["suggest"] if row["suggest"] else "-", row["verdict"], ratios))
    print("")
    print("%-22s %-7s %-9s %-9s %s"
          % ("用例", "比值", "py ns", "acc ns", "判定"))
    for row in report["scalar_cases"]:
        if "ratio" not in row:
            print("%-22s %s" % (row["case"], row.get("skipped", row.get("error"))))
            continue
        print("%-22s %-7.2f %-9.1f %-9.1f %s"
              % (row["case"], row["ratio"], row["ns_python"], row["ns_accel"],
                 row["verdict"]))
    print("")
    print("明细已写入 %s" % path)


if __name__ == "__main__":
    sys.exit(main())
