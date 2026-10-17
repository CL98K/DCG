"""静默窗口终值采集骨架（Lead m01316 ⑥ 流程）。

窗口由 **Lead 判定并开窗**，本脚本不自己挑时机。用途：在 Lead 发「停手」后的**同一分钟**内，
由我和 examples-dev 各自跑一次终值采集，输出里必须自带：

  1. ISO 时间戳（含时区）
  2. 关键文件的 sha1（codegen/runtime/schema/__init__）+ 字节数
  3. **python 进程表**（确认机器上没有其他并发负载；若有则 WARN 并如实记录）
  4. Lead 的 `evidence/accept.py` 原文输出（我不改该文件，只运行并冻结）
  5. `bench_row.py`（比值判定）+ `bench_codegen.py`（A/B 双基准、九条规则、字符数）
  6. `pytest` 两个套件的结果

用法：
    python -X utf8 bench/quiet_window.py --dry-run          # 只看环境快照，不跑门
    python -X utf8 bench/quiet_window.py --window           # Lead 已开窗，正式采集

不传 `--window` 时会拒绝采集并提示等 Lead 信号（防止在 codegen 还在变时产出假终值）。
输出：`evidence/quiet_window_<YYYYmmdd-HHMMSS>.txt`（已存在则拒绝覆盖）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(ROOT, "evidence")
PINNED = ("dcg/codegen.py", "dcg/runtime.py", "dcg/schema.py", "dcg/__init__.py",
          "dcg/build.py", "dcg/jx/compiler.py", "evidence/accept.py")


def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha1_line(rel):
    p = os.path.join(ROOT, *rel.split("/"))
    try:
        with open(p, "rb") as fh:
            b = fh.read()
        return f"{rel:26s} sha1={hashlib.sha1(b).hexdigest().upper()} bytes={len(b)}"
    except OSError as exc:
        return f"{rel:26s} MISSING ({exc.__class__.__name__})"


def process_table():
    """tasklist on Windows, ps elsewhere; returns (text, other_python_count)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                                 capture_output=True, text=True,
                                 errors="replace").stdout
        else:
            out = subprocess.run(["ps", "-eo", "pid,comm,etime"],
                                 capture_output=True, text=True,
                                 errors="replace").stdout
    except OSError as exc:
        return f"<process table unavailable: {exc}>", -1
    lines = [ln for ln in out.splitlines() if ln.strip()]
    others = sum(1 for ln in lines if "python" in ln.lower()) - 1  # minus this process
    return "\n".join(lines), others


def run_step(title, cmd, lines, timeout=1800):
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"### {title}")
    lines.append(f"$ {' '.join(cmd)}")
    lines.append(f"started {stamp()}")
    lines.append("=" * 78)
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        lines.append(p.stdout or "")
        if p.stderr:
            lines.append("--- stderr ---")
            lines.append(p.stderr)
        lines.append(f"[exit={p.returncode}]  finished {stamp()}")
        return p.returncode
    except subprocess.TimeoutExpired:
        lines.append(f"[TIMEOUT after {timeout}s]")
        return 124


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true",
                    help="确认 Lead 已开窗（不传则拒绝采集终值）")
    ap.add_argument("--dry-run", action="store_true", help="只打环境快照")
    ap.add_argument("--n", default="200000")
    ap.add_argument("--reps", default="3")
    a = ap.parse_args()

    py = sys.executable
    lines = [
        "=" * 78,
        "DCG v2 静默窗口终值采集（verifier-dev / task-3）",
        f"时间戳: {stamp()}",
        f"cwd: {ROOT}",
        f"python: {py}  {sys.version.split()[0]}",
        "=" * 78,
        "",
        "--- 关键文件 sha1（数字必须与这些 sha1 绑定） ---",
    ]
    for rel in PINNED:
        lines.append(sha1_line(rel))

    ptxt, others = process_table()
    lines += ["", f"--- python 进程表（并发负载；others={others}） ---", ptxt]
    if others > 0:
        lines.append(f"WARN: 检测到 {others} 个其他 python 进程；"
                     "静默窗口要求机器负载归零，请 Lead 确认后再采信本文件。")

    if a.dry_run and not a.window:
        print("\n".join(lines))
        print("\n[dry-run] 未采集任何 v2 数字。开窗后加 --window 重跑。")
        return 0

    if not a.window:
        print("\n".join(lines))
        print("\nREFUSED: 未确认 Lead 开窗（缺 --window）。"
              "codegen 在变动期产出的字符数/性能数字不可引用。")
        return 3

    # readiness gate: never write v2 numbers if the engine is not callable
    probe = ("import dcg, json;"
             " s={'name':'probe','output':{'shape':'list','single':False},"
             "    'out':{'type':'object','fields':{'v':{'type':'int64','value':'$.0'}}}};"
             " src=dcg.generate(s); compile(src,'<p>','exec');"
             " print('READY', len(src))")
    lines.append("")
    lines.append(f"--- readiness probe @ {stamp()} ---")
    r = subprocess.run([py, "-X", "utf8", "-c", probe], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines.append((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        lines.append("v2 NOT READY -> STOPPING, no v2 number written")
        out = os.path.join(EVIDENCE, f"quiet_window_{datetime.now():%Y%m%d-%H%M%S}.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print(f"\n".join(lines[-12:]))
        print(f"\nwrote {out}")
        return 4

    run_step("Lead 的 evidence/accept.py（原文冻结，回报 Lead）",
             [py, "-X", "utf8", os.path.join("evidence", "accept.py")], lines)
    run_step("pytest tests/test_e2e_flight.py",
             [py, "-X", "utf8", "-m", "pytest", "tests/test_e2e_flight.py",
              "-q", "--no-header", "-rA"], lines)
    run_step("pytest tests/test_sentinel_integrity.py",
             [py, "-X", "utf8", "-m", "pytest", "tests/test_sentinel_integrity.py",
              "-q", "--no-header", "-rA"], lines)
    run_step("bench/bench_codegen.py（A/B 双基准 + 九条规则）",
             [py, "-X", "utf8", os.path.join("bench", "bench_codegen.py"),
              "--reps", a.reps], lines)
    run_step("bench/bench_row.py（三方比值判定，终值）",
             [py, "-X", "utf8", os.path.join("bench", "bench_row.py"),
              "--reps", a.reps, "--n", a.n], lines)
    lines += ["", f"finished {stamp()}"]

    out = os.path.join(EVIDENCE, f"quiet_window_{datetime.now():%Y%m%d-%H%M%S}.txt")
    if os.path.exists(out):
        print(f"REFUSED: {out} already exists")
        return 5
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines[-6:]))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
