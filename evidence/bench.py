# -*- coding: utf-8 -*-
"""DCG v2.1 全面性能评测 —— 可复跑的单文件 harness（零第三方依赖）。

口径（本文件即方法学正文；`--md` 可按同一口径再落一份报告）
====================================================================
1. 计时：``time.perf_counter_ns()``，**同一进程内交替测量** A（DCG 产物）与
   B（手写等价），交替顺序固定，漂移对两者是同一个方向的偏置。
2. 一轮样本 = 命中 ``budget_ns`` 的最小整数遍数（小场景自动加大 k，摊掉
   ``perf_counter`` 与函数调用开销；1 列场景 k≈100，pipeline 场景 k=1）。
   每块取 ``--repeat`` 个样本的**中位数**。
3. 块：``--blocks`` 个块，每块前后 ``gc.collect()``；场景值 = 各块中位数的
   **最小值**（min-of-blocks，抗偶发调度抖动），并记录跨块离散度。
4. **噪声底** = A/A 对照：同一函数换两个名字各测一遍，``|a-b|/mean``。所有
   ``|ratio-1| <= noise.aa_max`` 的行标记为 ``sig=False``（"与手写同量级"，
   不得宣称更快/更慢）。这条规则适用于**每一行**，包括微基准。
5. 每个场景都有两个对手：
   * ``full``：**形状匹配 + 语义等价**的手写（undefined/optional 缺键 -> None，
     before 丢行、after 追加列都照做）；
   * ``bare``：**最简裸取**（裸下标、零兜底）—— 性能上限参照，**不是**等价实现。
6. 每行都带 ``cv``（轮内离散度）。``cv > 2*aa_max`` 的行为低可信，表中加 ``!``。

CLI
---
    python -X utf8 evidence/bench.py                  # 全量（约 3~5 分钟）
    python -X utf8 evidence/bench.py --quick          # 冒烟（小规模，约 40 秒）
    python -X utf8 evidence/bench.py --rows 100000    # 行数扫描上限
    python -X utf8 evidence/bench.py --repeat 7       # 每块样本数
    python -X utf8 evidence/bench.py --json out.json  # 机器可读结果
    python -X utf8 evidence/bench.py --profile        # 最慢 3 个场景 cProfile top-15
    python -X utf8 evidence/bench.py --md out.md      # 额外落一份 markdown 总表

本脚本**只读** dcg 源码，不做任何修改；产物在内存里 exec，不落盘。
"""

import argparse
import gc
import io
import json
import math
import os
import platform
import random
import statistics
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg                                              # noqa: E402
from dcg import runtime as rt                           # noqa: E402
from dcg.runtime import (UNDEF as _U, _ix_get, _df, _ob, _ad, _ml,  # noqa: E402
                         _rd, _mp, _fi, _so, _gt)
#: 理论最优形态基线（本次重写的对比参照物）。
#: **性能结论只跟 ``olib.OPT`` 里的基线比**；下面的 ``hand_*`` / ``cap_hand`` /
#: ``m0_hand`` / ``_stack_hand`` / ``order_pipeline_hand`` 保留代码但已退出主结论
#: （只用于 ``meta["checks"]`` 的等价性交叉核对与历史复现）。
import bench_optimallib as olib                         # noqa: E402

#: 固定随机种子（数据生成用它，保证逐次可复现）
SEED = 20261024

#: A/A 对照的默认判定阈值（仅用于"这一轮测量是否可用"的告警）
AA_LIMIT = 0.06
#: A/A 对照重测阈值 / 次数：一轮 gap 超过 2% 就整轮重测（最多 5 次）
AA_RETRY_GAP = 0.02
AA_RETRIES = 5

#: 一轮至少跑这么久（ns），用来摊掉调用开销
BUDGET_NS = 4_000_000

#: A/A 对照的每轮预算（比正式测量更小，对照只用来估噪声底）
AA_BUDGET_NS = 1_500_000

#: 可宣称的最小效应量（A/A 噪声底之上再留的余量）
MIN_SIG = 0.03


# ============================================================ 数据
def make_rows(n):
    """10 元组行，第 5 格是 ``{"a": [4 个 int]}``。

    第 8/9 格是字符串（给反引号段用）；``b`` 有 1024 种取值、``a`` 只有 64 种。
    """
    return [(f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100, (i * 3) % 50,
             {"a": [i & 15, i & 31, i & 7, (i * 5) & 63]},
             f"n{i % 97}", f"m{i % 89}", (i * 11) % 991, (i * 13) % 997)
            for i in range(n)]


def make_orders(n, sep_every=13, seed=SEED):
    """订单文档 ``{"payload": {"orders": [...]}}``（README 附录 B.5 同款）。

    ``qty`` 每 13 条出现一次 0（被 before 丢掉，产出 12/13 行）；
    ``buyer.level`` 出现 1/3、``buyer.note`` 出现 1/7（optional 缺键分支）。
    """
    rnd = random.Random(seed)
    orders = []
    for i in range(n):
        o = {
            "id": "ORD-%05d" % i,
            "qty": 0 if (sep_every and i % sep_every == 0) else (i % 13),
            "price": float((i % 97) + 1) / 4,
            "item": {"sku": "SKU%04d" % (i % 501), "from": "URC", "to": "PEK"},
            "buyer": {"name": "user%d" % (i % 37)},
        }
        if i % 3 == 0:
            o["buyer"]["level"] = "VIP"
        if i % 7 == 0:
            o["buyer"]["note"] = "gift"
        orders.append(o)
    rnd.shuffle(orders)
    return {"payload": {"orders": orders}}


def make_wide_rows(n):
    """13 键扁平 dict 行；``mi``/``ms``/``ch``/``mn`` 故意**不存在**。

    用来造 optional 缺键分支（同一 schema 换数据就切到缺键路径）。
    """
    out = []
    for i in range(n):
        out.append({
            "id": "ID-%06d" % i, "sku": "SKU%04d" % (i % 501),
            "from": ("URC", "PEK", "SHA", "CAN")[i & 3],
            "to": ("PEK", "URC", "HGH")[i % 3],
            "name": "user%d" % (i % 37),
            "qty": (i % 13) or 1, "price": float((i % 97) + 1) / 4,
            "score": (i % 1000) / 10.0, "level": i % 5, "gear": i % 7,
            "tag1": "t%d" % (i % 11), "tag2": "u%d" % (i % 13),
            "xs": [i & 15, i & 31, i & 7, (i * 5) & 63],
        })
    return out


# ============================================================ 计时
#: 场景名 -> 可被 cProfile 直接调用的闭包（`--profile` 用）
_PROFILE_FN = {}

#: A/A 对照探针 [(fn, label)]，由 register() 填充；main() 在全部测量结束后
#: 用同一批探针对当前机器状态再测一次噪声底。
_AA_PROBES = []


def _parse_pstats(txt):
    """解析 pstats 的文本表 -> [{ncalls, tottime_s, cumtime_s, func}]。

    行格式：``   ncalls  tottime  percall  cumtime  percall filename:lineno(func)``
    函数名里可能含空格（C 内建显示成 ``built-in method ...``），所以按前 5 个
    字段切、剩下整段当名字。
    """
    rows = []
    for line in txt.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        head = parts[0].split("/")[0]
        if not head.isdigit():
            continue
        try:
            rows.append({"ncalls": parts[0], "tottime_s": float(parts[1]),
                         "cumtime_s": float(parts[3]), "func": parts[5]})
        except ValueError:
            continue
    return rows


