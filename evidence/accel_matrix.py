"""在**任意一台机器**上复现「哪些 Python 版本能加载同一个 `dcg/_accel` 二进制」。

解决什么问题
------------
`dcg/_accel` 是可选 Rust 加速后端，`Cargo.toml` 用 `abi3-py38`，承诺
「**一个**二进制覆盖 CPython 3.8–3.15」。这句话在 CI 上只能证明一次：
`.github/workflows/ci.yml` 的 `accel` job 在四个 runner 上各构建一遍，
再从 3.8 切到 3.15 各加载一次。但 CI 覆盖不到下面这些情况：

* 换了机器 / 换了 Python 安装（比如本机装了 3.9，CI 只测了 3.8–3.15 的矩阵与两端的 abi3 加载）；
* 本机重新编译过扩展，想知道「现在这个二进制到底还能不能被老版本加载」；
* CI 红在某个平台，本地想先用一条命令看清是导入坏了还是门控坏了。

本脚本就是那条命令：它枚举本机能找到的每个解释器，逐个尝试
「导入 `dcg._accel._accel`」与「跑门控等价性测试」，把结果同时打成
人类可读表和 JSON，并给出一个可以进 CI / 进验收的退出码。

怎么用
------
    python evidence/accel_matrix.py
    python evidence/accel_matrix.py -o _tmp/matrix_win.json

Windows 控制台默认是 GBK，脚本里的中文表头会显示成乱码（数据本身是对的），
加 `-X utf8` 即可：`python -X utf8 evidence/accel_matrix.py`。

* 默认输出到 `_tmp/accel_matrix.json`（`_tmp/` 已被 `.gitignore` 忽略，
  它是工具产物而不是证据）。
* 退出码 0 = 找到的解释器全部通过（或全部 SKIP）；
  退出码 1 = 至少一个「找到了、产物也在、但导入失败或门控失败」。
`SKIP` 的语义
-------------
SKIP **不是失败**，它有两种来源，含义都是「这个版本在本机无法验证」：

1. 找不到这个解释器：PATH 上没有 `python3.9`、Windows 的 `py -3.9` 也起不来。
   本机只装了 3.13 时，3.8–3.12 与 3.14–3.15 全部是这种 SKIP —— 这是正常的，不是缺陷。
2. 找到了但没装 pytest：导入冒烟仍然会做（那是最关键的一步），只是门控
   等价性测试跑不了，于是门控记 SKIP 而不是 FAIL。

判定口径
--------
`import` 一栏的三个值区分得很细，因为「没构建」和「构建坏了」必须分开：

* ``ok`` —— 产物存在，导入成功，导出集合恰好是期望的 10 个名字；
* ``missing`` —— `dcg/_accel` 下根本没有任何 `.pyd` / `.so`（从未构建）。
                 这时不算失败：没东西可加载，与 SKIP 等价；
* ``error`` —— 产物在，但这个解释器加载失败（ABI 不匹配、导出集合漂移、
                 动态库依赖缺失……）。这是真失败，退出码 1。

本文件只用标准库，且必须保持 **Python 3.8 语法**：`evidence/ci_syntax_floor.py`
会用 3.8 的语法规则解析它。所以这里不用 `list[int]`、`match`、
`str.removeprefix`，也不用内置泛型下标（`from __future__ import annotations`
救不了运行期求值）。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

#: 仓库根目录（本文件在 `<root>/evidence/` 下）。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 构建产物所在目录。
ACCEL_DIR = os.path.join(ROOT, "dcg", "_accel")

#: `src/lib.rs` + `src/newfn.rs` 合起来的导出面，Python 侧看到的就是这 16 个。
#: 这是导出面的**唯一来源**：`ci.yml` 的 `assert artifact and exports` 用
#: `ast.literal_eval` 读这里，不另抄一份名单。
#:
#: `mt`（`$match`）故意不在此列：`o_mt` 的实现在 `newfn.rs` 里保留，但
#: `register()` 不注册它 —— 实测 0.56–0.68×（7 档全亏，结构性原因：内部仍走
#: CPython `finditer` + 每命中 3 次 FFI 跨调用）。注册即负优化，故不出现在
#: 导出面。别把它当「漏注册」补回来。
EXPECTED_EXPORTS = [
    "av", "cl", "ds", "du", "eu", "fb", "fgi", "fms", "fnm", "mn", "mx", "pgi",
    "tms", "ud", "ue", "zp",
]

#: 需要覆盖的版本区间，与 `abi3-py38` 和 CI 矩阵保持一致。
MINORS = ("3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.15")

#: 门控等价性测试文件（相对仓库根）。
GATE_TEST = os.path.join("tests", "test_accel_gate.py")

#: 单个子进程的上限：pytest 门控在本机约 2 秒，120 秒足够，且不会挂死整轮。
GATE_TIMEOUT = 120
PROBE_TIMEOUT = 60

#: 子进程里跑的探测代码：只要解释器能起来，就把真实可执行文件与版本吐成 JSON。
PROBE_CODE = (
    "import json, platform, sys;"
    "print(json.dumps({'exe': sys.executable,"
    " 'version': platform.python_version()}))"
)

#: `EXPECTED_EXPORTS` 的 Python 字面量形式，供下面的子进程字符串注入 —— 导出面只有
#: 一个来源（本文件顶部那个常量），改 Rust 导出时不用再同步第二份手抄名单。
_EXPORTS_LITERAL = "[" + ", ".join('"%s"' % n for n in EXPECTED_EXPORTS) + "]"

#: 子进程里跑的导入冒烟。单独一段字符串而不是内联，避免与父进程的引号互相干扰；
#: 传参用 `sys.argv[1]`，不靠 shell 展开，Windows 与 POSIX 行为一致。
#: `__WANT__` 用 `str.replace` 注入而不是 `%` 格式化：本段里有 `%s` 和 `%` 运算符，
#: 走格式化会先把它们当占位符吃掉。
IMPORT_CODE = r'''
import json, os, sys

root = sys.argv[1]
accel_dir = os.path.join(root, "dcg", "_accel")
want = __WANT__
out = {"status": "error", "exports": [], "detail": "", "artifacts": []}
try:
    out["artifacts"] = sorted(
        n for n in os.listdir(accel_dir)
        if n.endswith(".pyd") or n.endswith(".so"))
except OSError as exc:
    out["detail"] = "无法列出 %s: %s" % (accel_dir, exc)
try:
    sys.path.insert(0, root)
    import dcg._accel._accel as mod
except Exception as exc:
    out["status"] = "missing" if not out["artifacts"] else "error"
    if not out["detail"]:
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
else:
    out["exports"] = sorted(n for n in dir(mod) if not n.startswith("_"))
    if out["exports"] == want:
        out["status"] = "ok"
    else:
        out["status"] = "error"
        out["detail"] = "导出集合不符：期望 %s，实际 %s" % (want, out["exports"])
print(json.dumps(out, ensure_ascii=False))
'''.replace("__WANT__", _EXPORTS_LITERAL)


def _decode(raw):
    """把子进程的原始字节解成文本：不猜编码，坏字节替换掉而不是抛异常。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw


def _child_env():
    """子进程环境：强制 UTF-8，否则 Windows 上按 GBK 写 stdout 会截断中文摘要。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _candidate_commands():
    """返回候选解释器命令（每个都是 argv 列表），已按「命令名」去重。

    三条来源：当前解释器、PATH 上的版本化名字、Windows 的 `py` 启动器。
    `py -3.8` 这类注册项可能与实际安装不一致（注册表里有、文件已被删），
    所以一律先探测再采信，探测失败就当作「找不到」。
    """
    commands = []
    seen = set()

    def add(cmd):
        key = tuple(cmd)
        if key not in seen:
            seen.add(key)
            commands.append(cmd)

    if sys.executable:
        add([sys.executable])

    # PATH 上无版本号的 `python` / `python3` 也值得试：用 `py -3.13` 跑本脚本时，
    # PATH 上的 `python` 完全可能是另一个版本。
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            add([found])

    for minor in MINORS:
        compact = minor.replace(".", "")
        for name in ("python" + minor, "python" + compact):
            found = shutil.which(name)
            if found:
                add([found])
            if sys.platform == "win32":
                # 有的安装目录里只有 `python3.8.exe` 而没有 `python3.8`，
                # `shutil.which` 在 Windows 上会自己补 .exe，这里显式再来一次。
                found = shutil.which(name + ".exe")
                if found:
                    add([found])

    if sys.platform == "win32":
        launcher = shutil.which("py")
        if launcher:
            for minor in MINORS:
                add([launcher, "-" + minor])

    return commands


def _probe(cmd, env):
    """探测一个候选命令：成功返回 {exe, version}，起不来返回 None。"""
    try:
        done = subprocess.run(
            cmd + ["-c", PROBE_CODE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ROOT, env=env, timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    lines = [x for x in _decode(done.stdout).splitlines() if x.strip()]
    if not lines:
        return None
    try:
        info = json.loads(lines[-1])
    except ValueError:
        return None
    if not isinstance(info, dict) or not info.get("exe"):
        return None
    return info


def _key(path):
    """去重键：Windows 上路径大小写不敏感，且软链接要解析到真实文件。"""
    try:
        real = os.path.realpath(path)
    except OSError:
        real = path
    return os.path.normcase(real)


def _run_import(exe, env):
    """在一个解释器里做导入冒烟，返回 (status, exports, detail)。"""
    try:
        done = subprocess.run(
            [exe, "-c", IMPORT_CODE, ROOT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ROOT, env=env, timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", [], "子进程无法启动: %s" % exc
    text = _decode(done.stdout)
    lines = [x for x in text.splitlines() if x.strip()]
    if not lines:
        return "error", [], "没有输出（stderr: %s）" % _decode(done.stderr).strip()[:200]
    try:
        info = json.loads(lines[-1])
    except ValueError:
        return "error", [], "输出不是 JSON: %s" % lines[-1][:200]
    status = info.get("status", "error")
    exports = info.get("exports") or []
    detail = info.get("detail") or ""
    if status == "ok":
        detail = "产物 %s" % ", ".join(info.get("artifacts") or [])
    return status, exports, detail


#: 从 pytest 输出里挑「摘要行」。按可信度依次尝试三种形态：
#:
#: 1. `COUNT_SUMMARY: 6 passed (total 6)` —— 本仓库 `tests/conftest.py` 的
#: `pytest_terminal_summary` 自己打的机器可读行，**最可信**：它直接数 stats，
#: 不依赖 pytest 的默认汇总行是否被打印。
#: 2. `6 passed in 0.31s` —— pytest 自己的汇总行（`-q` 且未被 `-qq` 压掉时才有）。
#: 3. 最后一行 —— 兜底。
#:
#: 为什么需要第 1 条：`pyproject.toml` 里已有 `addopts = "-q"`，调用方再传一个
#: `-q` 就变成 `-qq`，pytest 的汇总行整条消失，只看第 2 条会退化成「最后一行」，
#: 表里出现的是 `SANDBOX_REASON: ...` 这种与被测行为无关的行。
_SUMMARY_RE = re.compile(
    r"^(?:\d+\s+(?:passed|failed|error|errors|skipped|deselected)|no tests ran)")
_COUNT_PREFIX = "COUNT_SUMMARY:"


def _pick_summary(lines):
    """按「COUNT_SUMMARY > pytest 汇总行 > 末行」的优先级挑一行摘要。"""
    for line in reversed(lines):
        if line.startswith(_COUNT_PREFIX):
            return line[len(_COUNT_PREFIX):].strip()
    for line in reversed(lines):
        if _SUMMARY_RE.search(line):
            return line
    return lines[-1] if lines else ""


def _run_gate(exe, env):
    """跑门控等价性测试，返回 (status, detail)。"""
    # 传 `-q` 与任务约定一致；但 `pyproject.toml` 的 `addopts` 已有 `-q`，
    # 两个叠起来是 `-qq`，pytest 自己的汇总行会被压掉。所以摘要靠下面的
    # `_pick_summary`：它优先取 conftest 直接打的 `COUNT_SUMMARY:` 行，
    # 那行不受 `-qq` 影响。
    try:
        done = subprocess.run(
            [exe, "-m", "pytest", GATE_TEST, "-q"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ROOT, env=env, timeout=GATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "fail", "超时（>%d 秒）" % GATE_TIMEOUT
    except (OSError, subprocess.SubprocessError) as exc:
        return "skip", "子进程无法启动: %s" % exc
    text = _decode(done.stdout) + "\n" + _decode(done.stderr)
    if "No module named pytest" in text or "No module named 'pytest'" in text:
        return "skip", "该解释器没装 pytest"
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    summary = _pick_summary(lines)
    if done.returncode == 0:
        return "pass", summary or "rc=0"
    return "fail", "%s（rc=%d）" % (summary or "无摘要", done.returncode)


def _display_width(text):
    """终端显示宽度：CJK 全角字符占两列，直接用 len 会把表头挤歪。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text, width):
    """按显示宽度左对齐补空格。"""
    text = str(text)
    return text + " " * max(0, width - _display_width(text))


