# -*- coding: utf-8 -*-
"""原生加速后端（`dcg._accel`）的「真实配比」裁决基准。

目的：给「某算子该不该接线到 Rust」一个**可复现的判据**，而不是看单一样本
的漂亮数字。判定规则来自用户指令：

    该规模实测加速比 >= 1.3x 才接线；按真实配比来。

## 三条方法约束（都来自第一轮的教训）

1. **走真实路径**：`dcg.generate(schema)` → `exec` → `run(rows)`。不用
   `rt._op()` 裸调用代替端到端，因为产物里还有行构造、输出对象组装、
   字段取值等固定开销，裸调用会把加速比吹大。
2. **真实配比 = 混合分布，不是单一样本**。教训：`$parseInteger` 在单图样上
   只有 0.67x，换成 20000 行真实配比（80/20）后是 2.30x，最坏 50/50 仍 1.33x。
   所以每个算子都配一个带权重的取值分布（短/长/边界/非字符串），逐行按
   确定性哈希取档，同一次运行可复现，不同次运行也不会换结论。
3. **关掉 Rust 必须是机器验证的**，不能只写 `true`：先装一个「一碰就炸」的
   探针到 `rt._ACC`，确认 gated 算子真的会去调它（证明入口唯一），再把
   `rt._ACC = None`，确认同一算子照常出值（证明 None 真的绕开了 Rust）。

统计量写死：**各轮 `off/on` 比值的中位数**（>1 表示开 Rust 更快）。每轮
内部是 `RUNS` 次去极值均值（去掉最大最小），双臂按奇偶轮次换序以抵消漂移。

## 判据取哪一条比值

每个算子同时测两条口径：

* `helper_only.ratio` —— 直接比 Rust 算子与 Python 算子（不含行构造、不含
  输出对象组装）。**门槛与回退都按它判**，理由：需求说的是「RUST 实现的
  算子性能是 python 的 1.3 倍」；而且「才接线」意味着测量发生在接线之前，
  那时端到端根本测不出 Rust 的收益。
* `ratio` —— 端到端（`run(rows)` 整体）。它会被产物侧的行构造与结果保留
  稀释，低 `op_share` 的算子天然贴近 1.0，所以**只作诊断列**：低于
  `regression_floor` 时记 `end_to_end_slow`，不影响退出码。

`op_share` 给出算子在单行成本里的占比，`amdahl_upper_bound` 给出「算子免费」
时端到端的理论上界，两者一起解释端到端为什么贴着 1.0。

## 三种接线状态（`wire_state`）

`operators[].status` 只有 `wired` / `candidate` 两个值（CI 契约），但单靠它
无法区分「Rust 里根本没有」「Rust 里有但没注册」「已经接线」，所以另给一个
`wire_state` 字段，三态互斥：

* `wired` —— 运行期门控里真的调 `_ACC.<name>`，且扩展里导出该名字；
* `implemented_not_wired` —— 扩展里**有**该导出，但运行期还没接；
* `needs_rebuild` —— Rust 源码里有 `fn o_<name>`、`register()` 也注册了，但**已编译的
  扩展**还没导出它（改了源码没重建）；
* `not_implemented` —— 扩展里没有该导出（源码里也没有 `fn o_<name>`）。

`rust_source` 字段进一步区分「连 Rust 源码都没有」与「源码有、只是没注册」，
它扫 `dcg/_accel/src/*.rs` 里的 `fn <name>` / `fn o_<name>`，不导入扩展。

## 输出

* 人类可读表格打到 stdout；
* 机器可读 JSON 写到 `--out`（默认 `_tmp/accel_bench.json`，`_tmp/` 已被
  `.gitignore` 忽略，跑多少次都不脏树；要留证据请显式 `--out evidence/...`
  并登记进 `验收报告.md`）。

## 退出码

* `0` —— 跑完，且没有「已接线算子的 `helper_only.ratio` 掉到
  `regression_floor` 以下」（「判定不接线」不是失败，是结论）；
* `1` —— 检出回退（仅当 `--fail-on-regression`，默认开）；
* `2` —— 本机没有可用的 Rust 后端（`dcg._accel.status()["loaded"] is False`）。
  **构建失败不应落到这个码**：那是编排问题，不是环境不支持。

## 用法

    python -X utf8 evidence/accel_bench.py                       # 全量
    python -X utf8 evidence/accel_bench.py --ci                  # CI 档（快）
    python -X utf8 evidence/accel_bench.py --only mt,ue,ud,fb    # 只看候选
    python -X utf8 evidence/accel_bench.py --out _tmp/x.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402

# ---------------------------------------------------------------- 协议常量

#: 接线门槛：实测加速比必须达到这个倍级。
THRESHOLD = 1.3
#: 已接线算子的**回退**下限：低于它才算「接错了」（开了 Rust 反而更慢）。
#: 这里刻意取 1.0 而不是 1.3 —— 1.3 是「该不该接线」的判据，接线之后是否
#: 仍然值 1.3 是另一件事（`below_threshold_wired` 只报告不失败）。把 1.3 当
#: 回退线会让「门槛该不该下调」这种讨论把 CI 弄红，而 CI 要抓的是「接线断了
#: 或接反了」这类硬错误。
REGRESSION_FLOOR = 1.0
#: 单轮内部重复次数（去极值均值）。
RUNS = 5
#: 预热遍数（不计入）。
WARMUP = 2
#: 判定用的统计量，写死并写进 JSON，避免「换个统计量换个结论」。
STATISTIC = "median of per-repeat off/on ratios"
#: 「贴线」带：判据比值落在门槛 ±10% 内就标记出来（1.17–1.43）。
#: 只作标注、不改判定 —— `$zip` 的 helper 比值在多次运行里实测 1.27–1.54，
#: 会跨过 1.3 两侧，但它离 1.0 还很远，不是「接错了」。CI 的回退判据是
#: `REGRESSION_FLOOR = 1.0`，所以贴线不会造成随机红灯；这里只是让报告能
#: 一眼看出「这一项本来就在门槛附近，别拿单次结果下结论」。
BORDERLINE = 0.10

UNDEF = getattr(rt, "UNDEF", None)


# ---------------------------------------------------------------- 计时原语


def trimmed_mean(samples):
    """去掉一个最大值和一个最小值后的均值（样本 <3 时退化为普通均值）。"""
    xs = sorted(samples)
    if len(xs) >= 3:
        xs = xs[1:-1]
    return sum(xs) / len(xs)


def measure(fn, runs=RUNS, warmup=WARMUP):
    """跑 `warmup` 遍预热后计时 `runs` 遍，返回 (去极值均值秒, 全部样本)。"""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return trimmed_mean(samples), samples


def median(xs):
    ys = sorted(xs)
    n = len(ys)
    if n == 0:
        return float("nan")
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2.0


# ---------------------------------------------------------------- 双臂切换


class _Spy:
    """一碰就炸的探针：用来证明「Rust 入口只有 `rt._ACC` 这一条」。"""

    def __getattr__(self, name):
        raise AssertionError(
            "探针被调用（属性 %r）—— 说明存在绕过 rt._ACC 的 Rust 入口" % (name,))


#: 探针覆盖的 gated 算子：数值/字符串/结构三类各取一个，门控一改就会露馅。
def _probes():
    return [
        ("$min", lambda: rt._mn([i % 97 for i in range(64)])),
        ("$distinct", lambda: rt._ds([i % 40 for i in range(16)])),
        ("$encodeUrl", lambda: rt._eu("a b/c?d=1")),
        ("$decodeUrl", lambda: rt._du("a%20b%2Fc")),
        ("$clone", lambda: rt._cl({"a": 1, "b": 2})),
        ("$zip", lambda: rt._zp(list(range(192)), list(range(192)))),
    ]


def verify_rust_off():
    """机器验证「`rt._ACC = None` 真的关掉了 Rust」。

    两步：
      1. 装探针，逐个调 gated 算子 —— 至少一个必须抛 `AssertionError`
         （说明入口唯一，没有旁路）；
      2. `_ACC = None` 后同样逐个调 —— 全部必须正常返回（说明 None 生效）。

    返回 dict，`verified` 是两步都成立的合取。
    """
    saved = getattr(rt, "_ACC", None)
    spy_hits, spy_misses = [], []
    try:
        for label, fn in _probes():
            rt._ACC = _Spy()
            try:
                fn()
            except AssertionError:
                spy_hits.append(label)
            except Exception:  # noqa: BLE001 - 其它异常也算「没走到探针」
                spy_misses.append(label)
            else:
                spy_misses.append(label)
    finally:
        rt._ACC = saved

    off_errors = []
    rt._ACC = None
    try:
        for label, fn in _probes():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                off_errors.append("%s: %s: %s" % (label, type(exc).__name__, exc))
    finally:
        rt._ACC = saved

    return {
        "method": ("装入会抛 AssertionError 的探针到 rt._ACC，逐个调 gated 算子，"
                   "至少一个必须命中（证明入口唯一）；再把 rt._ACC 置 None，"
                   "同样逐个调，全部必须正常返回（证明 None 真的绕开 Rust）"),
        "spy_hits": spy_hits,
        "spy_misses": spy_misses,
        "off_errors": off_errors,
        "verified": bool(spy_hits) and not off_errors,
    }


class Arms:
    """在 on / off 两条臂之间切换，退出时复位。"""

    def __init__(self):
        self.acc = getattr(rt, "_ACC", None)

    def on(self):
        rt._ACC = self.acc

    def off(self):
        rt._ACC = None

    def restore(self):
        rt._ACC = self.acc


# ---------------------------------------------------------------- 深比较


def same(a, b, _depth=0):
    """逐值深比较：类型必须一致（`True` 不等于 `1`），undefined 按单例算同。"""
    if _depth > 40:
        return repr(a) == repr(b)
    if a is b:
        return True
    if a is UNDEF or b is UNDEF:
        return False
    ta, tb = type(a), type(b)
    if ta is not tb:
        return False
    if ta is float:
        if a != a and b != b:  # NaN
            return True
        return a == b
    if ta is dict:
        if len(a) != len(b) or set(a) != set(b):
            return False
        return all(same(a[k], b[k], _depth + 1) for k in a)
    if ta in (list, tuple):
        if len(a) != len(b):
            return False
        return all(same(x, y, _depth + 1) for x, y in zip(a, b))
    return a == b


# ---------------------------------------------------------------- Rust 源码探测


_RUST_SRC_DIR = os.path.join(ROOT, "dcg", "_accel", "src")


def rust_source_index():
    """扫 `dcg/_accel/src/*.rs`，返回 {导出名: 命中的 (文件, 行号)}。

    只认 `fn <name>(` 与 `fn o_<name>(`；不导入扩展，因此「源码有、产物没注册」
    也能分辨出来。文件缺失时返回空 dict（源码树没有 Rust 是正常情况）。
    """
    index = {}
    if not os.path.isdir(_RUST_SRC_DIR):
        return index
    pat = re.compile(r"\bfn\s+(o_)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for name in sorted(os.listdir(_RUST_SRC_DIR)):
        if not name.endswith(".rs"):
            continue
        path = os.path.join(_RUST_SRC_DIR, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    m = pat.search(line)
                    if not m:
                        continue
                    fn = m.group(2)
                    index.setdefault(fn, "%s:%d" % (name, lineno))
        except OSError:
            continue
    return index


# ---------------------------------------------------------------- 取值分布
#
# 每个算子一个「真实配比」：[(权重, 造值函数)]。逐行按 i 的确定性哈希取档，
# 因此同一次运行、不同次运行都是同一个分布，不会因为随机种子改变结论。


def _pick(mix, i):
    total = sum(w for w, _ in mix)
    h = (i * 2654435761) % total
    acc = 0
    for w, fn in mix:
        acc += w
        if h < acc:
            return fn(i)
    return mix[-1][1](i)


def _ints(n, base, span=1000):
    return [(base + k * 7) % span for k in range(n)]


def _num_array_mix():
    """数值数组：真实数据里既有短数组（落不到门槛）也有长数组。"""
    return [
        (30, lambda i: _ints(8, i)),
        (45, lambda i: _ints(24, i)),
        (25, lambda i: _ints(64, i)),
    ]


def _str_mix():
    """字符串：短标识符 / 中长路径 / 带查询串的长串 / 非字符串（落 undefined）。"""
    return [
        (35, lambda i: "s%03d" % (i % 200)),
        (30, lambda i: "Path/To Item %d?q=%d&x=1" % (i % 50, i % 17)),
        (25, lambda i: ("a/b c%d?" % i) * 12),
        (10, lambda i: None),
    ]


def _encoded_mix():
    return [
        (40, lambda i: "s%03d" % (i % 200)),
        (30, lambda i: "Path%%2FTo%%20Item%%20%d" % (i % 50)),
        (30, lambda i: "%%E4%%B8%%AD%%E6%%96%%87%d" % (i % 9)),
    ]


def _plain_mix():
    """纯文本分布：**完全不含** `%`。用于量化「`'%' in v` 门控」在真实数据上的代价。"""
    return [
        (40, lambda i: "plain_text_%d" % i),
        (35, lambda i: "s%03d" % (i % 200)),
        (15, lambda i: "\u4e2d\u6587\u7eaf\u6587\u672c%d" % i),
        (10, lambda i: None),
    ]


def _clone_mix():
    """`$clone`：dict 与序列各半，宽度落在门槛两侧（最坏元素类型那一侧）。"""
    def dict_small(i):
        return {"a": i, "b": "x%d" % i}

    def dict_wide(i):
        return {"k%d" % k: k for k in range(4)}

    def list_wide(i):
        return [i + k for k in range(8)]

    def list_wider(i):
        return [{"v": i + k} for k in range(8)]

    return [(25, dict_small), (25, dict_wide), (25, list_wide), (25, list_wider)]


def _zp_mix():
    return [
        (40, lambda i: (_ints(192, i), _ints(192, i + 1))),
        (40, lambda i: (_ints(256, i), _ints(256, i + 1))),
        (20, lambda i: (_ints(512, i), _ints(512, i + 1))),
    ]


def _match_mix():
    return [
        (40, lambda i: "abc%03d def%d" % (i % 100, i % 10)),
        (30, lambda i: ("x%d y%d " % (i, i)) * 8),
        (20, lambda i: "no digits here"),
        (10, lambda i: ""),
    ]


def _base_mix():
    return [
        (40, lambda i: i % 1000),
        (30, lambda i: (i * 7919) % 1000000),
        (30, lambda i: -((i * 31) % 5000)),
    ]


def _big_int_mix():
    """大整数分布：`$formatBase` 的量级门控（`|n| >= 10**6`）会放行的那一档。

    与 `_base_mix` 配对使用 —— 门控拦截档看 `_base_mix`，放行档看这里。
    两者必须分开报，混在一起算平均数会把「门控挡掉」与「真跑 Rust」搅在一起。
    """
    return [
        (40, lambda i: 10 ** 6 + i * 7919),
        (30, lambda i: 10 ** 12 + i * 104729),
        (30, lambda i: -(10 ** 9 + i * 6151)),
    ]


def _millis_mix():
    return [
        (50, lambda i: i * 60000),
        (30, lambda i: 1700000000000 + i * 1000),
        (20, lambda i: 0),
    ]


def _iso_mix():
    return [
        (60, lambda i: "2024-01-%02dT12:34:56Z" % (i % 28 + 1)),
        (25, lambda i: "2024-06-%02dT00:00:00.000Z" % (i % 28 + 1)),
        (15, lambda i: "not-a-date"),
    ]


def _decimal_mix():
    return [
        (50, lambda i: "n%02d" % (i % 20)),
        (30, lambda i: "%06d" % (i % 1000000)),
        (20, lambda i: "abc"),
    ]


def _float_mix():
    return [
        (50, lambda i: float(i % 50) / 4.0),
        (30, lambda i: (i % 1000) * 1.5),
        (20, lambda i: 0.0),
    ]


#: `$match` 的 helper-only 臂复用一个编译好的正则（产物侧每行也走缓存）。
_MT_RX = re.compile(r"[a-z]+[0-9]+")


# ---------------------------------------------------------------- 算子表
#
# status：`wired`（已接线）/ `candidate`（待评）—— CI 契约只认这两个值。
# rust_export：Rust 侧对应的导出名；None 表示「Rust 侧还没有实现」。

OPS = [
    # ---- 已接线 13 个（回归）----
    dict(name="mn", symbol="$min", status="wired", rust_export="mn",
         expr="$min($.a)", mix=_num_array_mix, helper=lambda d: rt._mn(d["a"]),
         gate="len(a) >= 24"),
    dict(name="mx", symbol="$max", status="wired", rust_export="mx",
         expr="$max($.a)", mix=_num_array_mix, helper=lambda d: rt._mx(d["a"]),
         gate="len(a) >= 24"),
    dict(name="av", symbol="$average", status="wired", rust_export="av",
         expr="$average($.a)", mix=_num_array_mix, helper=lambda d: rt._av(d["a"]),
         gate="len(a) >= 24"),
    dict(name="ds", symbol="$distinct", status="wired", rust_export="ds",
         expr="$distinct($.a)", mix=_num_array_mix, helper=lambda d: rt._ds(d["a"]),
         gate="len(a) >= 8"),
    dict(name="eu", symbol="$encodeUrl", status="wired", rust_export="eu",
         expr="$encodeUrl($.a)", mix=_str_mix, helper=lambda d: rt._eu(d["a"]),
         gate="type(v) is str"),
    dict(name="du", symbol="$decodeUrl", status="wired", rust_export="du",
         expr="$decodeUrl($.a)", mix=_encoded_mix, helper=lambda d: rt._du(d["a"]),
         gate="type(v) is str（无长度门控）"),
    dict(name="zp", symbol="$zip", status="wired", rust_export="zp",
         expr="$zip($.a, $.b)", mix=_zp_mix, helper=lambda d: rt._zp(d["a"], d["b"]),
         gate="len(cols) == 2 且 min(len(c)) >= 192"),
    dict(name="cl", symbol="$clone", status="wired", rust_export="cl",
         expr="$clone($.a)", mix=_clone_mix, helper=lambda d: rt._cl(d["a"]),
         gate="dict 宽 >= 2；list/tuple 宽 >= 8"),
    dict(name="fms", symbol="$fromMillis", status="wired", rust_export="fms",
         expr="$fromMillis($.a)", mix=_millis_mix, helper=lambda d: rt._fms(d["a"]),
         gate="picture is undefined/None"),
    dict(name="tms", symbol="$toMillis", status="wired", rust_export="tms",
         expr="$toMillis($.a)", mix=_iso_mix, helper=lambda d: rt._tms(d["a"]),
         gate="无门控"),
    dict(name="fgi", symbol="$formatInteger", status="wired", rust_export="fgi",
         expr="$formatInteger($.a, '0000')", mix=_base_mix,
         helper=lambda d: rt._fgi(d["a"], "0000"),
         gate="picture 不在 _FGI_ALPHA 且不含 '.'"),
    dict(name="pgi", symbol="$parseInteger", status="wired", rust_export="pgi",
         expr="$parseInteger($.a, '0000')", mix=_decimal_mix,
         helper=lambda d: rt._pgi(d["a"], "0000"),
         gate="无门控"),
    dict(name="fnm", symbol="$formatNumber", status="wired", rust_export="fnm",
         expr="$formatNumber($.a, '#,##0.00')", mix=_float_mix,
         helper=lambda d: rt._fnm(d["a"], "#,##0.00", UNDEF),
         gate="options 为空、单段、_fnm_rust_ok"),

    # ---- 本轮新接线的 3 个（$encodeUrlComponent / $decodeUrlComponent / $formatBase）
    # 运行期已经接上（`dcg/runtime.py` 的 `_ue`/`_ud`/`_fb` 里有 `_ACC.*` 分支），
    # 所以 status 必须是 wired：判据取 helper_only（走真实运行期门控的两臂），
    # 而不是 direct（那条臂拿「直接调扩展」比「已经会调扩展的 helper」，没意义）。
    # 按数据分布拆成多行，因为这三个算子都是**双模态**的 —— 同一个算子在不同
    # 分布下比值差一个量级，混成一个平均数会把「门控放行档」和「门控拦截档」
    # 搅在一起，两边都不可信。
    dict(name="ue", symbol="$encodeUrlComponent", status="wired", rust_export="ue",
         expr="$encodeUrlComponent($.a)", mix=_str_mix,
         helper=lambda d: rt._ue(d["a"]),
         gate="无条件接（type(v) is str 由 Rust 侧返回 None 兜底）"),
    dict(name="ud", symbol="$decodeUrlComponent", status="wired", rust_export="ud",
         expr="$decodeUrlComponent($.a)", mix=_encoded_mix,
         helper=lambda d: rt._ud(d["a"]),
         gate="type(v) is str 且 '%' in v（放行档：真实 URL 场景）"),
    dict(name="ud_text", symbol="$decodeUrlComponent", status="wired", rust_export="ud",
         expr="$decodeUrlComponent($.a)", mix=_plain_mix,
         helper=lambda d: rt._ud(d["a"]),
         gate="type(v) is str 且 '%' in v（拦截档：纯文本，门控挡掉不进 Rust）"),
    dict(name="fb", symbol="$formatBase", status="wired", rust_export="fb",
         expr="$formatBase($.a, 16)", mix=_base_mix,
         helper=lambda d: rt._fb(d["a"], 16),
         gate="基数 2/8/10/16 走 Python `format()`，不碰 Rust（拦截档）"),
    dict(name="fb_cold", symbol="$formatBase", status="wired", rust_export="fb",
         expr="$formatBase($.a, 7)", mix=_base_mix,
         helper=lambda d: rt._fb(d["a"], 7),
         gate="冷门基数但小值（|n| < 10**6）—— 量级门控挡掉，属拦截档"),
    dict(name="fb_big", symbol="$formatBase", status="wired", rust_export="fb",
         expr="$formatBase($.a, 7)", mix=_big_int_mix,
         helper=lambda d: rt._fb(d["a"], 7),
         gate="冷门基数 + 大值（|n| >= 10**6）—— 放行档，真跑 Rust"),
    dict(name="mt", symbol="$match", status="candidate", rust_export="mt",
         expr="$match($.a, /[a-z]+[0-9]+/)", mix=_match_mix,
         helper=lambda d: rt._mt(d["a"], _MT_RX),
         rust_call=lambda b, d: b(d["a"], _MT_RX),
         deliberately_unregistered=True,
         gate="（故意不注册：实测 0.56–0.68×，注册即负优化）"),
]


# ---------------------------------------------------------------- 状态判定


def wire_state_of(spec, acc, src_index):
    """三态判定：wired / implemented_not_wired / not_implemented。

    `wired` 需要两件事同时成立：运行期门控里调 `_ACC.<name>`（由算子表声明），
    且扩展里确实导出了该名字。声明与实物不符时降级为「未接线」并记一条告警，
    免得「表里写了 wired、其实早就断了」这种漂移悄悄过掉。

    第四态 `needs_rebuild`：Rust 源码里已经有 `fn o_<name>`、`register()` 也注册了，
    但**已编译的扩展**还没导出它 —— 这是「改了源码没重新构建」，跑基准前
    `python build_accel.py` 即可，不是设计缺口。

    第五态 `deliberately_unregistered`：源码里有实现，但**故意不注册**（`$match`
    就是这种：实测 0.56–0.68×，注册即负优化）。这与「漏注册」是两件事，混在一起
    会让每次基准都报一条「需要重建」，把真正的漏注册淹掉。
    """
    exported = acc is not None and hasattr(acc, spec["rust_export"])
    in_source = spec["rust_export"] in src_index
    if spec["status"] == "wired" and exported:
        return "wired", exported, in_source
    if spec.get("deliberately_unregistered") and in_source and not exported:
        return "deliberately_unregistered", exported, in_source
    if in_source and not exported:
        return "needs_rebuild", exported, in_source
    if exported or in_source:
        return "implemented_not_wired", exported, in_source
    return "not_implemented", exported, in_source


# ---------------------------------------------------------------- 单算子测量


def _rows_for(spec, n):
    mix = spec["mix"]()
    rows = []
    for i in range(n):
        v = _pick(mix, i)
        rows.append({"a": v[0], "b": v[1]} if isinstance(v, tuple) else {"a": v})
    return rows


def _schema(spec, n):
    return {
        "name": "accel_%s_%d" % (spec["name"], n),
        "output": {"shape": "list"},
        "out": {"type": "object",
                "fields": {"o": {"type": "any", "expr": spec["expr"]}}},
    }


def _compile(schema, tag):
    ns = {}
    exec(compile(dcg.generate(schema), "<%s>" % tag, "exec"), ns)
    return ns["run"]


def _call_all(spec, data):
    """helper-only 臂：只跑算子本身，不含行构造与产物组装。

    注意：这条臂对**已接线**算子成立 —— `rt._<name>` 内部有 `_ACC.<name>` 分支，
    开关 `rt._ACC` 就能切到 Rust/Python 两臂。对**未接线**算子它恒等于 1.0，
    因为 Python helper 根本不分派到 Rust；那种情况要看 `_call_direct` 的
    `direct_ratio`（直接调扩展导出 vs 直接调 Python helper）。
    """
    helper = spec["helper"]
    for d in data:
        helper(d)


def _call_direct(spec, acc, data):
    """直接对比臂：扩展导出 vs Python helper，两者都不含行构造。

    这是「还没接线」时的唯一有效口径：此时运行期不调 Rust，`_call_all` 的两臂
    是同一份 Python 代码（比值恒 ≈1.0），只有直接调扩展才能测出算子自身的快慢。
    """
    fn = spec.get("rust_call")
    if fn is None or acc is None or not hasattr(acc, spec["rust_export"]):
        return None
    bound = getattr(acc, spec["rust_export"])
    for d in data:
        fn(bound, d)


def _same_direct(spec, acc, data):
    """直接臂的输出一致性：扩展导出 vs Python helper，逐值深比较。

    逐行比而不是整批比 —— 算子里有「首个非法输入即返回 undefined」这类短路
    语义时，整批比会把后面的差异掩盖掉。

    `None` 的语义按既有 13 个导出的约定处理：**`None` = 「Rust 不接管，落回
    Python」**，不是「结果是 None」。所以 `None` 时该行的期望值是 Python helper
    的结果，直接拿 `None` 去比会把「正确回退」误判成输出不一致（`ud` 对非字符串
    输入就是这种情形：Rust 返回 `None`，Python 侧返回 undefined）。
    """
    fn = spec.get("rust_call")
    if fn is None or acc is None or not hasattr(acc, spec["rust_export"]):
        return None
    bound = getattr(acc, spec["rust_export"])
    for d in data:
        got = fn(bound, d)
        want = spec["helper"](d)
        if got is None:
            continue          # 回退约定：这一行本来就该由 Python 出结果
        if not same(got, want):
            return False
    return True


def _count_rust_calls(spec, acc, data):
    """统计 helper_only 的 on 臂里 Rust 导出真被调用了几次。

    这是判据的守门人。带门控的算子里，有一类数据分布会让门控**整档拦截**：
    `ud` 的串里没有 `%`、`fb` 走 C 基数（2/8/10/16）时 Rust 一次都不会被调用，
    两臂跑的是同一份 Python 代码。此时量到的比值要么是门控自身的开销（`ud`
    多一次 `"%" in v`，实测约 −9%），要么纯粹是噪声 —— 拿它判 `regression`
    就是假红。所以先数调用次数，一次都没调就单列成 `gate_intercept`，不进
    回退判据。
    """
    fn = getattr(acc, spec["rust_export"], None)
    if fn is None:
        return None
    hits = [0]

    def wrapper(*a, **k):
        hits[0] += 1
        return fn(*a, **k)

    setattr(acc, spec["rust_export"], wrapper)
    try:
        rt._ACC = acc
        _call_all(spec, data)
    finally:
        setattr(acc, spec["rust_export"], fn)
        rt._ACC = acc
    return hits[0]


def bench_op(spec, rows_n, reps, arms, src_index):
    """跑一个算子：端到端 + helper-only 两条口径，各出 on/off 比值。"""
    state, exported, in_source = wire_state_of(spec, arms.acc, src_index)
    rec = {
        "name": spec["name"], "symbol": spec["symbol"], "status": spec["status"],
        "wire_state": state,
        "rust_export": spec["rust_export"],
        "rust_exported": exported,
        "rust_in_source": in_source,
        "rust_source_hit": src_index.get(spec["rust_export"]),
        "expr": spec["expr"], "gate": spec["gate"], "rows": rows_n,
    }
    try:
        data = _rows_for(spec, rows_n)
        fn = _compile(_schema(spec, rows_n), spec["name"])

        # 输出一致性：两条臂各跑一次，逐值深比较
        arms.on()
        out_on = fn(data)
        arms.off()
        out_off = fn(data)
        rec["same_output"] = same(out_on, out_off)
        arms.restore()

        # ---- 端到端双臂交错（奇数轮先 off 后 on，抵消顺序漂移）
        on_ms, off_ms, ratios = [], [], []
        for r in range(reps):
            if r % 2 == 0:
                arms.on()
                a, _ = measure(lambda: fn(data))
                arms.off()
                b, _ = measure(lambda: fn(data))
            else:
                arms.off()
                b, _ = measure(lambda: fn(data))
                arms.on()
                a, _ = measure(lambda: fn(data))
            on_ms.append(a * 1e3)
            off_ms.append(b * 1e3)
            ratios.append(b / a if a else float("inf"))
        arms.restore()
        rec.update({
            "rust_ms": round(median(on_ms), 4),
            "python_ms": round(median(off_ms), 4),
            "ratio": round(median(ratios), 4),
            "ratio_samples": [round(x, 4) for x in ratios],
            "jitter": {"min": round(min(ratios), 4),
                       "median": round(median(ratios), 4),
                       "max": round(max(ratios), 4)},
            "samples": len(ratios),
            "python_us_per_row": round(median(off_ms) * 1e3 / rows_n, 4),
            # 未接线的算子两条臂走的是同一份 Python 代码，比值只是噪声
            # 端到端比值只在已接线时有意义（未接线时两臂是同一份 Python 代码）
            "ratio_meaningful": state == "wired",
            # 判据比值（= helper_only.ratio）：接线与回退都按它判
            "criterion_ratio": None,
        })

        # ---- helper-only：算子本身能快多少（不看行构造）
        h_on, h_off, h_ratios = [], [], []
        for r in range(reps):
            if r % 2 == 0:
                arms.on()
                a2, _ = measure(lambda: _call_all(spec, data))
                arms.off()
                b2, _ = measure(lambda: _call_all(spec, data))
            else:
                arms.off()
                b2, _ = measure(lambda: _call_all(spec, data))
                arms.on()
                a2, _ = measure(lambda: _call_all(spec, data))
            h_on.append(a2 * 1e3)
            h_off.append(b2 * 1e3)
            h_ratios.append(b2 / a2 if a2 else float("inf"))
        arms.restore()
        rec["helper_only"] = {
            "rust_ms": round(median(h_on), 4),
            "python_ms": round(median(h_off), 4),
            "ratio": round(median(h_ratios), 4),
            "python_us_per_row": round(median(h_off) * 1e3 / rows_n, 4),
            "arm": "runtime gate（_ACC 开/关）",
        }

        # ---- direct：直接调扩展导出 vs 直接调 Python helper
        #
        # **只对未接线算子有意义。** 已接线时 `_call_all` 走的是 `rt._<name>`，
        # 而它内部已经会分派到 Rust —— 那条「python」臂其实也在调 Rust，量出来的
        # 是「Python 包装开销 vs 裸调用」而不是「Rust vs Python」，比值会虚高到
        # 1.8 倍上下，纯属误导。所以这里只给未接线状态算，已接线一律 None。
        d_on, d_off, d_ratios = [], [], []
        if state != "wired" and spec.get("rust_call") is not None \
                and hasattr(arms.acc, spec["rust_export"]):
            for r in range(reps):
                if r % 2 == 0:
                    a3, _ = measure(lambda: _call_direct(spec, arms.acc, data))
                    b3, _ = measure(lambda: _call_all(spec, data))
                else:
                    b3, _ = measure(lambda: _call_all(spec, data))
                    a3, _ = measure(lambda: _call_direct(spec, arms.acc, data))
                d_on.append(a3 * 1e3)
                d_off.append(b3 * 1e3)
                d_ratios.append(b3 / a3 if a3 else float("inf"))
            rec["direct"] = {
                "rust_ms": round(median(d_on), 4),
                "python_ms": round(median(d_off), 4),
                "ratio": round(median(d_ratios), 4),
                "ratio_samples": [round(x, 4) for x in d_ratios],
                "jitter": {"min": round(min(d_ratios), 4),
                           "median": round(median(d_ratios), 4),
                           "max": round(max(d_ratios), 4)},
                "same_output": _same_direct(spec, arms.acc, data),
                "arm": "direct export vs python helper（绕开运行期门控）",
            }
            # 未接线时 `same_output` 若取自运行期两臂，两边都是同一份 Python 代码，
            # 恒为 True，等于没验证。改用 direct 臂的逐行对拍结果。
            if state != "wired" and rec["direct"]["same_output"] is not None:
                rec["same_output"] = rec["direct"]["same_output"]
                rec["same_output_source"] = "direct"

        # ---- 算子在单行成本里占多大份额，以及 Amdahl 上界
        #
        # `op_share` = 算子自身耗时 / 单行总耗时。>=0.5 说明这一行的成本主要
        # 就是这个算子，只要 Rust 侧更快，端到端就有大空间；接近 0 说明行构造
        # 与产物组装才是大头，算子再快也拉不动端到端（这时该看 Amdahl 上界）。
        # 两次测量各自带噪声，所以 op_share 允许 >1，此时按「算子主导」处理。
        total = rec["python_us_per_row"]
        op = rec["helper_only"]["python_us_per_row"]
        share = (op / total) if total > 0 else None
        rec["op_share"] = round(share, 3) if share is not None else None
        rec["amdahl_upper_bound"] = (
            round(1.0 / (1.0 - share), 3) if share is not None and 0 < share < 1 else None)

        # ---- 判据口径：接线判据与回退判据都取「算子自身」的加速比
        #
        # 设计依据（三条，缺一不可）：
        # 1. 需求原文是「RUST 实现的**算子性能**是 python 的 1.3 倍」——说的是
        #    算子自身的性能比，不是整个产物流水线的比值。
        # 2. 「才**接线**」意味着测量发生在接线**之前**：那时运行期还没调 Rust，
        #    端到端根本测不出 Rust 的收益，只有直接比 Rust 算子与 Python 算子
        #    才有意义。用端到端当门槛在逻辑上不成立。
        # 3. 端到端会被产物侧的行构造与结果保留稀释：`$zip` 保留整列结果每行
        #    多花约 90 µs，与走不走 Rust 无关。op_share=0.243 时，即使算子免费，
        #    端到端上界也只有 1/(1-0.243)=1.32；helper 1.379x 对应的端到端期望
        #    值是 1.072。所以端到端贴着 1.0 是**结构性正确结果**，不是接错线。
        #    拿它当门槛等于「谁的行构造占比高谁就永远不够格」，与算子质量无关。
        #
        # 端到端仍然有独立价值（能发现「接了线反而拖慢流水线」），所以保留为
        # 诊断列，并用非致命的 `end_to_end_slow` 标出来，不影响 exit code。
        #
        # 判据比值的取法：
        # * 已接线 —— 取 `helper_only.ratio`（走运行期门控，`_ACC` 开/关两臂）；
        # * 未接线 —— 取 `direct.ratio`（直接调扩展导出 vs 直接调 Python helper）。
        #   未接线时运行期不调 Rust，`helper_only` 两臂同源恒 ≈1.0，只有 direct
        #   才能回答「这个算子自身值不值得接线」。
        direct = rec.get("direct") or {}
        if state == "wired":
            rec["criterion_ratio"] = rec["helper_only"]["ratio"]
            rec["criterion_arm"] = "helper_only"
        elif direct.get("ratio") is not None:
            rec["criterion_ratio"] = direct["ratio"]
            rec["criterion_arm"] = "direct"
        else:
            rec["criterion_ratio"] = None
            rec["criterion_arm"] = None

        # ---- 门控是否真的放行了 Rust（判据的前提）
        #
        # 带门控的算子存在「整档拦截」的分布：`ud` 的串里没有 `%`、`fb` 走 C
        # 基数时，Rust 一次都不会被调用。这时 helper_only 的两臂是同一份 Python
        # 代码，量到的比值只是门控自身开销（`ud` 约 −9%）或噪声，不能当回退判据。
        rec["rust_calls"] = _count_rust_calls(spec, arms.acc, data) if state == "wired" else None
        rec["gate_intercept"] = bool(state == "wired" and rec["rust_calls"] == 0)
        # 贴线：判据比值落在门槛 ±BORDERLINE 内。只标注、不改判定（见常量处说明）。
        crit = rec.get("criterion_ratio")
        rec["borderline"] = bool(
            crit is not None
            and abs(crit - THRESHOLD) <= THRESHOLD * BORDERLINE)
        rec["end_to_end_slow"] = bool(
            state == "wired" and not rec["gate_intercept"]
            and rec["ratio"] < REGRESSION_FLOOR)
        rec["e2e_diluted"] = bool(
            state == "wired" and not rec["gate_intercept"]
            and rec["criterion_ratio"] is not None
            and rec["criterion_ratio"] >= THRESHOLD and rec["ratio"] < THRESHOLD)

        rec["verdict"] = _verdict(state, rec, in_source)
    except Exception as exc:  # noqa: BLE001 - 单算子失败不拖垮整轮
        rec["error"] = "%s: %s" % (type(exc).__name__, exc)
        rec["verdict"] = "error"
    return rec


def _verdict(state, rec, in_source):
    ratio = rec.get("ratio")
    if state == "wired":
        if ratio is None:
            return "error"
        # 门控整档拦截：Rust 一次没被调用，比值不是算子性能，不能判回退。
        # 单独成一档，报告里能一眼看出「这一行是分布选错了，不是接线坏了」。
        if rec.get("gate_intercept"):
            return "gate_intercept"
        # 判据统一取 `criterion_ratio`（已接线 = helper_only，未接线 = direct）。
        # 端到端只作诊断：`end_to_end_slow` 由 bench_op 标出，不进这里。
        gate = rec.get("criterion_ratio")
        if gate is None:
            gate = ratio
        if gate < REGRESSION_FLOOR:
            return "regression"
        if gate < THRESHOLD:
            return "below_threshold_wired"
        return "keep"
    # ---- 未接线：先看「就算零成本够不够得着门槛」，够不着就不必实现
    if state == "deliberately_unregistered":
        return "deliberately_not_wired"
    if state == "needs_rebuild":
        return "rebuild_then_measure"
    # 直接对拍已经拿到真数字时，它比 Amdahl 上界更权威：direct 就是「算子自身」
    # 的实测比值，上界只是按 op_share 推的期望值。
    direct = rec.get("direct") or {}
    d = direct.get("ratio")
    if d is not None:
        if direct.get("same_output") is False:
            return "output_mismatch"
        if d >= THRESHOLD:
            return "register_then_measure" if state == "implemented_not_wired" \
                else "implement_then_measure"
        return "below_threshold_candidate"
    bound = rec.get("amdahl_upper_bound")
    if bound is not None and bound < THRESHOLD:
        return "no_headroom"
    if state == "implemented_not_wired":
        return "register_then_measure"
    return "implement_then_measure" if in_source is False else "measure_missing"


# ---------------------------------------------------------------- 输出


def _fmt(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, float):
        return ("%." + str(nd) + "f") % x
    return str(x)


HEAD = ("算子", "符号", "status", "wire_state", "行数", "Rust ms", "Python ms",
        "端到端", "helper", "direct", "判据", "Amdahl", "同值", "判定")


def _cells(r):
    h = r.get("helper_only") or {}
    d = r.get("direct") or {}
    ratio = r.get("ratio")
    ratio_s = "-" if ratio is None else "%.3fx" % ratio
    if ratio is not None and not r.get("ratio_meaningful", True):
        ratio_s += "*"
    crit = r.get("criterion_ratio")
    crit_s = "-" if crit is None else "%.3fx" % crit
    arm = r.get("criterion_arm")
    if arm == "direct":
        crit_s += "^"
    return [
        r.get("name", "?"),
        r.get("symbol", "?"),
        r.get("status", "?"),
        r.get("wire_state", "?"),
        str(r.get("rows", "-")),
        _fmt(r.get("rust_ms")),
        _fmt(r.get("python_ms")),
        ratio_s,
        _fmt(h.get("ratio")) + "x",
        _fmt(d.get("ratio")) + "x" if d.get("ratio") is not None else "-",
        crit_s,
        _fmt(r.get("amdahl_upper_bound")),
        "Y" if r.get("same_output") else "N",
        r.get("verdict", "?"),
    ]


def print_table(recs):
    rows = [_cells(r) for r in recs]
    widths = [max([len(HEAD[i])] + [len(row[i]) for row in rows])
              for i in range(len(HEAD))]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(HEAD)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(HEAD))))
    if any(r.get("ratio_meaningful") is False for r in recs):
        print("* 端到端比值：未接线时两臂走同一份 Python 代码，只是噪声")
    if any(r.get("criterion_arm") == "direct" for r in recs):
        print("^ 判据取 direct（直接调扩展导出 vs Python helper），绕开运行期门控")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="原生加速后端真实配比裁决基准（>=1.3x 才接线）")
    ap.add_argument("--rows", type=int, default=None,
                    help="每个算子的配比行数（默认 20000；--ci 下 2000）")
    ap.add_argument("--reps", type=int, default=None,
                    help="双臂交错的轮数（默认 5；--ci 下 3）")
    ap.add_argument("--only", default=None,
                    help="只跑这些算子，逗号分隔（如 mt,ue,ud,fb）")
    ap.add_argument("--out", default=None,
                    help="JSON 输出路径（默认 _tmp/accel_bench.json）")
    ap.add_argument("--ci", action="store_true",
                    help="CI 档：rows=2000、reps=3，JSON 标 ci_mode=true")
    ap.add_argument("--fail-on-regression", dest="fail_on_regression",
                    action="store_true", default=True,
                    help="检出回退时 exit 1（默认开）")
    ap.add_argument("--no-fail-on-regression", dest="fail_on_regression",
                    action="store_false",
                    help="只报不失败（本地看数用）")
    args = ap.parse_args(argv)

    rows_n = args.rows if args.rows is not None else (2000 if args.ci else 20000)
    reps = args.reps if args.reps is not None else (3 if args.ci else 5)
    out_path = args.out or os.path.join("_tmp", "accel_bench.json")
    if not os.path.isabs(out_path):
        out_path = os.path.join(ROOT, out_path)

    # ---- 后端可用性（exit 2 只给「真·没有 Rust」）
    try:
        from dcg._accel import status as accel_status
        status = accel_status()
    except Exception as exc:  # noqa: BLE001
        status = {"loaded": False, "path": None,
                  "error": "%s: %s" % (type(exc).__name__, exc),
                  "candidates": []}

    arms = Arms()
    available = arms.acc is not None and bool(status.get("loaded"))
    exports = sorted(n for n in dir(arms.acc) if not n.startswith("_")) \
        if arms.acc is not None else []
    src_index = rust_source_index()

    print("accel backend:", "on" if available else "off", flush=True)
    print("status:", json.dumps(status, ensure_ascii=False), flush=True)
    if exports:
        print("exports(%d):" % len(exports), exports, flush=True)

    payload = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "ci_mode": bool(args.ci),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "protocol": {
            "threshold": THRESHOLD,
            "regression_floor": REGRESSION_FLOOR,
            "criterion": "criterion_ratio",
            "criterion_note": (
                "判据比值 criterion_ratio：已接线算子取 helper_only.ratio（走运行期"
                "门控，_ACC 开/关两臂）；未接线算子取 direct.ratio（直接调扩展导出 vs "
                "直接调 Python helper）。两者都是「算子自身」的性能比，不含行构造与"
                "产物组装。端到端 ratio 会被行构造与结果保留稀释，只作诊断：低于 "
                "regression_floor 时记 end_to_end_slow，不影响退出码。"
                "门控整档拦截（rust_calls == 0）的分布单列 gate_intercept，不进回退"
                "判据 —— 那时两臂跑的是同一份 Python 代码，比值只是门控开销。"),
            "statistic": STATISTIC,
            "rows": rows_n,
            "reps": reps,
            "runs_per_measure": RUNS,
            "warmup": WARMUP,
            "ratio_note": "比值 = 关 Rust / 开 Rust；>1 表示开 Rust 更快",
            "same_output_required": True,
            "wire_states": {
                "wired": "运行期门控里调 _ACC.<name>，且扩展导出该名字",
                "implemented_not_wired": "扩展里（或 Rust 源码里）有，但运行期还没接",
                "needs_rebuild": "Rust 源码里有且已注册，但已编译的扩展没导出（需 python build_accel.py）",
                "deliberately_unregistered": "源码里有实现但故意不注册（如 $match：注册即负优化），不是漏注册",
                "not_implemented": "扩展与 Rust 源码里都没有",
            },
            "rust_verified_off": {},
        },
        "accel": {
            "available": available,
            "path": status.get("path"),
            "error": status.get("error"),
            "candidates": status.get("candidates", []),
            "exports": exports,
        },
        "operators": [],
        "summary": {},
    }

    if not available:
        payload["summary"] = {
            "verdict": "accel_unavailable",
            "note": "本机没有可用的 Rust 后端；这是环境限制，不是回退。",
        }
        _write(payload, out_path)
        print("accel backend unavailable -> exit 2", flush=True)
        return 2

    # ---- 机器验证「关 Rust 真的关掉了」
    probe = verify_rust_off()
    payload["protocol"]["rust_verified_off"] = probe
    print("rust_verified_off:", json.dumps(probe, ensure_ascii=False), flush=True)
    if not probe["verified"]:
        payload["summary"] = {
            "verdict": "rust_off_not_verified",
            "note": "无法证明纯 Python 臂真的关掉了 Rust，加速比不可信。",
        }
        _write(payload, out_path)
        print("rust_verified_off failed -> exit 1", flush=True)
        return 1

    wanted = None
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in OPS if wanted is None or s["name"] in wanted]
    missing = sorted(wanted - {s["name"] for s in specs}) if wanted else []

    recs = []
    for spec in specs:
        rec = bench_op(spec, rows_n, reps, arms, src_index)
        recs.append(rec)
        print(json.dumps({k: rec.get(k) for k in
                          ("name", "wire_state", "ratio", "verdict", "same_output")},
                         ensure_ascii=False), flush=True)

    arms.restore()
    print()
    print_table(recs)

    by_verdict = {}
    for r in recs:
        by_verdict.setdefault(r.get("verdict", "?"), []).append(r["name"])

    regressions = sorted(by_verdict.get("regression", []))
    payload["operators"] = recs
    payload["summary"] = {
        "verdict_counts": {k: len(v) for k, v in sorted(by_verdict.items())},
        "by_verdict": {k: sorted(v) for k, v in sorted(by_verdict.items())},
        "by_wire_state": _group(recs, "wire_state"),
        "regressions": regressions,
        "end_to_end_slow": sorted(
            r["name"] for r in recs if r.get("end_to_end_slow")),
        "e2e_diluted": sorted(r["name"] for r in recs if r.get("e2e_diluted")),
        "gate_intercept": sorted(r["name"] for r in recs if r.get("gate_intercept")),
        "borderline": sorted(r["name"] for r in recs if r.get("borderline")),
        "candidates_to_wire": sorted(by_verdict.get("wire", [])),
        "unknown_only_names": missing,
        "all_same_output": all(r.get("same_output") is True for r in recs),
    }
    _write(payload, out_path)

    print()
    print("summary:", json.dumps(payload["summary"], ensure_ascii=False))
    print("written", out_path, flush=True)

    if regressions and args.fail_on_regression:
        print("REGRESSION: %s 低于 %.2fx" % (regressions, REGRESSION_FLOOR),
              flush=True)
        return 1
    return 0


def _group(recs, key):
    out = {}
    for r in recs:
        out.setdefault(r.get(key, "?"), []).append(r["name"])
    return {k: sorted(v) for k, v in sorted(out.items())}


def _write(payload, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