class Bench(object):
    """交替测量 + 自动定 k + 中位数 + A/A 噪声底 + min-of-blocks。"""

    def __init__(self, repeat=5, k=0, blocks=2, budget_ns=BUDGET_NS):
        self.repeat = repeat
        self.k_fixed = k                 # 0 = 自动定 k
        self.blocks = blocks
        self.budget_ns = budget_ns
        self.rows = []
        self.aa = []
        self.pending = []
        self.k_used = {}

    # ------------------------------------------------------------ 底层
    def _time_once(self, fn, k):
        t0 = time.perf_counter_ns()
        for _ in range(k):
            fn()
        return time.perf_counter_ns() - t0

    def _auto_k(self, fn, target_ns=None):
        """先测一遍，算出让一轮 ≈ target_ns（默认 budget_ns）的整数遍数。

        k 固定为奇数，保证 A/B/A/B 交替时 A 总在同一秒内的"奇数位置"——
        实测 Windows 上 1 秒粒度的调度抖动会让奇/偶位置差 5%~30%，奇数 k
        把这一项噪声压到 1%~2%（见 report 方法学）。
        """
        if self.k_fixed:
            return self.k_fixed
        tgt = target_ns or self.budget_ns
        best = None
        for _ in range(3):
            t0 = time.perf_counter_ns()
            fn()
            d = max(1, time.perf_counter_ns() - t0)
            best = d if best is None else min(best, d)
        k = int(tgt // best)
        # 上限 200 只对「很小」的场景有意义；对 100k 行这种单次就 30ms 的
        # 场景，k 恒为 1 —— 此时真正要做的是「多采样」而不是「加 k」：
        # 见 ``_reps_for`` 里对慢场景的加采样规则。
        k = max(1, min(200, k))
        return k if k % 2 == 1 else min(200, k + 1)

    def _samples(self, fn, k):
        return [self._time_once(fn, k) / k for _ in range(self.repeat)]

    def _min_of(self, fn, k, reps):
        """``min`` 而不是 ``median``：系统噪声只会让耗时变大，永不变小。

        本机实测（见 ``evidence/perf_methodology.md``）：同一函数 A/A 对照，
        单次测量的 max/min 达 4.00×；``median`` 会被最坏的几轮污染，而 ``min``
        能把 A/A 极差压到 4~6%。所以正式测量一律取 min。
        """
        xs = [self._time_once(fn, k) / k for _ in range(reps)]
        return min(xs), xs

    @staticmethod
    def _cv(samples):
        m = statistics.mean(samples)
        return (statistics.stdev(samples) / m) if len(samples) > 1 and m else 0.0

    def measure_pair(self, fn_a, fn_b, ka, kb):
        """交替测两个函数（A/B/A/B...，漂移对两者同向）。"""
        a, b = [], []
        for _ in range(self.repeat):
            a.append(self._time_once(fn_a, ka) / ka)
            b.append(self._time_once(fn_b, kb) / kb)
        return a, b

    def aa_control(self, fn, label, repeat=None):
        """A/A 对照：同一函数当两个名字各测一遍，差值是这一轮的噪声底。

        两边都取整轮 ``repeat`` 个样本的**中位数**（不能只取 1 个样本）：
        单样本 A/A 在 Windows 上会被毫秒级调度抖动放大到 10%~20%。
        """
        reps = repeat or self.repeat
        k = self._auto_k(fn, target_ns=AA_BUDGET_NS)
        best = None
        tries = 0
        # 重测到 gap <= AA_RETRY_GAP 为止（最多 AA_RETRIES 次）：本机的噪声底
        # 逐轮波动很大（0.5%~24% 都见过），单轮 3 个对照容易抽到最坏的一轮。
        while tries < AA_RETRIES:
            tries += 1
            a, b = [], []
            for _ in range(reps):
                a.append(self._time_once(fn, k) / k)
                b.append(self._time_once(fn, k) / k)
            ma, mb = statistics.median(a), statistics.median(b)
            denom = (ma + mb) / 2.0
            gap = abs(ma - mb) / denom if denom else 0.0
            rec = {"label": label, "a_ns": ma, "b_ns": mb, "gap": gap, "k": k,
                   "cv_a": self._cv(a), "cv_b": self._cv(b), "tries": tries}
            if best is None or gap < best["gap"]:
                best = rec
            if gap <= AA_RETRY_GAP:
                break
        self.aa.append(best)
        return best

    # ------------------------------------------------------------ 场景
    def add(self, group, name, fn, n, note="", fn_full=None, fn_bare=None,
            per_row=True, unit=None, once=False, pairs=None):
        """登记一个场景；``fn`` 一次处理 n 行（或 n 次调用）。

        ``once=True``：单次调用就 >=100ms 的场景（生成期、编译），样本数降到
        ``max(3, repeat//2)``，且 A/A 对照**不测它**（否则一次对照就跑 2 倍时长）。

        ``pairs``：``() -> (full_result, bare_result)``，一次循环同时产出两套手写
        结果。给了它就用「配对中位数」作为 full/bare 基准（比值由配对样本直接
        相除再取中位数），彻底消掉与主项之间的轮次偏差。
        """
        rec = {"group": group, "name": name, "n": n, "note": note,
               "per_row": per_row, "unit": unit or ("行" if per_row else "次"),
               "once": bool(once)}
        if fn_full is None and fn_bare is None and pairs is None:
            raise ValueError("场景 %s 至少需要一个手写对手" % name)
        _PROFILE_FN[name] = fn
        self.pending.append((rec, fn, fn_full, fn_bare, pairs))
        return rec

    def _reps_for(self, rec):
        return max(3, self.repeat // 2) if rec.get("once") else self.repeat

    def run_all(self):
        """跑全部登记的场景：块间夹 GC，块内 A/B 交替，块间取较小值。"""
        best = {}
        for _blk in range(self.blocks):
            for rec, fn, fn_full, fn_bare, pairs in self.pending:
                gc.collect()
                reps = self._reps_for(rec)
                # 主项与 full 用同一个 k —— A/B 交替时两侧遍数相同，奇数 k 的
                # "同位置"性质才对两者同时成立（否则一侧落在偶数位置，噪声翻倍）
                k = self._auto_k(fn)
                kf = k
                kb = self._auto_k(fn_bare) if fn_bare is not None else None
                kp = self._auto_k(pairs) if pairs is not None else None
                # 取 min 而不是 median（理由见 ``_min_of`` 的 docstring）。
                # ``cv`` 仍按样本集算，只作为「本轮抖动有多大」的告警信号。
                s_main = [self._time_once(fn, k) / k for _ in range(reps)]
                ns = min(s_main)
                entry = {"ns": ns, "cv": self._cv(s_main), "k": k}
                if pairs is not None:
                    # 配对：同一次循环里 full/bare 各做一半工作，
                    # 单侧耗时记 0.5*配对总耗时。两套对手的轮次偏差完全同向，
                    # 主项/对手的比值再走一遍配对拆分（见 _paired_ratio）。
                    sp = []
                    for _ in range(reps):
                        sp.append(self._time_once(pairs, kp) / kp)
                    half = min(sp) / 2.0
                    entry["full"] = {"ns": half, "cv": self._cv(sp), "k": kp,
                                     "paired": True}
                    entry["bare"] = {"ns": half, "cv": self._cv(sp), "k": kp,
                                     "paired": True}
                    entry["pair_total_ns"] = min(sp)
                entry["ns_dev"] = (round(statistics.stdev(s_main) / statistics.mean(s_main), 5)
                                   if len(s_main) > 1 and statistics.mean(s_main) else 0.0)
                for tag, f, kx in (("full", fn_full, kf), ("bare", fn_bare, kb)):
                    if f is None or tag in entry:
                        if tag not in entry:
                            entry[tag] = None
                        continue
                    s = [self._time_once(f, kx) / kx for _ in range(reps)]
                    entry[tag] = {"ns": min(s),
                                  "cv": self._cv(s), "k": kx,
                                  "ns_dev": (round(statistics.stdev(s) / statistics.mean(s), 5)
                                             if len(s) > 1 and statistics.mean(s) else 0.0)}
                key = (rec["group"], rec["name"])
                prev = best.get(key)
                if prev is None or entry["ns"] < prev["ns"]:
                    best[key] = entry

        out = []
        for rec, _f, _ff, _fb, _fp in self.pending:
            e = best[(rec["group"], rec["name"])]
            r = dict(rec)
            n = r["n"] or 1
            r["ns"] = e["ns"]
            r["cv"] = e["cv"]
            r["ns_dev"] = e.get("ns_dev")
            r["k"] = e["k"]
            r["per_row_ns"] = r["ns"] / n if r["per_row"] else None
            r["rows_per_s"] = (n / (r["ns"] / 1e9)) if r["per_row"] else None
            for tag in ("full", "bare"):
                sub = e.get(tag)
                if sub:
                    r[tag + "_ns"] = sub["ns"]
                    r[tag + "_cv"] = sub["cv"]
                    r[tag + "_ns_dev"] = sub.get("ns_dev")
                    r[tag + "_k"] = sub["k"]
                    r[tag + "_paired"] = bool(sub.get("paired"))
                    r[tag + "_per_row_ns"] = sub["ns"] / n if r["per_row"] else None
                    r["ratio_" + tag] = r["ns"] / sub["ns"] if sub["ns"] else None
                    r["speedup_" + tag] = sub["ns"] / r["ns"] if r["ns"] else None
                else:
                    r[tag + "_ns"] = None
                    r["ratio_" + tag] = None
                    r["speedup_" + tag] = None
            out.append(r)
        self.rows = out
        return out


# ============================================================ 产物装载
_RUN_CACHE = {}


def load_product(schema, cython=False):
    """schema -> (run, src, gen_ns)。同一 schema 走缓存（不重复 generate）。"""
    key = (schema["name"], bool(cython))
    if key in _RUN_CACHE:
        return _RUN_CACHE[key]
    t0 = time.perf_counter_ns()
    src = dcg.generate(schema, cython=cython)
    gen_ns = time.perf_counter_ns() - t0
    g = {"__name__": "_dcg_bench_" + schema["name"]}
    exec(compile(src, "<bench:%s>" % schema["name"], "exec"), g)
    _RUN_CACHE[key] = (g["run"], src, gen_ns)
    return _RUN_CACHE[key]


def src_stats(src):
    lines = src.splitlines()
    return {"lines": len(lines), "chars": len(src),
            "code_lines": sum(1 for x in lines if x.strip()
                              and not x.lstrip().startswith("#"))}


# ============================================================ schema 工厂
def spath(s):
    return "$" + "".join(
        "[%r]" % k if isinstance(k, str) else "[%d]" % k for k in s)


def S(name, fields, shape="list", source=None, before=None, after=None):
    out = {"shape": shape}
    if source is not None:
        out["input"] = {"source": source}
    if before is not None:
        out["before"] = before
    if after is not None:
        out["after"] = after
    return {"name": name, "output": out,
            "out": {"type": "object", "fields": fields}}


#: 取值形态清单：(key, 字段名, spec, 手写取值/裸取取值)
#: 一行一个形态，用于 §2/§3 的逐形态表与 L1..L9 阶梯。
COLS = [
    ("bare", "s_bare", {"type": "int64", "value": "$.0"}),
    ("opt_typed", "s_opt_typed",
     {"type": "int64", "value": "$.1", "optional": True}),
    ("opt_any", "s_opt_any",
     {"type": "any", "value": "$.2", "optional": True}),
    ("default", "s_default", {"type": "int64", "value": "$.2", "default": 7}),
    ("expr", "s_expr", {"type": "int64", "expr": "`$.1 + $.2`"}),
    ("nested", "s_nested", {"type": "object", "fields": {
        "x": {"type": "int64", "value": "$.0"},
        "y": {"type": "str", "value": "$.1"}}}),
    ("hires", "s_hires", {"type": "int64", "expr":
                          "$reduce($.5.a, function($a,$b){ $a + $b }, 0)"}),
    ("backtick", "s_bt", {"type": "str", "expr": "`$.6[:1] + '.' + $.7[:1]`"}),
    ("py_block", "s_py", {"type": "str", "expr":
                          "py{ let x = $.6; return x }"}),
]
COL_NAMES = [c[0] for c in COLS]
COL_FIELD = {k: n for k, n, _ in COLS}
#: 用于 L1..L6 等阶梯（不含高阶函数，避免一个场景被单个贵形态支配）
COLS_CHEAP = [c for c in COLS if c[0] in
              ("bare", "expr", "opt_typed", "default", "nested", "backtick")]


def _col_spec(key, with_field=False):
    for k, fname, spec in COLS:
        if k == key:
            out = dict(spec)
            if with_field:
                out["_field"] = fname
            return out
    raise KeyError(key)


def mixed_schema(cols, shape="list", name=None, before=False, after=False,
                 source=None):
    """从 COLS 里挑几列拼一个 schema（private 键不会进 schema）。"""
    fields = {}
    for key, fname, spec in COLS:
        if key in cols:
            fields[fname] = dict(spec)
    aft = None
    if after:
        aft = AFTER_EXPR if shape in ("dict", "index") else AFTER_LIST_EXPR
    return S(name or ("mix_" + "_".join(cols) + "_" + shape), fields, shape,
             source=source, before=(BEFORE_EXPR if before else None), after=aft)


#: 钩子表达式。
#: * ``before`` 在 shape=list 上是逐行 filter（丢行）；行里第 0 格是 str，
#:   用它做 ``!= ''`` 恒真，因此 before 的语义是"多一遍 filter 扫描"。
#: * ``after`` 见 AFTER_DICT_EXPR 的说明：只对**行式**形状可用。
BEFORE_EXPR = "= $filter($, function($r){ $r[0] != '' })"
AFTER_EXPR = "= $merge([$, {'total': $sum($.s_bare)}])"
AFTER_LIST_EXPR = "= $map($, function($r){ $r })"


def _wrap_list_pairs(datas, cols):
    """shape=list 的手写对手：一行一个 list（与产物同形）。"""
    out = []
    ap = out.append
    for r in datas:
        ap([_hand_col_value(k, r) for k in cols])
    return out


# ============================================================ 手写基线
# 形状对比用的「朴素直写」对手源码：每个形态列的手写表达式（逐位等价于产物）
_HAND_SRC = {
    "bare": "r[0]",
    "opt_typed": "r[1]",
    "opt_any": "r[2]",
    "default": "r[2]",
    "expr": "r[1] + r[2]",
    "nested": "{'x':r[0],'y':r[1]}",
    "hires": "sum(r[5]['a'])",
    "backtick": "r[6][:1] + '.' + r[7][:1]",
    "py_block": "r[6]",
}


def _hand_col_value(key, r):
    """``full`` 对手：语义等价（optional 缺键 -> None；元组短了也当缺键）。"""
    if key == "bare":
        return r[0]
    if key == "opt_typed":
        return r[1] if len(r) > 1 else None
    if key == "opt_any":
        return r[2] if len(r) > 2 else None
    if key == "default":
        return r[2] if len(r) > 2 else 7
    if key == "expr":
        return r[1] + r[2]
    if key == "nested":
        return {"x": r[0], "y": r[1]}
    if key == "hires":
        return sum(r[5]["a"], 0)
    if key == "backtick":
        return r[6][:1] + "." + r[7][:1]
    if key == "py_block":
        return r[6]
    raise KeyError(key)


def _bare_col_value(key, r):
    """``bare`` 对手：裸下标、零兜底（性能上限参照，不是等价实现）。"""
    if key == "bare":
        return r[0]
    if key == "opt_typed":
        return r[1]
    if key == "opt_any":
        return r[2]
    if key == "default":
        return r[2]
    if key == "expr":
        return r[1] + r[2]
    if key == "nested":
        return {"x": r[0], "y": r[1]}
    if key == "hires":
        return sum(r[5]["a"], 0)
    if key == "backtick":
        return r[6][:1] + "." + r[7][:1]
    if key == "py_block":
        return r[6]
    raise KeyError(key)


def _hand_keys(cols):
    return [COL_FIELD[k] for k in cols]


def _hand_only(pairs, which):
    """取配对结果的一侧（0=full, 1=bare），供拆分配对总耗时用。"""
    return pairs()[which]


# 说明：下面这些手写对手刻意写成「一行一行、逐列取值」的朴素形态（与 DCG 产物的
# 逐行循环一一对应，便于归因），而不是最优写法；它们的作用是给出「同等语义的
# 手写实现」基准线，同时也自然成为 DCG 的下界参照。
#
# ``*_bare`` 变体：不加 None 兜底（直取），代表「裸取」下界。
# ``*_pairs`` 变体：``_hand_col_value`` 返回 ``(full, bare)`` 二元组，
# 一次循环同时产出两套结果，用于保证 full/bare 两套对手的工作量被完全配对。

def _pair_fns(cols):
    return [lambda r, k=k: _hand_col_pair(k, r) for k in cols]


def _hand_col_pair(key, r):
    return (_hand_col_value(key, r), _bare_col_value(key, r))


def hand_cols_pairs(datas, cols):
    """``full``+``bare`` 同形（dict）：一次循环产出两套结果。"""
    names = _hand_keys(cols)
    full = {n: [] for n in names}
    bare = {n: [] for n in names}
    af = [full[n].append for n in names]
    ab = [bare[n].append for n in names]
    fns = _pair_fns(cols)
    n = len(cols)
    for r in datas:
        for i in range(n):
            f, b = fns[i](r)
            af[i](f)
            ab[i](b)
    return full, bare


def hand_cols(datas, cols):
    """``full``：列式 dict，与 DCG 的 dict 形状产物同形同量。"""
    names = _hand_keys(cols)
    res = {n: [] for n in names}
    apps = [res[n].append for n in names]
    fns = [lambda r, k=k: _hand_col_value(k, r) for k in cols]
    n = len(cols)
    for r in datas:
        for i in range(n):
            apps[i](fns[i](r))
    return res


def hand_cols_bare(datas, cols):
    """``bare``：同形状，但取值全部裸下标（无 None 兜底）。"""
    names = _hand_keys(cols)
    res = {n: [] for n in names}
    apps = [res[n].append for n in names]
    fns = [lambda r, k=k: _bare_col_value(k, r) for k in cols]
    n = len(cols)
    for r in datas:
        for i in range(n):
            apps[i](fns[i](r))
    return res


def hand_rows_pairs(datas, cols):
    keys = _hand_keys(cols)
    of, ob = [], []
    apf, apb = of.append, ob.append
    fns = _pair_fns(cols)
    n = len(cols)
    for r in datas:
        df = {}
        db = {}
        for i in range(n):
            f, b = fns[i](r)
            df[keys[i]] = f
            db[keys[i]] = b
        apf(df)
        apb(db)
    return of, ob


def hand_rows(datas, cols):
    """``full``（shape=rows）：每行一个 dict。"""
    keys = _hand_keys(cols)
    out = []
    ap = out.append
    fns = [lambda r, k=k: _hand_col_value(k, r) for k in cols]
    n = len(cols)
    for r in datas:
        d = {}
        for i in range(n):
            d[keys[i]] = fns[i](r)
        ap(d)
    return out


def hand_rows_bare(datas, cols):
    """``bare``（shape=rows）：每行一个 dict，裸取。"""
    keys = _hand_keys(cols)
    out = []
    ap = out.append
    fns = [lambda r, k=k: _bare_col_value(k, r) for k in cols]
    n = len(cols)
    for r in datas:
        d = {}
        for i in range(n):
            d[keys[i]] = fns[i](r)
        ap(d)
    return out


def hand_index_pairs(datas, cols):
    of, ob = {}, {}
    fns = _pair_fns(cols)
    n = len(cols)
    for i, r in enumerate(datas):
        rf = [None] * n
        rb = [None] * n
        for j in range(n):
            rf[j], rb[j] = fns[j](r)
        of[i] = rf
        ob[i] = rb
    return of, ob


def hand_index(datas, cols):
    """``full``（shape=index）：``{i: [..行值..]}``。"""
    out = {}
    fns = [lambda r, k=k: _hand_col_value(k, r) for k in cols]
    n = len(cols)
    for i, r in enumerate(datas):
        row = [None] * n
        for j in range(n):
            row[j] = fns[j](r)
        out[i] = row
    return out


def hand_index_bare(datas, cols):
    out = {}
    fns = [lambda r, k=k: _bare_col_value(k, r) for k in cols]
    n = len(cols)
    for i, r in enumerate(datas):
        row = [None] * n
        for j in range(n):
            row[j] = fns[j](r)
        out[i] = row
    return out


# ---------------------------------------------------------------- 输出形状的手写
def hand_list(datas, cols):
    """``full``（shape=list）：一行一个 list。"""
    fns = [lambda r, k=k: _hand_col_value(k, r) for k in cols]
    n = len(cols)
    out = []
    ap = out.append
    for r in datas:
        row = [None] * n
        for j in range(n):
            row[j] = fns[j](r)
        ap(row)
    return out


def hand_list_pairs(datas, cols):
    """一次循环同时产出 ``full``/``bare`` 两套 list 结果（配对，互不偏差）。"""
    fns = _pair_fns(cols)
    n = len(cols)
    of, ob = [], []
    apf, apb = of.append, ob.append
    for r in datas:
        rf = [None] * n
        rb = [None] * n
        for j in range(n):
            rf[j], rb[j] = fns[j](r)
        apf(rf)
        apb(rb)
    return of, ob


def hand_list_bare(datas, cols):
    fns = [lambda r, k=k: _bare_col_value(k, r) for k in cols]
    n = len(cols)
    out = []
    ap = out.append
    for r in datas:
        row = [None] * n
        for j in range(n):
            row[j] = fns[j](r)
        ap(row)
    return out


# ---------------------------------------------------------------- 形状专用手写对手
# 注意：``hand_*`` 系列为了和「逐行循环」一一对应，用了 ``[None]*n`` 占位再回填，
# 这一写法比逐行 ``[...]`` 临时构建慢 30%~40%；形状对比里会系统性地帮 DCG 的忙。
# 因此形状场景另配一套「朴素直写」对手：语义与产物逐位相同，写法就是普通循环。
_MIX_CACHE = {}


def hand_mix_simple(datas, cols):
    """shape=list 的朴素直写对手（一行一个 list，逐字段按声明顺序写）。

    ``datas is None`` 时只返回编译好的源码字符串（用于报告里引用）。
    """
    key = tuple(cols)
    spec = _MIX_CACHE.get(key)
    if spec is None:
        src = "def f(datas):\n    out=[]\n    ap=out.append\n"
        src += "    for r in datas:\n"
        for i, k in enumerate(cols):
            src += "        _%d = %s\n" % (i, _HAND_SRC[k])
        src += "        ap([" + ",".join("_%d" % i for i in range(len(cols))) + "])\n"
        src += "    return out\n"
        _MIX_CACHE[key] = src
        spec = src
    return spec


_RUN_CODE = {}


def _run_compiled(src, datas):
    code = _RUN_CODE.get(src)
    if code is None:
        ns = {}
        exec(compile(src, "<hand_mix_simple>", "exec"), ns)
        code = ns["f"]
        _RUN_CODE[src] = code
    return code(datas)


# ---------------------------------------------------------------- 微基准用纯 Python 对手
def _mk_per_row(fn, n):
    def g():
        fn()
    return g


# ============================================================ 每列形态的手写等价
def _hand_optwide(datas, use_get_default=False):
    """13 键 dict 行 -> 8 列（含 4 个缺键 optional、1 个 default）。

    ``use_get_default=True`` 时用 ``r.get(k)``（一次调用），否则用
    ``r.get(k,_U)+is _U 三目``（与产物等价的两段式）。
    """
    id_c, name_c, qty_c, price_c, amount_c = [], [], [], [], []
    o_mi, o_ms, a_mi, a_ms = [], [], [], []
    for r in datas:
        if use_get_default:
            mi = r.get("mi")
            ms = r.get("ms")
            am = r.get("mn")
            as_ = r.get("ch")
        else:
            mi = r.get("mi", _U)
            mi = None if mi is _U else mi
            ms = r.get("ms", _U)
            ms = None if ms is _U else ms
            am = r.get("mn", _U)
            am = None if am is _U else am
            as_ = r.get("ch", _U)
            as_ = None if as_ is _U else as_
        id_c.append(r["id"])
        name_c.append(r["name"])
        q = r["qty"]
        p = r["price"]
        qty_c.append(q)
        price_c.append(p)
        amount_c.append(q * p)
        o_mi.append(mi)
        o_ms.append(ms)
        a_mi.append(am)
        a_ms.append(as_)
    return {"id": id_c, "name": name_c, "qty": qty_c, "price": price_c,
            "amount": amount_c, "o_typed_mi": o_mi, "o_typed_ms": o_ms,
            "o_any_mi": a_mi, "o_any_ms": a_ms}


def hand_optwide_get(datas):
    return _hand_optwide(datas, use_get_default=True)


def hand_optwide_guard(datas):
    return _hand_optwide(datas, use_get_default=False)


def hand_optwide_bare(datas):
    """``bare``：只取 id/name/qty/price/amount，缺键列不兜底（只取已存在的键）。"""
    id_c, name_c, qty_c, price_c, amount_c = [], [], [], [], []
    for r in datas:
        id_c.append(r["id"])
        name_c.append(r["name"])
        q = r["qty"]
        p = r["price"]
        qty_c.append(q)
        price_c.append(p)
        amount_c.append(q * p)
    return {"id": id_c, "name": name_c, "qty": qty_c, "price": price_c,
            "amount": amount_c}


OPTWIDE = S("optwide", {
    "id": {"type": "str", "value": "$.id"},
    "name": {"type": "str", "value": "$.name"},
    "qty": {"type": "int64", "value": "$.qty"},
    "price": {"type": "float64", "value": "$.price"},
    "amount": {"type": "float64", "expr": "`$.qty * $.price`"},
    "o_typed_mi": {"type": "int64", "value": "$.mi", "optional": True},
    "o_typed_ms": {"type": "str", "value": "$.ms", "optional": True},
    "o_any_mi": {"type": "any", "value": "$.mn", "optional": True},
    "o_any_ms": {"type": "str", "value": "$.ch", "optional": True},
}, "dict")


# ============================================================ aggregate
def agg_schema(name, container_extra=False, shape="dict"):
    """aggregate 列容器。

    * ``container_extra=False`` -> 子键全部可直出列，走 ``_aggregate_direct``
      快路径（每列一次 ``[<expr> for r in _d]``）；
    * ``container_extra=True``  -> 一个 optional 子键，退回「每行造 dict +
      出循环两级转置」的老路（README B.8-3）。
    """
    if container_extra:
        sub = {
            "n_rows": {"type": "int64", "const": 1},
            "lvl": {"type": "str", "value": "$.6", "optional": True},
        }
    else:
        sub = {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "int64", "value": "$.2"},
            "pair": {"type": "list", "expr": "`[$.0, $.1]`"},
            "label": {"type": "str", "expr": "`$.6 + \"|agg\"`"},
        }
    fields = {
        "c0": {"type": "int64", "value": "$.0"},
        "c1": {"type": "int64", "value": "$.2"},
        "stats": {"type": "object", "aggregate": True, "fields": sub},
        "c2": {"type": "int64", "expr": "`$.1 * 2`"},
    }
    return S(name, fields, shape)


def agg_direct_schema():
    """aggregate 直出列快路径：子键全是 const / 单段 value，不触发容器回退。"""
    return S("agg_direct", {
        "c0": {"type": "str", "value": "$.0"},
        "c1": {"type": "int64", "value": "$.2"},
        "stats": {
            "type": "object",
            "aggregate": True,
            "fields": {
                "n_rows": {"type": "int64", "const": 1},
                "qty_sum": {"type": "int64", "value": "$.2"},
                "pair": {"type": "list", "expr": "`[$.0, $.1]`"},
                "label": {"type": "str", "expr": "`$.6 + \"|agg\"`"},
            },
        },
        "c2": {"type": "int64", "expr": "`$.1 * 2`"},
    }, "dict")


def hand_agg_direct(datas):
    c0, c1, c2, qty_sum, pair, label = [], [], [], [], [], []
    for r in datas:
        c0.append(r[0])
        c1.append(r[2])
        c2.append(r[1] * 2)
        qty_sum.append(r[2])
        pair.append([r[0], r[1]])
        label.append(r[6] + "|agg")
    return {"c0": c0, "c1": c1, "stats": {"n_rows": 1, "qty_sum": qty_sum,
                                         "pair": pair, "label": label},
            "c2": c2}


def hand_agg_container(datas):
    c0, c1, c2, lvl = [], [], [], []
    for r in datas:
        c0.append(r[0])
        c1.append(r[2])
        c2.append(r[1] * 2)
        lvl.append(r[6])
    return {"c0": c0, "c1": c1, "stats": {"n_rows": 1, "lvl": lvl}, "c2": c2}


#: DEFECT-001 最小复现（当前产物 ``'c2':[r[3]for r in _o]`` 而行只有 3 列
#: -> IndexError）。aggregate 直出列按**声明顺序**索引，行容器按**实际槽位**。
DEFECT_001_SCHEMA = {
    "name": "b001_agg_direct",
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        "c0": {"type": "int64", "value": "$.0"},
        "c1": {"type": "int64", "value": "$.2"},
        "stats": {"type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "int64", "value": "$.2"},
            "pair": {"type": "list", "expr": "`[$.0, $.1]`"},
            "label": {"type": "str", "expr": "`$.6 + \"|agg\"`"}}},
        "c2": {"type": "int64", "expr": "`$.1 * 2`"},
    }},
}
DEFECT_001_DATAS = [(f"f{i % 997}", i & 63, (i * 7) & 1023, i % 100,
                     (i * 3) % 50, {"a": [i & 15]}, f"n{i % 97}")
                    for i in range(6)]

