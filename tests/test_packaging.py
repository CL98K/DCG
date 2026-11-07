"""打包契约：`pyproject.toml` 声明的包集合必须等于磁盘上真实的 `dcg` 子包集合。

背景：`[tool.setuptools] packages` 曾是一行硬编码白名单
（`packages = ["dcg", "dcg.jx", "dcg._accel"]`）。新增子包时漏改这一行**不会
有任何报错** —— `pip wheel` 照常成功，只是产物里静默少一个目录，装包的用户
`ImportError` 才会发现。这类缺陷在「装源码目录」的开发机上永远测不出来。

现在改成 `[tool.setuptools.packages.find]` + `include = ["dcg*"]`，白名单风险
从「漏写一行」变成「include/exclude 写错」。本文件守住两件事：

1. `find` 的匹配结果（用与 setuptools 相同的规则自己算，不引入新依赖）必须
   恰好等于磁盘上所有含 `__init__.py` 的 `dcg` 子包 —— 不多不少；
2. `exclude` 必须挡住 `build*`、`dist*`、`tests*` 这些不该进 wheel 的目录。
   注意 `find` 本身只递归进含 `__init__.py` 的目录，`build/lib/dcg`（Cython
   试验产物）**不会**被 `dcg*` 拾取 —— `exclude` 是防御性的，不是必需的；
   测试断言它确实被写下来了，避免有人「简化」成 `include = ["*"]`。
"""

from __future__ import annotations

import fnmatch
import io
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYPROJECT = os.path.join(_ROOT, "pyproject.toml")
_MANIFEST = os.path.join(_ROOT, "MANIFEST.in")

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.8 / 3.9 / 3.10
    tomllib = None


def _read_pyproject() -> dict:
    """读 `pyproject.toml` 的 `[tool.setuptools]` 段。

    3.11+ 用 `tomllib`；更老的解释器（本项目 `requires-python = ">=3.8"`，CI 里
    还有 3.8 的 abi3 job）没有 `tomllib`，退化成 `re` 只取本文件需要的两个键。
    """
    with io.open(_PYPROJECT, encoding="utf-8") as fh:
        text = fh.read()
    if tomllib is not None:
        return tomllib.loads(text)["tool"]["setuptools"]

    # 退化路径：只认 `include = [...]` / `exclude = [...]` 两种单行数组写法。
    out = {}
    for key in ("include", "exclude"):
        m = re.search(r"^\s*%s\s*=\s*\[(.*?)\]" % key, text, re.M | re.S)
        out[key] = re.findall(r'"([^"]*)"', m.group(1)) if m else []
    return {"packages": {"find": out}}


