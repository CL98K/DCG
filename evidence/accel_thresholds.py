# -*- coding: utf-8 -*-
"""复核 `dcg._accel` 的双活门控阈值：一条命令在任何机器上重测「多少个元素才值得走 Rust」。

为什么需要这个脚本
------------------
用户 m03837 第⑦条定的规则是「Rust 实现没有倍级提升就不做」，2026-11 由用户指令 B
把倍级门槛从 2× 下调到 **1.5×**（2× 会把稳定获益的 `$zip`、`$clone` 挡在门外；
1.5× 仍高于 ±10% 噪声带两倍以上）。`dcg/runtime.py` 里每个被门控的 helper 都写死了
一个门槛（`_mn`/`_mx`/`_av` 用 24、`_ds` 用 8 个元素；`_zp` 只对**两列**且列长 ≥384
放行；`_cl` 按形状：dict 用 2、list/tuple 用 8；`_fgi` 只对十进制掩码图样放行），
那些数字来自本机一次测量。换机器、换 CPU、换 Python 版本后是否还成立，不能靠
口头保证 —— 这个脚本把那套测量口径固化下来：

  * 两条臂来自**同一份源码**，唯一差别是是否走 Rust，因此测出来的比值只反映
    「Rust 相对 Python」，不含调用点差异；
  * 受测臂**不带门槛**（见 `_ungated`）：每次调用都先问 Rust，它返回 `None` 才落回纯
    Python。若直接拿 `dcg.runtime` 里那个带门槛的 helper 当受测臂，门槛以下的档位两臂
    就是同一份代码、比值恒为 1.0，测出来的是「门槛让不让过」而不是「Rust 快不快」，
    还会在门槛那一档画出假断崖；
  * 两臂**交错**测量（每轮奇偶换序），取 `REPEATS` 个比值的中位数；
    本机单臂计时漂移可以到 ±30%，不交错就会得出相反结论；
  * 每个样本内跑若干次调用（按 `ELEMENT_BUDGET` 折算，见 `_calls_for`），
    按 `RUNS` 遍去极值后取均值。

读法
----
比值 = 纯 Python 耗时 / 走加速耗时，**>1 表示加速更快**。
脚本对每个用例给出：
  * `ratio`：各档实测比值；
  * `current`：`dcg/runtime.py` 里现在写死的阈值（从源码正则读出，不抄常量，避免漂移）；
  * `suggest`：按 `--target`（默认 1.5，即倍级门槛）算出的最小档位；
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
#: 数组类用例每个样本内重复多少次调用（按元素预算折算，见 `_calls_for`）
CALLS = 500
#: 单个样本的「元素预算」：`_calls_for(n)` 让 `calls * n` 落在这个量级，
#: 否则 n=1024 那一档会比 n=8 慢两个数量级、整轮跑不完。
ELEMENT_BUDGET = CALLS * 64
#: 判读「不宣称快慢」的噪声阈值（与 evidence/bench_protocol.py 同口径）
NOISE = 1.10


def _calls_for(n):
    """按元素预算折算调用次数。

    下限是 100 而不是 20：n=1024 的档若只跑 31 次，单样本总工作量太小，
    循环与计时本身的开销会挤进比值里。但下限也不能太高 —— 实测同一档
    （`$zip` 3 列 n=1024）50 次是 1.67×、500 次是 1.45×，批量越大 GC 与分配器
    的影响越重。所以报告里的比值要按「跨批量的区间」读，单点数字不足以定门槛。
    """
    return max(100, min(CALLS, ELEMENT_BUDGET // max(1, n)))


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
#: 造参函数返回**完整实参元组**，这样变参的 `$zip` 也能用同一套计时口径。
ARRAY_CASES = [
    ("$min", "_mn", "mn", lambda n: (_ints(n),)),
    ("$max", "_mx", "mx", lambda n: (_ints(n),)),
    ("$average(int)", "_av", "av", lambda n: (_ints(n),)),
    ("$average(float)", "_av", "av", lambda n: (_floats(n),)),
    ("$distinct", "_ds", "ds", lambda n: (_ints(n),)),
    ("$zip(2col)", "_zp", "zp", lambda n: (_ints(n), _floats(n))),
    ("$zip(3col)", "_zp", "zp", lambda n: (_ints(n), _floats(n), _ints(n))),
]

#: 数组类用例测试哪些元素数。跨度覆盖「现在阈值以下 / 附近 / 以上」。
#: 384/512/1024 三档是为 `$zip` 加的（2 列在 384 才稳定过 1.5×、3 列要 512 才刚够到，
#: 4 列以上又退化回去 —— 所以门控最终收窄成「恰好两列且 ≥384」）。
ARRAY_SIZES = (2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 192, 384, 512, 1024, 2048)

#: `$clone` 两套形状各测哪些档：扁平宽度（门控依据 `_CL_MIN_DICT` / `_CL_MIN_SEQ`）
#: 与嵌套深度（Rust `deep_copy` 上限 64，超过返回 `None`）。2/3/4/6/8 五档是刻意的：
#: dict 在宽 2 就已过线、序列要到宽 8 才稳，档位不铺开就看不出两者差在哪。
CLONE_SIZES = (2, 3, 4, 6, 8, 12, 16, 32, 64)


def _fat_dict(n):
    """`n` 个键的扁平 dict：`$clone` 最常见的用法是「拷贝一条记录」。"""
    return dict(("k%d" % i, i) for i in range(n))


def _fat_list(n):
    """`n` 个元素的扁平 list。"""
    return list(range(n))


def _fat_tuple(n):
    """`n` 个元素的扁平 tuple（`$clone` 对 tuple 也返回 list，走同一条 Rust 路径）。"""
    return tuple(range(n))


def _nested(n):
    """`n` 层嵌套：每层是一个 list + 一个 dict，节点数 **线性** 于 n。

    这里绝不能写成 `v = [v, {"k": v}]` —— 那样每层把上一层的引用塞进去两次，
    节点数是 2^n，n=32 就是 40 亿个节点，`$clone` 的深拷贝会直接把内存吃光
    （2026-11 实测：机子被拖死）。深度靠「新 list 套住旧 v」实现，引用只有一份。
    """
    v = [1, 2, 3]
    for _ in range(n):
        v = [v, {"k": 1}]
    return v

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
    ("$formatInteger(A)", "_fgi", "fgi", (1234567, "A")),
    ("$formatInteger(w)", "_fgi", "fgi", (1234567, "w")),
    ("$formatInteger(#,##0)", "_fgi", "fgi", (1234567, "#,##0")),
    ("$formatInteger(0)", "_fgi", "fgi", (1234567, "0")),
    ("$formatInteger(#,##0.00)", "_fgi", "fgi", (1234567, "#,##0.00")),
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


def _ungated(name, attr, acc):
    """「Rust 成功就用 Rust、返回 `None` 就用纯 Python」——**不带任何规模门槛**。

    这是本脚本唯一正确的受测臂。`dcg.runtime` 里的 helper 本身是带门槛的：低于门槛时
    它照样走纯 Python，两臂就变成同一份代码、比值恒为 1.0。那样测出来的不是「Rust 在
    这个规模上快不快」，而是「现役门槛让不让过」—— 门槛会自己证明自己合适，改阈值时
    还会出现「门槛以下全是 1.0、门槛以上突然 1.6×」的假断崖（2026-11 实测踩过）。
    这里改成每次调用都先问 Rust，它拒绝才落回纯 Python，于是比值只反映两种实现的成本差。
    """
    pure = _pure(name)
    raw = getattr(acc, attr)

    def call(*a):
        r = raw(*a)
        if r is None:
            return pure(*a)
        return r

    return call


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

# 标记不带前导 `if `：`_zp` 的门控是 `if (_ACC is not None and len(cols) == ...`，
# 括号紧跟在 `if ` 后面，带 `if ` 的写法匹配不到，`_zp` 会从
# `current_thresholds()` 里整个消失（报告里 `$zip` 的「现阈值」变成空、`suggest`
# 永远给 `n/a`）。
_GATE_MARK = "_ACC is not None and"
# 常量取值认 `==` / `>`：`_zp` 的列数条件是 `len(cols) == _ZP_COLS`（**恰好**两列，
# 三列起实测退化到 1.28–1.57× 也不过门槛，所以 `==` 是上界语义而不是下界）。
_GATE_CONST = re.compile(r"(?:>=|==|>)\s*(_[A-Z][A-Z0-9_]*)\b")
# 数字字面量只认下界 `>=` / `>`：`_fnm` 里有 `len(parts) == 1`，那不是尺寸阈值，
# 认 `==` 会把 `_fnm` 误收成「阈值 1」。
_GATE_NUM = re.compile(r"(?:>=|>)\s*([0-9]+)\b")


def _gate_conditions(src):
    """取出源码里**所有** `if _ACC is not None and ...` 条件（可能折行），其余源码丢弃。

    一个 helper 可能有多条门控分支（`_cl` 对 dict 与序列分别定阈值），
    只看第一条会把另一条的形状漏掉。
    """
    lines = src.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if _GATE_MARK not in lines[i]:
            i += 1
            continue
        cond = [lines[i][lines[i].index(_GATE_MARK):]]
        j = i + 1
        while not cond[-1].rstrip().endswith(":") and j < len(lines):
            cond.append(lines[j])
            j += 1
        out.append(" ".join(cond))
        i = j
    return out


def current_thresholds():
    """`{helper 名: 元素数阈值}`；没有数值门槛的门控不出现。

    阈值有两种写法：直接写数字（`len(a) >= 24`）或用模块级常量
    （`min(len(c) for c in cols) >= _ZP_MIN`、`len(v) >= _CL_MIN_DICT`）。后者从
    `dcg.runtime` 现取，保证报告不会和源码漂移。

    一条条件里可能出现多个比较（`_zp` 是 `len(cols) == _ZP_COLS and min(len(c)) >= _ZP_MIN`），
    一个 helper 也可能有多条分支（`_cl` 的 dict / 序列各一条）。这里一律取**最大**值：
    它是「最保守的那条门槛」，适合做汇总；要按形状分开看，用 `$clone` 那几行逐形状
    记的 `threshold_const`（那里记的是常量名，不是汇总值）。
    """
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
        values = []
        for cond in _gate_conditions(src):
            values.extend(int(v) for v in _GATE_NUM.findall(cond))
            for const in _GATE_CONST.findall(cond):
                value = getattr(rt, const, None)
                if isinstance(value, int):
                    values.append(value)
        if values:
            out[name] = max(values)
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
    ap.add_argument("--target", type=float, default=1.5,
                    help="倍级门槛（默认 1.5；2026-11 由 2.0 下调，见用户指令 B）")
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
    args_target = args.target

    for label, helper, attr, make in ARRAY_CASES:
        live = _ungated(helper, attr, acc)
        off = _pure(helper)
        accel_fn = getattr(acc, attr)
        sizes, ratios, cells = [], [], []
        for n in ARRAY_SIZES:
            # 注意不要叫 `args`：`args` 是 argparse 的命名空间，下面还要用 `args.out`。
            call_args = make(n)
            if accel_fn(*call_args) is None:
                # 该档会被 Rust 拒绝（落回 Python），两条臂实际是同一份代码，
                # 比值必然约 1.0 —— 记下来，不要误当「Rust 没提速」。
                cells.append({"n": n, "skipped": "accel returned None"})
                continue
            try:
                # 调用次数按元素预算折算：n=1024 的档只跑 20 次，n=8 的档跑满 500 次，
                # 这样每档的样本工作量接近，整轮不会被最大的两档拖死。
                got = _bench(live, off, call_args, _calls_for(n))
            except Exception as exc:  # noqa: BLE001 - 单档报错不该拖垮整轮
                failed = True
                cells.append({"n": n, "error": "%s: %s" % (type(exc).__name__, exc)})
                continue
            got["n"] = n
            sizes.append(n)
            ratios.append(got["ratio"])
            cells.append(got)
        current = report["current"].get(helper)
        suggest = _suggest(sizes, ratios, args_target) if sizes else None
        report["array_cases"].append({
            "case": label,
            "helper": helper,
            "current": current,
            "suggest": suggest,
            "verdict": _verdict(sizes, ratios, current, args_target, suggest),
            "cells": cells,
        })

    # `$clone`：门控比的是**本层宽度**，而 dict 与序列的阶跃点不同（dict 宽 2 就稳定过线、
    # 序列要到宽 8），所以三种形状各用各的现役门槛。Rust 侧深度 >64 会返回 None，
    # 嵌套一档单列出来——只测深度会得出「n=2 时 0.99×」这种与门控无关的结论。
    live, off, accel_fn = _ungated("_cl", "cl", acc), _pure("_cl"), acc.cl
    for label, const_name, make in (
            ("$clone(dict width)", "_CL_MIN_DICT", _fat_dict),
            ("$clone(list width)", "_CL_MIN_SEQ", _fat_list),
            ("$clone(tuple width)", "_CL_MIN_SEQ", _fat_tuple),
            ("$clone(depth)", "_CL_MIN_SEQ", _nested)):
        sizes, ratios, cells = [], [], []
        for n in CLONE_SIZES:
            arg = make(n)
            if accel_fn(arg) is None:
                cells.append({"n": n, "skipped": "accel returned None (depth > 64)"})
                continue
            try:
                got = _bench(live, off, (arg,), _calls_for(n))
            except Exception as exc:  # noqa: BLE001
                failed = True
                cells.append({"n": n, "error": "%s: %s" % (type(exc).__name__, exc)})
                continue
            got["n"] = n
            sizes.append(n)
            ratios.append(got["ratio"])
            cells.append(got)
        current = getattr(rt, const_name, None)
        suggest = _suggest(sizes, ratios, args_target) if sizes else None
        report["array_cases"].append({
            "case": label, "helper": "_cl", "threshold_const": const_name,
            "current": current,
            "suggest": suggest,
            "verdict": _verdict(sizes, ratios, current, args_target, suggest),
            "cells": cells,
        })

    for label, helper, attr, params in SCALAR_CASES:
        live = _ungated(helper, attr, acc)
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