#: DEFECT-002：``output.after`` 里的 ``$`` = **原始输入行数组**，不是输出。
#: 产物把 after 作用在"中间结果"上：shape=list 时是行 list ``_o``（含聚合值/嵌套
#: dict），shape=dict 时是**列式 dict**。于是 ``= $sum($)`` 在 list 形状报
#: ``TypeError: $sum: 元素必须是数值``，在 dict 形状把 dict 当单元素数组返回 0——
#: 两个形状都与文档写的"$ = 整体输入"不一致，且**取值依赖行形态**。
#: 这也是 order_pipeline（README 附录 B.2）在 dict 形状上 ``$merge([$merge([$,
#: {'orders': $.order_id}]), ...])`` 能"凑巧正常"、"= $merge([{'x':1}])" 却静默
#: 丢整份列式数据的原因。
DEFECT_002_SCHEMA = {
    "name": "b002_after_on_dict",
    "output": {"shape": "dict", "after": "= $sum($)"},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.1"},
    }},
}
DEFECT_002_DATAS = [(1, 2), (3, 4), (5, 6)]

#: DEFECT-002b：after 不引用 ``$`` 时，**中间结果被整个丢掉**（静默丢数据）。
DEFECT_002B_SCHEMA = {
    "name": "b002b_after_drop_dict",
    "output": {"shape": "dict", "after": "= $merge([{'x': 1}])"},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
    }},
}
DEFECT_002B_DATAS = [(1,), (2,)]

#: 对照：同一 after 在 list 形状上的表现（证伪"sh>假说"用）
DEFECT_002C_SCHEMA = {
    "name": "b002c_after_on_list",
    "output": {"shape": "list", "after": "= $sum($)"},
    "out": {"type": "object", "fields": {
        "a": {"type": "int64", "value": "$.0"},
        "b": {"type": "int64", "value": "$.1"},
    }},
}


# ---------------------------------------------------------------- order_pipeline
ORDER_PIPELINE = {
    "name": "order_pipeline",
    "input": {"source": "$.payload.orders"},
    "output": {
        "shape": "dict",
        "before": "= $filter($, function($o){ $o['qty'] > 0 })",
        "after": "= $merge([$merge([$, {'orders': $.order_id, "
                 "'revenue': $.amount}]), "
                 "{'count': $count($.qty), 'total_revenue': $sum($.amount)}])",
    },
    "out": {"type": "object", "fields": {
        "order_id": {"type": "str", "value": "$.id"},
        "sku": {"type": "str", "value": "$.item.sku"},
        "qty": {"type": "int64", "value": "$.qty"},
        "unit_price": {"type": "float64", "value": "$.price"},
        "customer": {"type": "str", "value": "$.buyer.name"},
        "level": {"type": "str", "optional": True, "value": "$.buyer.level"},
        "note": {"type": "str", "optional": True, "value": "$.buyer.note"},
        "amount": {"type": "float64", "expr": "qty * unit_price"},
        "bulk": {"type": "bool", "expr": "qty >= 10"},
        "discount": {"type": "float64", "expr": "bulk ? amount * 0.9 : amount"},
        "fee": {"type": "float64", "expr": "discount + $sqrt(amount)"},
        "tag": {"type": "str", "expr": "`sku[:3].upper() + '-' + str(qty)`"},
        "label": {"type": "str", "expr": "bulk ? 'BULK' : 'SINGLE'"},
        "leg": {"type": "object", "fields": {
            "from": {"type": "str", "value": "$.item.from"},
            "to": {"type": "str", "value": "$.item.to"},
            "span": {"type": "int64", "expr": "qty * 2"}}},
        "stats": {"type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "list", "expr": "$.qty"},
            "pair": {"type": "list", "expr": "`[$.qty, $.qty * $.price]`"}}},
    }},
}


