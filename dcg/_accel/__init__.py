"""可选 Rust 加速后端（`dcg._accel._accel`）的宿主包。

Linux 与 Windows 的产物用标准名字（`_accel.abi3.so` / `_accel.pyd`），直接导入即可命中。
macOS 的产物只能由 CI 构建，且同一目录里放不下两个都叫 `_accel.abi3.so` 的 Mach-O，
所以文件名带架构后缀（`_accel-macos-arm64.abi3.so` / `_accel-macos-x64.abi3.so`），
由下面的 `_load()` 按机器架构手动加载。构建脚本 `build_accel.py:artifact_name()` 用
同一张表决定产物名，`tests/test_accel_gate.py` 断言两侧一致。

加载失败不再完全无声：目录里**确实有**候选二进制却加载不起来时发 `RuntimeWarning`
（含失败原因与查找过的路径），并可用 `status()` 查详情。源码树里没编译扩展是正常
情况，那种情况下既没有候选文件、也不会警告。

加载成功之后还要校验**导出面**（`REQUIRED_EXPORTS`）：二进制比源码旧时（典型场景是
只重建了 Windows 的 `.pyd`，Linux/macOS 的 `.so` 还是上一次 CI 的产物），`dcg/runtime.py`
里的无条件调用会撞上 `AttributeError` —— 用户看到的是崩溃，而不是退回纯 Python。
所以缺符号时发 `RuntimeWarning` 并记进 `status()["missing"]`，但**仍然返回模块**：
少几个算子还能用其余十几个，比整个后端作废强。
"""

import glob
import importlib.machinery
import importlib.util
import os
import platform
import sys
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))

#: macOS 产物名族：精确名由架构决定，glob 兜底认这一族。
_MACOS_PREFIX = "_accel-macos-"
_MACOS_SUFFIX = ".abi3.so"

#: 加载成功后**必须**存在的导出符号。
#:
#: 这是运行期侧的一份**冗余**副本：唯一来源是 `evidence/accel_matrix.py` 的
#: `EXPECTED_EXPORTS`（`ci.yml` 的 `assert artifact and exports` 直接读它）。
#: 这里不能 `import evidence.*` —— 那是开发目录，wheel 里根本没有；所以只能硬编码，
#: 并由 `tests/test_accel_gate.py::TestExportContract` 断言两处逐项相等来防漂移。
#:
#: 少了任何一个，`dcg/runtime.py` 里的无条件 `_ACC.xxx(...)` 就会在旧二进制上抛
#: `AttributeError`（Windows 新、Linux/macOS 旧时，本机永远测不出来）。
REQUIRED_EXPORTS = (
    "av", "cl", "ds", "du", "eu", "fb", "fgi", "fms", "fnm", "mn", "mx", "pgi",
    "tms", "ud", "ue", "zp",
)

_state = {
    "loaded": False,
    "path": None,
    "error": None,
    "candidates": [],
    "missing": [],
}


def _missing_exports(module):
    """`module` 上缺失的必需导出（按 `REQUIRED_EXPORTS` 的顺序）。"""
    return [n for n in REQUIRED_EXPORTS if not hasattr(module, n)]


def _check_exports(module, filename, path):
    """校验导出面；缺符号时发 `RuntimeWarning` 并记录，**仍返回原模块**。

    选择在**加载时**一次性算清（而不是首次调用时按需检查）：运行期零开销，且
    「装了旧二进制」这件事在 `import dcg` 那一刻就该被看见，而不是等到某条数据
    恰好走到 `$formatBase` 才崩。
    """
    missing = _missing_exports(module)
    _state["missing"] = missing
    if missing:
        warnings.warn(
            "Rust 加速后端 %s 缺 %d 个导出符号：%s。源码要求 %d 个：%s。"
            "该二进制很可能比源码旧（重建时只更新了本平台的产物？），"
            "缺失的算子会退化为 AttributeError 而不是纯 Python —— 请重新构建。"
            "产物：%s"
            % (filename, len(missing), missing, len(REQUIRED_EXPORTS),
               list(REQUIRED_EXPORTS), path),
            RuntimeWarning,
            stacklevel=3,
        )
    return module


def _candidates():
    """目录里所有看起来像加速产物的文件（按名字排序，便于报错复现）。"""
    names = set()
    for pattern in ("_accel*.pyd", "_accel*.so"):
        names.update(os.path.basename(p) for p in glob.glob(os.path.join(_HERE, pattern)))
    return sorted(names)


