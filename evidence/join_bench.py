# -*- coding: utf-8 -*-
"""DCG join 基准（2.2.3 的 ``$lookup`` 多参形态）—— 可复跑的单文件 harness。

为什么单独一个脚本
==================
``$lookup`` 表连接的代价分布在两处：**建索引**（一次性）与**行内探测**（每行）。
``evidence/bench.py`` 测的是表达式编译产物，跟右表规模无关；这里只关心
「右表从 1e3 涨到 1e6，每行的探测成本怎么变」，所以单独一份、只测 join。

两个层次（同一份数据、同一套 case）
===================================
* ``runtime``：直接 ``dcg.Table`` / ``index`` / ``g`` / ``a`` / ``agg`` / ``range``。
  这是冻结的运行时契约（``dcg/table.py``），**现在就能跑**，也是本脚本的主结论。
* ``e2e``：``dcg.generate`` → ``exec`` → ``run(data, tables=...)``。
  多参 ``$lookup`` 由生成器侧实现，**尚未合并时必然失败**；本脚本先探测一次，
  不可用就把整层标 ``skipped: <原因>``，不崩、不假装有数字。

口径
====
1. 计时 ``time.perf_counter``；每项 **warmup 1 次（右表 > 20 万行时 2 次）+ 采样
   ``--repeat`` 次取中位数**。中位数只由中间那次决定，偶发调度抖动最多污染一个样本。
   采样段先 ``gc.collect()`` 再 ``gc.disable()``，``finally`` 恢复：大表上循环分配
   会让分代 GC 落进计时窗口，实测能把极差从 44% 压到 7%。
   ``e2e`` 层只计 ``run`` 调用本身：``dcg.generate`` + ``exec`` 是**一次性**成本
   （每次进程一次），混进中位数只会让首项虚高。
2. ``us_per_row`` = ``median_ms * 1000 / left_rows``，跨右表规模可比。
3. 内存拆两列，**不可跨层比较**：
   * ``peak_build_mb``：建右表 + 建索引的峰值（``runtime`` 包 builder，``e2e`` 包
     ``Table(...)`` + ``prepare``）。
   * ``peak_run_mb``：一次 ``run`` 的峰值（``e2e`` 含左表输出组装，所以它随左表
     走、与右表规模几乎无关）。
   两列都**只对右表 ≤ 20 万行测量**：tracemalloc 会给每次分配留一条 trace，
   1e6 行时自身开销可达数倍，测出来的不是表的大小而是 tracemalloc 的。超限写 ``n/a``。
4. ``build_ms`` 记建表 + 建索引的一次性墙钟耗时；它不在 ``median_ms`` 里，
   但「1:N 预聚合到底多贵」只有这一列看得见。
5. ``fanout`` 是该 case 在右表上的平均每键行数。三档右表的扇出被钉在 ``FANOUT``
   （见 ``_n_oid``），所以 ``agg1n`` 三档的 ``us_per_row`` 才可比；否则 1e6 档的
   桶宽变化会被误读成「连接随右表规模劣化」。
6. 每个 case 只建自己需要的那张右表，跑完 ``del`` + ``gc.collect()``；
   1e6 档不会三张表同时驻留。
7. 内存预检：按 ``右表行数 × 420 B`` 估峰值，超过可用内存 70% 直接
   ``skipped`` 并写明原因（1e6 在 4 GB 机器上就该跳过，而不是 OOM 到一半）。
   可用内存取 ``min(物理可用, cgroup 限额)``——容器里 ``SC_AVPHYS_PAGES`` 读的是
   宿主内存，只用它会让 70% 线形同虚设。

case
====
简单查询（单表 N:1 取列）：
    ``simple_n1``      ``customers.id -> name``
复杂查询：
    ``chain2``         2 跳：``customers.id -> region_id``，``regions.id -> name``
    ``composite_key``  复合键：``(sku, region) -> stock.price``
    ``range_pred``     区间：``RangeIndex.pick(amount)``（``rates.lo/hi``，
                       右表固定 200 档，**不随 ``--right`` 变化**——区间索引的
                       成本与右表行数无关，这一项是「每行一次 bisect」的常数项）
    ``agg1n``          1:N 聚合：``items`` 按 ``oid`` 分组的 ``amount`` 预聚合
    ``agg1n_scan``     1:N 逐行求和（不预聚合的对照，``sum(a(k))``）

端到端层的 5 个 schema 与上面一一对应，**但 ``range_pred`` 这一项两层算法不同**：
``RangeIndex`` 只在运行时层暴露，端到端层只能用「等值前缀（``region``）+ 谓词
（``$r.lo <= $l.amount and $r.hi > $l.amount``）」表达，生成码发的是
``Index.qa`` 谓词扫描而不是 ``bisect``。所以这一格两层不可当作「固定开销」比较。

CLI
===
    python -X utf8 evidence/join_bench.py --help
    python -X utf8 evidence/join_bench.py                       # 200k 行 × 三档右表
    python -X utf8 evidence/join_bench.py --rows 20000 \
        --right 1000 --right 10000 --case all --json .tmp_bench.json
    python -X utf8 evidence/join_bench.py --ci --json join-bench.json
    python -X utf8 evidence/join_bench.py --ci --full           # 含 1e6 档

``--ci`` / ``--full`` 的采样次数都是 3（``--repeat`` 可覆盖）；1e6 档首个样本系统性
偏低，``repeat=1`` 取到的会是偏小的那个数。

本脚本只读 ``dcg`` 源码，不改任何文件；产物在内存里 exec，不落盘。
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dcg                                              # noqa: E402

#: ``dcg/table.py`` 是 2.2.3 的运行时层；优先从模块导入，兼容将来挂到包顶层。
try:                                                     # noqa: E402
    from dcg.table import Table, prepare, bind
except ImportError:                                      # pragma: no cover
    Table = getattr(dcg, "Table", None)
    prepare = getattr(dcg, "prepare", None)
    bind = getattr(dcg, "bind", None)

#: 固定种子：数据生成是确定性的，两次跑的输入逐字节相同。
SEED = 20261105

#: ``peak_mem_mb`` 的测量上限（右表行数）。超过就写 n/a，见文件头口径 3。
MEM_LIMIT_ROWS = 200000

#: 内存预检用的每行字节数（dict 行 + 哈希索引一条 entry 的粗略上界）。
BYTES_PER_ROW = 420

#: ``agg1n`` 的桶数上限：1:N 的 N 太大时 ``sum(a(k))`` 的对照项会失去意义。
MAX_OID = 20000

#: 1:N 的平均每键行数。**三档右表必须一致**，否则 1e6 档的桶宽变化会被读成
#: 「连接随右表规模劣化」。取 50 是因为 ``MAX_OID`` 只有 20000：
#: 1e6 / 20000 = 50，再小就装不下 1e6 档了。
FANOUT = 50


def _n_oid(right_rows):
    """``agg1n`` 的桶数：钉住扇出，只在 1e6 档才撞 ``MAX_OID`` 上限。"""
    return max(1, min(right_rows // FANOUT, MAX_OID))

#: 右表地区数（复合键第二分量 / 区间谓词的等值前缀）。
N_REGIONS = 50

#: 区间档数：200 档 × 5000 = [0, 1e6)，与左表 ``amount`` 值域对齐。
N_BANDS = 200
BAND_W = 5000


class _Skip(Exception):
    """本 case 在本机/本版本上不可测：记录原因，不视为失败。"""


def _contract_check():
    """启动时跑一遍运行时契约自检，返回一行可读结论。

    基准本身只依赖 ``Table`` / ``index`` / ``g`` / ``a`` / ``agg`` / ``range``；
    ``prepare`` / ``bind`` 是生成代码侧的建索引入口（``_t[name].s0``），这里一并
    验一遍：它们坏了，e2e 层即使接上也会是另一种形态，得在报告里说清楚。
    自检失败只记文本，不中断基准。
    """
    checks = []
    try:
        tables = {"t": Table([{"id": 1, "k": "a", "v": 2},
                              {"id": 2, "k": "a", "v": 3}])}
        specs = [{"kind": "hash", "table": "t", "key": "id",
                  "mode": "strict", "strategy": "one"}]
        prepare(specs, tables, release=False)
        hit = tables["t"].s0.g(1) is not None
        bind(specs, {"t": tables["t"]})
        checks.append("prepare/bind=%s" % ("ok" if hit else "MISS"))
    except Exception as e:
        checks.append("prepare/bind=%s" % type(e).__name__)
    try:
        t = Table([{"lo": 0, "hi": 5}, {"lo": 5, "hi": 9}])
        checks.append("range=%s" % ("ok" if t.range("lo", "hi").pick(6) else "MISS"))
    except Exception as e:
        checks.append("range=%s" % type(e).__name__)
    try:
        t = Table([{"k": 1, "v": 2}, {"k": 1, "v": 3}])
        i = t.index("k", "strict", "all")
        checks.append("agg=%s" % ("ok" if i.agg("v", "sum").get(1) == 5 else "MISS"))
    except Exception as e:
        checks.append("agg=%s" % type(e).__name__)
    return " ".join(checks)


# ---------------------------------------------------------------------------
# 机器与内存
# ---------------------------------------------------------------------------
def _cgroup_limit_mb():
    """容器内存限额（MB）；非容器 / 读不到返回 ``None``。

    ``SC_AVPHYS_PAGES`` 在容器里报的是**宿主**的可用内存，CI runner 或任何受限
    容器上都会把 70% 预检线撑得形同虚设。v2 读 ``/sys/fs/cgroup/memory.max``，
    v1 读 ``memory/memory.limit_in_bytes``；值是字符串 ``"max"`` 时表示无限制。
    """
    if not sys.platform.startswith("linux"):
        return None
    for path in ("/sys/fs/cgroup/memory.max",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except (OSError, IOError):
            continue
        if not raw or raw == "max":
            return None
        try:
            limit = int(raw)
        except ValueError:
            continue
        if limit <= 0:
            continue
        # v1 无限制时是一个巨大的哨兵值（约 2^63），当无限制处理。
        if limit >= (1 << 62):
            return None
        return limit / (1024.0 * 1024.0)
    return None


def _avail_mem_mb():
    """可用内存（MB）：取 ``min(物理可用, cgroup 限额)``；取不到返回 ``None``。"""
    phys = _phys_avail_mb()
    limit = _cgroup_limit_mb()
    if phys is None:
        return limit
    if limit is None:
        return phys
    return min(phys, limit)


def _phys_avail_mb():
    """可用物理内存（MB）；取不到返回 ``None``。只用标准库。"""
    try:
        if os.name == "nt":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullAvailPhys / (1024.0 * 1024.0)
        pages = os.sysconf("SC_AVPHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return pages * size / (1024.0 * 1024.0)
    except Exception:
        return None


def _mem_guard(right_rows, left_rows):
    """1e6 档在内存不够时提前跳过，给出可读原因而不是 MemoryError。"""
    avail = _avail_mem_mb()
    if avail is None:
        return
    need = (right_rows + left_rows) * BYTES_PER_ROW / (1024.0 * 1024.0)
    if need > avail * 0.70:
        raise _Skip(
            "内存预检不通过：估需 %.0f MB（右表 %d 行 + 左表 %d 行），"
            "可用 %.0f MB；如需强跑请释放内存或减小 --right"
            % (need, right_rows, left_rows, avail))


# ---------------------------------------------------------------------------
# 数据生成（全部确定性，不依赖 random 的版本细节）
# ---------------------------------------------------------------------------
def make_left(n, right_rows):
    """左表 ``orders``：``cust`` / ``sku`` / ``region`` / ``amount`` 覆盖右表值域。"""
    out = []
    n_oid = _n_oid(right_rows)
    for i in range(n):
        sku = (i * 104729) % right_rows + 1
        out.append({
            "id": i + 1,
            "cust": (i * 7919) % right_rows + 1,
            "sku": sku,
            "region": (sku - 1) % N_REGIONS,
            "amount": (i * 2654435761) % 1000000,
            "oid": i % n_oid + 1,
        })
    return out


def gen_customers(n):
    return [{"id": i + 1, "name": "c%07d" % i, "region_id": i % N_REGIONS + 1,
             "tier": i % 4} for i in range(n)]


def gen_regions(n=N_REGIONS):
    return [{"id": j + 1, "name": "r%02d" % j} for j in range(n)]


def gen_stock(n):
    return [{"sku": i + 1, "region": i % N_REGIONS, "price": (i % 997) + 1}
            for i in range(n)]


def gen_rates():
    """``N_BANDS`` 个半开区间 ``[lo, hi)``，每地区各一档，供区间谓词用。"""
    rows = []
    for k in range(N_BANDS):
        rows.append({"lo": k * BAND_W, "hi": (k + 1) * BAND_W,
                     "rate": 0.01 * (k % 9 + 1), "region": k % N_REGIONS})
    return rows


def gen_items(n):
    n_oid = _n_oid(n)
    return [{"oid": i % n_oid + 1, "amount": i % 100} for i in range(n)]


def _fanout(case, right_rows):
    """该 case 在右表上的平均每键行数（``agg1n``/``agg1n_scan`` 才有意义）。"""
    if case in ("agg1n", "agg1n_scan"):
        return round(right_rows / float(_n_oid(right_rows)), 2)
    return None


# ---------------------------------------------------------------------------
# 运行时层 case
# ---------------------------------------------------------------------------
def _need_table():
    if Table is None:                                    # pragma: no cover
        raise _Skip("dcg.Table 不可用（dcg/table.py 未导出）")


def rt_simple(right_rows, left):
    """N:1 取列：``customers.id -> name``。"""
    _need_table()
    idx = Table(gen_customers(right_rows)).index("id", "strict", "one")

    def run():
        acc = 0
        for row in left:
            c = idx.g(row["cust"])
            if c is not None:
                acc += len(c["name"])
        return acc

    return run


def rt_chain2(right_rows, left):
    """2 跳链式：``customers.id -> region_id``，``regions.id -> name``。"""
    _need_table()
    cust = Table(gen_customers(right_rows)).index("id", "strict", "one")
    reg = Table(gen_regions()).index("id", "strict", "one")

    def run():
        acc = 0
        for row in left:
            c = cust.g(row["cust"])
            if c is not None:
                r = reg.g(c["region_id"])
                if r is not None:
                    acc += len(r["name"])
        return acc

    return run


def _composite_key_form():
    """复合键（右键数组）是否已被 ``dcg.table`` 支持；返回可用的 key 形态。

    两种写法都试：``["sku", "region"]``（契约里的路径数组）与元组。谁先成功用谁，
    这样 Lead 侧无论用哪种归一化落地，基准都能自动接上。
    """
    last = None
    for form in (["sku", "region"], ("sku", "region")):
        try:
            t = Table([{"sku": 1, "region": 1, "price": 1},
                       {"sku": 2, "region": 1, "price": 2}])
            t.index(form, "strict", "one")
            return form
        except Exception as e:
            last = e
    raise _Skip("dcg.table 尚不支持复合键（右键数组）：%s: %s"
                % (type(last).__name__, last))


def rt_composite(right_rows, left):
    """复合键：``(sku, region) -> stock.price``。"""
    _need_table()
    key_form = _composite_key_form()
    idx = Table(gen_stock(right_rows)).index(key_form, "strict", "one")

    def run():
        acc = 0
        for row in left:
            r = idx.g((row["sku"], row["region"]))
            if r is not None:
                acc += r["price"]
        return acc

    return run


def rt_range(right_rows, left):
    """区间：``rates`` 按 ``lo`` 排序，``pick(amount)`` 找 ``lo <= x < hi``。"""
    _need_table()
    idx = Table(gen_rates()).range("lo", "hi")

    def run():
        acc = 0
        for row in left:
            r = idx.pick(row["amount"])
            if r is not None:
                acc += 1
        return acc

    return run


def rt_agg1n(right_rows, left):
    """1:N 聚合（C7 预聚合）：``agg("amount", "sum")`` 后每行一次 dict 查找。"""
    _need_table()
    idx = Table(gen_items(right_rows)).index("oid", "strict", "all")
    sums = idx.agg("amount", "sum")

    def run():
        acc = 0
        for row in left:
            v = sums.get(row["oid"])
            if v is not None:
                acc += v
        return acc

    return run


def rt_agg1n_scan(right_rows, left):
    """1:N 逐行求和（不预聚合的对照）：``sum(r["amount"] for r in a(k))``。"""
    _need_table()
    idx = Table(gen_items(right_rows)).index("oid", "strict", "all")

    def run():
        acc = 0
        for row in left:
            for r in idx.a(row["oid"]):
                acc += r["amount"]
        return acc

    return run


# ---------------------------------------------------------------------------
# 端到端层 case（生成器侧多参 $lookup）
# ---------------------------------------------------------------------------
def _sch(name, fields):
    return {"name": name, "output": {"shape": "list"},
            "out": {"type": "object", "fields": fields}}


E2E_SCHEMAS = {
    "simple_n1": lambda: (
        _sch("bench_simple", {
            "oid": {"type": "int64", "value": "$.id"},
            "cname": {"type": "str",
                      "expr": "$lookup($.cust,'customers','id','name')"},
        }),
        lambda right_rows: {"customers": gen_customers(right_rows)}),

    "chain2": lambda: (
        _sch("bench_chain2", {
            "oid": {"type": "int64", "value": "$.id"},
            "rname": {"type": "str", "expr":
                      "$lookup($lookup($.cust,'customers','id','region_id'),"
                      "'regions','id','name')"},
        }),
        lambda right_rows: {"customers": gen_customers(right_rows),
                            "regions": gen_regions()}),

    "composite_key": lambda: (
        _sch("bench_composite", {
            "oid": {"type": "int64", "value": "$.id"},
            "price": {"type": "int64", "expr":
                      "$lookup([$.sku,$.region],'stock',['sku','region'],'price')"},
        }),
        lambda right_rows: {"stock": gen_stock(right_rows)}),

    "range_pred": lambda: (
        _sch("bench_range", {
            "oid": {"type": "int64", "value": "$.id"},
            "rate": {"type": "float64", "expr":
                     "$sum($lookup($.region,'rates','region','rate','strict','all',"
                     "'$l.amount >= $r.lo and $r.hi > $l.amount'))"},
        }),
        lambda right_rows: {"rates": gen_rates()}),

    "agg1n": lambda: (
        _sch("bench_agg", {
            "oid": {"type": "int64", "value": "$.id"},
            "total": {"type": "int64", "expr":
                      "$sum($lookup($.oid,'items','oid','amount','strict','all'))"},
        }),
        lambda right_rows: {"items": gen_items(right_rows)}),
}


def _e2e_probe():
    """探测一次端到端层：generate + exec + 两参 run 是否都成立。

    返回 ``(ok, reason)``。失败只记原因，不算错误 —— 生成器侧由 Lead 并行实现。
    """
    try:
        schema, tables_of = E2E_SCHEMAS["simple_n1"]()
        src = dcg.generate(schema)
        ns = {}
        exec(compile(src, "<join_bench>", "exec"), ns)
        fn = ns["run"]
        data = [{"id": 1, "cust": 1, "sku": 1, "region": 0,
                 "amount": 1000, "oid": 1}]
        fn(data, tables=tables_of(2))
        return True, ""
    except Exception as e:
        msg = str(e).strip().splitlines()
        return False, "%s: %s" % (type(e).__name__, (msg[0] if msg else "")[:200])


# ---------------------------------------------------------------------------
# 计时与表格
# ---------------------------------------------------------------------------
def _warmups(right_rows):
    """大表需要更多 warmup：1e6 档首个样本系统性偏低（见 RFC §6.3）。"""
    return 2 if right_rows > MEM_LIMIT_ROWS else 1


def _timeit(fn, repeat, warmups=1):
    """warmup ``warmups`` 次 + ``repeat`` 次采样，返回 (中位数 ms, 样本 ms 列表)。

    采样段关掉分代 GC：``left`` 是几十万行的 dict 列表，行循环里的临时元组/字符串
    会触发 GC，且触发点落在哪次采样上是随机的。实测（e2e ``simple_n1``，左表 5 万）
    GC 开着极差 44%、关掉 7%。
    """
    for _ in range(warmups):
        fn()
    samples = []
    gc.collect()
    gc.disable()
    try:
        for _ in range(repeat):
            t0 = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - t0) * 1000.0)
    finally:
        gc.enable()
    return statistics.median(samples), samples


def _build_with_mem(build, right_rows):
    """建表 + 建索引，返回 ``(fn, build_ms, peak_build_mb)``。

    ``peak_build_mb`` 只含建表 + 建索引（不含 run）；大表跳过测量。``build_ms``
    用 ``perf_counter`` 包住 builder —— 它是**一次性**成本，不进 ``median_ms``，
    但没有它就看不出 1:N 预聚合的真实代价。
    """
    if right_rows > MEM_LIMIT_ROWS:
        t0 = time.perf_counter()
        fn = build()
        return fn, (time.perf_counter() - t0) * 1000.0, None
    tracemalloc.start()
    try:
        t0 = time.perf_counter()
        fn = build()
        build_ms = (time.perf_counter() - t0) * 1000.0
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return fn, build_ms, peak / (1024.0 * 1024.0)


def _fmt(v, width, digits=3):
    if v is None:
        s = "n/a"
    elif isinstance(v, float):
        s = ("%." + str(digits) + "f") % v
    else:
        s = str(v)
    return s.rjust(width)


HEADERS = ("case", "layer", "right_rows", "left_rows", "median_ms",
           "us_per_row", "build_ms", "peak_build_mb", "peak_run_mb",
           "fanout", "status")


def _print_table(rows):
    widths = (14, 8, 11, 10, 11, 11, 10, 14, 13, 8, 8)
    print("  ".join(h.ljust(w) for h, w in zip(HEADERS, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        cells = (
            r["case"],
            r["layer"],
            str(r["right_rows"]),
            str(r["left_rows"]),
            _fmt(r.get("median_ms"), widths[4]),
            _fmt(r.get("us_per_row"), widths[5]),
            _fmt(r.get("build_ms"), widths[6]),
            _fmt(r.get("peak_build_mb"), widths[7], 3),
            _fmt(r.get("peak_run_mb"), widths[8], 3),
            _fmt(r.get("fanout"), widths[9]),
            r["status"],
        )
        print("  ".join(c.ljust(w) for c, w in zip(cells, widths)))
        if r.get("note"):
            print("      note: " + r["note"])


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
RUNTIME_CASES = [
    ("simple_n1", "simple", rt_simple),
    ("chain2", "complex", rt_chain2),
    ("composite_key", "complex", rt_composite),
    ("range_pred", "complex", rt_range),
    ("agg1n", "complex", rt_agg1n),
    ("agg1n_scan", "complex", rt_agg1n_scan),
]

E2E_CASES = ["simple_n1", "chain2", "composite_key", "range_pred", "agg1n"]

# 类别归属的唯一来源：run_runtime / run_e2e 都从这里取，避免两处各写一份而漂移。
# 口径与 docs/join-design.md §6.1 一致：
#   simple  = 单表连接、单键、等值（只此一项）；
#   complex = 多级链式 / 复合键 / 范围谓词 / 1:N 聚合 —— 命中后还要再走一跳、
#             组键匹配、区间谓词或聚合，成本结构与单键等值不是一回事。
GROUPS = {name: group for name, group, _ in RUNTIME_CASES}


def _new_row(case, layer, group, right_rows, left_rows):
    return {"case": case, "layer": layer, "group": group,
            "right_rows": right_rows, "left_rows": left_rows,
            "median_ms": None, "us_per_row": None, "build_ms": None,
            "peak_build_mb": None, "peak_run_mb": None,
            "fanout": _fanout(case, right_rows),
            "status": "ok", "note": "", "samples_ms": []}


def run_runtime(name, builder, right_rows, left, repeat):
    row = _new_row(name, "runtime", GROUPS[name], right_rows, len(left))
    _mem_guard(right_rows, len(left))
    fn, build_ms, peak_build = _build_with_mem(
        lambda: builder(right_rows, left), right_rows)
    row["build_ms"] = build_ms
    row["peak_build_mb"] = peak_build
    # runtime 层只有「建」与「跑」两段；peak_run_mb 单独测一次，避免和建库混算。
    median_ms, samples = _timeit(fn, repeat, _warmups(right_rows))
    row["median_ms"] = median_ms
    row["us_per_row"] = median_ms * 1000.0 / len(left)
    row["samples_ms"] = [round(x, 3) for x in samples]
    if right_rows <= MEM_LIMIT_ROWS:
        tracemalloc.start()
        try:
            fn()
            row["peak_run_mb"] = tracemalloc.get_traced_memory()[1] / (1024.0 * 1024.0)
        finally:
            tracemalloc.stop()
    return row


def run_e2e(name, right_rows, left, repeat, available, reason):
    row = _new_row(name, "e2e", GROUPS[name], right_rows, len(left))
    if not available:
        row["status"] = "skipped"
        row["note"] = reason
        return row
    _mem_guard(right_rows, len(left))
    schema, tables_of = E2E_SCHEMAS[name]()
    use_mem = right_rows <= MEM_LIMIT_ROWS
    try:
        src = dcg.generate(schema)
        ns = {}
        exec(compile(src, "<join_bench>", "exec"), ns)
        fn = ns["run"]
        # 右表包成 Table 后**在计时之外**建齐索引。
        #
        # 这一步不能省：直接传裸行列表的话，生成码里的 ``_tm`` 每次调用都会把列表
        # 重新包成新的 ``Table``，``Table.index()`` 的幂等缓存只在同一个 Table 对象
        # 内生效，于是**每次 run 都在重建索引**——测出来的是「建索引 + 连接」，
        # 右表 1e6 时建索引会盖掉连接本身（差一个量级）。RFC §6.2 要求的是
        # 「索引一次建好、行循环内无构建分支」，所以这里先 prepare 再计时。
        if use_mem:
            tracemalloc.start()
        try:
            t0 = time.perf_counter()
            tables = {k: dcg.Table(v) for k, v in tables_of(right_rows).items()}
            dcg.prepare(dcg.specs_of(schema), tables, release=False)
            row["build_ms"] = (time.perf_counter() - t0) * 1000.0
            row["peak_build_mb"] = (tracemalloc.get_traced_memory()[1]
                                    / (1024.0 * 1024.0) if use_mem else None)
        finally:
            if use_mem:
                tracemalloc.stop()
        for _ in range(_warmups(right_rows)):         # warmup 兼可用性确认
            fn(left, tables=tables)
        call = lambda: fn(left, tables=tables)        # noqa: E731 —— 只为绑定实参
        row["median_ms"], samples = _timeit(call, repeat, 0)
        if use_mem:
            tracemalloc.start()
            try:
                fn(left, tables=tables)
                row["peak_run_mb"] = (tracemalloc.get_traced_memory()[1]
                                      / (1024.0 * 1024.0))
            finally:
                tracemalloc.stop()
    except Exception as e:                            # 生成器侧未就绪 / 语义差异
        row["status"] = "skipped"
        msg = str(e).strip().splitlines()
        row["note"] = "%s: %s" % (type(e).__name__, (msg[0] if msg else "")[:200])
        return row
    row["us_per_row"] = row["median_ms"] * 1000.0 / len(left)
    row["samples_ms"] = [round(x, 3) for x in samples]
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="join_bench.py",
        description="DCG join 基准：运行时层（Table/index/g/a/agg/range）+ "
                    "端到端层（generate/exec/run(data, tables=...)）。",
        epilog="示例：python -X utf8 evidence/join_bench.py --rows 20000 "
               "--right 1000 --right 10000 --case all --json .tmp_bench.json")
    ap.add_argument("--rows", type=int, default=None,
                    help="左表行数（默认 200000；--ci 下 50000）")
    ap.add_argument("--right", type=int, action="append", default=None,
                    metavar="N", help="右表体量，可重复给多个（默认 1000/10000/1000000）")
    ap.add_argument("--case", choices=("simple", "complex", "all"), default="all",
                    help="只跑简单查询、只跑复杂查询，或全部（默认 all）")
    ap.add_argument("--repeat", type=int, default=None,
                    help="每项采样次数，取中位数（默认 5；--ci / --full 都是 3）")
    ap.add_argument("--json", dest="json_path", default=None,
                    metavar="PATH", help="把机器可读结果写到 PATH")
    ap.add_argument("--ci", action="store_true",
                    help="CI 档：缩规模（5 万行 / 1e3+1e4 右表 / 3 次采样），稳定输出")
    ap.add_argument("--full", action="store_true",
                    help="含 1e6 右表档（配合 --ci 用于 workflow_dispatch）")
    ap.add_argument("--no-mem", action="store_true",
                    help="完全跳过 tracemalloc 峰值测量")
    args = ap.parse_args(argv)

    rows = args.rows
    rights = list(args.right) if args.right else None
    repeat = args.repeat

    if args.ci:
        rows = 50000 if rows is None else rows
        if rights is None:
            rights = [1000, 10000]
    if args.full:
        if args.right is None:
            rights = [1000, 10000, 1000000]
    if (args.ci or args.full) and repeat is None:
        # CI 档与 1e6 档**统一 3 次采样**：1e6 档首个样本系统性偏低（同一 run 内
        # e2e simple_n1 实测 37.5 / 81.8 / 82.9 ms），repeat=1 会把 37.5 当中位数。
        repeat = 3

    rows = 200000 if rows is None else rows
    rights = [1000, 10000, 1000000] if rights is None else rights
    repeat = 5 if repeat is None else repeat
    if repeat < 1:
        ap.error("--repeat 必须 >= 1")

    global MEM_LIMIT_ROWS
    if args.no_mem:
        MEM_LIMIT_ROWS = -1

    started = time.time()
    print("DCG join bench | dcg %s | python %s | %s"
          % (getattr(dcg, "__version__", "?"), platform.python_version(),
             platform.platform()))
    print("rows=%d right=%s repeat=%d case=%s ci=%s"
          % (rows, rights, repeat, args.case, args.ci))
    avail = _avail_mem_mb()
    print("avail_mem=%s MB" % ("n/a" if avail is None else "%.0f" % avail))
    contract = _contract_check()
    print("runtime contract: %s" % contract)

    e2e_ok, e2e_reason = _e2e_probe()
    print("e2e layer: %s" % ("available" if e2e_ok else "SKIPPED (%s)" % e2e_reason))
    print("")

    results = []
    for right in rights:
        left = make_left(rows, right)
        print("== running right_rows=%d (left_rows=%d) ==" % (right, rows))
        for name, group, fn in RUNTIME_CASES:
            if args.case == "simple" and group != "simple":
                continue
            if args.case == "complex" and group != "complex":
                continue
            try:
                results.append(run_runtime(name, fn, right, left, repeat))
            except _Skip as e:
                row = _new_row(name, "runtime", group, right, len(left))
                row["status"] = "skipped"
                row["note"] = str(e)
                results.append(row)
            except MemoryError:
                row = _new_row(name, "runtime", group, right, len(left))
                row["status"] = "skipped"
                row["note"] = "MemoryError：建 %d 行右表时内存不足" % right
                results.append(row)
            gc.collect()

        for name in E2E_CASES:
            group = GROUPS[name]
            if args.case == "simple" and group != "simple":
                continue
            if args.case == "complex" and group != "complex":
                continue
            try:
                results.append(run_e2e(name, right, left, repeat, e2e_ok, e2e_reason))
            except _Skip as e:
                row = _new_row(name, "e2e", group, right, len(left))
                row["status"] = "skipped"
                row["note"] = str(e)
                results.append(row)
            except MemoryError:
                row = _new_row(name, "e2e", group, right, len(left))
                row["status"] = "skipped"
                row["note"] = "MemoryError：%d 行右表内存不足" % right
                results.append(row)
            gc.collect()
        del left
        gc.collect()
        print("")

    _print_table(results)

    elapsed = time.time() - started
    payload = {
        "meta": {
            "dcg_version": getattr(dcg, "__version__", "?"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "rows": rows,
            "right": rights,
            "repeat": repeat,
            "case": args.case,
            "ci": bool(args.ci),
            "full": bool(args.full),
            "seed": SEED,
            "random": False,
            "mem_limit_rows": MEM_LIMIT_ROWS,
            "bytes_per_row": BYTES_PER_ROW,
            "fanout": FANOUT,
            "gc_disabled": True,
            "avail_mem_mb": None if avail is None else round(avail, 1),
            "contract": contract,
            "e2e": {"status": "ok" if e2e_ok else "skipped", "reason": e2e_reason},
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "elapsed_s": round(elapsed, 3),
        },
        "results": results,
    }
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("\njson -> %s" % args.json_path)
    print("elapsed %.1f s" % elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