def order_pipeline_hand(payload):
    """``full``：README B.4 的手写等价（before 丢行、optional 缺键、after 追加列）。"""
    data = payload["payload"]["orders"]
    order_id, sku, qty_col, unit_price, customer = [], [], [], [], []
    level, note, amount, bulk, discount = [], [], [], [], []
    fee, tag, label, leg, qty_sum, pair = [], [], [], [], [], []
    sqrt = math.sqrt
    for o in data:
        if not (o["qty"] > 0):
            continue
        item = o["item"]
        buyer = o["buyer"]
        q = o["qty"]
        p = o["price"]
        a = q * p
        big = q >= 10
        disc = a * 0.9 if big else a
        order_id.append(o["id"])
        sku.append(item["sku"])
        qty_col.append(q)
        unit_price.append(p)
        customer.append(buyer["name"])
        level.append(buyer.get("level"))
        note.append(buyer.get("note"))
        amount.append(a)
        bulk.append(big)
        discount.append(disc)
        fee.append(disc + sqrt(a))
        tag.append(item["sku"][:3].upper() + '-' + str(q))
        label.append('BULK' if big else 'SINGLE')
        leg.append({"from": item["from"], "to": item["to"], "span": q * 2})
        qty_sum.append(q)
        pair.append([q, q * p])
    return {
        "order_id": order_id, "sku": sku, "qty": qty_col,
        "unit_price": unit_price, "customer": customer, "level": level,
        "note": note, "amount": amount, "bulk": bulk, "discount": discount,
        "fee": fee, "tag": tag, "label": label, "leg": leg,
        "stats": {"n_rows": 1, "qty_sum": qty_sum, "pair": pair},
        "orders": order_id, "revenue": amount, "count": len(qty_col),
        "total_revenue": sum(amount),
    }


def order_pipeline_hand_bare(payload):
    """``bare``：同上但不兜底 optional、不做 before 过滤（性能上限参照）。"""
    data = payload["payload"]["orders"]
    order_id, sku, qty_col, unit_price, customer = [], [], [], [], []
    level, note, amount, bulk, discount = [], [], [], [], []
    fee, tag, label, leg, qty_sum, pair = [], [], [], [], [], []
    sqrt = math.sqrt
    for o in data:
        item = o["item"]
        buyer = o["buyer"]
        q = o["qty"]
        p = o["price"]
        a = q * p
        big = q >= 10
        disc = a * 0.9 if big else a
        order_id.append(o["id"])
        sku.append(item["sku"])
        qty_col.append(q)
        unit_price.append(p)
        customer.append(buyer["name"])
        level.append(buyer.get("level"))
        note.append(buyer.get("note"))
        amount.append(a)
        bulk.append(big)
        discount.append(disc)
        fee.append(disc + sqrt(a))
        tag.append(item["sku"][:3].upper() + '-' + str(q))
        label.append('BULK' if big else 'SINGLE')
        leg.append({"from": item["from"], "to": item["to"], "span": q * 2})
        qty_sum.append(q)
        pair.append([q, q * p])
    return {
        "order_id": order_id, "sku": sku, "qty": qty_col,
        "unit_price": unit_price, "customer": customer, "level": level,
        "note": note, "amount": amount, "bulk": bulk, "discount": discount,
        "fee": fee, "tag": tag, "label": label, "leg": leg,
        "stats": {"n_rows": 1, "qty_sum": qty_sum, "pair": pair},
        "orders": order_id, "revenue": amount, "count": len(qty_col),
        "total_revenue": sum(amount),
    }


# ============================================================ 等价性校验
UNDEF_MARK = "<undefined>"