def _load(filename):
    """按文件路径加载扩展模块；文件不存在返回 None，加载失败发警告后返回 None。"""
    path = os.path.join(_HERE, filename)
    if not os.path.isfile(path):
        return None
    name = __name__ + "._accel"
    try:
        loader = importlib.machinery.ExtensionFileLoader(name, path)
        spec = importlib.util.spec_from_file_location(name, path, loader=loader)
        module = importlib.util.module_from_spec(spec)
        # 注册进 sys.modules：否则 `import dcg._accel._accel` 会再走一遍文件搜索，
        # 而磁盘上的文件名带架构后缀、按模块名找不到 —— macOS 上 CI 的
        # `import dcg._accel._accel` 与运行期加载的就不是同一个模块了。
        sys.modules[name] = module
        loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 坏二进制可能抛 OSError/ImportError/...
        sys.modules.pop(name, None)
        _state["error"] = "%s: %s" % (type(exc).__name__, exc)
        warnings.warn(
            "Rust 加速后端加载失败（%s），已退回纯 Python 路径。文件：%s；"
            "同目录候选：%s。原因：%s: %s"
            % (filename, path, _candidates() or "无", type(exc).__name__, exc),
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    _state["loaded"] = True
    _state["path"] = path
    return _check_exports(module, filename, path)


def _macos_artifact_name(machine=None):
    """macOS 上该架构对应的产物名；不认识的架构返回 None。

    单独成函数是为了让 `tests/test_accel_gate.py` 能把它和
    `build_accel.artifact_name()` 直接对拍 —— 这两张表漂移过一次，代价是
    「CI 验新二进制、用户跑旧二进制」。
    """
    m = (platform.machine() if machine is None else machine).lower()
    if m in ("arm64", "aarch64"):
        return "_accel-macos-arm64.abi3.so"
    if m in ("x86_64", "amd64", "x64"):
        return "_accel-macos-x64.abi3.so"
    return None


def status():
    """加速后端的加载状态。

    返回 `{"loaded": bool, "path": str|None, "error": str|None,
    "candidates": [str], "missing": [str]}`。
    `candidates` 是 `dcg/_accel/` 下所有候选产物的文件名（不含目录），用于判断
    「到底没编译」还是「编译了但加载不起来」。`missing` 是已加载的二进制缺的
    必需导出符号（空列表 = 导出面完整；加载失败时也是空列表，看 `error`）。
    """
    return {
        "loaded": bool(_state["loaded"]),
        "path": _state["path"],
        "error": _state["error"],
        "candidates": _candidates(),
        "missing": list(_state["missing"]),
    }


if sys.platform == "darwin":
    _wanted = _macos_artifact_name()
    _accel = _load(_wanted) if _wanted else None
    if _accel is None:
        # glob 兜底：精确名找不到时，认目录里唯一/第一个同族文件。构建脚本与这里的
        # 名字表曾经漂移过（脚本产出 `_accel.abi3.so`，这里只找带后缀的名字），
        # 结果是重建之后运行期仍然加载**旧**二进制 —— 静默、且 CI 看不出来。
        family = sorted(
            n for n in _candidates()
            if n.startswith(_MACOS_PREFIX) and n.endswith(_MACOS_SUFFIX))
        if family and _wanted not in family:
            _accel = _load(family[0])
            if _accel is not None:
                warnings.warn(
                    "macOS 加速产物名与期望不符：期望 %s，实际加载 %s。"
                    "请用当前 build_accel.py 重新构建，旧名字的二进制可能是上一次的构建"
                    % (_wanted, family[0]),
                    RuntimeWarning,
                    stacklevel=2,
                )
else:
    try:
        from . import _accel  # noqa: F401  (标准导入：Linux → _accel.abi3.so，Windows → _accel.pyd)
    except ImportError as _exc:
        _accel = None
        _state["error"] = "%s: %s" % (type(_exc).__name__, _exc)
        if _candidates():
            # 目录里有产物却导入失败：abi3 版本不匹配、坏文件、架构不对……都是
            # 值得知道的异常，不能像「没编译」那样静默。
            warnings.warn(
                "Rust 加速后端导入失败（%s），已退回纯 Python 路径。候选：%s；原因：%s"
                % (_exc, _candidates(), _exc),
                RuntimeWarning,
                stacklevel=2,
            )
    else:
        _state["loaded"] = True
        _state["path"] = getattr(_accel, "__file__", None)
        # 标准导入这条路也必须校验：Linux/macOS 的旧 `.so` 就是从这里进来的。
        _check_exports(
            _accel,
            os.path.basename(_state["path"] or "_accel"),
            _state["path"],
        )