def _on_disk() -> list:
    """磁盘上真实的包名：`dcg` 下所有含 `__init__.py` 的目录（含 `dcg` 自身）。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(_ROOT, "dcg")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        if "__init__.py" not in filenames:
            continue
        rel = os.path.relpath(dirpath, _ROOT).replace(os.sep, ".")
        found.append(rel)
    return sorted(found)


def _find_packages(include: list, exclude: list) -> list:
    """按 setuptools 的 `find_packages` 语义（前缀匹配 + 排除）算出打包结果。

    自己实现而不 `from setuptools import find_packages`：测试不该依赖构建后端
    在运行环境里可用，且这样连 `build/lib/dcg` 这种「磁盘上有、但不是源码包」
    的情形也能被如实算出来。
    """
    names = set()
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        rel = os.path.relpath(dirpath, _ROOT).replace(os.sep, ".")
        depth = 0 if rel == "." else rel.count(".") + 1
        if depth > 8:  # 防御：不往很深的目录里钻
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        if rel == "." or "__init__.py" not in filenames:
            continue
        names.add(rel)
    picked = [
        n for n in names
        if any(n == p.rstrip("*") or n.startswith(p.rstrip("*")) for p in include)
    ]
    kept = [
        n for n in picked
        if not any(n == e.rstrip("*") or n.startswith(e.rstrip("*")) for e in exclude)
    ]
    return sorted(kept)


def test_packages_find_matches_disk():
    """`include`/`exclude` 算出来的包集合必须恰好等于磁盘上的 `dcg` 子包集合。"""
    cfg = _read_pyproject()["packages"]
    assert "find" in cfg, (
        "pyproject.toml 里没有 [tool.setuptools.packages.find]；"
        "硬编码 packages 白名单会静默漏包（见本文件模块 docstring）"
    )
    include = cfg["find"].get("include") or []
    exclude = cfg["find"].get("exclude") or []
    assert include, "[tool.setuptools.packages.find] 缺 include（`find` 会扫全仓库）"
    assert exclude, (
        "[tool.setuptools.packages.find] 缺 exclude；防御性排除被删掉了"
        "（`build*` / `dist*` / `tests*` 不该进 wheel）"
    )

    got = _find_packages(include, exclude)
    want = _on_disk()
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    assert not missing and not extra, (
        "打包结果与磁盘上的 dcg 子包不一致：\n  漏包（wheel 里会缺）: %s\n"
        "  多包（不该进 wheel）: %s\n  当前 include=%r exclude=%r"
        % (missing, extra, include, exclude)
    )


def test_accel_backend_is_packaged():
    """`dcg._accel` 必须真的在打包结果里 —— 否则双活退化成单活且不报错。"""
    cfg = _read_pyproject()["packages"]
    assert "find" in cfg, "pyproject.toml 里没有 [tool.setuptools.packages.find]"
    got = _find_packages(cfg["find"].get("include") or [],
                         cfg["find"].get("exclude") or [])
    assert "dcg._accel" in got, (
        "dcg._accel 没被打包：`dcg/runtime.py` 会静默退回纯 Python，"
        "而 `dcg/_accel/*.so` 与 `*.pyd` 仍随 wheel 发布，白占体积"
    )


def test_package_data_still_ships_binaries():
    """`package-data` 必须继续把预编译扩展带进 wheel。"""
    cfg = _read_pyproject()
    data = cfg.get("package-data") or {}
    assert "dcg._accel" in data, "[tool.setuptools.package-data] 缺 dcg._accel 条目"
    pats = data["dcg._accel"]
    assert any(p.endswith(".so") for p in pats) and any(p.endswith(".pyd") for p in pats), (
        "package-data 的 dcg._accel 模式必须同时覆盖 *.so 与 *.pyd，实际是 %r" % (pats,)
    )


# --------------------------------------------------------------- sdist 清单


def _manifest_rules():
    """把 `MANIFEST.in` 解析成 `[(命令, [参数, ...]), ...]`，**只扫非注释行**。

    注释必须排除：本文件与 `MANIFEST.in` 的注释里都写着 `*.py[cod]` 这几个字
    （用来警告它会把 `.pyd` 一起排掉）。若做全文 `in` 匹配，下面的反向断言会被
    自己的注释骗过 —— 所以先按行切词、跳过 `#` 开头的行，再只比对 token。
    """
    rules = []
    with io.open(_MANIFEST, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            rules.append((parts[0].lower(), parts[1:]))
    return rules


def _manifest_selects(rules, relpath, include_only=False):
    """按 distutils 的直觉模拟：include 类规则先收集，exclude 类规则后剔除。

    `recursive-include <dir> <pat>...` 只在 `<dir>/` 前缀内按 glob 匹配；
    `exclude` / `global-exclude` 按整个相对路径匹配。返回 True 表示该路径
    最终会进 sdist。`include_only=True` 时只看收集阶段（用于区分「压根没有
    收集规则」和「有规则但被 exclude 剔掉了」两种失败）。
    """
    selected = False
    for cmd, args in rules:
        if not args:
            continue
        if cmd == "include":
            if any(fnmatch.fnmatch(relpath, a) for a in args):
                selected = True
        elif cmd == "recursive-include":
            base = args[0].rstrip("/")
            if relpath.startswith(base + "/") and any(
                fnmatch.fnmatch(relpath, a) for a in args[1:]
            ):
                selected = True
    if include_only:
        return selected
    for cmd, args in rules:
        if not args:
            continue
        if cmd == "exclude":
            if any(fnmatch.fnmatch(relpath, a) for a in args):
                selected = False
        elif cmd == "recursive-exclude":
            base = args[0].rstrip("/")
            if relpath.startswith(base + "/") and any(
                fnmatch.fnmatch(relpath, a) for a in args[1:]
            ):
                selected = False
        elif cmd == "global-exclude":
            if any(fnmatch.fnmatch(relpath, a) for a in args):
                selected = False
    return selected


def test_manifest_in_covers_rust_rebuild_inputs():
    """`MANIFEST.in` 必须让 sdist 能重建 `dcg/_accel`：Rust 源 + Cargo.lock 都要在。

    背景：2.2.3 交付前 sdist 曾完全不含 `dcg/_accel` 的 Rust 源，从 sdist 无法
    重建 Rust 扩展；且清单里若写 `global-exclude *.py[cod]`，字符类会匹配 `.pyd`，
    把预编译二进制一起排掉（实测 sdist 里 `_accel.pyd` 消失）。两条都靠人肉发现，
    故在此固化。

    只读 `MANIFEST.in` 文本：不依赖 `dist/`（被 `.gitignore` 排除，CI 干净树里
    没有）、不 skip、不读二进制 —— 守的是**根因**（规则写错/写漏），不是产物。
    """
    assert os.path.isfile(_MANIFEST), (
        "仓库根缺 MANIFEST.in：sdist 会退回 setuptools 默认规则，"
        "`dcg/_accel` 的 Rust 源（*.rs / Cargo.toml / build.rs / Cargo.lock）"
        "一个都进不去，从 sdist 无法重建 Rust 扩展"
    )
    rules = _manifest_rules()
    assert rules, "MANIFEST.in 里没有任何有效规则（只有空行或注释）"

    # 1. 逐条要求：Rust 源、Cargo.lock、build.rs、预编译二进制都要有**收集**规则。
    #    只取 include 类命令的参数，避免把 `recursive-exclude ... *` 之类的通配
    #    误当成覆盖证据；比较用 basename，这样 `include dcg/_accel/Cargo.lock`
    #    也能满足 `Cargo.lock` 这一项。
    collected = []
    for cmd, args in rules:
        if cmd in ("include", "recursive-include"):
            collected.extend(os.path.basename(a) for a in args)
    for need, why in (
        ("*.rs", "Rust 源（`src/lib.rs` / `src/newfn.rs`）"),
        ("*.toml", "`dcg/_accel/Cargo.toml`"),
        ("build.rs", "`dcg/_accel/build.rs`"),
        ("Cargo.lock", "`dcg/_accel/Cargo.lock`（缺了就不是可重复构建）"),
        ("*.so", "Linux / macOS 预编译扩展"),
        ("*.pyd", "Windows 预编译扩展"),
    ):
        assert any(fnmatch.fnmatch(need, pat) for pat in collected), (
            "MANIFEST.in 没有收集 %s 的规则（需要 `%s`）：%s 不会进 sdist。"
            "当前收集到的模式：%r" % (why, need, need, sorted(set(collected)))
        )

    # 2. 反向断言：任何一条**非注释**规则都不得写成 `*.py[cod]` 这类字符类。
    charclass = []
    for _cmd, args in rules:
        for a in args:
            m = re.match(r"^\*\.py\[([^\]]*)\]$", a)
            if m and "d" in m.group(1):
                charclass.append(a)
    assert not charclass, (
        "MANIFEST.in 里出现了 %r：字符类 `[cod]` 会匹配 `d`，"
        "`_accel.pyd` 被当成字节码一起排掉，sdist 里的 Windows 二进制会消失"
        "（实测过：改成 `global-exclude *.pyc` + `*.pyo` 两行才恢复）"
        % (sorted(set(charclass)),)
    )

    # 3. 端到端模拟：真正需要的文件必须全部「被规则选中且没被 exclude 剔除」。
    needed = [
        "dcg/_accel/Cargo.toml",
        "dcg/_accel/Cargo.lock",
        "dcg/_accel/build.rs",
        "dcg/_accel/src/lib.rs",
        "dcg/_accel/src/newfn.rs",
        "dcg/_accel/_accel.pyd",
        "dcg/_accel/_accel.abi3.so",
        "dcg/_accel/_accel-macos-arm64.abi3.so",
        "dcg/_accel/_accel-macos-x64.abi3.so",
    ]
    dropped = [p for p in needed if not _manifest_selects(rules, p)]
    assert not dropped, (
        "这些文件按 MANIFEST.in 的规则**不会**进 sdist，从 sdist 就无法重建 "
        "Rust 扩展或会丢掉预编译二进制：%s\n"
        "（常见原因：缺 `recursive-include dcg/_accel *.rs *.toml build.rs`，"
        "或 `global-exclude *.py[cod]` 把 `*.pyd` 一起排掉了）" % dropped
    )