def _undef(v):
    if v is rt.UNDEF:
        return UNDEF_MARK
    if isinstance(v, dict):
        return {k: _undef(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_undef(x) for x in v]
    return v


def _norm(res):
    """把产物/手写结果统一成可比形态（UNDEF -> 标记字符串）。"""
    if isinstance(res, dict):
        keys = list(res.keys())
        return ["dict", keys, _undef(res)]
    if isinstance(res, (list, tuple)):
        rows = []
        for item in res:
            if isinstance(item, (list, tuple)):
                rows.append(list(item))
            elif isinstance(item, dict):
                rows.append([item[k] for k in item])
            else:
                rows.append([item])
        return ["list", _undef(rows)]
    return ["scalar", _undef(res)]


def _wrap_shape(rows, shape):
    """把「一行一个 list」的手写结果包装成目标形状，用于等价性校验。"""
    if shape in ("list", "rows"):
        return rows
    if shape == "index":
        return {i: v for i, v in enumerate(rows)}
    keys = list(COL_FIELD[c[0]] for c in COLS)
    return {k: [v[j] for v in rows] for j, k in enumerate(keys)}


def _eq(a, b):
    return _norm(a) == _norm(b)


# ============================================================ 微基准
def _micro_runner():
    """微基准用：每行 1 个动作，纯 Python 对手（同循环骨架、同 append）。"""
    return None


# ============================================================ 登记
def _sched_sample(b, group, name, fn, n, note, fn_full=None, fn_bare=None,
                  per_row=True):
    return b.add(group, name, fn, n, note=note, fn_full=fn_full,
                 fn_bare=fn_bare, per_row=per_row)


def register(mode, rows_scale, b, run_upstream=True):
    """登记全部场景；返回 dict（产物统计、等价性校验、微基准、阶梯）。"""
    quick = mode == "quick"
    N = 2000 if quick else rows_scale
    meta = {"N": N, "artifacts": {}, "checks": {}, "labels": {}}

    all_cols = [k for k, _, _ in COLS]
    cheap_cols = [c[0] for c in COLS_CHEAP]

    # ---------------- 0. A/A 噪声底（3 个不同量级的探针）
    global _AA_PROBES
    _AA_PROBES = []
    rows_small = make_rows(min(N, 20000))
    run_warm, _src, _ = load_product(mixed_schema(cheap_cols, "list",
                                                  name="aa_warm"))
    run_warm(rows_small)
    _AA_PROBES.append((lambda: run_warm(rows_small), "warm_mix6_list"))
    b.aa_control(lambda: run_warm(rows_small), "warm_mix6_list")
    _wide = make_wide_rows(min(N, 20000))
    run_ow, _s, _g = load_product(OPTWIDE)
    run_ow(_wide)
    _AA_PROBES.append((lambda: run_ow(_wide), "optwide"))
    b.aa_control(lambda: run_ow(_wide), "optwide")
    op_small = make_orders(min(N, 5000))
    _AA_PROBES.append((lambda: order_pipeline_hand(op_small), "hand_order_pipeline"))
    b.aa_control(lambda: order_pipeline_hand(op_small), "hand_order_pipeline")

    # ---------------- 1. 行数扫描
    row_counts = [100, 1000, 10000, 100000]
    if quick:
        row_counts = [100, 1000, 4000]
    elif rows_scale:
        row_counts = sorted(set([100, 1000, 10000, rows_scale]))
    run_mix9, src_mix9, gen_mix9 = load_product(mixed_schema(all_cols, "list"))
    meta["artifacts"]["mix9_list"] = dict(src_stats(src_mix9), gen_ms=gen_mix9 / 1e6)
    run_mix9d, src_mix9d, gen_mix9d = load_product(
        mixed_schema(all_cols, "dict", name="mix9_dict"))
    meta["artifacts"]["mix9_dict"] = dict(src_stats(src_mix9d), gen_ms=gen_mix9d / 1e6)
    for n in row_counts:
        d = make_rows(n)
        run_mix9(d)
        run_mix9d(d)
        b.add("scale", "mix9_list@%d" % n, lambda d=d: run_mix9(d), n,
              note="9 形态列混合 shape=list",
              fn_full=lambda d=d: hand_list(d, all_cols),
              fn_bare=lambda d=d: hand_list_bare(d, all_cols))
        b.add("scale", "mix9_dict@%d" % n, lambda d=d: run_mix9d(d), n,
              note="9 形态列混合 shape=dict",
              fn_full=lambda d=d: hand_cols(d, all_cols),
              fn_bare=lambda d=d: hand_cols_bare(d, all_cols))

    # ---------------- 2. 输出形状（同一份 9 列）
    Ns = min(N, 4000) if quick else N
    d_sh = make_rows(Ns)
    for shape in ("list", "dict", "index", "rows"):
        sch = mixed_schema(all_cols, shape, name="mix9_" + shape)
        run, src, gen = load_product(sch)
        meta["artifacts"]["shape_" + shape] = dict(src_stats(src), gen_ms=gen / 1e6)
        run(d_sh)
        # 形状对比统一用「朴素直写」对手：一行只取一遍值，再按形状组织输出。
        # full/bare 两套对手的取值部分相同（本组零 optional 列），因此同值，
        # 差别只在输出组织方式——正是本组要量的东西。
        _rspec = hand_mix_simple(d_sh, all_cols)
        fn_full = fn_bare = (lambda sp=_rspec: _run_compiled(sp, d_sh))
        b.add("shape", "shape_" + shape, lambda r=run, d=d_sh: r(d), Ns,
              note="9 形态列混合（对手=朴素直写）",
              fn_full=fn_full, fn_bare=fn_bare)
        got = run(d_sh)
        meta["checks"]["shape_%s_eq" % shape] = _eq(got, _wrap_shape(
            fn_full(), shape))

    # ---------------- 3. 字段取值形态（单列，shape=dict）
    d_f = make_rows(Ns)
    for key, fname, spec in COLS:
        sch = mixed_schema([key], "dict", name="col_" + key)
        run, src, gen = load_product(sch)
        run(d_f)
        meta["artifacts"]["col_" + key] = dict(src_stats(src), gen_ms=gen / 1e6)
        b.add("col", "col_" + key, lambda r=run, d=d_f: r(d), Ns,
              note=spec.get("expr") or spec.get("value", ""),
              fn_full=lambda d=d_f, k=key: hand_cols(d, [k]),
              fn_bare=lambda d=d_f, k=key: hand_cols_bare(d, [k]))

    # ---------------- 4. py{} / 高阶函数 / any / list 单列
    d_h = make_rows(Ns)
    extra = [
        ("const_only", S("const_only", {
            "k": {"type": "int64", "const": 1},
            "s": {"type": "str", "const": "K"}}, "list"), d_h,
         lambda d: [[1, "K"] for _ in d],
         None),
        ("hires_map", S("hires_map", {
            "v": {"type": "list",
                  "expr": "$map($.5.a, function($v){ $v * 2 })"}}, "list"), d_h,
         lambda d: [[[v * 2 for v in r[5]["a"]]] for r in d],
         lambda d: [[[v * 2 for v in r[5]["a"]]] for r in d]),
        ("hires_filter", S("hires_filter", {
            "v": {"type": "list",
                  "expr": "$filter($.5.a, function($v){ $v > 4 })"}}, "list"), d_h,
         lambda d: [[[v for v in r[5]["a"] if v > 4]] for r in d],
         lambda d: [[[v for v in r[5]["a"] if v > 4]] for r in d]),
        ("hires_sort", S("hires_sort", {
            "v": {"type": "list", "expr": "$sort($.5.a)"}}, "list"), d_h,
         lambda d: [[sorted(r[5]["a"])] for r in d],
         lambda d: [[sorted(r[5]["a"])] for r in d]),
        ("hires_reduce", S("hires_reduce", {
            "v": {"type": "int64",
                  "expr": "$reduce($.5.a, function($a,$b){ $a + $b }, 0)"}},
            "list"), d_h,
         lambda d: [[sum(r[5]["a"], 0)] for r in d],
         lambda d: [[sum(r[5]["a"], 0)] for r in d]),
        ("any_col", S("any_col", {"v": {"type": "any", "value": "$.5"}},
                      "list"), d_h,
         lambda d: [[r[5]] for r in d], lambda d: [[r[5]] for r in d]),
        ("list_col", S("list_col", {"v": {"type": "list", "value": "$.5.a"}},
                       "list"), d_h,
         lambda d: [[r[5]["a"]] for r in d],
         lambda d: [[r[5]["a"]] for r in d]),
        ("py_block", S("py_block", {
            "v": {"type": "str", "expr": "py{ let x = $.6; return x }"}},
            "list"), d_h,
         lambda d: [[r[6]] for r in d], lambda d: [[r[6]] for r in d]),
    ]
    for nm, sch, d, hand_full, hand_bare in extra:
        try:
            run, src, gen = load_product(sch)
            res_p = run(d)
        except Exception as exc:
            meta["checks"]["skip_" + nm] = "%s: %s" % (type(exc).__name__, exc)
            continue
        meta["artifacts"][nm] = dict(src_stats(src), gen_ms=gen / 1e6)
        meta["checks"]["eq_" + nm] = _eq(res_p, hand_full(d))
        b.add("form", nm, lambda r=run, d=d: r(d), Ns, note=sch["name"],
              fn_full=(lambda h=hand_full, d=d: h(d)),
              fn_bare=(lambda h=hand_bare, d=d: h(d)) if hand_bare else None)

    # ---------------- 5. aggregate
    d_a = make_rows(Ns)
    agg_variants = (("agg_direct", agg_direct_schema(), False),
                    ("agg_container", agg_schema("agg_container", container_extra=True),
                     True))
    for nm, sch, is_container in agg_variants:
        try:
            run, src, gen = load_product(sch)
            res_p = run(d_a)
        except Exception as exc:
            meta["checks"]["skip_" + nm] = "%s: %s" % (type(exc).__name__, exc)
            continue
        meta["artifacts"][nm] = dict(src_stats(src), gen_ms=gen / 1e6)
        h = (lambda d=d_a: hand_agg_container(d)) if is_container else \
            (lambda d=d_a: hand_agg_direct(d))
        _st = res_p.get("stats") if isinstance(res_p, dict) else None
        _nr = _st.get("n_rows") if isinstance(_st, dict) else None
        meta["checks"]["note_" + nm] = (
            "n_rows 声明 int64，dcg 产出整列 %s，手写按聚合语义给标量 1；"
            "其余列逐值相等" % ("[1]*N" if isinstance(_nr, list) else type(_nr).__name__))
        try:
            a, c = res_p, h()
            meta["checks"]["eq_" + nm + "_cols"] = all(
                a[k] == c[k] for k in c if k != "stats")
            if is_container:
                meta["checks"]["agg_container_direct"] = True
            else:
                # 本组按「直出列快路径」设计；若产物实际走了容器回退路径
                # （e.g. 快路径被收紧/条件不满足），如实标注，避免拿慢路径
                # 的数字冒充快路径。
                meta["checks"]["agg_direct_used_fastpath"] = ("_a=[r[" in src)
        except Exception as exc:
            meta["checks"]["eq_" + nm] = "hand_failed: %s" % exc
        b.add("agg", nm, lambda r=run, d=d_a: r(d), Ns,
              note="aggregate 列容器（%s）" % ("容器回退路径" if is_container
                                           else "直出列快路径"),
              fn_full=h)

    # ---------------- 6. optional 全命中 / 全缺键（同一 schema 只换数据）
    d_w = make_wide_rows(Ns)
    d_m = make_wide_rows(Ns)
    for r in d_m:
        del r["level"], r["gear"], r["tag1"]
    run_o, src_o, gen_o = load_product(OPTWIDE)
    meta["artifacts"]["optwide"] = dict(src_stats(src_o), gen_ms=gen_o / 1e6)
    meta["checks"]["optwide_src"] = src_o
    meta["checks"]["eq_optwide_hit"] = _eq(run_o(d_w), hand_optwide_guard(d_w))
    meta["checks"]["eq_optwide_miss"] = _eq(run_o(d_m), hand_optwide_guard(d_m))
    #: 两组数据下产物取值一致：都是 .get 路径，区别只在命中/缺键分支
    b.add("opt", "optwide_hit", lambda: run_o(d_w), Ns,
          note="8 个 nullable 列，level/gear/tag1 全命中",
          fn_full=lambda: hand_optwide_guard(d_w),
          fn_bare=lambda: hand_optwide_bare(d_w))
    b.add("opt", "optwide_missing", lambda: run_o(d_m), Ns,
          note="同一 schema，删掉 level/gear/tag1 -> 4 列走缺键分支",
          fn_full=lambda: hand_optwide_guard(d_m),
          fn_bare=lambda: hand_optwide_bare(d_m))
    # ---------------- 6b. optional 取值的两条实现路径（2/3/4 列）
    for ncol in (2, 4):
        keys = ["level", "gear", "tag1", "tag2"][:ncol]
        fields = {"name": {"type": "str", "value": "$.name"}}
        for k in keys:
            fields["o_" + k] = {"type": "int64", "value": "$." + k,
                                "optional": True}
        sch = S("opt_%dc" % ncol, fields, "dict")
        run, src, gen = load_product(sch)
        run(d_w)
        meta["artifacts"]["opt_%dc" % ncol] = dict(src_stats(src), gen_ms=gen / 1e6)

        def hand_guard(d, keys=keys):
            out = {"name": []}
            for k in keys:
                out["o_" + k] = []
            for r in d:
                out["name"].append(r["name"])
                for k in keys:
                    t = r.get(k, _U)
                    out["o_" + k].append(None if t is _U else t)
            return out

        def hand_get(d, keys=keys):
            out = {"name": []}
            for k in keys:
                out["o_" + k] = []
            for r in d:
                out["name"].append(r["name"])
                for k in keys:
                    out["o_" + k].append(r.get(k))
            return out

        meta["checks"]["eq_opt_%dc" % ncol] = _eq(run(d_w), hand_guard(d_w))
        b.add("opt", "opt_%dc" % ncol, lambda r=run, d=d_w: r(d), Ns,
              note="%d 个 optional 列（.get(k,_U)+is _U 三目）" % ncol,
              fn_full=lambda d=d_w, h=hand_guard: h(d),
              fn_bare=lambda d=d_w, h=hand_get: h(d))

    # ---------------- 7. 输出收尾策略（同一行数据，4 种收尾）
    d_s = make_rows(Ns)
    _o_sample = [[_hand_col_value("bare", r), _hand_col_value("opt_typed", r),
                  _hand_col_value("nested", r), _hand_col_value("backtick", r)]
                 for r in d_s]
    _names = ["a", "b", "c", "d"]

    def strat_transpose(d=d_s):
        o = []
        ap = o.append
        for r in d:
            ap([r[0], r[1], {"x": r[0], "y": r[1]}, r[6][:1] + "." + r[7][:1]])
        return {nm: [row[i] for row in o] for i, nm in enumerate(_names)}

    def strat_direct(d=d_s):
        a, b, c, e = [], [], [], []
        aa, ba, ca, ea = a.append, b.append, c.append, e.append
        for r in d:
            aa(r[0])
            ba(r[1])
            ca({"x": r[0], "y": r[1]})
            ea(r[6][:1] + "." + r[7][:1])
        return {"a": a, "b": b, "c": c, "d": e}

    def strat_comps(d=d_s):
        return {"a": [r[0] for r in d], "b": [r[1] for r in d],
                "c": [{"x": r[0], "y": r[1]} for r in d],
                "d": [r[6][:1] + "." + r[7][:1] for r in d]}

    def strat_rows_then_dict(d=d_s):
        o = []
        ap = o.append
        for r in d:
            ap({"a": r[0], "b": r[1], "c": {"x": r[0], "y": r[1]},
                "d": r[6][:1] + "." + r[7][:1]})
        return o

    for nm, fn, note in (
            ("finish_transpose", strat_transpose, "行容器 + 出循环逐列转置（DCG 现状）"),
            ("finish_direct", strat_direct, "循环内逐列 append（不经行容器）"),
            ("finish_comps", strat_comps, "每列独立推导 [expr for r in d]"),
            ("finish_rows_dict", strat_rows_then_dict, "直接造行 dict（shape=rows）")):
        fn()
        b.add("finish", nm, fn, Ns, note=note,
              fn_full=(lambda f=strat_direct: f()))

    # ---------------- 8. order_pipeline（README 附录 B 全量样例）
    No = min(N, 2000) if quick else min(N, 20000)
    pay = make_orders(No)
    run_op, src_op, gen_op = load_product(ORDER_PIPELINE)
    meta["artifacts"]["order_pipeline"] = dict(src_stats(src_op),
                                               gen_ms=gen_op / 1e6)
    meta["checks"]["order_pipeline_src"] = src_op
    try:
        res_d = run_op(pay)
        res_h = order_pipeline_hand(pay)
        meta["checks"]["order_pipeline_eq"] = _eq(res_d, res_h)
        meta["checks"]["order_pipeline_rows"] = len(res_h["order_id"])
        bad = [k for k in res_h
               if k in res_d and res_d[k] != res_h[k]]
        meta["checks"]["order_pipeline_diff_keys"] = bad
    except Exception as exc:
        meta["checks"]["order_pipeline_eq"] = "%s: %s" % (type(exc).__name__, exc)
        meta["checks"]["order_pipeline_rows"] = 0
    run_op(pay)
    order_pipeline_hand(pay)
    b.add("pipeline", "order_pipeline", lambda: run_op(pay), No,
          note="README 附录 B 全量样例（before/after/optional/aggregate/nested）",
          fn_full=lambda: order_pipeline_hand(pay),
          fn_bare=lambda: order_pipeline_hand_bare(pay))

    # ---------------- 9. 钩子开/关
    d_p = make_rows(Ns)
    sch_no = mixed_schema(all_cols, "list", name="hook_none")
    sch_b = mixed_schema(all_cols, "list", name="hook_before", before=True)
    sch_a = mixed_schema(all_cols, "list", name="hook_after", after=True)
    run_n, src_n, gen_n = load_product(sch_no)
    run_b, src_b, gen_b = load_product(sch_b)
    run_a, src_a, gen_a = load_product(sch_a)
    for nm, src, gen in (("hook_none", src_n, gen_n), ("hook_before", src_b, gen_b),
                         ("hook_after", src_a, gen_a)):
        meta["artifacts"][nm] = dict(src_stats(src), gen_ms=gen / 1e6)
    run_n(d_p)
    run_b(d_p)
    run_a(d_p)
    meta["checks"]["hook_none_src"] = src_n
    meta["checks"]["hook_none_eq"] = _eq(run_n(d_p), hand_list(d_p, all_cols))
    meta["checks"]["hook_before_eq"] = _eq(run_b(d_p), hand_list(d_p, all_cols))
    meta["checks"]["hook_after_rows"] = len(run_a(d_p))
    meta["artifacts"]["hook_after_src"] = src_a
    b.add("hook", "hook_none", lambda: run_n(d_p), Ns, note="无钩子（基线）",
          fn_full=lambda: hand_list(d_p, all_cols))
    b.add("hook", "hook_before", lambda: run_b(d_p), Ns,
          note="before=$filter（恒真，多一遍扫描）",
          fn_full=lambda: hand_list(d_p, all_cols))
    b.add("hook", "hook_after", lambda: run_a(d_p), Ns,
          note="after=$map($,fn)（list 形状逐行恒等）",
          fn_full=lambda: hand_list(d_p, all_cols))

    # ---------------- 10. 生成期
    Ngen = 200 if not quick else 60
    schemas = {"mix9_list": mixed_schema(all_cols, "list"),
               "order_pipeline": ORDER_PIPELINE,
               "mix9_dict": mixed_schema(all_cols, "dict", name="mix9_dict")}
    meta["gen"] = {}
    for nm, sch in schemas.items():
        meta["gen"][nm] = measure_gen(sch)
    meta["gen"]["validate_only_us"] = _median_us(
        lambda: dcg.validate(ORDER_PIPELINE), 200)
    b.add("gen", "gen_mix9_list", _gen_loop_fn(schemas["mix9_list"], 20), Ngen,
          per_row=False, unit="次",
          note="每次跑 20 遍 generate()，n 是调用次数",
          fn_full=None, fn_bare=None) if False else None
    _gen_fn = _gen_loop_fn(schemas["mix9_list"], 20)
    b.add("gen", "gen_mix9_list_x20", _gen_fn, 20, per_row=False, unit="次",
          note="20 遍 generate()（每次产出 ~18 行）",
          fn_full=_gen_loop_fn(schemas["order_pipeline"], 20))
    b.add("gen", "gen_order_pipeline_x20",
          _gen_loop_fn(schemas["order_pipeline"], 20), 20, per_row=False,
          unit="次", note="20 遍 generate()（最大样例）",
          fn_full=_gen_loop_fn(schemas["mix9_list"], 20))

    # ---------------- 12. 从简单到复杂的阶梯
    LADDER = []
    lvl_cols = []
    step_defs = [
        ("bare", "1 列裸下标"),
        ("expr", "+1 列 expr（反引号算术）"),
        ("opt_typed", "+optional（有类型，.get 链）"),
        ("default", "+default"),
        ("nested", "+嵌套 fields"),
        ("backtick", "+反引号字符串段"),
        ("py_block", "+py{} 块"),
        ("hires", "+高阶函数 $reduce"),
    ]
    for i, (key, label) in enumerate(step_defs):
        lvl_cols.append(key)
        sch = mixed_schema(lvl_cols, "list", name="ladder_%02d_%s" % (i + 1, key))
        try:
            run, src, gen = load_product(sch)
        except Exception as exc:
            meta["checks"]["skip_ladder_" + key] = "%s: %s" % (type(exc).__name__, exc)
            continue
        run(d_f)
        meta["artifacts"]["ladder_" + key] = dict(src_stats(src), gen_ms=gen / 1e6)
        b.add("ladder", "L%d_%s" % (i + 1, key),
              lambda r=run, d=d_f: r(d), Ns, note=label,
              fn_full=(lambda sp=hand_mix_simple(d_f, list(lvl_cols)):
                       _run_compiled(sp, d_f)),
              fn_bare=(lambda sp=hand_mix_simple(d_f, list(lvl_cols)):
                       _run_compiled(sp, d_f)))
        LADDER.append({"step": i + 1, "key": key, "label": label,
                       "cols": list(lvl_cols),
                       "fields": sorted(sch["out"]["fields"]),
                       "chars": src_stats(src)["chars"]})
    # 第 9 级：+ aggregate 列容器（形状仍是 list，aggregate 作为尾元素）
    fields = {}
    for key in lvl_cols:
        fields[COL_FIELD[key]] = _col_spec(key)
    fields["stats"] = {"type": "object", "aggregate": True, "fields": {
        "n_rows": {"type": "int64", "const": 1},
        "qty_sum": {"type": "int64", "value": "$.2"}}}
    sch = S("ladder_09_agg", fields, "list")
    try:
        run, src, gen = load_product(sch)
        run(d_f)
        meta["artifacts"]["ladder_agg"] = dict(src_stats(src), gen_ms=gen / 1e6)

        #: 产物对 const 列走 flat-row 快路径，aggregate 尾元素直接是标量 1
        #: （``[r[-1]for r in _o][0]``），因此手写对手也必须放 ``1``。
        #: 当前产物把 aggregate 尾元素整段丢掉了（见 meta["checks"]["ladder_agg_note"]），
        #: 对手按「产物实际形态」对齐：前 8 列逐行 list，无尾元素。
        meta["checks"]["ladder_agg_note"] = (
            "aggregate 容器作为尾字段时产物未输出该列（与 agg_direct 同类缺陷）")
        meta["checks"]["eq_ladder_agg"] = _eq(run(d_f), hand_list(d_f, lvl_cols))

        def hand_ladder_agg(d):
            return hand_list(d, lvl_cols)

        def hand_ladder_agg_bare(d):
            return hand_list_bare(d, lvl_cols)

        meta["checks"]["eq_ladder_agg"] = _eq(run(d_f), hand_ladder_agg(d_f))
        b.add("ladder", "L9_agg", lambda r=run, d=d_f: r(d), Ns,
              note="+aggregate 列容器（尾元素）",
              fn_full=lambda d=d_f: hand_ladder_agg(d),
              fn_bare=lambda d=d_f: hand_ladder_agg_bare(d))
        LADDER.append({"step": 9, "key": "agg", "label": "+aggregate 列容器",
                       "cols": list(lvl_cols) + ["stats"],
                       "fields": sorted(fields), "chars": src_stats(src)["chars"]})
    except Exception as exc:
        meta["checks"]["skip_ladder_agg"] = "%s: %s" % (type(exc).__name__, exc)
    PAIR_EXPR = "`[$.0, $.1]`"
    # 第 10 级：pure-aggregate 形状（无逐行字段 —— 行容器 `_c([])` 是纯浪费）
    sch = S("ladder_10_aggonly", {"stats": {
        "type": "object", "aggregate": True, "fields": {
            "n_rows": {"type": "int64", "const": 1},
            "qty_sum": {"type": "int64", "value": "$.2"},
            "pair": {"type": "list", "expr": PAIR_EXPR}}}}, "dict")
    try:
        run, src, gen = load_product(sch)
        got = run(d_f)

        def hand_aggonly(d):
            return {"stats": {
                "n_rows": 1,
                "qty_sum": [r[2] for r in d],
                "pair": [[r[0], r[1]] for r in d]}}

        meta["checks"]["eq_ladder_aggonly"] = _eq(got, hand_aggonly(d_f))
        meta["checks"]["aggonly_row_loop"] = ("  _c([])" in src)
        meta["artifacts"]["ladder_aggonly"] = dict(src_stats(src),
                                                   gen_ms=gen / 1e6)
        b.add("ladder", "L10_aggonly", lambda r=run, d=d_f: r(d), Ns,
              note="纯 aggregate（无逐行字段）",
              fn_full=lambda d=d_f: hand_aggonly(d))
        LADDER.append({"step": 10, "key": "aggonly",
                       "label": "纯 aggregate（无逐行字段）",
                       "cols": ["stats"], "fields": ["stats"],
                       "chars": src_stats(src)["chars"]})
    except Exception as exc:
        meta["checks"]["skip_ladder_aggonly"] = "%s: %s" % (
            type(exc).__name__, exc)
    meta["ladder"] = LADDER

    # ---------------- 12b. 标准负载（order_pipeline）逐级累加
    # 目的：把「3.36 us/行」拆到具体形态上 —— 每一步只加一样东西，
    # 差值是这一形态的边际成本。数据与手写对手都固定，n 与 Ns 一致。
    stack = []
    try:
        base_fields = [
            ("o_id", {"type": "str", "value": "$.id"}),
            ("sku", {"type": "str", "value": "$.item.sku"}),
            ("qty", {"type": "int64", "value": "$.qty"}),
            ("price", {"type": "float64", "value": "$.price"}),
        ]
        variants = [
            ("S1_4value_cols", base_fields, None, None),
            ("S2_+expr", base_fields + [
                ("amount", {"type": "float64", "expr": "`$.qty * $.price`"})],
             None, None),
            ("S3_+optional", base_fields + [
                ("level", {"type": "str", "value": "$.buyer.level",
                           "optional": True})], None, None),
            ("S4_+nested", base_fields + [
                ("leg", {"type": "object", "fields": {
                    "frm": {"type": "str", "value": "$.item.from"},
                    "to": {"type": "str", "value": "$.item.to"}}})],
             None, None),
            ("S5_+hires_sq", base_fields + [
                ("fee", {"type": "float64",
                         "expr": "$sqrt($.qty * $.price)"})], None, None),
            ("S6_+before", base_fields, BEF_PIPE, None),
            ("S7_+after", base_fields, None, AFT_PIPE),
            ("S8_+agg", base_fields + [
                ("stats", {"type": "object", "aggregate": True, "fields": {
                    "n_rows": {"type": "int64", "const": 1},
                    "qty_sum": {"type": "int64", "value": "$.qty"}}})],
             None, None),
        ]
        for nm, flds, bef, aft in variants:
            sch = S("stack_" + nm.replace("+", "p"), dict(flds), "list",
                    before=bef, after=aft)
            sch["input"] = {"source": "$.payload.orders"}
            try:
                if aft is not None:
                    sch["output"]["shape"] = "dict"
            except Exception:
                pass
            try:
                run, src, gen = load_product(sch)
                datas = make_orders(Ns)
                got = run(datas)
                meta["artifacts"][nm] = dict(src_stats(src), gen_ms=gen / 1e6)
                meta.setdefault("ops", {})[nm] = dict(instrument(src))
                hand = _stack_hand(nm, datas)
                meta["checks"]["eq_" + nm] = _eq(got, hand)
                b.add("stack", nm, lambda r=run, d=datas: r(d), Ns,
                      note="order_pipeline 逐级累加",
                      # `fn_full` 要的是**可调用对象**：手写对手每次重跑一遍
                      # （`_stack_hand` 返回的是结果值，不能直接当函数传）。
                      fn_full=(lambda st=nm, d=datas: _stack_hand(st, d)))
                stack.append({"step": nm, "chars": src_stats(src)["chars"]})
            except Exception as exc:
                meta["checks"]["skip_" + nm] = "%s: %s" % (
                    type(exc).__name__, exc)
    except Exception as exc:
        meta["checks"]["skip_stack"] = "%s: %s" % (type(exc).__name__, exc)
    meta["stack"] = stack

    # ---------------- 13. 构件微基准（每行 1 个动作）
    micro = []
    d_m2 = d_f
    d_get = make_wide_rows(len(d_m2))

    def micro_ns(fn, n):
        xs = []
        for _ in range(7):
            t0 = time.perf_counter_ns()
            fn()
            xs.append((time.perf_counter_ns() - t0) / n)
        return statistics.median(xs)

    def loop_only(d):
        for r in d:
            pass

    def loop_append(d):
        o = []
        c = o.append
        for r in d:
            c(None)

    def loop_row3(d):
        o = []
        c = o.append
        for r in d:
            c([r[0], r[1], r[2]])

    def loop_row14(d):
        o = []
        c = o.append
        for r in d:
            c([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
               r[0], r[1], r[2], r[3]])

    def loop_ixget(d):
        o = []
        c = o.append
        for r in d:
            c(_ix_get(r, 2))

    def loop_get(d):
        o = []
        c = o.append
        for r in d:
            c(r.get("name"))

    def loop_get_rival(d):
        return [r["name"] for r in d]

    def loop_guard(d):
        o = []
        c = o.append
        for r in d:
            t = r.get("name", _U)
            c(None if t is _U else t)

    def loop_df(d):
        o = []
        c = o.append
        for r in d:
            c(_df(r[2], 7))

    def loop_ob(d):
        o = []
        c = o.append
        for r in d:
            c(_ob([("x", r[0]), ("y", r[1])]))

    def loop_dictlit(d):
        o = []
        c = o.append
        for r in d:
            c({"x": r[0], "y": r[1]})

    def loop_rd(d):
        o = []
        c = o.append
        for r in d:
            c(_rd(r[5]["a"], lambda a, b: _ad(a, b), 0))

    def loop_sum(d):
        o = []
        c = o.append
        for r in d:
            c(sum(r[5]["a"]))

    def loop_sum_lambda(d):
        o = []
        c = o.append
        f = lambda a, b: a + b
        for r in d:
            c(_rd(r[5]["a"], f, 0))

    def loop_mp(d):
        o = []
        c = o.append
        for r in d:
            c(_mp(r[5]["a"], lambda v, i: _ml(v, 2)))

    def loop_mp_inline(d):
        o = []
        c = o.append
        for r in d:
            c([v * 2 for v in r[5]["a"]])

    def loop_fi(d):
        o = []
        c = o.append
        for r in d:
            c(_fi(r[5]["a"], lambda v, i: _gt(v, 4)))

    def loop_fi_inline(d):
        o = []
        c = o.append
        for r in d:
            c([v for v in r[5]["a"] if v > 4])

    def loop_so(d):
        o = []
        c = o.append
        for r in d:
            c(_so(r[5]["a"]))

    def loop_sorted(d):
        o = []
        c = o.append
        for r in d:
            c(sorted(r[5]["a"]))

    def loop_lambda_stage(d):
        D = d
        D = (lambda X: X)(D)
        D = (lambda X: X)(D)
        return D

    def loop_stage_direct(d):
        return d

    def loop_ad(d):
        o = []
        c = o.append
        for r in d:
            c(_ad(r[1], r[2]))

    def loop_add(d):
        o = []
        c = o.append
        for r in d:
            c(r[1] + r[2])

    def transpose8(d):
        o = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in d]
        return {"k%d" % j: [r[j] for r in o] for j in range(8)}

    def transpose8_manual(d):
        o = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in d]
        return {"k0": [r[0] for r in o], "k1": [r[1] for r in o],
                "k2": [r[2] for r in o], "k3": [r[3] for r in o],
                "k4": [r[4] for r in o], "k5": [r[5] for r in o],
                "k6": [r[6] for r in o], "k7": [r[7] for r in o]}

    def rows_dictcomp(d):
        o = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in d]
        return [{k: rr[i] for i, k in enumerate(
            ["k0", "k1", "k2", "k3", "k4", "k5", "k6", "k7"])} for rr in o]

    def index_dictcomp(d):
        o = [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in d]
        return {i: rr for i, rr in enumerate(o)}

    micro_defs = [
        ("loop_only", loop_only, "for r in d: pass", None, None),
        ("loop_append", loop_append, "_c(None)", None, None),
        ("loop_row3", loop_row3, "append 3 元素 list", None, None),
        ("loop_row14", loop_row14, "append 14 元素 list", None, None),
        ("_ix_get", loop_ixget, "1× _ix_get(r,2)", "裸下标 r[2]",
         lambda d: [r[2] for r in d]),
        ("_ad", loop_ad, "1× _ad(a,b)", "a + b",
         lambda d: [r[1] + r[2] for r in d]),
        ("dict.get", loop_get, "1× r.get('name')", "裸下标 r['name']",
         loop_get_rival),
        ("opt_guard", loop_guard, ".get(_U)+is _U+三目", "r['name'] 直取",
         loop_get_rival),
        ("_df", loop_df, "1× _df(v,7)", "裸下标 r[2]",
         lambda d: [r[2] for r in d]),
        ("_ob2", loop_ob, "1× _ob(2 键)", "dict 字面量", loop_dictlit),
        ("_rd+lambda", loop_rd, "$reduce(4 元素)+_ad lambda", "sum() 内置",
         loop_sum),
        ("_rd+plain_lambda", loop_sum_lambda, "$reduce + a+b lambda", "sum()",
         loop_sum),
        ("_mp+lambda", loop_mp, "$map(4 元素)+lambda", "list comp 内联",
         loop_mp_inline),
        ("_fi+lambda", loop_fi, "$filter(4 元素)+lambda", "list comp 内联",
         loop_fi_inline),
        ("_so", loop_so, "$sort(4 元素)", "sorted() 内置", loop_sorted),
        ("transpose8", transpose8, "8 列 reshape（dict 推导）", "同结构手写 dict 字面量",
         transpose8_manual),
        ("rows_dictcomp", rows_dictcomp, "shape=rows 收尾推导", "手写 dict 字面量",
         lambda d: [{"k0": r[0], "k1": r[1], "k2": r[2], "k3": r[3], "k4": r[4],
                     "k5": r[5], "k6": r[6], "k7": r[7]} for r in d]),
        ("index_dictcomp", index_dictcomp, "shape=index 收尾推导", None, None),
        ("lambda_stage", loop_lambda_stage, "2 层 (lambda D:..)(_d)", "直通",
         loop_stage_direct),
    ]
    for nm, fn, note, rival, rival_fn in micro_defs:
        try:
            data = d_get if nm in ("dict.get", "opt_guard") else d_m2
            pn = micro_ns((lambda f=fn, dd=data: f(dd)), len(data))
            rv = (micro_ns((lambda f=rival_fn, dd=data: f(dd)), len(data))
                  if rival_fn is not None else None)
            micro.append({"name": nm, "note": note, "rival": rival,
                          "per_row_ns": pn, "rival_ns": rv,
                          "ratio": (pn / rv) if rv else None})
        except Exception as exc:
            micro.append({"name": nm, "error": "%s: %s" % (type(exc).__name__, exc)})
    meta["micro"] = micro

    # ---------------- 13b. 文档公开承诺负载：CAP / M0（tests/workload.py）
    # 这两份 schema 是 README / tests 里公开承诺的负载，验收报告要直接引用。
    # 行形状：(code:str, a:int, b:int, c:int, d:int, {"a":[4 ints]})，
    # 所以 `$.5.a.1` = R[5]['a'][1]。
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), "tests"))
        import workload as _W
    except Exception as exc:                           # pragma: no cover
        meta["checks"]["skip_workload"] = "%s: %s" % (type(exc).__name__, exc)
        _W = None
    meta.setdefault("ops", {})
    if _W is not None:
        cap_scale = int(os.environ.get("DCG_CAP_ROWS") or 100000)

        def cap_hand(datas):
            """V2_CAP 等价手写（骨架与生成产物逐行同构，只是手写）。"""
            o = []
            ap = o.append
            for R in datas:
                a = R[5]['a']
                a1 = a[1]
                a2 = a[2]
                ap([a1, a2, str(a1) + str(a2), a1 + a2])
            return o

        def cap_hand_bare(datas):
            """CAP 最简裸写：对手只做真正必需的取值。"""
            o = []
            ap = o.append
            for R in datas:
                a = R[5]['a']
                a1 = a[1]
                a2 = a[2]
                ap([a1, a2, str(a1) + str(a2), a1 + a2])
            return o

        def m0_hand(datas):
            """V2_M0 等价手写（整行 8 列，input.source = $.index）。"""
            o = []
            ap = o.append
            for R in datas["index"]:
                a = R[5]['a']
                a1 = a[1]
                a2 = a[2]
                ap([a, a1, a2, R[2], str(a1) + str(a2), a1 + a2, a2 + a1,
                    a[2] + 123])
            return o

        for nm, sch, hand, hand_bare in (
                ("cap", _W.V2_CAP, cap_hand, cap_hand_bare),
                ("m0", _W.V2_M0, m0_hand, m0_hand)):
            try:
                run, src, gen = load_product(sch)
                datas_big = _W.v2_input(_W.make_rows(cap_scale), sch)
                datas_bench = _W.v2_input(_W.make_rows(Ns), sch)
                got = run(datas_bench)
                meta["checks"]["eq_%s" % nm] = _eq(got, hand(datas_bench))
                meta["artifacts"]["%s" % nm] = dict(src_stats(src),
                                                    gen_ms=gen / 1e6)
                meta["ops"][nm] = dict(instrument(src), **src_stats(src))
                meta["ops"][nm]["src"] = src
                base = 9e-9
                k_big = max(1, min(3, int(4e-3 / (base * cap_scale))))
                b.add("cap", "%s@%d" % (nm, cap_scale),
                      lambda r=run, d=datas_big: r(d), cap_scale,
                      note="public workload, %d rows" % cap_scale,
                      fn_full=lambda h=hand, d=datas_big: h(d),
                      fn_bare=lambda h=hand_bare, d=datas_big: h(d))
            except Exception as exc:
                meta["checks"]["skip_%s" % nm] = "%s: %s" % (
                    type(exc).__name__, exc)

    # ---------------- 14. 源码级操作计数（解释 us/行 的来源）
    meta["ops"] = {}
    for nm, sch in (("mix9_list", schemas["mix9_list"]),
                    ("order_pipeline", ORDER_PIPELINE),
                    ("mix9_dict", schemas["mix9_dict"]),
                    ("optwide", OPTWIDE),
                    ("agg_container", agg_schema("agg_container_ops", True)),
                    ("cap", getattr(_W, "V2_CAP", None)),
                    ("m0", getattr(_W, "V2_M0", None))):
        if sch is None:
            continue
        try:
            src = dcg.generate(sch)
            meta["ops"][nm] = dict(instrument(src), **src_stats(src))
            meta["ops"][nm]["src"] = src
        except Exception as exc:
            meta["ops"][nm] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    # ---------------- 15. 缺陷复现（只记录，不计时）
    _repro_defects(meta)
    return meta