def _print_table(rows):
    """打印人类可读表。rows 是已排好序的元组列表。"""
    headers = ("解释器", "版本", "导入", "门控", "说明")
    widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(str(cell)))
    line = "  ".join(_pad(h, widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(_pad(c, widths[i]) for i, c in enumerate(row)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="枚举本机可用的 Python 解释器，逐个验证能否加载同一个 dcg/_accel 二进制。")
    parser.add_argument(
        "-o", "--output", default=os.path.join("_tmp", "accel_matrix.json"),
        help="JSON 输出路径（默认 _tmp/accel_matrix.json，相对仓库根）")
    args = parser.parse_args(argv)

    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(ROOT, output)

    env = _child_env()
    results = []
    skipped = []
    covered = set()
    seen_exe = set()

    for cmd in _candidate_commands():
        info = _probe(cmd, env)
        if info is None:
            # 候选命令起不来：可能只是 `py -3.8` 这类注册表残留，记 SKIP 不记失败。
            skipped.append({"candidate": " ".join(cmd), "reason": "无法启动或版本探测失败"})
            continue
        exe = info["exe"]
        key = _key(exe)
        if key in seen_exe:
            continue
        seen_exe.add(key)

        version = info.get("version", "?")
        parts = version.split(".")
        minor = ".".join(parts[:2]) if len(parts) >= 2 else version
        covered.add(minor)

        status, exports, detail = _run_import(exe, env)
        if status == "ok":
            gate, gate_detail = _run_gate(exe, env)
            if gate_detail:
                detail = detail + "；门控 " + gate_detail
        elif status == "missing":
            gate, gate_detail = "skip", "无产物可加载"
            detail = "dcg/_accel 下没有 .pyd/.so"
        else:
            gate, gate_detail = "skip", "导入未成功"
            if gate_detail:
                detail = detail + "；门控 " + gate_detail

        results.append({
            "exe": exe,
            "version": version,
            "import": status,
            "exports": exports,
            "gate": gate,
            "detail": detail,
            "command": " ".join(cmd),
        })

    # 一个解释器都没找到是不可能的（至少有 sys.executable），但版本覆盖可能不全。
    missing_minors = [m for m in MINORS if m not in covered]

    print("accel matrix: 仓库 %s" % ROOT)
    print("当前解释器 %s，平台 %s" % (sys.version.split()[0], sys.platform))
    print("期望导出 %s" % ", ".join(EXPECTED_EXPORTS))
    print("")

    rows = []
    for item in results:
        rows.append((os.path.basename(item["exe"]), item["version"],
                     item["import"], item["gate"], item["detail"]))
    for minor in missing_minors:
        rows.append(("python" + minor, minor, "SKIP", "SKIP", "本机找不到该版本"))
    _print_table(rows)
    print("")

    for item in skipped:
        print("SKIP  候选 `%s`：%s" % (item["candidate"], item["reason"]))
    if missing_minors:
        print("SKIP  未覆盖版本：%s（本机没装，不算失败）"
              % ", ".join(missing_minors))

    failures = [r for r in results if r["import"] == "error" or r["gate"] == "fail"]
    ok_count = len([r for r in results if r["import"] == "ok"])
    print("")
    print("小结：找到 %d 个解释器，导入成功 %d 个，失败 %d 个，未覆盖版本 %d 个"
          % (len(results), ok_count, len(failures), len(missing_minors)))

    payload = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "expected_exports": EXPECTED_EXPORTS,
        "results": results,
        "skipped_candidates": skipped,
        "missing_minors": missing_minors,
    }
    parent = os.path.dirname(output)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("JSON 已写入 %s" % output)

    if failures:
        print("RESULT: FAIL —— %d 个解释器找到了产物却加载/门控失败："
              % len(failures))
        for item in failures:
            print("  %s (%s): %s" % (item["exe"], item["version"], item["detail"]))
        return 1

    print("RESULT: OK —— 没有「找到但失败」的解释器")
    return 0


if __name__ == "__main__":
    sys.exit(main())
