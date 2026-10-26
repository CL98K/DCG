"""Cython 产物实测（第三产物形态的对照基准）。

**不在正式判定协议内**（`bench_run.py` 的 13 项比值只测纯 Python 产物）：
本脚本回答一个独立问题——「`generate(schema, cython=True)` 的产物编译成 C
扩展后，比纯 Python 产物快吗？」

做法：对 13 项 case 各做一次
``generate(cython=True) -> .pyx -> cython -3 -> .c -> gcc -shared -> .pyd``，
导入后与「纯 Python 产物」「手写等价实现」三路交错测量，协议与
``bench_run.py`` 一致（每项 5 遍 trimmed mean，整组连跑 3 次取比值中位数）。

结论口径：`ratio_cy_py` < 1 表示 Cython 产物更快。因为 Cython 产物的
局部量全是 ``cdef object``（类型未特化），预期收益有限；实测见
``evidence/cython_protocol.json``。

依赖：Cython（`pip install cython`）+ 一个 C 编译器（Windows 上 MSVC `cl`
或 MinGW `gcc` 均可）。缺任一项时脚本不失败，输出 status=skipped 并说明。

用法::

    python -X utf8 evidence/bench_cython.py               # 13 项全量
    python -X utf8 evidence/bench_cython.py --only hires  # 子集
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench_fast as F          # noqa: E402
import bench_protocol as P      # noqa: E402
import bench_run as BR          # noqa: E402
import dcg                      # noqa: E402

BUILD = os.path.join(ROOT, "build", "cybench")
PYPATH = sys.base_prefix
INCLUDE = os.path.join(PYPATH, "Include")
LIBS = os.path.join(PYPATH, "libs")
CC = shutil.which("cl") or shutil.which("gcc") or shutil.which("gcc.exe")


def _sanitize(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def _cc_supports_mingw():
    return bool(CC) and os.path.basename(CC).lower().startswith("gcc")


def build_pyd(name, schema):
    """generate(cython=True) -> .pyx -> cython -> .c -> 编译成 .pyd；返回 (pyd_path, meta, src)。"""
    mod = "cyp_" + _sanitize(name)
    d = os.path.join(BUILD, _sanitize(name))
    os.makedirs(d, exist_ok=True)
    pyd = os.path.join(d, mod + ".pyd")
    src = dcg.generate(schema, cython=True)
    meta = {"pyx_lines": len(src.splitlines())}
    if os.path.exists(pyd):
        return pyd, dict(meta, cached=True), src

    pyx = os.path.join(d, mod + ".pyx")
    with open(pyx, "w", encoding="utf-8") as f:
        f.write(src)
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "cython", "-3",
                        "--module-name", mod, pyx],
                       capture_output=True, text=True, cwd=d)
    if r.returncode != 0:
        raise RuntimeError("cythonize 失败 %s: %s" % (name, (r.stderr or "")[-800:]))
    cfile = os.path.join(d, mod + ".c")
    meta["c_lines"] = sum(1 for _ in open(cfile, encoding="utf-8", errors="ignore"))

    if _cc_supports_mingw():
        cmd = [CC, "-shared", "-O2", "-DMS_WIN64", "-I", INCLUDE,
               "-o", pyd, cfile, "-L", LIBS, "-lpython313"]
    else:
        # MSVC（cl）路径：不在此脚本内实现 pyd 链接，交由调用方自行处理。
        raise RuntimeError(
            "只实现了 MinGW gcc 的链接路径（本机 cl 也未探测到）；"
            "如需 MSVC 请用 setuptools 构建扩展")
    r2 = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
    if r2.returncode != 0:
        raise RuntimeError("gcc 失败 %s: %s" % (name, (r2.stderr or "")[-1500:]))
    return pyd, dict(meta, cached=False), src


def load_pyd(pyd, name):
    mod = "cyp_" + _sanitize(name)
    if mod in sys.modules:
        del sys.modules[mod]
    spec = importlib.util.spec_from_file_location(mod, pyd)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m.run


def load_py(schema):
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<dcg>", "exec"), ns)
    return ns["run"], src


def measure3(cy_run, py_run, hand, data):
    """三路交错：预热 2 次，再 5 遍；整组连跑 REPEATS 次取比值中位数。"""
    for _ in range(2):
        cy_run(data)
        py_run(data)
        hand(data)
    passes = []
    for _ in range(BR.REPEATS):
        cs, ps, hs = [], [], []
        for _ in range(P.RUNS):
            gc.collect()
            t0 = time.perf_counter(); cy_run(data); t1 = time.perf_counter()
            cs.append(t1 - t0)
            gc.collect()
            t0 = time.perf_counter(); py_run(data); t1 = time.perf_counter()
            ps.append(t1 - t0)
            gc.collect()
            t0 = time.perf_counter(); hand(data); t1 = time.perf_counter()
            hs.append(t1 - t0)
        passes.append({
            "cy_s": P.trimmed_mean(cs),
            "py_s": P.trimmed_mean(ps),
            "hand_s": P.trimmed_mean(hs),
        })
    for p in passes:
        p["r_cy_hand"] = P.ratio(p["cy_s"], p["hand_s"])
        p["r_cy_py"] = P.ratio(p["cy_s"], p["py_s"])
    med = lambda key: sorted(p[key] for p in passes)[len(passes) // 2]
    r_cy_hand = med("r_cy_hand")
    r_cy_py = med("r_cy_py")
    picked = min(passes, key=lambda p: abs(p["r_cy_hand"] - r_cy_hand))
    return {
        "ratios_cy_hand": [round(p["r_cy_hand"], 4) for p in passes],
        "ratios_cy_py": [round(p["r_cy_py"], 4) for p in passes],
        "ratio_cy_hand": round(r_cy_hand, 4),
        "ratio_cy_py": round(r_cy_py, 4),
        "cy_s": picked["cy_s"], "py_s": picked["py_s"], "hand_s": picked["hand_s"],
    }


def _cython_version():
    try:
        import Cython
        return Cython.__version__
    except Exception as exc:
        return "unavailable: %s" % exc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--rows", type=int, default=P.ROWS)
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "cython_protocol.json"))
    args = ap.parse_args(argv)

    names = list(BR.CASES)
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        names = [n for n in names if any(w in n for w in want)]

    base = {
        "protocol": {
            "rows": args.rows, "runs": P.RUNS, "repeats": BR.REPEATS,
            "stat": "三路交错（Cython 产物 / 纯 Python 产物 / 手写等价实现）各 5 遍 trimmed_mean；"
                    "整组连跑 3 次取比值中位数",
            "cc": CC, "cython": _cython_version(), "python_include": INCLUDE,
            "note": "非判定协议：正式 13 项比值只测纯 Python 产物；本文件只回答「Cython 值不值」",
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
    }

    if not CC:
        payload = dict(base, status="skipped",
                       reason="无 C 编译器（cl / gcc 都不在 PATH），无法编译 .pyd", rows=[])
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("skipped: 无 C 编译器；已写", args.out)
        return 0

    data_rows = F.rows(args.rows)
    data_order = (F.make_orders(args.rows) if "order_pipeline" in names else None)

    results = []
    for name in names:
        sch, hand = BR.CASES[name]()
        pyd, meta, _src_cy = build_pyd(name, sch)
        cy_run = load_pyd(pyd, name)
        py_run, _src_py = load_py(sch)

        if name == "order_pipeline":
            data = data_order
            n = sum(1 for o in data["payload"]["orders"] if o["qty"] > 0)
        else:
            data = data_rows
            n = len(data_rows)

        eq = cy_run(data) == py_run(data)   # 语义等价性（必须恒 True）
        m = measure3(cy_run, py_run, hand, data)
        row = {
            "name": name, "n": n, "eq": eq,
            "cython_lines": meta["pyx_lines"], "c_lines": meta.get("c_lines"),
            "cy_us": P.per_row(m["cy_s"], n),
            "py_us": P.per_row(m["py_s"], n),
            "hand_us": P.per_row(m["hand_s"], n),
            **{k: m[k] for k in ("ratio_cy_hand", "ratio_cy_py",
                                 "ratios_cy_hand", "ratios_cy_py")},
        }
        results.append(row)
        print("%-18s eq=%-5s cy=%8.4f us py=%8.4f us hand=%8.4f us "
              "cy/hand=%5.2fx cy/py=%5.2fx (cy/hand 各轮=%s)"
              % (name, eq, row["cy_us"], row["py_us"], row["hand_us"],
                 row["ratio_cy_hand"], row["ratio_cy_py"],
                 " ".join("%.2f" % x for x in row["ratios_cy_hand"])))
        sys.stdout.flush()

    payload = dict(base, status="ok", rows=results)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("wrote", args.out)
    bad = [r["name"] for r in results if not r["eq"]]
    print("ALL_EQ:", not bad, "BAD:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