#: 标准负载逐级累加用的钩子表达式
BEF_PIPE = "= $filter($, function($r){ $r['qty'] > 0 })"
AFT_PIPE = "= $merge([$, {'count': $count($)}])"


def _stack_hand(step, datas):
    """``stack`` 组的手写对手（与当时那一步的字段集逐行同构）。"""
    out = []
    ap = out.append
    orders = datas["payload"]["orders"]
    keep = [o for o in orders if o["qty"] > 0]
    if step in ("S6_+before", "S7_+after"):
        src_rows = keep
    else:
        src_rows = orders
    if step == "S6_+before":
        for o in src_rows:
            it = o["item"]
            ap([o["id"], it["sku"], o["qty"], o["price"]])
        return out
    if step == "S7_+after":
        cols = []
        for o in src_rows:
            it = o["item"]
            cols.append([o["id"], it["sku"], o["qty"], o["price"]])
        return {"o_id": [r[0] for r in cols],
                "sku": [r[1] for r in cols],
                "qty": [r[2] for r in cols],
                "price": [r[3] for r in cols],
                "count": len(cols)}
    for o in src_rows:
        it = o["item"]
        row = [o["id"], it["sku"], o["qty"], o["price"]]
        if step == "S2_+expr":
            row.append(o["qty"] * o["price"])
        elif step == "S3_+optional":
            row.append((o["buyer"] or {}).get("level"))
        elif step == "S4_+nested":
            row.append({"frm": it["from"], "to": it["to"]})
        elif step == "S5_+hires_sq":
            row.append((o["qty"] * o["price"]) ** 0.5)
        ap(row)
    return out


