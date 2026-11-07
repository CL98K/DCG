"""沙箱环境探测 + 环境性跳过 —— 只兜住**环境限制**造成的失败，不改被测行为。

## 为什么需要这个文件

DSH 的文件沙箱（Windows，workspace-write）会剥掉 `tempfile.mkdtemp` 新建
子目录的目录列举权限。实测后果（pytest 9.1.1）：

| 现象 | 根因 |
| --- | --- |
| `tmp_path` 用例 ERROR / PermissionError WinError 5 | 被测代码/夹具要在该目录里 `rglob`/`scandir` |
| `TemporaryDirectory.__exit__` 抛 PermissionError → 用例 FAILED（断言早已通过） | teardown `shutil.rmtree` → `os.chmod` 被拒 |
| 汇总行消失 | `sessionfinish` 里 `_pytest.tmpdir.cleanup_dead_symlinks` 扫描 basetemp 被拒 |

这三类都**不是代码缺陷**。所以这里做两件事，且都只在探测到沙箱限制时生效：

1. **`pytest_collection_modifyitems`**：给"必须有可列举的临时目录"的用例打上
   `skip`，理由写明是环境限制。判定是**运行时探测**，不是猜路径。
2. **teardown 容错**：`TemporaryDirectory.cleanup` 清理失败时吞掉
   `PermissionError`/`OSError`（断言已经跑完了，清理失败不该记成用例失败）。

正常机器上（临时目录可读可写可列）探测结果为"不受限"，本文件**完全不介入**：
既不 skip 任何用例，也不改任何清理行为。

## 环境变量

* `DCG_SANDBOX_COMPAT=1` —— 强制开启补偿（调试用）
* `DCG_SANDBOX_COMPAT=0` —— 强制关闭（想在沙箱里看到真实的红）

## `build_dir` 夹具：为什么后来又不需要跳过这 5 条了

原先有 5 条 build 类用例在 `tmp_path` 里生成产物后用 `rglob` 搜回来，沙箱剥掉
列举权限就只能跳过，于是 skipped 数随环境漂成 8/9/13/14，`验收报告.md` 被迫
写四套口径。

`dcg/codegen.py::build` 的产物路径是**确定**的：`<out_dir>/gen/<name>.py`、
`<out_dir>/gen/<name>.manifest.json`（+ 可选 `<name>.trace.py`），函数体只
`os.makedirs` + `open(..., "w")`，自己不列举任何目录。所以这 5 条改成用
`build_dir`（项目内 `_tmp/build/<用例名>`，`.gitignore` 已忽略 `_tmp/`）并
**按已知路径直接访问**产物，不再需要 `rglob`/`listdir`，对"临时目录能不能
列举"完全免疫。`_NEEDS_LISTABLE_TMPDIR` 因此清空，跳过机制原样保留。
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_MARKER = "dcg_sandbox_unavailable"
_FORCED = os.environ.get("DCG_SANDBOX_COMPAT", "").strip().lower()

#: 这些用例要求"在临时目录里建文件、再列举/搜索回来"。沙箱剥掉列举权限后，
#: 它们**必然**失败于 PermissionError，与实现无关。
#:
#: **目前为空**：原来那 5 条已改用下面的 `build_dir` 夹具 + 按已知产物路径
#: 访问（`<out_dir>/gen/<name>.py` 由 `dcg/codegen.py::build` 确定），
#: 不再依赖目录列举，所以 skipped 数不再随环境漂移。
#: 集合与 `pytest_collection_modifyitems` 的机制保留，将来真有环境性跳过时
#: 往这里加 nodeid 即可。
_NEEDS_LISTABLE_TMPDIR: tuple = ()


#: `build_dir` 的落脚点：项目内目录，路径可预测、不需要列举。
#: 不用 `tmp_path` 是因为临时目录正是可能被沙箱剥掉列举权限的那一类。
_BUILD_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_tmp", "build")


@pytest.fixture
def build_dir(request):
    """给 build 类用例用的显式目录（替代 `tmp_path`）。

    每次跑都从一个干净目录开始，结束后尽力删除；删除失败不影响用例结果
    （沙箱里 `rmtree` 可能被拒，被删的只是本用例自己的产物）。
    """
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in request.node.name)
    path = os.path.join(_BUILD_ROOT, safe)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _probe_tmpdir() -> str | None:
    """运行时探测：新建一个临时子目录，试列举。

    返回 None 表示"可正常使用"；返回字符串表示受限原因。
    这是**实测**而不是猜环境变量 —— 判定句柄随机器不同自动正确。
    """
    if _FORCED in ("0", "false", "no", "off"):
        return None  # 用户显式要求看到真实结果
    try:
        td = tempfile.mkdtemp(prefix="dcg_probe_")
    except OSError as e:
        return f"无法创建临时目录：{type(e).__name__}: {e}"
    try:
        try:
            os.listdir(td)
        except OSError as e:
            return (f"临时目录不可列举（文件沙箱剥离了目录列举权限）："
                    f"{type(e).__name__}: {e}")
        try:
            p = os.path.join(td, "w.txt")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
            with open(p, encoding="utf-8") as fh:
                fh.read()
        except OSError as e:
            return (f"临时目录不可写/不可读（文件沙箱限制）："
                    f"{type(e).__name__}: {e}")
        return None
    finally:
        import shutil

        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:  # pragma: no cover
            pass


_PROBE_REASON: str | None = None
_PROBED = False


def _reason() -> str | None:
    global _PROBE_REASON, _PROBED
    if not _PROBED:
        _PROBE_REASON = _probe_tmpdir()
        _PROBED = True
    return _PROBE_REASON


def pytest_configure(config):
    if _FORCED in ("1", "true", "yes", "on") and _reason() is None:
        config.dcg_sandbox_forced = True
    # 清理容错：只有探测到受限时才生效，正常机器上一个字节都不改
    if _reason() is None:
        return
    try:
        import _pytest.pathlib as _pl
        import _pytest.tmpdir as _td

        def _noop_cleanup_dead_symlinks(*args, **kwargs):
            return None

        _pl.cleanup_dead_symlinks = _noop_cleanup_dead_symlinks
        _td.cleanup_dead_symlinks = _noop_cleanup_dead_symlinks
    except Exception:  # pragma: no cover - pytest 版本差异
        pass

    _orig_cleanup = tempfile.TemporaryDirectory.cleanup

    def _tolerant_cleanup(self):
        try:
            _orig_cleanup(self)
        except (PermissionError, OSError):
            self._finalizer.detach()

    tempfile.TemporaryDirectory.cleanup = _tolerant_cleanup


def pytest_collection_modifyitems(config, items):
    """给"必须有可列举临时目录"的用例打环境性 skip。

    `_NEEDS_LISTABLE_TMPDIR` 目前为空（那 5 条已改为 `build_dir` + 已知路径），
    所以正常情况这里直接返回；机制保留，将来加 nodeid 即生效。
    """
    want = set(_NEEDS_LISTABLE_TMPDIR)
    if not want:
        return
    reason = _reason()
    if reason is None:
        return

    for item in items:
        if item.nodeid in want:
            item.add_marker(pytest.mark.skip(
                reason=f"环境性跳过（不是代码缺陷）：{reason}"
                       "  —— 本用例需要在临时目录里建文件后列举/搜索回来；"
                       "正常机器上会照常执行。"))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """始终输出一行机器可读统计。

    pytest 的默认汇总行在 sessionfinish 崩溃时不打印；这一行从 stats 直接数，
    不依赖那条被吞掉的输出，便于把结果写进 evidence/。
    """
    stats = terminalreporter.stats

    def n(key):
        return len(stats.get(key, ()))

    keys = ("passed", "failed", "error", "skipped", "xfailed", "xpassed", "deselected")
    parts = [f"{n(k)} {k}" for k in keys if n(k)]
    total = sum(n(k) for k in keys)
    terminalreporter.write_sep("-", "COUNT_SUMMARY")
    sandbox = _reason()
    terminalreporter.write_line(
        "COUNT_SUMMARY: " + " / ".join(parts) + f"  (total {total})"
        + ("" if sandbox is None else "  [沙箱环境：已跳过环境性用例]"))
    if sandbox is not None:
        terminalreporter.write_line("SANDBOX_REASON: " + sandbox)