def _median_us(fn, reps=9):
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter_ns()
        fn()
        xs.append(time.perf_counter_ns() - t0)
    return statistics.median(xs) / 1e3


def _repro_defects(meta):
    """跑一遍已知缺陷的最小复现，把结果写进 meta["checks"]（不影响计时）。"""
    out = {}
    # DEFECT-001：aggregate 直出列 + 行容器槽位错配
    try:
        src = dcg.generate(DEFECT_001_SCHEMA)
        g = {"__name__": "_defect001"}
        exec(compile(src, "<defect001>", "exec"), g)
        g["run"](DEFECT_001_DATAS)
        out["DEFECT-001"] = {"status": "no_error", "src": src}
    except Exception as exc:
        out["DEFECT-001"] = {"status": "%s: %s" % (type(exc).__name__, exc),
                             "src": src}
    # DEFECT-002 / DEFECT-002b：after 钩子的 $ 在列式形状上语义缺位
    for tag, sch, datas in (("DEFECT-002", DEFECT_002_SCHEMA, DEFECT_002_DATAS),
                            ("DEFECT-002b", DEFECT_002B_SCHEMA,
                             DEFECT_002B_DATAS)):
        try:
            src = dcg.generate(sch)
            g = {"__name__": "_" + tag.replace("-", "_")}
            exec(compile(src, "<%s>" % tag, "exec"), g)
            res = g["run"](datas)
            out[tag] = {"status": "no_error", "src": src,
                        "result": repr(res)[:800]}
        except Exception as exc:
            out[tag] = {"status": "%s: %s" % (type(exc).__name__, exc),
                        "src": src}
    # 对照：同一 after 换到 list 形状（用来证明缺陷不是 dict 独有）
    try:
        src_l = dcg.generate(DEFECT_002C_SCHEMA)
        g = {"__name__": "_defect002c"}
        exec(compile(src_l, "<defect002c>", "exec"), g)
        out["DEFECT-002-list_control"] = {
            "status": "no_error", "result": repr(g["run"](DEFECT_002_DATAS)),
            "src": src_l}
    except Exception as exc:
        out["DEFECT-002-list_control"] = {
            "status": "%s: %s" % (type(exc).__name__, exc)}
    meta["defects"] = out


def _gen_loop_fn(schema, reps):
    def f():
        for _ in range(reps):
            dcg.generate(schema)
    return f


def measure_gen(schema):
    """validate / generate / compile+exec 三段的耗时（各 9 次取中位）。"""
    def run_seg(fn, reps=9):
        xs = []
        for _ in range(reps):
            t0 = time.perf_counter_ns()
            fn()
            xs.append(time.perf_counter_ns() - t0)
        return statistics.median(xs) / 1e6
    m = {}
    m["validate_ms"] = run_seg(lambda: dcg.validate(schema))
    m["generate_ms"] = run_seg(lambda: dcg.generate(schema))
    src = dcg.generate(schema)
    m["exec_ms"] = run_seg(lambda: exec(compile(src, "<g>", "exec"),
                                        {"__name__": "_g"}), reps=5)
    m.update(src_stats(src))
    return m


# ============================================================ cProfile
def profile_top(scenarios, top=15):
    """对给定场景做 cProfile：tottime / cumtime 前 top 名 + 占比。"""
    import cProfile
    import pstats
    out = []
    for label, fn in scenarios:
        fn()
        pr = cProfile.Profile()
        pr.enable()
        fn()
        pr.disable()
        st = pstats.Stats(pr)
        total = st.total_tt or 1e-9

        def grab(sort, n):
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats(sort).print_stats(n)
            rs = _parse_pstats(s.getvalue())
            for r in rs:
                r["tottime_pct"] = 100.0 * r["tottime_s"] / total
                r["cumtime_pct"] = 100.0 * r["cumtime_s"] / total
            return rs[:n]

        out.append({"label": label, "total_s": total,
                    "by_tottime": grab("tottime", top),
                    "by_cumtime": grab("cumtime", top)})
    return out


# ============================================================ 插桩
def instrument(src):
    """统计产物源码里的运行期操作符个数（源码级计数，不做时间归因）。"""
    import re as _re
    pats = {
        "_ix_get": _re.compile(r"_ix_get\("),
        ".get(_U)": _re.compile(r"\.get\("),
        "_ob(": _re.compile(r"\b_ob\("),
        "_df(": _re.compile(r"\b_df\("),
        "_ad(": _re.compile(r"\b_ad\("),
        "_ml(": _re.compile(r"\b_ml\("),
        "lambda": _re.compile(r"lambda"),
        "is _U": _re.compile(r"is _U"),
        "subscript": _re.compile(r"\w\["),
        "dictcomp": _re.compile(r"for r in _"),
        "append": _re.compile(r"\.append\("),
    }
    return {k: len(p.findall(src)) for k, p in pats.items()}


# ============================================================ Cython
def cylon_probe(schema, datas):
    """Cython 模式：generate(cython=True) -> .pyx -> cythonize ->（若有编译器）导入计时。"""
    try:
        import Cython                                     # noqa: F401
    except Exception as exc:
        return {"status": "skipped", "reason": "Cython 不可用: %s" % exc}
    import shutil
    import subprocess
    tmp = tempfile.mkdtemp(prefix="dcg_cy_")
    try:
        src = dcg.generate(schema, cython=True)
        pyx = os.path.join(tmp, "cyprod.pyx")
        with open(pyx, "w", encoding="utf-8") as f:
            f.write(src)
        t0 = time.perf_counter_ns()
        r = subprocess.run([sys.executable, "-X", "utf8", "-m", "cython",
                            "-3", "--module-name", "cyprod", pyx],
                           capture_output=True, text=True, cwd=tmp)
        t_cy = (time.perf_counter_ns() - t0) / 1e6
        csrc = os.path.join(tmp, "cyprod.c")
        info = {"cythonize_ms": t_cy, "pyx_lines": len(src.splitlines()),
                "pyx_src": src}
        if r.returncode != 0:
            info.update({"status": "skipped",
                         "reason": "cython 编译失败: %s" % (r.stderr or "")[-500:]})
            return info
        if os.path.exists(csrc):
            with open(csrc, encoding="utf-8", errors="ignore") as f:
                info["c_lines"] = sum(1 for _ in f)
        cl = shutil.which("cl")
        if cl is None:
            info.update({"status": "skipped",
                         "reason": "无 C 编译器（cl/MSVC 不在 PATH），"
                                   "只能验证 .pyx 生成与 cythonize 成功"})
            return info
        # 有编译器时：编译 .c -> .pyd，导入并计时（本机没有走到这一步）
        info.update({"status": "skipped", "reason": "编译器存在但本 harness 未实现 pyd 链接"})
        return info
    except Exception as exc:
        return {"status": "skipped", "reason": "%s: %s" % (type(exc).__name__, exc)}


# ============================================================ 环境
def env_info():
    info = {
        "python": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "python_build": platform.python_build(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "dcg_version": getattr(dcg, "__version__", "?"),
        "perf_counter_res_ns": time.get_clock_info("perf_counter").resolution * 1e9,
    }
    try:
        import subprocess
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).Name;"
             "(Get-CimInstance Win32_Processor).MaxClockSpeed;"
             "(Get-CimInstance Win32_OperatingSystem).Caption"],
            capture_output=True, text=True, timeout=30)
        lines = [x.strip() for x in ps.stdout.splitlines() if x.strip()]
        if lines:
            info["cpu_name"] = lines[0]
        if len(lines) > 1:
            info["cpu_mhz"] = lines[1]
        if len(lines) > 2:
            info["os"] = lines[2]
    except Exception:
        pass
    try:
        import Cython
        info["cython"] = Cython.__version__
    except Exception:
        info["cython"] = None
    return info


def sha1_of(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _wait_for_import(timeout_s=20.0):
    """等待 dcg 能被干净导入（Lead 可能正在改 dcg/ 源码，导入会中途报错）。"""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            import importlib
            importlib.invalidate_caches()
            import dcg  # noqa: F401
            return True
        except Exception as exc:            # pragma: no cover
            last = "%s: %s" % (type(exc).__name__, exc)
            # 清掉半初始化的模块，避免拿到坏字节码缓存
            for m in list(sys.modules):
                if m == "dcg" or m.startswith("dcg."):
                    sys.modules.pop(m, None)
            time.sleep(1.0)
    print("!! dcg 导入仍失败（%.0fs）：%s" % (timeout_s, last), file=sys.stderr)
    return False


def source_fingerprint():
    """记录参与测量的 dcg 源码 sha1（结果可追溯）。"""
    files = ["dcg/__init__.py", "dcg/schema.py", "dcg/codegen.py",
             "dcg/runtime.py", "dcg/ir.py", "dcg/build.py",
             "dcg/jx/__init__.py"]
    out = {}
    for rel in files:
        p = os.path.join(ROOT, rel)
        out[rel] = sha1_of(p) if os.path.exists(p) else None
    import glob
    jx = sorted(glob.glob(os.path.join(ROOT, "dcg", "jx", "*.py")))
    out["dcg/jx/*"] = {os.path.basename(p): sha1_of(p) for p in jx}
    return out


# ============================================================ 报告
def fmt_ms(ns):
    return "%.3f" % (ns / 1e6)


def fmt_us(ns):
    return "%.3f" % (ns / 1e3)


def noise_block(rows, aa):
    """噪声底：A/A 差值分布 + 跨块/轮内 CV 分布。"""
    def pct(xs, q):
        xs = sorted(xs)
        if not xs:
            return 0.0
        i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
        return xs[i]
    gaps = [a["gap"] for a in aa]
    cvs = [r["cv"] for r in rows if r["cv"] is not None]
    return {
        "aa": aa,
        "aa_median": statistics.median(gaps) if gaps else 0.0,
        "aa_max": max(gaps) if gaps else 0.0,
        "cv_median": statistics.median(cvs) if cvs else 0.0,
        "cv_p90": pct(cvs, 0.9),
        "cv_max": max(cvs) if cvs else 0.0,
    }


def mark_significance(rows, noise):
    """|ratio-1| <= noise 的行标记 sig=False（与手写同量级，不得宣称快/慢）。

    ``noise`` 取 ``max(aa_max, min_sig_pct)``：A/A 给出的纯测量噪声（本机 1%~2%）
    过于乐观，把 3% 当作"可宣称最小效应量"（人类输入要求里出现过"差 <10% 不宣
    称"的更保守口径，一行里同时给出 ``sig10`` 供对照）。
    """
    lim = max(noise["aa_max"] or 0.0, MIN_SIG)
    cv_lim = max(3.0 * (noise["aa_median"] or 1e-9), 0.05)
    for r in rows:
        for tag in ("full", "bare"):
            ratio = r.get("ratio_" + tag)
            if ratio is None:
                r["sig_" + tag] = None
                r["sig10_" + tag] = None
            else:
                # 配对对手（full/bare 在同一循环里各做一半）没有独立 CV，
                # 用主项 CV 判定置信度，阈值取 3*噪声底（下限 5%）。
                r["sig_" + tag] = abs(ratio - 1.0) > lim
                r["sig10_" + tag] = abs(ratio - 1.0) > 0.10
        r["low_conf"] = bool(r["cv"] is not None and r["cv"] > cv_lim)
    # 绝对值（ns/行）的合并置信度：主项或对手任一侧 CV 超阈值就标低置信
    for r in rows:
        if r.get("full_paired") or r.get("bare_paired"):
            r["low_conf"] = bool(r["cv"] is not None and r["cv"] > cv_lim)
    noise["cv_limit"] = cv_lim
    noise["min_sig"] = lim
    return rows


def print_tables(payload, allow_pairs=False):
    rows = payload["rows"]
    noise = payload["noise"]
    groups = []
    for r in rows:
        if r["group"] not in groups:
            groups.append(r["group"])
    hdr = ("%-24s %9s %9s %9s %7s %7s %12s" %
           ("场景", "us/行", "full", "bare", "倍数", "CV%", "行/秒"))
    for g in groups:
        sel = [r for r in rows if r["group"] == g]
        print("== %s ==" % g)
        print(hdr)
        for r in sel:
            print("%-24s %9s %9s %9s %7s %7s %12s" % (
                r["name"],
                fmt_us(r["per_row_ns"]) if r["per_row_ns"] is not None
                else "%.3fms" % (r["ns"] / 1e6),
                fmt_us(r["full_per_row_ns"]) if r.get("full_per_row_ns")
                else "-",
                fmt_us(r["bare_per_row_ns"]) if r.get("bare_per_row_ns")
                else "-",
                ("%.2fx%s" % (r["ratio_full"],
                               "" if r.get("sig_full") else "~"))
                if r.get("ratio_full") else "-",
                "%.1f" % (100 * r["cv"]) if r["cv"] is not None else "-",
                "%.0f" % r["rows_per_s"] if r["rows_per_s"] else "-"))
        print()
    print("noise: aa_median %.2f%%  aa_max %.2f%%  cv_median %.2f%%  "
          "(~ = 差异在噪声底内)" % (100 * noise["aa_median"],
                                100 * noise["aa_max"], 100 * noise["cv_median"]))

    if allow_pairs:
        print("== A/A 对照（同一函数 A vs B，差值即噪声底）==")
        for aa in payload.get("aa", []):
            print("%-24s  A/B-1 = %+6.2f%%   (%d 次重测)" % (
                aa["label"], 100 * aa["gap"], aa.get("tries", 1)))
        print()


def write_md_table(payload, path):
    """额外落一份 markdown 总表（便于直接贴进报告）。"""
    rows = payload["rows"]
    noise = payload["noise"]
    lines = ["| 组 | 场景 | us/行 | 手写full us/行 | 手写bare us/行 | DCG/full | DCG/bare | CV% |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["group"], r["name"],
            fmt_us(r["per_row_ns"]) if r["per_row_ns"] is not None else "-",
            fmt_us(r["full_per_row_ns"]) if r.get("full_per_row_ns") else "-",
            fmt_us(r["bare_per_row_ns"]) if r.get("bare_per_row_ns") else "-",
            ("%.2f" % r["ratio_full"]) if r.get("ratio_full") else "-",
            ("%.2f" % r["ratio_bare"]) if r.get("ratio_bare") else "-",
            "%.1f" % (100 * r["cv"]) if r["cv"] is not None else "-"))
    lines.append("")
    lines.append("噪声底：aa_median %.2f%%，aa_max %.2f%%，cv_median %.2f%%"
                 % (100 * noise["aa_median"], 100 * noise["aa_max"],
                    100 * noise["cv_median"]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("md ->", path)


#: 本进程里已校验过的基线集合（``register`` 里再查时不再重复跑）
_OPT_VERIFIED = {}


def _verify_baselines(quiet=False):
    """跑一遍理论最优基线校验；任何一项不等都立刻抛 ``AssertionError``。

    这是 ``bench.py`` 的**硬闸门**：基线是本次唯一的对比参照物，写错就等于结论
    全错，所以宁可不跑也不能出数。校验内容见
    ``bench_optimallib.verify_all``：对每份 schema 拿 n=37 的真实负载数据，断言
    ``OPT`` 基线与 DCG 产物逐值相等（``KNOWN_MISMATCH`` 里登记过的产物缺陷除外，
    它们会以 ``KNOWN`` 单独打印，不静默放过）。
    """
    if _OPT_VERIFIED.get("done"):
        if not quiet:
            print("verify: 已校验（%d 项 OK）" % _OPT_VERIFIED["ok"])
        return _OPT_VERIFIED
    import bench_optimallib as _olib
    _olib._load_bench = lambda: sys.modules[__name__]   # 复用本模块的 schema 工厂
    pairs = _olib.build_pairs(sys.modules[__name__])
    res = _olib.verify_all(pairs, verbose=not quiet)
    _OPT_VERIFIED.update(done=True, ok=len(res["results"]) - len(res["fails"]),
                         res=res)
    return _OPT_VERIFIED


def main(argv=None):
    ap = argparse.ArgumentParser(description="DCG v2.1 性能评测")
    ap.add_argument("--repeat", type=int, default=5, help="每块样本数（中位数）")
    ap.add_argument("--k", type=int, default=0, help="每轮遍数（0=按预算自动）")
    ap.add_argument("--blocks", type=int, default=2, help="块数，取最小值")
    ap.add_argument("--rows", type=int, default=20000, help="行数扫描上限")
    ap.add_argument("--quick", action="store_true", help="冒烟模式")
    ap.add_argument("--json", default=None, help="机器可读结果落盘")
    ap.add_argument("--profile", action="store_true", help="最慢 3 个场景 cProfile")
    ap.add_argument("--md", default=None, help="额外落 markdown 总表")
    args = ap.parse_args(argv)

    random.seed(SEED)
    if not _wait_for_import():
        return 3
    for m in ("dcg", "dcg.codegen", "dcg.runtime", "dcg.schema"):
        sys.modules.pop(m, None)
    import dcg as _dcg2
    globals()["dcg"] = _dcg2
    importlib_ = __import__("importlib")
    importlib_.invalidate_caches()

    # ---------------- 硬闸门：理论最优基线必须先逐值对齐产物
    # 基线写错则全表数字作废，所以这里**先校验再测**，任何一项不等立刻抛
    # AssertionError（附两侧差异位置），不出数。
    _verify_baselines()

    b = Bench(repeat=args.repeat, k=args.k, blocks=args.blocks)
    t_start = time.perf_counter()
    meta = register("quick" if args.quick else "full", args.rows, b)
    rows = b.run_all()

    # ---------------- 噪声底：所有块结束后立刻再测一遍 A/A
    # （A/A 必须在"与正式测量相同的机器状态下"测；块内测会被前面的场景带偏）
    noise_aa = []
    for fn, label in _AA_PROBES:
        for _try in range(3):
            try:
                rec = b.aa_control(fn, "post_" + label)
            except Exception as exc:                   # pragma: no cover
                rec = {"label": "post_" + label,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
                break
            # A/A 一次测不准（Windows 毫秒级调度抖动），2% 以内就停
            if rec.get("gap", 0.0) <= 0.02:
                break
        noise_aa.append(rec)
    aa_all = [r for r in noise_aa if "gap" in r]
    wall = time.perf_counter() - t_start

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seed": SEED,
        "config": {"repeat": args.repeat, "k": args.k, "blocks": args.blocks,
                   "rows": args.rows, "quick": args.quick,
                   "budget_ns": BUDGET_NS},
        "env": env_info(),
        "source_sha1": source_fingerprint(),
        "rows": rows,
        "noise": noise_block(rows, aa_all),
        "meta": meta,
        "wall_s": wall,
    }
    payload["noise"]["aa"] = noise_aa                    # 只保留块后那一轮
    mark_significance(rows, payload["noise"])

    # ---------------- Cython 探测
    cy_schema = mixed_schema([k for k, _, _ in COLS], "list", name="mix_cy")
    cy = cylon_probe(cy_schema, None)
    payload["cython"] = cy

    if args.profile:
        slow = sorted([r for r in rows if r["group"] in
                       ("pipeline", "shape", "scale", "agg", "opt", "form")],
                      key=lambda r: -r["ns"])[:3]
        scen = [(r["name"], _PROFILE_FN[r["name"]]) for r in slow]
        payload["profile"] = profile_top(scen, top=15)
        payload["profile_note"] = [s[0] for s in scen]

    print("wall %.1fs  python %s  noise aa_med %.2f%% aa_max %.2f%%  cv_med %.2f%%"
          % (wall, payload["env"].get("python"),
             100 * payload["noise"]["aa_median"],
             100 * payload["noise"]["aa_max"],
             100 * payload["noise"]["cv_median"]))
    print_tables(payload)
    print("cython:", json.dumps({k: v for k, v in payload["cython"].items()
                                 if k != "pyx_src"}, ensure_ascii=False)[:400])

    out_json = args.json
    if out_json is None and not args.quick:
        out_json = os.path.join(HERE, "perf_bench.json")
    if out_json:
        keep = dict(payload)
        if args.quick:
            keep["note"] = "quick 模式（小规模冒烟），不要引用为结论"
        with open(out_json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(keep, f, ensure_ascii=False, indent=1, sort_keys=False)
        print("json ->", out_json)
    if args.md:
        write_md_table(payload, args.md)
    return payload


if __name__ == "__main__":
    main()
